"use client";

import {
  AlertCircle,
  Bot,
  CheckCircle2,
  ChevronDown,
  LoaderCircle,
  MessageSquareQuote,
  Send,
  X,
} from "lucide-react";
import { FormEvent, Fragment, useEffect, useRef, useState } from "react";

import {
  ApiError,
  createConversationTurn,
  createProjectConversation,
  getProjectConversation,
  waitForJob,
} from "@/lib/api/client";
import type {
  AgentTurn,
  Conversation,
  CreateAgentTurnRequest,
  PreviewAnnotation,
  RenderArtifact,
  WorkspaceSelection,
} from "@/lib/api/types";
import {
  AgentActivity,
  type RenderActivity,
} from "./AgentActivity";

interface ConversationPanelProps {
  projectId: string;
  currentRevisionId: string | null;
  playheadFrame: number | null;
  projectActivity?: ProjectActivity | null;
  previewAnnotations: PreviewAnnotation[];
  onRemovePreviewAnnotation: (annotationId: string) => void;
  onPreviewAnnotationsConsumed: () => void;
  onProcessingChange: (processing: boolean) => void;
  onRevisionChanged: () => void | Promise<void>;
}

export interface ProjectActivity {
  status: "running" | "succeeded" | "failed";
  headline: string;
  detail?: string;
}

function newStableId(prefix: string) {
  return `${prefix}_${crypto.randomUUID()}`;
}

function progressLabel(stage: string | null) {
  if (stage === "planning") return "Understanding the request";
  if (stage === "matching") return "Validating the edit";
  if (stage === "rendering_proxy") return "Rendering the new preview";
  if (stage === "rendering_hq") return "Rendering the final video";
  if (stage === "reviewing") return "Reviewing the rendered result";
  if (stage === "autofixing") return "Repairing a review issue";
  return "Editing the project";
}

function annotationRangeLabel(annotation: PreviewAnnotation) {
  const start = annotation.output_range.start_frame;
  const duration = annotation.output_range.duration_frames;
  return duration === 1
    ? `Frame ${start}`
    : `Frames ${start}–${start + duration - 1}`;
}

function userFacingMessage(role: "user" | "assistant", content: string) {
  if (role !== "assistant") return content;
  const legacyInternalStatus = content.indexOf("修改通过 ProjectService");
  if (legacyInternalStatus < 0) return content;
  return content.slice(0, legacyInternalStatus).trim();
}

function AnnotationQuote({
  annotation,
  onRemove,
  inverted = false,
}: {
  annotation: PreviewAnnotation;
  onRemove?: () => void;
  inverted?: boolean;
}) {
  return (
    <div
      className={`rounded-lg border px-2.5 py-2 text-left ${
        inverted
          ? "border-black/10 bg-white/75 text-ink"
          : "border-accent/20 bg-accent-soft/45 text-ink"
      }`}
    >
      <div className="flex items-center justify-between gap-2">
        <span
          className={`flex items-center gap-1 text-[10px] font-semibold uppercase tracking-wide ${
            inverted ? "text-ink-soft" : "text-accent"
          }`}
        >
          <MessageSquareQuote size={11} /> {annotationRangeLabel(annotation)}
          {annotation.spatial_region ? " · Area selected" : ""}
        </span>
        {onRemove && (
          <button
            type="button"
            aria-label="Remove preview annotation"
            onClick={onRemove}
            className="rounded p-0.5 text-ink-faint transition hover:bg-white/70 hover:text-ink"
          >
            <X size={12} />
          </button>
        )}
      </div>
      <p className="mt-1 whitespace-pre-wrap text-[11px] leading-4">
        {annotation.comment}
      </p>
    </div>
  );
}

