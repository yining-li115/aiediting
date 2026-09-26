from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest
import numpy as np
from PIL import Image

from aive.components import (
    ComponentRegistryApplication,
    LocalRemotionComponentRuntime,
    LocalRemotionSandbox,
    RegisteredCapabilityValidator,
    StorageComponentRegistry,
)
from aive.agent.tools import AgentToolRegistry
from aive.contracts.components import (
    ComponentLicense,
    ComponentSourceFile,
    CreateComponentCandidateRequest,
    RemotionComponentManifest,
    ComponentSearchQuery,
)
from aive.contracts.conversation import (
    AgentTurnContext,
    CreateComponentCandidateCall,
    PatchProposal,
    ProposePatchCall,
    SearchComponentsCall,
    WorkspaceSelection,
)
from aive.contracts.patch import Patch, PatchScope
from aive.contracts.patch_project import AddTrack, SetDependencies
from aive.contracts.project_doc import (
    AssetRef,
    DependencyRef,
    DependencySet,
    FrameRange,
    GraphicClip,
    GraphicTrack,
    OutputSettings,
    ProjectDoc,
    ProjectIntent,
    Rational,
    SourceRange,
    Timeline,
    VideoClip,
    VideoTrack,
)
from aive.contracts.revision import ProjectRevision, RevisionProvenance
from aive.project.hashing import canonical_model_hash
from aive.project.service import ProjectService
from aive.rendering import ProjectDocRemotionRenderer
from aive.storage import LocalStorage
from tests.conftest import requires_ffmpeg


RUNTIME_ROOT = Path(__file__).parents[2] / "apps" / "remotion"


def _component_request() -> CreateComponentCandidateRequest:
    return CreateComponentCandidateRequest(
        request_id="request_graphic_lower_third",
        candidate_id="candidate_graphic_lower_third",
        manifest=RemotionComponentManifest(
            capability_kind="component",
            usage="graphic",
            name="graphic.lower-third",
            version="1.0.0",
            remotion_version="4.0.529",
            parameter_schema={
                "type": "object",
                "properties": {"title": {"type": "string"}},
                "required": ["title"],
                "additionalProperties": False,
            },
            example_props={"title": "Preview"},
            license=ComponentLicense(
                license_id="generated-commercial-v1",
                source="generated",
                commercial_use_allowed=True,
            ),
            created_by_run_id="turn_component_create",
        ),
        source_files=(
            ComponentSourceFile(
                path="component.tsx",
                content="""import React from 'react';
import {AbsoluteFill, interpolate, useCurrentFrame} from 'remotion';
export const Component = ({title, aive}) => {
  const frame = useCurrentFrame();
  const opacity = interpolate(frame, [0, 4], [0, 1], {
    extrapolateLeft: 'clamp', extrapolateRight: 'clamp'
  });
  return <AbsoluteFill style={{backgroundColor: 'transparent'}}>
    <div style={{position: 'absolute', left: 20, bottom: 20, width: 180,
      backgroundColor: '#ff0000', color: 'white', padding: 12,
      fontSize: 22, opacity}}>{title}-{aive.usage}</div>
  </AbsoluteFill>;
};
""",
            ),
        ),
    )


def _base_document() -> ProjectDoc:
    return ProjectDoc(
        project_id="project_remotion_render",
        revision_id="revision_remotion_render",
        intent=ProjectIntent(task_mode="autonomous", target_duration_frames=30),
        assets=(AssetRef(asset_id="asset_source", kind="video"),),
        timeline=Timeline(
            duration_frames=30,
            output=OutputSettings(
                width=320,
                height=180,
                fps=Rational(num=30, den=1),
            ),
            tracks=(
                VideoTrack(
                    track_id="primary",
                    role="primary",
                    clips=(
                        VideoClip(
                            clip_id="visual",
                            asset_id="asset_source",
                            source=SourceRange(start=0, end=1_000, timescale=1_000),
                            output=FrameRange(start_frame=0, duration_frames=30),
                        ),
                    ),
                ),
            ),
        ),
        dependencies=DependencySet(
            renderer=DependencyRef(name="ffmpeg-remotion", version="1.0.0"),
        ),
    )


def _agent_context(
    document: ProjectDoc,
    *,
    turn_id: str,
    allowed_tools: tuple[str, ...],
) -> AgentTurnContext:
    return AgentTurnContext(
        turn_id=turn_id,
        project_id=document.project_id,
        conversation_id="conversation_remotion",
        base_revision_id=document.revision_id,
        document_hash=canonical_model_hash(document),
        instruction="Add an animated investor lower third",
        document=document,
        workspace=WorkspaceSelection(revision_id=document.revision_id),
        allowed_tools=allowed_tools,
        max_tool_steps=6,
    )


