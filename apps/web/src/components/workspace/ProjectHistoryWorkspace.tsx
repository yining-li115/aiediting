"use client";

import {
  AlertCircle,
  Check,
  Clock3,
  Download,
  Film,
  Flag,
  LoaderCircle,
  MessageSquare,
  Play,
  RotateCcw,
  Sparkles,
  X,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  ApiError,
  assetProxyUrl,
  assetThumbnailUrl,
  createRevisionRender,
  getCurrentProjectRevision,
  getLatestFirstCut,
  listProjectAssets,
  listProjectRevisions,
  listRevisionRenders,
  renderContentUrl,
  restoreProjectRevision,
  waitForJob,
} from "@/lib/api/client";
import type {
  AssetProcessingStatus,
  CreateRenderRequest,
  DirectorRun,
  FrameRange,
  PreviewAnnotation,
  ProjectRevision,
  RenderArtifact,
  RestoreRevisionRequest,
} from "@/lib/api/types";
import { ConversationPanel } from "./ConversationPanel";

interface ProjectHistoryWorkspaceProps {
  projectId: string;
}

function newStableId(prefix: string) {
  return `${prefix}_${crypto.randomUUID()}`;
}

function revisionTitle(revision: ProjectRevision) {
  if (revision.provenance.summary) return revision.provenance.summary;
  if (revision.provenance.kind === "initial") return "Initial version";
  if (revision.provenance.kind === "restore") return "Restored version";
  return "Project edit";
}

function revisionKind(revision: ProjectRevision) {
  if (revision.provenance.kind === "initial") return "Initial";
  if (revision.provenance.kind === "restore") return "Restore";
  return revision.provenance.origin === "agent" ? "AI edit" : "Edit";
}

