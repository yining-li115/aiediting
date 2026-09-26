"""Fail-closed local sandbox adapter for generated Remotion source."""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from ..contracts.components import (
    ComponentValidationIssue,
    ComponentValidationReport,
    CreateComponentCandidateRequest,
)


REMOTION_RUNTIME_VERSION = "4.0.529"


@dataclass(frozen=True)
class ComponentSandboxArtifacts:
    report: ComponentValidationReport
    source_archive: bytes
    bundle_archive: bytes | None
    preview_png: bytes | None


@runtime_checkable
class ComponentSandbox(Protocol):
    def validate(
        self,
        request: CreateComponentCandidateRequest,
    ) -> ComponentSandboxArtifacts: ...


class LocalRemotionSandbox:
    """Compile and render untrusted source inside a macOS deny-network profile.

    This is the local investor-demo adapter. Production must replace it with a
    container/microVM implementation; absence of ``sandbox-exec`` fails closed.
    """

    def __init__(
        self,
        *,
        runtime_root: Path,
        node_bin: str = "node",
        sandbox_bin: str = "sandbox-exec",
        chrome_executable: Path | None = None,
        timeout_seconds: int = 45,
    ) -> None:
        self._runtime_root = runtime_root.resolve()
        self._node_bin = node_bin
        self._sandbox_bin = sandbox_bin
        self._chrome = chrome_executable or find_remotion_browser(
            self._runtime_root
        )
        self._timeout = timeout_seconds

    def validate(
        self,
        request: CreateComponentCandidateRequest,
    ) -> ComponentSandboxArtifacts:
        started = time.monotonic()
        archived_source = source_archive(request)
        if request.manifest.remotion_version != REMOTION_RUNTIME_VERSION:
            return self._rejected(
                started,
                archived_source,
                "remotion_version_mismatch",
                f"candidate requires Remotion {request.manifest.remotion_version}, "
                f"runtime is {REMOTION_RUNTIME_VERSION}",
            )
        runner = self._runtime_root / "scripts" / "validate-candidate.mjs"
        if not runner.is_file() or not (self._runtime_root / "node_modules").is_dir():
            return self._rejected(
                started,
                archived_source,
                "remotion_runtime_unavailable",
                "Remotion runtime dependencies are not installed",
            )
        sandbox_bin = shutil.which(self._sandbox_bin)
        node_bin = shutil.which(self._node_bin)
        if sandbox_bin is None or node_bin is None:
            return self._rejected(
                started,
                archived_source,
                "sandbox_unavailable",
                "local Remotion validation requires sandbox-exec and Node.js",
            )
        if self._chrome is None:
            return self._rejected(
                started,
                archived_source,
                "browser_unavailable",
                "managed Remotion Headless Shell is not installed; run "
                "`npm run install-browser` in apps/remotion",
            )

        with tempfile.TemporaryDirectory(prefix="aive_remotion_candidate_") as raw:
            workspace = Path(raw).resolve()
            source_root = workspace / "source"
            output_root = workspace / "output"
            temp_root = workspace / "tmp"
            source_root.mkdir()
            output_root.mkdir()
            temp_root.mkdir()
            for source in request.source_files:
                target = (source_root / source.path).resolve()
                if not target.is_relative_to(source_root):
                    raise ValueError("component source escaped sandbox root")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(source.content, encoding="utf-8")
            manifest_path = workspace / "manifest.json"
            manifest_path.write_text(
                request.manifest.model_dump_json(),
                encoding="utf-8",
            )
            profile = workspace / "sandbox.sb"
            profile.write_text(remotion_sandbox_profile(workspace), encoding="utf-8")
            command = [
                sandbox_bin,
                "-f",
                str(profile),
                node_bin,
                str(runner),
                str(manifest_path),
                str(source_root),
                str(output_root),
                str(self._chrome),
            ]
            environment = {
                "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                "LANG": os.environ.get("LANG", "C.UTF-8"),
                "LC_ALL": os.environ.get("LC_ALL", "C.UTF-8"),
                "NODE_ENV": "production",
                "TMPDIR": str(temp_root),
            }
            network_probe = subprocess.run(
                [
                    sandbox_bin,
                    "-f",
                    str(profile),
                    node_bin,
                    "-e",
                    (
                        "fetch('https://example.com')"
                        ".then(() => process.exit(23))"
                        ".catch(() => process.exit(0))"
                    ),
                ],
                cwd=self._runtime_root,
                env=environment,
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            if network_probe.returncode != 0:
                return self._rejected(
                    started,
                    archived_source,
                    "sandbox_network_open",
                    "sandbox network-denial probe did not fail closed",
                )
            try:
                result = subprocess.run(
                    command,
                    cwd=self._runtime_root,
                    env=environment,
                    capture_output=True,
                    text=True,
                    timeout=self._timeout,
                    check=False,
                )
            except subprocess.TimeoutExpired:
                return self._rejected(
                    started,
                    archived_source,
                    "sandbox_timeout",
                    f"Remotion candidate exceeded {self._timeout} seconds",
                )
            if result.returncode != 0:
                message = (result.stderr or result.stdout or "Remotion validation failed")
                return self._rejected(
                    started,
                    archived_source,
                    "sandbox_build_failed",
                    _safe_diagnostic(message),
                )
            try:
                raw_result = json.loads(
                    (output_root / "result.json").read_text(encoding="utf-8")
                )
                bundle_dir = Path(raw_result["bundleDir"]).resolve()
                preview_path = Path(raw_result["previewPath"]).resolve()
                if not bundle_dir.is_relative_to(output_root):
                    raise ValueError("bundle path escaped output root")
                if not preview_path.is_relative_to(output_root):
                    raise ValueError("preview path escaped output root")
                bundle_archive = _directory_archive(bundle_dir)
                preview_png = preview_path.read_bytes()
                if not preview_png.startswith(b"\x89PNG\r\n\x1a\n"):
                    raise ValueError("Remotion preview is not a PNG")
                imported = tuple(raw_result.get("importedPackages", ()))
            except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                return self._rejected(
                    started,
                    archived_source,
                    "sandbox_artifact_invalid",
                    _safe_diagnostic(str(exc)),
                )

        return ComponentSandboxArtifacts(
            report=ComponentValidationReport(
                accepted=True,
                sandboxed=True,
                network_disabled=True,
                build_succeeded=True,
                preview_succeeded=True,
                runtime_version=REMOTION_RUNTIME_VERSION,
                imported_packages=imported,
                duration_ms=round((time.monotonic() - started) * 1_000),
            ),
            source_archive=archived_source,
            bundle_archive=bundle_archive,
            preview_png=preview_png,
        )

    def availability_issue(self) -> str | None:
        """Return why the host cannot launch the required OS sandbox, if any."""

        runner = self._runtime_root / "scripts" / "validate-candidate.mjs"
        if not runner.is_file() or not (self._runtime_root / "node_modules").is_dir():
            return "Remotion runtime dependencies are not installed"
        sandbox_bin = shutil.which(self._sandbox_bin)
        node_bin = shutil.which(self._node_bin)
        if sandbox_bin is None or node_bin is None:
            return "sandbox-exec or Node.js is unavailable"
        if self._chrome is None:
            return (
                "managed Remotion Headless Shell is unavailable; run "
                "`npm run install-browser` in apps/remotion"
            )
        probe = subprocess.run(
            [
                sandbox_bin,
                "-p",
                "(version 1)(allow default)(deny network*)",
                node_bin,
                "-e",
                "process.exit(0)",
            ],
            cwd=self._runtime_root,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if probe.returncode != 0:
            return _safe_diagnostic(probe.stderr or probe.stdout)
        return None

    @staticmethod
    def _rejected(
        started: float,
        source_archive: bytes,
        code: str,
        message: str,
    ) -> ComponentSandboxArtifacts:
        return ComponentSandboxArtifacts(
            report=ComponentValidationReport(
                accepted=False,
                sandboxed=True,
                network_disabled=True,
                build_succeeded=False,
                preview_succeeded=False,
                runtime_version=REMOTION_RUNTIME_VERSION,
                issues=(
                    ComponentValidationIssue(
                        code=code,
                        severity="error",
                        message=message,
                    ),
                ),
                duration_ms=round((time.monotonic() - started) * 1_000),
            ),
            source_archive=source_archive,
            bundle_archive=None,
            preview_png=None,
        )


def source_digest(request: CreateComponentCandidateRequest) -> str:
    digest = hashlib.sha256()
    for source in sorted(request.source_files, key=lambda item: item.path):
        digest.update(source.path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(source.content.encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


def source_archive(request: CreateComponentCandidateRequest) -> bytes:
    entries = tuple(
        (source.path, source.content.encode("utf-8"))
        for source in sorted(request.source_files, key=lambda item: item.path)
    )
    return _tar_gzip(entries)


def _directory_archive(root: Path) -> bytes:
    entries = tuple(
        (str(path.relative_to(root)), path.read_bytes())
        for path in sorted(root.rglob("*"))
        if path.is_file()
    )
    return _tar_gzip(entries)


def _tar_gzip(entries: tuple[tuple[str, bytes], ...]) -> bytes:
    tar_buffer = io.BytesIO()
    with tarfile.open(fileobj=tar_buffer, mode="w") as archive:
        for name, content in entries:
            info = tarfile.TarInfo(name=name)
            info.size = len(content)
            info.mtime = 0
            info.mode = 0o644
            info.uid = 0
            info.gid = 0
            archive.addfile(info, io.BytesIO(content))
    output = io.BytesIO()
    with gzip.GzipFile(fileobj=output, mode="wb", mtime=0) as compressed:
        compressed.write(tar_buffer.getvalue())
    return output.getvalue()


def remotion_sandbox_profile(workspace: Path) -> str:
    escaped = str(workspace).replace('"', '\\"')
    return f"""(version 1)
(allow default)
(deny network-outbound)
(deny network-inbound)
(allow network-outbound (remote ip "localhost:*"))
(allow network-inbound (local ip "localhost:*"))
(deny file-write*)
(allow file-write* (subpath \"{escaped}\"))
"""


def find_remotion_browser(runtime_root: Path) -> Path | None:
    """Resolve only Remotion's pinned Headless Shell, never a user browser."""

    cache_root = runtime_root / "node_modules" / ".remotion" / "chrome-headless-shell"
    if not cache_root.is_dir():
        return None
    candidates = (
        *cache_root.glob("*/chrome-headless-shell-*/chrome-headless-shell"),
        *cache_root.glob("*/chrome-headless-shell-*/headless_shell"),
        *cache_root.glob("*/chrome-headless-shell-*/chrome-headless-shell.exe"),
    )
    return next((path.resolve() for path in candidates if path.is_file()), None)


def _safe_diagnostic(message: str) -> str:
    compact = " ".join(message.split())
    return compact[-2_000:] or "Remotion validation failed"


__all__ = [
    "REMOTION_RUNTIME_VERSION",
    "ComponentSandbox",
    "ComponentSandboxArtifacts",
    "LocalRemotionSandbox",
    "find_remotion_browser",
    "remotion_sandbox_profile",
    "source_archive",
    "source_digest",
]