export function ConversationPanel({
  projectId,
  currentRevisionId,
  playheadFrame,
  projectActivity,
  previewAnnotations,
  onRemovePreviewAnnotation,
  onPreviewAnnotationsConsumed,
  onProcessingChange,
  onRevisionChanged,
}: ConversationPanelProps) {
  const [conversation, setConversation] = useState<Conversation | null>(null);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [progress, setProgress] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [activityJobId, setActivityJobId] = useState<string | null>(null);
  const [activityTurnId, setActivityTurnId] = useState<string | null>(null);
  const [activityExpanded, setActivityExpanded] = useState(false);
  const [activityStartedAt, setActivityStartedAt] = useState<number | null>(null);
  const [activityCompletedAt, setActivityCompletedAt] = useState<number | null>(null);
  const [renderActivity, setRenderActivity] =
    useState<RenderActivity | null>(null);
  const creationRequestId = useRef(newStableId("conversation_request"));
  const pendingTurn = useRef<CreateAgentTurnRequest | null>(null);
  const scrollEnd = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    createProjectConversation(projectId, {
      request_id: creationRequestId.current,
      project_id: projectId,
      purpose: "primary",
    })
      .then((ticket) => {
        if (!controller.signal.aborted) setConversation(ticket.conversation);
      })
      .catch((cause: unknown) => {
        if (!controller.signal.aborted) {
          setError(
            cause instanceof ApiError
              ? cause.detail
              : "The project conversation could not be opened.",
          );
        }
      });
    return () => controller.abort();
  }, [projectId]);

  useEffect(() => {
    scrollEnd.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, [activityExpanded, conversation?.messages, progress]);

  const refreshConversation = async () => {
    if (!conversation) return;
    const latest = await getProjectConversation(
      projectId,
      conversation.conversation_id,
    );
    setConversation(latest);
  };

  const send = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const instruction = draft.trim();
    if (!conversation || !currentRevisionId || !instruction || sending) return;

    let request = pendingTurn.current;
    if (
      request === null ||
      request.instruction !== instruction ||
      request.base_revision_id !== currentRevisionId ||
      request.workspace.playhead_frame !== (playheadFrame ?? undefined) ||
      JSON.stringify(request.workspace.preview_annotations ?? []) !==
        JSON.stringify(previewAnnotations)
    ) {
      request = {
        request_id: newStableId("agent_turn_request"),
        project_id: projectId,
        conversation_id: conversation.conversation_id,
        base_revision_id: currentRevisionId,
        idempotency_key: newStableId("agent_turn"),
        instruction,
        workspace: {
          revision_id: currentRevisionId,
          selected_target_ids: [],
          playhead_frame: playheadFrame ?? undefined,
          preview_annotations: previewAnnotations as NonNullable<
            WorkspaceSelection["preview_annotations"]
          >,
        },
      };
      pendingTurn.current = request;
    }
    if (request === null) return;

    onProcessingChange(true);
    setSending(true);
    setError(null);
    setActivityJobId(null);
    setActivityTurnId(null);
    setActivityExpanded(false);
    setActivityStartedAt(Date.now());
    setActivityCompletedAt(null);
    setRenderActivity(null);
    setProgress("Queuing the edit");
    try {
      const ticket = await createConversationTurn(
        projectId,
        conversation.conversation_id,
        request,
      );
      setActivityJobId(ticket.turn.job_id);
      setActivityTurnId(ticket.turn.turn_id);
      setConversation((existing) =>
        existing
          ? {
              ...existing,
              messages: [
                ...(existing.messages ?? []).filter(
                  (message) => message.message_id !== ticket.user_message.message_id,
                ),
                ticket.user_message,
              ],
            }
          : existing,
      );
      const completed = await waitForJob<AgentTurn>(ticket.turn.job_id, {
        onProgress: (job) => setProgress(progressLabel(job.stage)),
      });

      await refreshConversation();
      pendingTurn.current = null;
      setDraft("");
      onPreviewAnnotationsConsumed();

      if (completed.render_job_id) {
        setProgress("Rendering the new preview");
        setRenderActivity({
          status: "running",
          label: "Rendering the new preview",
          progress: 0,
        });
        try {
          await waitForJob<RenderArtifact>(completed.render_job_id, {
            onProgress: (job) => {
              const label = progressLabel(job.stage);
              setProgress(label);
              setRenderActivity({
                status: "running",
                label,
                progress: job.progress,
              });
            },
          });
          setRenderActivity({
            status: "succeeded",
            label: "Rendered the new preview",
            progress: 1,
          });
        } catch (cause) {
          const message =
            cause instanceof ApiError
              ? `The edit was saved, but its preview failed: ${cause.detail}`
              : "The edit was saved, but its preview could not be refreshed.";
          setError(message);
          setRenderActivity({
            status: "failed",
            label: "Preview render failed",
            error: message,
          });
          setActivityExpanded(true);
        }
      }
      await onRevisionChanged();
    } catch (cause) {
      if (cause instanceof ApiError && !cause.retryable) {
        pendingTurn.current = null;
      }
      try {
        await refreshConversation();
      } catch {
        // Preserve the actionable Agent/job error below.
      }
      const message =
        cause instanceof ApiError
          ? cause.detail
          : "The edit result is unknown. Retry to reuse the same request.";
      setError(message);
      setActivityExpanded(true);
    } finally {
      setActivityCompletedAt(Date.now());
      setProgress(null);
      setSending(false);
      onProcessingChange(false);
    }
  };

  const messages = conversation?.messages ?? [];
  const activityAssistantIsVisible = messages.some(
    (message) =>
      message.role === "assistant" &&
      message.agent_turn_id === activityTurnId &&
      !sending,
  );
  const activity = (
    <AgentActivity
      jobId={activityJobId}
      active={sending}
      headline={progress}
      error={error}
      render={renderActivity}
      startedAt={activityStartedAt}
      completedAt={activityCompletedAt}
      expanded={activityExpanded}
      onExpandedChange={setActivityExpanded}
    />
  );

  return (
    <div className="flex h-full min-h-0 flex-col bg-white">
      <div className="shrink-0 border-b border-line px-4 py-3">
        <div className="flex items-center gap-2">
          <Bot size={14} className="text-ink-soft" />
          <h2 className="text-xs font-semibold">Video Agent</h2>
        </div>
        {playheadFrame !== null && (
          <p className="mt-1 font-mono text-[10px] text-ink-faint">
            Preview · frame {playheadFrame}
          </p>
        )}
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4">
        {projectActivity && (
          <details className="group mb-5 text-[11px] text-ink-faint">
            <summary
              className={`flex cursor-pointer list-none items-center gap-2 py-1 ${
                projectActivity.status === "failed" ? "text-red-600" : ""
              }`}
            >
              {projectActivity.status === "running" ? (
                <LoaderCircle size={12} className="shrink-0 animate-spin" />
              ) : projectActivity.status === "failed" ? (
                <AlertCircle size={12} className="shrink-0" />
              ) : (
                <CheckCircle2 size={12} className="shrink-0" />
              )}
              <span className="shrink-0 font-medium">
                {projectActivity.status === "running"
                  ? "Thinking"
                  : projectActivity.status === "failed"
                    ? "Stopped"
                    : "Thought for a moment"}
              </span>
              <span className="min-w-0 flex-1 truncate">
                · {projectActivity.headline}
              </span>
              <ChevronDown
                size={12}
                className="shrink-0 transition-transform group-open:rotate-180"
              />
            </summary>
            {projectActivity.detail && (
              <p
                className={`mt-2 max-w-[95%] pl-5 leading-[1.55] ${
                  projectActivity.status === "failed"
                    ? "text-red-600"
                    : "text-ink-soft"
                }`}
              >
                {projectActivity.detail}
              </p>
            )}
          </details>
        )}

        {messages.length === 0 && !progress && !projectActivity ? (
          <p className="max-w-[92%] text-xs leading-5 text-ink-faint">
            Ask for an edit, or mark a frame in Preview edit and describe what should change.
          </p>
        ) : (
          messages.map((message) => {
            const isActiveAssistant =
              message.role === "assistant" &&
              message.agent_turn_id === activityTurnId;
            if (isActiveAssistant && sending) return null;
            return (
              <Fragment key={message.message_id}>
                {isActiveAssistant && activity}
                <div
                  className={`mb-4 flex ${
                    message.role === "user" ? "justify-end" : "justify-start"
                  }`}
                >
                  <div
                    className={`max-w-[92%] whitespace-pre-wrap text-xs leading-5 ${
                      message.role === "user"
                        ? "rounded-2xl bg-[#f1f1f1] px-3 py-2.5 text-ink"
                        : "px-0.5 py-1 text-ink"
                    }`}
                  >
                    {(message.workspace?.preview_annotations ?? []).length > 0 && (
                      <div className="mb-2 space-y-1.5">
                        {(message.workspace?.preview_annotations ?? []).map(
                          (annotation) => (
                            <AnnotationQuote
                              key={annotation.annotation_id}
                              annotation={annotation}
                              inverted={message.role === "user"}
                            />
                          ),
                        )}
                      </div>
                    )}
                    {userFacingMessage(message.role, message.content)}
                  </div>
                </div>
              </Fragment>
            );
          })
        )}
        {!activityAssistantIsVisible && activity}
        <div ref={scrollEnd} />
      </div>

      <form onSubmit={send} className="shrink-0 border-t border-line bg-white p-3">
        {previewAnnotations.length > 0 && (
          <div className="mb-2 space-y-1.5 rounded-xl border border-line bg-canvas p-2">
            <p className="px-0.5 text-[10px] font-semibold uppercase tracking-wide text-ink-faint">
              Referenced from preview
            </p>
            {previewAnnotations.map((annotation) => (
              <AnnotationQuote
                key={annotation.annotation_id}
                annotation={annotation}
                onRemove={() => onRemovePreviewAnnotation(annotation.annotation_id)}
              />
            ))}
          </div>
        )}
        <div className="flex items-end gap-1.5 rounded-xl border border-line-strong bg-white p-1.5 shadow-[0_1px_3px_rgba(0,0,0,0.04)] focus-within:border-zinc-400">
          <textarea
            aria-label="Ask the AI editor"
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                event.currentTarget.form?.requestSubmit();
              }
            }}
            disabled={!conversation || sending}
            rows={1}
            placeholder="Ask for an edit…"
            className="max-h-28 min-h-9 flex-1 resize-none bg-transparent px-2 py-2 text-xs leading-5 outline-none disabled:text-ink-faint"
          />
          <button
            type="submit"
            aria-label="Send edit request"
            disabled={!conversation || !currentRevisionId || !draft.trim() || sending}
            className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-ink text-white transition hover:bg-accent-hover disabled:cursor-not-allowed disabled:opacity-30"
          >
            {sending ? (
              <LoaderCircle size={15} className="animate-spin" />
            ) : (
              <Send size={15} />
            )}
          </button>
        </div>
      </form>
    </div>
  );
}