function formatCreatedAt(value: string) {
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

function durationLabel(revision: ProjectRevision) {
  const { duration_frames: frames, output } = revision.document.timeline;
  const seconds = (frames * output.fps.den) / output.fps.num;
  return `${seconds.toFixed(seconds % 1 === 0 ? 0 : 1)}s · ${frames} frames`;
}

function previewFrameAtTime(
  revision: ProjectRevision,
  artifact: RenderArtifact,
  currentTime: number,
) {
  const fps = revision.document.timeline.output.fps;
  const offset = artifact.output_range?.start_frame ?? 0;
  const duration =
    artifact.output_range?.duration_frames ??
    revision.document.timeline.duration_frames;
  const elapsedFrames = Number.isFinite(currentTime)
    ? Math.max(0, Math.floor((currentTime * fps.num) / fps.den))
    : 0;
  return Math.min(
    offset + duration - 1,
    offset + elapsedFrames,
  );
}

async function fetchRevisionState(projectId: string, signal?: AbortSignal) {
  const [history, head, assets, firstCut] = await Promise.all([
    listProjectRevisions(projectId, signal),
    getCurrentProjectRevision(projectId, signal),
    listProjectAssets(projectId, signal),
    getLatestFirstCut(projectId, signal),
  ]);
  return { history, head, assets, firstCut };
}

function readinessLabel(status?: AssetProcessingStatus) {
  if (!status) return "Not registered";
  if (status.readiness === "uploaded") return "Waiting";
  if (status.readiness === "processing") return "Preparing media";
  if (status.readiness === "ready") {
    if (status.analysis_readiness === "pending") return "Waiting for analysis";
    if (status.analysis_readiness === "processing") return "Understanding footage";
    if (status.analysis_readiness === "ready") {
      return `Understood · ${status.moment_count} ${status.moment_count === 1 ? "moment" : "moments"} · ${status.transcript_count} transcript segments`;
    }
    if (status.analysis_readiness === "partial") {
      return `Partially understood · ${status.moment_count} moments · ${status.transcript_count} transcript segments`;
    }
    return "Analysis failed";
  }
  return "Processing failed";
}

export function ProjectHistoryWorkspace({
  projectId,
}: ProjectHistoryWorkspaceProps) {
  const [revisions, setRevisions] = useState<ProjectRevision[]>([]);
  const [current, setCurrent] = useState<ProjectRevision | null>(null);
  const [assetStatuses, setAssetStatuses] = useState<AssetProcessingStatus[]>([]);
  const [firstCut, setFirstCut] = useState<DirectorRun | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [restoring, setRestoring] = useState(false);
  const [renderingKind, setRenderingKind] = useState<
    "preview" | "range" | "export" | null
  >(null);
  const [renderProgress, setRenderProgress] = useState(0);
  const [renderArtifacts, setRenderArtifacts] = useState<RenderArtifact[]>([]);
  const [rangeStart, setRangeStart] = useState(0);
  const [rangeDuration, setRangeDuration] = useState(90);
  const [previewPlayhead, setPreviewPlayhead] = useState<{
    artifactId: string;
    revisionId: string;
    frame: number;
  } | null>(null);
  const [previewMode, setPreviewMode] = useState<"preview" | "annotate">(
    "preview",
  );
  const [annotationStartFrame, setAnnotationStartFrame] = useState<number | null>(
    null,
  );
  const [annotationDraftRange, setAnnotationDraftRange] =
    useState<FrameRange | null>(null);
  const [annotationComment, setAnnotationComment] = useState("");
  const [previewAnnotations, setPreviewAnnotations] = useState<
    PreviewAnnotation[]
  >([]);
  const [error, setError] = useState<string | null>(null);
  const hasActiveMedia = assetStatuses.some(
    (asset) =>
      asset.readiness === "uploaded" ||
      asset.readiness === "processing" ||
      asset.analysis_readiness === "pending" ||
      asset.analysis_readiness === "processing",
  );
  const hasFailedMedia = assetStatuses.some(
    (asset) =>
      asset.readiness === "failed" || asset.analysis_readiness === "failed",
  );
  const currentHasPrimaryVisual = current?.document.timeline.tracks?.some(
    (track) =>
      track.kind === "video" &&
      track.role === "primary" &&
      track.enabled !== false &&
      (track.clips?.length ?? 0) > 0,
  );
  const firstCutActive =
    firstCut?.status === "queued" ||
    firstCut?.status === "running" ||
    firstCut?.status === "committed" ||
    firstCut?.status === "awaiting_review" ||
    firstCut?.status === "reviewing" ||
    firstCut?.status === "autofixing";
  const waitingForFirstCut =
    assetStatuses.length > 0 &&
    !currentHasPrimaryVisual &&
    !hasFailedMedia &&
    firstCut?.status !== "failed" &&
    firstCut?.status !== "render_failed" &&
    firstCut?.status !== "review_rejected" &&
    firstCut?.status !== "review_failed";
  const pendingRestore = useRef<{
    actionKey: string;
    request: RestoreRevisionRequest;
  } | null>(null);
  const pendingRenders = useRef(new Map<string, CreateRenderRequest>());
  const currentRevisionRef = useRef<string | null>(null);

  const refresh = useCallback(
    async (signal?: AbortSignal) => {
      const { history, head, assets, firstCut: latestFirstCut } = await fetchRevisionState(
        projectId,
        signal,
      );
      const previousHeadId = currentRevisionRef.current;
      setRevisions(history);
      setCurrent(head);
      setAssetStatuses(assets);
      setFirstCut(latestFirstCut);
      setSelectedId((existing) => {
        const stillExists = history.some(
          (item) => item.document.revision_id === existing,
        );
        if (!stillExists || existing === previousHeadId) {
          return head.document.revision_id;
        }
        return existing;
      });
      currentRevisionRef.current = head.document.revision_id;
    },
    [projectId],
  );

  useEffect(() => {
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      refresh(controller.signal)
        .catch((cause: unknown) => {
          if (cause instanceof DOMException && cause.name === "AbortError") return;
          setError(
            cause instanceof ApiError
              ? cause.detail
              : "Could not load this project's revision history.",
          );
        })
        .finally(() => {
          if (!controller.signal.aborted) setLoading(false);
        });
    }, 0);
    return () => {
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [projectId, refresh]);

  useEffect(() => {
    if (!hasActiveMedia && !firstCutActive && !waitingForFirstCut) return;
    const controller = new AbortController();
    let timer: number;
    const poll = async () => {
      try {
        await refresh(controller.signal);
      } catch (cause) {
        if (!(cause instanceof DOMException && cause.name === "AbortError")) {
          setError("Project generation status could not be refreshed.");
        }
      } finally {
        if (!controller.signal.aborted) {
          timer = window.setTimeout(poll, 1_500);
        }
      }
    };
    timer = window.setTimeout(poll, 1_500);
    return () => {
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [firstCutActive, hasActiveMedia, projectId, refresh, waitingForFirstCut]);

  const selected = useMemo(
    () =>
      revisions.find((item) => item.document.revision_id === selectedId) ??
      current,
    [current, revisions, selectedId],
  );

  useEffect(() => {
    if (!selected) return;
    const controller = new AbortController();
    listRevisionRenders(
      projectId,
      selected.document.revision_id,
      controller.signal,
    )
      .then(setRenderArtifacts)
      .catch((cause: unknown) => {
        if (!(cause instanceof DOMException && cause.name === "AbortError")) {
          setError("Preview history could not be loaded.");
        }
      });
    return () => controller.abort();
  }, [projectId, selected]);

  const selectedDuration = selected?.document.timeline.duration_frames ?? 1;
  const effectiveRangeStart = Math.min(
    rangeStart,
    Math.max(0, selectedDuration - 1),
  );
  const effectiveRangeDuration = Math.max(
    1,
    Math.min(rangeDuration, selectedDuration - effectiveRangeStart),
  );

  const readyPreview = useMemo(
    () => {
      const ready = [...renderArtifacts]
        .reverse()
        .filter(
          (artifact) =>
            artifact.revision_id === selected?.document.revision_id &&
            artifact.kind === "preview" &&
            artifact.status === "ready",
        );
      return ready.find((artifact) => !artifact.output_range) ?? ready[0];
    },
    [renderArtifacts, selected],
  );
  const readyExport = useMemo(
    () =>
      [...renderArtifacts]
        .reverse()
        .find(
          (artifact) =>
            artifact.revision_id === selected?.document.revision_id &&
            artifact.kind === "export" &&
            artifact.status === "ready",
        ),
    [renderArtifacts, selected],
  );
  const currentPreviewPlayhead =
    readyPreview &&
    current &&
    selected?.document.revision_id === current.document.revision_id &&
    previewPlayhead?.artifactId === readyPreview.artifact_id &&
    previewPlayhead.revisionId === current.document.revision_id
      ? previewPlayhead.frame
      : null;
  const currentPreviewAnnotations = previewAnnotations.filter(
    (annotation) =>
      annotation.revision_id === current?.document.revision_id,
  );
  const canAnnotateCurrentPreview = Boolean(
    readyPreview &&
      current &&
      selected?.document.revision_id === current.document.revision_id &&
      currentPreviewPlayhead !== null,
  );
  const capturePreviewPlayhead = (currentTime: number) => {
    if (!selected || !readyPreview) return;
    const frame = previewFrameAtTime(selected, readyPreview, currentTime);
    setPreviewPlayhead((existing) =>
      existing?.artifactId === readyPreview.artifact_id && existing.frame === frame
        ? existing
        : {
            artifactId: readyPreview.artifact_id,
            revisionId: selected.document.revision_id,
            frame,
          },
    );
  };
  const clearAnnotationDraft = () => {
    setAnnotationStartFrame(null);
    setAnnotationDraftRange(null);
    setAnnotationComment("");
  };
  const markCurrentFrame = () => {
    if (currentPreviewPlayhead === null) return;
    setAnnotationStartFrame(null);
    setAnnotationDraftRange({
      start_frame: currentPreviewPlayhead,
      duration_frames: 1,
    });
  };
  const toggleAnnotationRange = () => {
    if (currentPreviewPlayhead === null) return;
    if (annotationStartFrame === null) {
      setAnnotationStartFrame(currentPreviewPlayhead);
      setAnnotationDraftRange(null);
      return;
    }
    const start = Math.min(annotationStartFrame, currentPreviewPlayhead);
    const end = Math.max(annotationStartFrame, currentPreviewPlayhead) + 1;
    setAnnotationDraftRange({
      start_frame: start,
      duration_frames: end - start,
    });
    setAnnotationStartFrame(null);
  };
  const attachPreviewAnnotation = () => {
    const comment = annotationComment.trim();
    if (
      !current ||
      !readyPreview ||
      !annotationDraftRange ||
      !comment ||
      currentPreviewAnnotations.length >= 8
    ) {
      return;
    }
    setPreviewAnnotations((annotations) => [
      ...annotations,
      {
        annotation_id: newStableId("preview_annotation"),
        revision_id: current.document.revision_id,
        artifact_id: readyPreview.artifact_id,
        output_range: annotationDraftRange,
        comment,
      },
    ]);
    clearAnnotationDraft();
  };
  const firstReadyAsset = selected?.document.assets?.find((asset) =>
    assetStatuses.some(
      (status) => status.asset_id === asset.asset_id && status.readiness === "ready",
    ),
  );
  const hasPrimaryVisual = selected?.document.timeline.tracks?.some(
    (track) =>
      track.kind === "video" &&
      track.role === "primary" &&
      track.enabled !== false &&
      (track.clips?.length ?? 0) > 0,
  );
  const assetsReady = (selected?.document.assets ?? []).every((asset) =>
    assetStatuses.some(
      (status) => status.asset_id === asset.asset_id && status.readiness === "ready",
    ),
  );

  const runRender = async (
    action: "preview" | "range" | "export",
  ) => {
    if (!selected) return;
    const revisionId = selected.document.revision_id;
    const outputRange =
      action === "range"
        ? {
            start_frame: effectiveRangeStart,
            duration_frames: effectiveRangeDuration,
          }
        : undefined;
    const actionKey = `${revisionId}:${action}:${effectiveRangeStart}:${effectiveRangeDuration}`;
    let request = pendingRenders.current.get(actionKey);
    if (!request) {
      request = {
        request_id: newStableId("render_request"),
        project_id: projectId,
        revision_id: revisionId,
        idempotency_key: newStableId("render"),
        kind: action === "export" ? "export" : "preview",
        quality: action === "export" ? "hq" : "proxy",
        output_range: outputRange,
      };
      pendingRenders.current.set(actionKey, request);
    }
    setRenderingKind(action);
    setRenderProgress(0);
    setError(null);
    try {
      const ticket = await createRevisionRender(projectId, request);
      const artifact =
        ticket.artifact.status === "ready"
          ? ticket.artifact
          : await waitForJob<RenderArtifact>(ticket.artifact.job_id, {
              onProgress: (job) => setRenderProgress(job.progress),
            });
      pendingRenders.current.delete(actionKey);
      setRenderArtifacts((items) => [
        ...items.filter((item) => item.artifact_id !== artifact.artifact_id),
        artifact,
      ]);
    } catch (cause) {
      if (cause instanceof ApiError && !cause.retryable) {
        pendingRenders.current.delete(actionKey);
      }
      setError(
        cause instanceof ApiError
          ? cause.detail
          : "The render result is unknown. Retry to reuse the same request.",
      );
    } finally {
      setRenderingKind(null);
    }
  };

  const restore = async () => {
    if (!current || !selected) return;
    if (current.document.revision_id === selected.document.revision_id) return;

    const actionKey = `${current.document.revision_id}:${selected.document.revision_id}`;
    const existing = pendingRestore.current;
    const request =
      existing?.actionKey === actionKey
        ? existing.request
        : {
            request_id: newStableId("restore_request"),
            project_id: projectId,
            base_revision_id: current.document.revision_id,
            source_revision_id: selected.document.revision_id,
            idempotency_key: newStableId("restore"),
            origin: "user" as const,
            summary: `Restore ${revisionTitle(selected)}`,
          };
    pendingRestore.current = { actionKey, request };
    setRestoring(true);
    setError(null);
    try {
      const result = await restoreProjectRevision(projectId, request);
      pendingRestore.current = null;
      if (result.revision) {
        const restored = result.revision;
        setCurrent(restored);
        setRevisions((history) =>
          history.some(
            (item) =>
              item.document.revision_id === restored.document.revision_id,
          )
            ? history
            : [...history, restored],
        );
        setSelectedId(restored.document.revision_id);
        clearAnnotationDraft();
        setPreviewAnnotations([]);
      }
      try {
        await refresh();
      } catch {
        setError(
          "The version was restored, but the latest history could not be refreshed.",
        );
      }
    } catch (cause) {
      if (cause instanceof ApiError && !cause.retryable) {
        pendingRestore.current = null;
      }
      setError(
        cause instanceof ApiError
          ? cause.detail
          : "The restore result is unknown. Retry to safely reuse the same request.",
      );
    } finally {
      setRestoring(false);
    }
  };

  if (loading) {
    return (
      <div className="flex h-full items-center justify-center gap-2 text-sm text-ink-soft">
        <LoaderCircle className="animate-spin" size={18} />
        Loading project history…
      </div>
    );
  }

  return (
    <div className="mx-auto flex w-full max-w-[100rem] flex-col gap-5 px-4 py-5 sm:px-6 lg:px-7">
      <div>
        <p className="text-xs font-medium uppercase tracking-[0.14em] text-ink-faint">
          Project {projectId}
        </p>
        <h1 className="mt-1 text-2xl font-semibold tracking-tight">AI editing workspace</h1>
        <p className="mt-1 text-sm text-ink-soft">
          Direct the edit on the left, preview the selected Revision on the right, and restore any committed version without losing history.
        </p>
      </div>

      {error && (
        <div className="flex items-start gap-2 rounded-[var(--radius-control)] border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          <AlertCircle className="mt-0.5 shrink-0" size={16} />
          <span>{error}</span>
        </div>
      )}

      {(hasActiveMedia || firstCutActive || waitingForFirstCut) && (
        <div className="flex items-start gap-3 rounded-[var(--radius-control)] border border-accent/20 bg-accent-soft/50 px-4 py-3 text-sm text-ink">
          <LoaderCircle className="mt-0.5 shrink-0 animate-spin text-accent" size={17} />
          <div>
            <p className="font-medium">
              {hasActiveMedia
                ? "Understanding your media"
                : firstCut?.status === "autofixing"
                  ? "Improving your AI first cut"
                : firstCut?.status === "reviewing"
                  ? "Reviewing your AI first cut"
                  : firstCut?.status === "committed" ||
                      firstCut?.status === "awaiting_review"
                    ? "Rendering your AI first cut"
                  : "Directing your AI first cut"}
            </p>
            <p className="mt-0.5 text-xs text-ink-soft">
              {firstCut?.status === "autofixing"
                ? "The Reviewer found a repairable issue. The Director is creating a new candidate without changing your current version."
                : "The Director builds a candidate revision, then renders and reviews it before making it current."}
            </p>
          </div>
        </div>
      )}

      {(firstCut?.status === "failed" ||
        firstCut?.status === "render_failed" ||
        firstCut?.status === "review_rejected" ||
        firstCut?.status === "review_failed") && (
        <div className="flex items-start gap-2 rounded-[var(--radius-control)] border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          <AlertCircle className="mt-0.5 shrink-0" size={16} />
          <span>
            {firstCut.status === "render_failed"
              ? "The candidate preview failed to render; the previous revision is still current."
              : firstCut.status === "review_rejected"
                ? firstCut.render_review?.summary ||
                  "The rendered candidate did not pass review; the previous revision is still current."
                : firstCut.status === "review_failed"
                  ? firstCut.error ||
                    "The rendered candidate could not be reviewed; the previous revision is still current."
              : firstCut.error || "The AI first cut failed; the previous revision is still current."}
          </span>
        </div>
      )}

      {firstCut?.status === "ready" &&
        firstCut.decision &&
        firstCut.result_revision_id === current?.document.revision_id && (
        <div className="flex items-start gap-3 rounded-[var(--radius-control)] border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-900">
          <Sparkles className="mt-0.5 shrink-0" size={17} />
          <div>
            <p className="font-medium">AI first cut ready</p>
            <p className="mt-0.5 text-xs text-emerald-800/80">
              {firstCut.decision.explanation}
            </p>
            {firstCut.render_review && (
              <p className="mt-1 text-xs text-emerald-800/80">
                Reviewer: {firstCut.render_review.summary}
              </p>
            )}
            {(firstCut.render_autofix_attempts ?? 0) > 0 && (
              <p className="mt-1 text-xs text-emerald-800/80">
                Passed after {firstCut.render_autofix_attempts ?? 0} bounded AutoFix attempt.
              </p>
            )}
          </div>
        </div>
      )}

      <div className="grid min-h-[32rem] overflow-hidden rounded-[var(--radius-card)] border border-line bg-surface lg:grid-cols-[22rem_minmax(0,1fr)]">
        <section id="history" className="border-b border-line lg:border-b-0 lg:border-r">
          <ConversationPanel
            projectId={projectId}
            currentRevisionId={current?.document.revision_id ?? null}
            playheadFrame={currentPreviewPlayhead}
            previewAnnotations={currentPreviewAnnotations}
            onRemovePreviewAnnotation={(annotationId) =>
              setPreviewAnnotations((annotations) =>
                annotations.filter(
                  (annotation) => annotation.annotation_id !== annotationId,
                ),
              )
            }
            onPreviewAnnotationsConsumed={() => setPreviewAnnotations([])}
            onRevisionChanged={refresh}
          />
          <div className="border-b border-line px-5 py-4">
            <h2 className="text-sm font-semibold">Committed versions</h2>
            <p className="mt-0.5 text-xs text-ink-faint">
              {revisions.length} {revisions.length === 1 ? "version" : "versions"}
            </p>
          </div>
          <div className="max-h-[65vh] overflow-y-auto p-2">
            {[...revisions].reverse().map((revision) => {
              const revisionId = revision.document.revision_id;
              const isCurrent = revisionId === current?.document.revision_id;
              const isSelected = revisionId === selected?.document.revision_id;
              return (
                <button
                  key={revisionId}
                  type="button"
                  onClick={() => {
                    setSelectedId(revisionId);
                    setRangeStart(0);
                    setRangeDuration(
                      Math.min(90, revision.document.timeline.duration_frames),
                    );
                    clearAnnotationDraft();
                    setPreviewAnnotations([]);
                  }}
                  className={`mb-1 w-full rounded-[var(--radius-control)] border px-3.5 py-3 text-left transition-colors ${
                    isSelected
                      ? "border-accent bg-accent-soft/60"
                      : "border-transparent hover:border-line hover:bg-canvas"
                  }`}
                >
                  <div className="flex items-start justify-between gap-3">
                    <span className="min-w-0">
                      <span className="block truncate text-[13px] font-medium">
                        {revisionTitle(revision)}
                      </span>
                      <span className="mt-1 block text-[11px] text-ink-faint">
                        {formatCreatedAt(revision.created_at)}
                      </span>
                    </span>
                    {isCurrent && (
                      <span className="flex shrink-0 items-center gap-1 rounded-full bg-white px-2 py-0.5 text-[10px] font-semibold text-accent">
                        <Check size={10} /> Current
                      </span>
                    )}
                  </div>
                  <div className="mt-2 flex items-center justify-between text-[11px] text-ink-soft">
                    <span>{revisionKind(revision)}</span>
                    <span className="max-w-32 truncate font-mono">{revisionId}</span>
                  </div>
                </button>
              );
            })}
          </div>
        </section>

        <section className="min-w-0 p-5 sm:p-7">
          {selected ? (
            <>
              <div className="flex flex-col justify-between gap-4 sm:flex-row sm:items-start">
                <div className="min-w-0">
                  <div className="flex items-center gap-2 text-xs font-medium text-accent">
                    <Clock3 size={14} /> {revisionKind(selected)}
                  </div>
                  <h2 className="mt-2 truncate text-xl font-semibold">
                    {revisionTitle(selected)}
                  </h2>
                  <p className="mt-1 break-all font-mono text-xs text-ink-faint">
                    {selected.document.revision_id}
                  </p>
                </div>
                <button
                  type="button"
                  onClick={restore}
                  disabled={
                    restoring ||
                    selected.document.revision_id === current?.document.revision_id
                  }
                  className="flex h-9 shrink-0 items-center justify-center gap-2 rounded-[var(--radius-control)] bg-ink px-4 text-[13px] font-medium text-white transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-35"
                >
                  {restoring ? (
                    <LoaderCircle className="animate-spin" size={14} />
                  ) : (
                    <RotateCcw size={14} />
                  )}
                  {selected.document.revision_id === current?.document.revision_id
                    ? "Current version"
                    : "Restore as new version"}
                </button>
              </div>

              <div className="mt-7 overflow-hidden rounded-[var(--radius-card)] border border-line bg-[#111318]">
                <div className="flex items-center justify-between border-b border-white/10 px-4 py-2.5 text-white">
                  <div className="flex rounded-lg bg-white/5 p-1 text-xs">
                    <button
                      type="button"
                      onClick={() => setPreviewMode("preview")}
                      className={`rounded-md px-3 py-1.5 transition ${
                        previewMode === "preview"
                          ? "bg-white font-semibold text-black"
                          : "text-white/60 hover:text-white"
                      }`}
                    >
                      Preview
                    </button>
                    <button
                      type="button"
                      onClick={() => setPreviewMode("annotate")}
                      className={`rounded-md px-3 py-1.5 transition ${
                        previewMode === "annotate"
                          ? "bg-white font-semibold text-black"
                          : "text-white/60 hover:text-white"
                      }`}
                    >
                      Preview edit
                    </button>
                  </div>
                  {previewMode === "annotate" && currentPreviewPlayhead !== null && (
                    <span className="font-mono text-[11px] text-white/55">
                      Frame {currentPreviewPlayhead}
                    </span>
                  )}
                </div>
                <div className="flex min-h-72 items-center justify-center bg-black/40 p-4">
                  {readyPreview ? (
                    <video
                      key={readyPreview.artifact_id}
                      controls
                      playsInline
                      onLoadedMetadata={(event) =>
                        capturePreviewPlayhead(event.currentTarget.currentTime)
                      }
                      onTimeUpdate={(event) =>
                        capturePreviewPlayhead(event.currentTarget.currentTime)
                      }
                      onSeeked={(event) =>
                        capturePreviewPlayhead(event.currentTarget.currentTime)
                      }
                      className="max-h-[32rem] w-full rounded-md object-contain"
                      src={renderContentUrl(projectId, readyPreview.artifact_id)}
                    >
                      Your browser does not support video playback.
                    </video>
                  ) : firstReadyAsset?.kind === "photo" ? (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img
                      alt={firstReadyAsset.label || "Uploaded source"}
                      className="max-h-[32rem] w-full rounded-md object-contain"
                      src={assetProxyUrl(projectId, firstReadyAsset.asset_id)}
                    />
                  ) : firstReadyAsset ? (
                    <video
                      controls
                      playsInline
                      className="max-h-[32rem] w-full rounded-md object-contain"
                      poster={assetThumbnailUrl(projectId, firstReadyAsset.asset_id)}
                      src={assetProxyUrl(projectId, firstReadyAsset.asset_id)}
                    >
                      Your browser does not support video playback.
                    </video>
                  ) : (
                    <div className="flex flex-col items-center gap-2 text-center text-sm text-white/60">
                      <Film size={28} />
                      <span>Processed media will appear here.</span>
                    </div>
                  )}
                </div>

                <div className="border-t border-white/10 p-4 text-white">
                  <div className="flex flex-wrap items-center justify-between gap-3">
                    <div>
                      <p className="text-sm font-medium">
                        {readyPreview
                          ? readyPreview.output_range
                            ? "Range preview"
                            : "Revision preview"
                          : firstReadyAsset
                            ? "Source proxy"
                            : "No preview yet"}
                      </p>
                      <p className="mt-0.5 text-xs text-white/50">
                        {readyPreview
                          ? `Bound to ${readyPreview.revision_id}`
                          : "A source proxy is not the edited ProjectDoc output."}
                      </p>
                    </div>
                    <div className="flex flex-wrap gap-2">
                      <button
                        type="button"
                        onClick={() => runRender("preview")}
                        disabled={!hasPrimaryVisual || !assetsReady || renderingKind !== null}
                        className="flex h-9 items-center gap-2 rounded-[var(--radius-control)] bg-white px-3.5 text-xs font-semibold text-black disabled:cursor-not-allowed disabled:opacity-35"
                      >
                        {renderingKind === "preview" ? (
                          <LoaderCircle className="animate-spin" size={14} />
                        ) : (
                          <Play size={14} />
                        )}
                        Render preview
                      </button>
                      <button
                        type="button"
                        onClick={() => runRender("export")}
                        disabled={!hasPrimaryVisual || !assetsReady || renderingKind !== null}
                        className="flex h-9 items-center gap-2 rounded-[var(--radius-control)] border border-white/20 px-3.5 text-xs font-semibold disabled:cursor-not-allowed disabled:opacity-35"
                      >
                        {renderingKind === "export" ? (
                          <LoaderCircle className="animate-spin" size={14} />
                        ) : (
                          <Download size={14} />
                        )}
                        Export HQ
                      </button>
                    </div>
                  </div>

                  {previewMode === "annotate" && (
                    <div className="mt-4 rounded-xl border border-white/15 bg-white/5 p-3">
                      {!readyPreview ? (
                        <p className="text-xs leading-5 text-white/60">
                          Render a Revision preview before adding an annotation.
                        </p>
                      ) : selected.document.revision_id !==
                        current?.document.revision_id ? (
                        <p className="text-xs leading-5 text-white/60">
                          Preview annotations can only target the current Revision. Restore
                          this version first if you want to edit it.
                        </p>
                      ) : (
                        <>
                          <div className="flex flex-wrap items-center gap-2">
                            <button
                              type="button"
                              onClick={markCurrentFrame}
                              disabled={currentPreviewPlayhead === null}
                              className="flex h-8 items-center gap-1.5 rounded-md bg-white px-3 text-xs font-semibold text-black disabled:cursor-not-allowed disabled:opacity-35"
                            >
                              <Flag size={13} /> Mark this frame
                            </button>
                            <button
                              type="button"
                              onClick={toggleAnnotationRange}
                              disabled={currentPreviewPlayhead === null}
                              className="flex h-8 items-center gap-1.5 rounded-md border border-white/20 px-3 text-xs font-medium disabled:cursor-not-allowed disabled:opacity-35"
                            >
                              {annotationStartFrame === null
                                ? "Start range here"
                                : "Finish range here"}
                            </button>
                            {annotationStartFrame !== null && (
                              <span className="font-mono text-[11px] text-amber-300">
                                Started at frame {annotationStartFrame}
                              </span>
                            )}
                          </div>

                          {annotationDraftRange && (
                            <div className="mt-3 rounded-lg border border-white/10 bg-black/20 p-3">
                              <div className="flex items-center justify-between gap-2">
                                <p className="flex items-center gap-1.5 text-xs font-medium">
                                  <MessageSquare size={13} />
                                  {annotationDraftRange.duration_frames === 1
                                    ? `Frame ${annotationDraftRange.start_frame}`
                                    : `Frames ${annotationDraftRange.start_frame}–${
                                        annotationDraftRange.start_frame +
                                        annotationDraftRange.duration_frames -
                                        1
                                      }`}
                                </p>
                                <button
                                  type="button"
                                  aria-label="Cancel annotation"
                                  onClick={clearAnnotationDraft}
                                  className="rounded p-1 text-white/50 hover:bg-white/10 hover:text-white"
                                >
                                  <X size={13} />
                                </button>
                              </div>
                              <textarea
                                aria-label="Preview annotation comment"
                                value={annotationComment}
                                onChange={(event) =>
                                  setAnnotationComment(event.target.value)
                                }
                                maxLength={2000}
                                rows={2}
                                placeholder="What should change here?"
                                className="mt-2 w-full resize-none rounded-lg border border-white/15 bg-white/5 px-3 py-2 text-xs leading-5 text-white outline-none placeholder:text-white/35 focus:border-white/40"
                              />
                              <div className="mt-2 flex items-center justify-between gap-3">
                                <span className="text-[10px] text-white/40">
                                  The exact Revision, preview, and frame range will be sent
                                  with your message.
                                </span>
                                <button
                                  type="button"
                                  onClick={attachPreviewAnnotation}
                                  disabled={
                                    !annotationComment.trim() ||
                                    !canAnnotateCurrentPreview ||
                                    currentPreviewAnnotations.length >= 8
                                  }
                                  className="shrink-0 rounded-md bg-accent px-3 py-1.5 text-xs font-semibold text-white disabled:cursor-not-allowed disabled:opacity-35"
                                >
                                  Add to prompt
                                </button>
                              </div>
                            </div>
                          )}

                          <p className="mt-2 text-[11px] leading-4 text-white/45">
                            {currentPreviewAnnotations.length > 0
                              ? `${currentPreviewAnnotations.length} annotation${
                                  currentPreviewAnnotations.length === 1 ? "" : "s"
                                } attached to the AI editor.`
                              : "Pause or scrub the video, mark a frame or range, then add a comment."}
                          </p>
                        </>
                      )}
                    </div>
                  )}

                  <div className="mt-4 flex flex-wrap items-end gap-2 border-t border-white/10 pt-4">
                    <label className="text-[11px] text-white/60">
                      Start frame
                      <input
                        type="number"
                        min={0}
                        max={Math.max(0, selected.document.timeline.duration_frames - 1)}
                        value={effectiveRangeStart}
                        onChange={(event) => {
                          const value = Math.max(0, Number(event.target.value) || 0);
                          const next = Math.min(
                            value,
                            selected.document.timeline.duration_frames - 1,
                          );
                          setRangeStart(next);
                          setRangeDuration((duration) =>
                            Math.min(
                              duration,
                              selected.document.timeline.duration_frames - next,
                            ),
                          );
                        }}
                        className="mt-1 block h-8 w-28 rounded-md border border-white/20 bg-white/5 px-2 text-xs text-white"
                      />
                    </label>
                    <label className="text-[11px] text-white/60">
                      Frames
                      <input
                        type="number"
                        min={1}
                        max={selected.document.timeline.duration_frames - effectiveRangeStart}
                        value={effectiveRangeDuration}
                        onChange={(event) =>
                          setRangeDuration(
                            Math.max(
                              1,
                              Math.min(
                                Number(event.target.value) || 1,
                                selected.document.timeline.duration_frames - effectiveRangeStart,
                              ),
                            ),
                          )
                        }
                        className="mt-1 block h-8 w-28 rounded-md border border-white/20 bg-white/5 px-2 text-xs text-white"
                      />
                    </label>
                    <button
                      type="button"
                      onClick={() => runRender("range")}
                      disabled={!hasPrimaryVisual || !assetsReady || renderingKind !== null}
                      className="flex h-8 items-center gap-2 rounded-md border border-white/20 px-3 text-xs font-medium disabled:cursor-not-allowed disabled:opacity-35"
                    >
                      {renderingKind === "range" && (
                        <LoaderCircle className="animate-spin" size={13} />
                      )}
                      Render range
                    </button>
                    {renderingKind && (
                      <span className="pb-1 text-xs text-white/50">
                        {Math.round(renderProgress * 100)}%
                      </span>
                    )}
                    {readyExport && (
                      <a
                        className="ml-auto flex h-8 items-center gap-2 rounded-md bg-emerald-500 px-3 text-xs font-semibold text-black"
                        download
                        href={renderContentUrl(projectId, readyExport.artifact_id)}
                      >
                        <Download size={13} /> Download export
                      </a>
                    )}
                  </div>

                  {(!hasPrimaryVisual || !assetsReady) && (
                    <p className="mt-3 text-xs text-amber-300/80">
                      {!hasPrimaryVisual
                        ? "This revision has no executable primary visual track yet."
                        : "All referenced media must finish proxy processing before rendering."}
                    </p>
                  )}
                </div>
              </div>

              <div className="mt-7 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
                <Metric label="Duration" value={durationLabel(selected)} />
                <Metric label="Assets" value={String(selected.document.assets?.length ?? 0)} />
                <Metric label="Tracks" value={String(selected.document.timeline.tracks?.length ?? 0)} />
                <Metric label="Locks" value={String(selected.document.locks?.length ?? 0)} />
              </div>

              <div className="mt-6 rounded-[var(--radius-control)] border border-line bg-canvas p-4">
                <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-ink-faint">
                  <Film size={14} /> Creative intent
                </div>
                <p className="mt-2 text-sm leading-6 text-ink">
                  {selected.document.intent.prompt || "No prompt recorded for this version."}
                </p>
              </div>

              {(selected.document.assets?.length ?? 0) > 0 && (
                <div className="mt-6 rounded-[var(--radius-control)] border border-line bg-canvas p-4">
                  <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-ink-faint">
                    <Film size={14} /> Project media
                  </div>
                  <ul className="mt-3 space-y-2">
                    {selected.document.assets?.map((asset) => {
                      const processing = assetStatuses.find(
                        (item) => item.asset_id === asset.asset_id,
                      );
                      return (
                        <li
                          key={asset.asset_id}
                          className="flex items-center justify-between gap-4 text-sm"
                        >
                          <span className="min-w-0">
                            <span className="block truncate text-ink">
                              {asset.label || asset.asset_id}
                            </span>
                            {(processing?.error || processing?.analysis_error) && (
                              <span className="mt-0.5 block truncate text-xs text-red-600">
                                {processing.error || processing.analysis_error}
                              </span>
                            )}
                          </span>
                          <span className="shrink-0 text-right text-xs text-ink-faint">
                            <span className="block">
                              {asset.kind} · {(asset.roles ?? ["source"]).join(", ")}
                            </span>
                            <span
                              className={
                                processing?.analysis_readiness === "ready"
                                  ? "text-emerald-600"
                                  : processing?.analysis_readiness === "partial"
                                    ? "text-amber-600"
                                  : processing?.readiness === "failed"
                                      || processing?.analysis_readiness === "failed"
                                    ? "text-red-600"
                                    : "text-accent"
                              }
                            >
                              {readinessLabel(processing)}
                            </span>
                          </span>
                        </li>
                      );
                    })}
                  </ul>
                </div>
              )}

              <dl className="mt-6 grid gap-x-8 gap-y-4 text-sm sm:grid-cols-2">
                <Detail label="Created" value={formatCreatedAt(selected.created_at)} />
                <Detail label="Origin" value={selected.provenance.origin} />
                <Detail label="Parent revision" value={selected.document.parent_revision_id ?? "None"} mono />
                <Detail
                  label="Restored from"
                  value={selected.provenance.restored_from_revision_id ?? "Not a restore"}
                  mono={Boolean(selected.provenance.restored_from_revision_id)}
                />
                <Detail label="Commit" value={selected.commit_id} mono />
                <Detail label="Document hash" value={selected.document_hash} mono />
              </dl>
            </>
          ) : (
            <div className="flex h-full items-center justify-center text-sm text-ink-soft">
              This project has no committed revisions yet.
            </div>
          )}
        </section>
      </div>
    </div>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-[var(--radius-control)] border border-line px-4 py-3">
      <p className="text-[11px] uppercase tracking-wide text-ink-faint">{label}</p>
      <p className="mt-1 text-sm font-semibold">{value}</p>
    </div>
  );
}

function Detail({
  label,
  value,
  mono = false,
}: {
  label: string;
  value: string;
  mono?: boolean;
}) {
  return (
    <div className="min-w-0">
      <dt className="text-xs text-ink-faint">{label}</dt>
      <dd className={`mt-1 break-all text-ink-soft ${mono ? "font-mono text-xs" : ""}`}>
        {value}
      </dd>
    </div>
  );
}
