"""Execute an approved Remotion bundle inside the local render sandbox."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from pydantic import JsonValue

from ..contracts.components import PublishedComponent
from .repository import ComponentRegistry
from .sandbox import find_remotion_browser, remotion_sandbox_profile


class RemotionComponentRuntimeError(RuntimeError):
    pass


@dataclass(frozen=True)
class ComponentRenderRequest:
    component: PublishedComponent
    component_props: dict[str, JsonValue]
    aive_context: dict[str, JsonValue]
    duration_in_frames: int
    fps: float
    width: int
    height: int


@runtime_checkable
class RemotionComponentRuntime(Protocol):
    def validate_available(self) -> None: ...

    def render(self, request: ComponentRenderRequest, output: Path) -> None: ...


class LocalRemotionComponentRuntime:
    """Render only validated registry bundles with external networking denied."""

    def __init__(
        self,
        *,
        runtime_root: Path,
        registry: ComponentRegistry,
        node_bin: str = "node",
        sandbox_bin: str = "sandbox-exec",
        chrome_executable: Path | None = None,
        timeout_seconds: int = 120,
    ) -> None:
        self._runtime_root = runtime_root.resolve()
        self._registry = registry
        self._node_bin = node_bin
        self._sandbox_bin = sandbox_bin
        self._chrome = chrome_executable or find_remotion_browser(self._runtime_root)
        self._timeout = timeout_seconds

    def validate_available(self) -> None:
        runner = self._runtime_root / "scripts" / "render-component.mjs"
        sandbox_bin = shutil.which(self._sandbox_bin)
        node_bin = shutil.which(self._node_bin)
        if not runner.is_file() or not (self._runtime_root / "node_modules").is_dir():
            raise RemotionComponentRuntimeError(
                "Remotion runtime dependencies are not installed"
            )
        if sandbox_bin is None or node_bin is None:
            raise RemotionComponentRuntimeError(
                "local Remotion rendering requires sandbox-exec and Node.js"
            )
        if self._chrome is None:
            raise RemotionComponentRuntimeError(
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
            message = " ".join((probe.stderr or probe.stdout).split())[-2_000:]
            raise RemotionComponentRuntimeError(
                message or "macOS render sandbox is unavailable"
            )

    def render(self, request: ComponentRenderRequest, output: Path) -> None:
        if request.duration_in_frames <= 0:
            raise RemotionComponentRuntimeError("component duration must be positive")
        if request.fps <= 0 or request.width <= 0 or request.height <= 0:
            raise RemotionComponentRuntimeError("component render metadata is invalid")
        self.validate_available()
        runner = self._runtime_root / "scripts" / "render-component.mjs"
        sandbox_bin = shutil.which(self._sandbox_bin)
        node_bin = shutil.which(self._node_bin)
        assert sandbox_bin is not None
        assert node_bin is not None
        assert self._chrome is not None

        bundle_archive = self._registry.published_artifact(
            request.component,
            "bundle.tar.gz",
        )
        with tempfile.TemporaryDirectory(prefix="aive_remotion_render_") as raw:
            workspace = Path(raw).resolve()
            bundle_root = workspace / "bundle"
            temp_root = workspace / "tmp"
            bundle_root.mkdir()
            temp_root.mkdir()
            _extract_archive(bundle_archive, bundle_root)
            input_path = workspace / "input.json"
            input_path.write_text(
                json.dumps(
                    {
                        "componentProps": request.component_props,
                        "aive": request.aive_context,
                        "__aiveRuntime": {
                            "durationInFrames": request.duration_in_frames,
                            "fps": request.fps,
                            "width": request.width,
                            "height": request.height,
                        },
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                encoding="utf-8",
            )
            sandbox_output = workspace / "component.mov"
            profile = workspace / "sandbox.sb"
            profile.write_text(remotion_sandbox_profile(workspace), encoding="utf-8")
            environment = {
                "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                "LANG": os.environ.get("LANG", "C.UTF-8"),
                "LC_ALL": os.environ.get("LC_ALL", "C.UTF-8"),
                "NODE_ENV": "production",
                "TMPDIR": str(temp_root),
            }
            try:
                result = subprocess.run(
                    [
                        sandbox_bin,
                        "-f",
                        str(profile),
                        node_bin,
                        str(runner),
                        str(bundle_root),
                        str(input_path),
                        str(sandbox_output),
                        str(self._chrome),
                    ],
                    cwd=self._runtime_root,
                    env=environment,
                    capture_output=True,
                    text=True,
                    timeout=self._timeout,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                raise RemotionComponentRuntimeError(
                    f"Remotion component exceeded {self._timeout} seconds"
                ) from exc
            if result.returncode != 0:
                message = " ".join(
                    (result.stderr or result.stdout or "component render failed").split()
                )[-2_000:]
                raise RemotionComponentRuntimeError(message)
            if not sandbox_output.is_file() or sandbox_output.stat().st_size == 0:
                raise RemotionComponentRuntimeError(
                    "Remotion component produced no output"
                )
            output.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(sandbox_output, output)


def _extract_archive(archive_path: Path, destination: Path) -> None:
    with tarfile.open(archive_path, mode="r:gz") as archive:
        for member in archive.getmembers():
            member_path = (destination / member.name).resolve()
            if (
                not member_path.is_relative_to(destination)
                or member.issym()
                or member.islnk()
                or not (member.isfile() or member.isdir())
            ):
                raise RemotionComponentRuntimeError(
                    "published component bundle contains an unsafe archive entry"
                )
            if member.isdir():
                member_path.mkdir(parents=True, exist_ok=True)
                continue
            source = archive.extractfile(member)
            if source is None:
                raise RemotionComponentRuntimeError(
                    "published component bundle contains an unreadable file"
                )
            member_path.parent.mkdir(parents=True, exist_ok=True)
            with source, member_path.open("wb") as target:
                shutil.copyfileobj(source, target)


__all__ = [
    "ComponentRenderRequest",
    "LocalRemotionComponentRuntime",
    "RemotionComponentRuntime",
    "RemotionComponentRuntimeError",
]
