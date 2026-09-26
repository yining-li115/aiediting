from __future__ import annotations

from pathlib import Path

import pytest

from aive.components import LocalRemotionSandbox
from aive.contracts.components import (
    ComponentLicense,
    ComponentSourceFile,
    CreateComponentCandidateRequest,
    RemotionComponentManifest,
)


RUNTIME_ROOT = Path(__file__).parents[2] / "apps" / "remotion"


def _request(source: str) -> CreateComponentCandidateRequest:
    return CreateComponentCandidateRequest(
        request_id="request_real_remotion_1",
        candidate_id="candidate_real_remotion_1",
        manifest=RemotionComponentManifest(
            capability_kind="component",
            usage="graphic",
            name="graphic.sandbox-proof",
            version="1.0.0",
            remotion_version="4.0.529",
            parameter_schema={
                "type": "object",
                "properties": {"title": {"type": "string"}},
                "required": ["title"],
                "additionalProperties": False,
            },
            example_props={"title": "Sandboxed"},
            license=ComponentLicense(
                license_id="generated-commercial-v1",
                source="generated",
                commercial_use_allowed=True,
            ),
        ),
        source_files=(ComponentSourceFile(path="component.tsx", content=source),),
    )


@pytest.mark.timeout(60)
def test_real_remotion_candidate_builds_and_renders_png() -> None:
    sandbox = LocalRemotionSandbox(runtime_root=RUNTIME_ROOT)
    if issue := sandbox.availability_issue():
        pytest.skip(issue)
    result = sandbox.validate(
        _request(
            """import React from 'react';
import {AbsoluteFill, interpolate, useCurrentFrame} from 'remotion';
export const Component = ({title}: {title: string}) => {
  const frame = useCurrentFrame();
  const scale = interpolate(frame, [0, 29], [0.8, 1]);
  return <AbsoluteFill style={{backgroundColor: '#121826', color: 'white',
    alignItems: 'center', justifyContent: 'center', fontSize: 56,
    transform: `scale(${scale})`}}>{title}</AbsoluteFill>;
};
"""
        )
    )

    assert result.report.accepted, result.report.issues
    assert result.report.network_disabled is True
    assert result.bundle_archive is not None
    assert result.preview_png is not None
    assert result.preview_png.startswith(b"\x89PNG\r\n\x1a\n")


@pytest.mark.timeout(60)
def test_real_remotion_candidate_rejects_unapproved_import() -> None:
    sandbox = LocalRemotionSandbox(runtime_root=RUNTIME_ROOT)
    if issue := sandbox.availability_issue():
        pytest.skip(issue)
    result = sandbox.validate(
        _request(
            """import React from 'react';
import fs from 'node:fs';
export const Component = () => <div>{String(fs)}</div>;
"""
        )
    )

    assert result.report.accepted is False
    assert result.report.issues[0].code == "sandbox_build_failed"
    assert "disallowed package" in result.report.issues[0].message