@requires_ffmpeg
@pytest.mark.timeout(180)
def test_published_component_renders_into_revision_bound_video(tmp_path: Path) -> None:
    sandbox = LocalRemotionSandbox(runtime_root=RUNTIME_ROOT, timeout_seconds=90)
    if issue := sandbox.availability_issue():
        pytest.skip(issue)
    storage = LocalStorage(tmp_path / "storage")
    registry = StorageComponentRegistry(storage)
    components = ComponentRegistryApplication(
        registry=registry,
        sandbox=sandbox,
        clock=lambda: datetime(2026, 9, 26, tzinfo=timezone.utc),
    )
    creation_tools = AgentToolRegistry(
        _agent_context(
            _base_document(),
            turn_id="turn_component_create",
            allowed_tools=("create_component_candidate",),
        ),
        component_tools=components,
    )
    creation = creation_tools.execute(
        CreateComponentCandidateCall(
            call_id="call_create_component",
            request=_component_request(),
        )
    )
    candidate = creation.component_candidate
    assert candidate is not None
    published = components.publish(
        candidate_id=candidate.candidate_id,
        approved_by="reviewer_1",
        summary="Real bundle and preview accepted.",
    )
    runtime = LocalRemotionComponentRuntime(
        runtime_root=RUNTIME_ROOT,
        registry=registry,
        timeout_seconds=120,
    )
    base_document = _base_document()
    use_tools = AgentToolRegistry(
        _agent_context(
            base_document,
            turn_id="turn_component_use",
            allowed_tools=("search_components", "propose_patch"),
        ),
        component_tools=components,
    )
    search = use_tools.execute(
        SearchComponentsCall(
            call_id="call_search_component",
            query=ComponentSearchQuery(usage="graphic"),
        )
    )
    assert search.components == (published,)
    proposal = PatchProposal(
        summary="Add the approved investor lower third",
        rationale="The exact published component and bundle digest are pinned.",
        scope=PatchScope(kind="global"),
        operations=(
            SetDependencies(
                operation_id="operation_pin_component",
                dependencies=base_document.dependencies.model_copy(
                    update={
                        "components": (
                            DependencyRef(
                                name=published.manifest.name,
                                version=published.manifest.version,
                                content_hash=published.bundle_digest,
                            ),
                        )
                    }
                ),
            ),
            AddTrack(
                operation_id="operation_add_graphics",
                track=GraphicTrack(
                    track_id="graphics",
                    graphics=(
                        GraphicClip(
                            graphic_id="lower_third",
                            output=FrameRange(start_frame=5, duration_frames=20),
                            component_name=published.manifest.name,
                            component_version=published.manifest.version,
                            props={"title": "Investor Demo"},
                        ),
                    ),
                ),
            ),
        ),
    )
    proposal_result = use_tools.execute(
        ProposePatchCall(call_id="call_propose_component", proposal=proposal)
    )
    assert proposal_result.proposal == proposal
    base_revision = ProjectRevision(
        commit_id="commit_remotion_base",
        created_at=datetime(2026, 9, 26, tzinfo=timezone.utc),
        document_hash=canonical_model_hash(base_document),
        provenance=RevisionProvenance(kind="initial", origin="system"),
        document=base_document,
    )
    patch = Patch(
        patch_id="patch_remotion_component",
        project_id=base_document.project_id,
        base_revision_id=base_document.revision_id,
        idempotency_key="commit_remotion_component",
        origin="agent",
        summary=proposal.summary,
        scope=proposal.scope,
        operations=proposal.operations,
    )
    document = ProjectService(
        RegisteredCapabilityValidator(
            registry,
            supported_usages=frozenset({"caption", "graphic"}),
            runtime=runtime,
        )
    ).create_patch_candidate(
        base_revision=base_revision,
        patch=patch,
        commit_id="commit_remotion_component",
        revision_id="revision_remotion_component",
        created_at=datetime(2026, 9, 26, tzinfo=timezone.utc),
    ).revision.document
    source = tmp_path / "source.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=320x180:r=30:d=1",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=48000:cl=stereo:d=1",
            "-shortest",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(source),
        ],
        check=True,
    )
    output = tmp_path / "render.mp4"
    renderer = ProjectDocRemotionRenderer(
        registry=registry,
        component_runtime=runtime,
    )

    renderer.validate(document)
    result = renderer.render(
        document,
        "hq",
        output,
        {"asset_source": source},
    )

    assert result.duration_sec == pytest.approx(1, abs=0.08)
    assert _red_pixel_count(output, tmp_path / "frame.png", at_seconds=0.5) > 500

    range_output = tmp_path / "range-preview.mp4"
    range_result = renderer.render(
        document,
        "proxy",
        range_output,
        {"asset_source": source},
        FrameRange(start_frame=10, duration_frames=10),
    )

    assert range_result.duration_sec == pytest.approx(10 / 30, abs=0.08)
    assert (
        _red_pixel_count(
            range_output,
            tmp_path / "range-frame.png",
            at_seconds=0.15,
        )
        > 500
    )


def _red_pixel_count(video: Path, frame: Path, *, at_seconds: float) -> int:
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            str(at_seconds),
            "-i",
            str(video),
            "-frames:v",
            "1",
            str(frame),
        ],
        check=True,
    )
    pixels = np.asarray(Image.open(frame).convert("RGB"))
    return int(
        (
            (pixels[..., 0] > 180)
            & (pixels[..., 1] < 80)
            & (pixels[..., 2] < 80)
        ).sum()
    )
