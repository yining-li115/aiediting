"use client";

import {
  Bot,
  LoaderCircle,
  MessageSquareQuote,
  Send,
  UserRound,
  X,
} from "lucide-react";
import { FormEvent, useEffect, useRef, useState } from "react";

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

interface ConversationPanelProps {
  projectId: string;
  currentRevisionId: string | null;
  playheadFrame: number | null;
  previewAnnotations: PreviewAnnotation[];
  onRemovePreviewAnnotation: (annotationId: string) => void;
  onPreviewAnnotationsConsumed: () => void;
  onRevisionChanged: () => void | Promise<void>;
}

function newStableId(prefix: string) {
  return `${prefix}_${crypto.randomUUID()}`;
}

function progressLabel(stage: string | null) {
  if (stage === "planning") return "Understanding the request";
  if (stage === "matching") return "Validating the edit";
  if (stage === "rendering_proxy") return "Rendering the new preview";
  return "Editing the project";
}

function annotationRangeLabel(annotation: PreviewAnnotation) {
  const start = annotation.output_range.start_frame;
  const duration = annotation.output_range.duration_frames;
  return duration === 1
    ? `Frame ${start}`
    : `Frames ${start}–${start + duration - 1}`;
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
          ? "border-white/15 bg-white/10 text-white"
          : "border-accent/20 bg-accent-soft/45 text-ink"
      }`}
    >
      <div className="flex items-center justify-between gap-2">
        <span
          className={`flex items-center gap-1 text-[10px] font-semibold uppercase tracking-wide ${
            inverted ? "text-white/65" : "text-accent"
          }`}
        >
          <MessageSquareQuote size={11} /> {annotationRangeLabel(annotation)}
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
  previewAnnotations,
  onRemovePreviewAnnotation,
  onPreviewAnnotationsConsumed,
  onRevisionChanged,
}: ConversationPanelProps) {
  const [conversation, setConversation] = useState<Conversation | null>(null);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [progress, setProgress] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
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
  }, [conversation?.messages, progress]);

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

    setSending(true);
    setError(null);
    setProgress("Queuing the edit");
    try {
      const ticket = await createConversationTurn(
        projectId,
        conversation.conversation_id,
        request,
      );
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
      await onRevisionChanged();
      pendingTurn.current = null;
      setDraft("");
      onPreviewAnnotationsConsumed();

      if (completed.render_job_id) {
        setProgress("Rendering the new preview");
        try {
          await waitForJob<RenderArtifact>(completed.render_job_id);
          await onRevisionChanged();
        } catch (cause) {
          setError(
            cause instanceof ApiError
              ? `The edit was saved, but its preview failed: ${cause.detail}`
              : "The edit was saved, but its preview could not be refreshed.",
          );
        }
      }
    } catch (cause) {
      if (cause instanceof ApiError && !cause.retryable) {
        pendingTurn.current = null;
      }
      try {
        await refreshConversation();
      } catch {
        // Preserve the actionable Agent/job error below.
      }
      setError(
        cause instanceof ApiError
          ? cause.detail
          : "The edit result is unknown. Retry to reuse the same request.",
      );
    } finally {
      setProgress(null);
      setSending(false);
    }
  };

  return (
    <div className="border-b border-line">
      <div className="px-4 pb-2 pt-4">
        <div className="flex items-center gap-2">
          <Bot size={15} className="text-accent" />
          <h2 className="text-sm font-semibold">AI editor</h2>
        </div>
        <p className="mt-1 text-[11px] leading-4 text-ink-faint">
          Ask about the footage or request an edit. Every accepted change creates a new version.
        </p>
        {playheadFrame !== null && (
          <p className="mt-1 font-mono text-[10px] text-accent">
            Preview position · frame {playheadFrame}
          </p>
        )}
      </div>

      <div className="max-h-72 min-h-28 overflow-y-auto px-3 py-2">
        {(conversation?.messages ?? []).length === 0 && !progress ? (
          <p className="rounded-lg bg-canvas px-3 py-2.5 text-xs leading-5 text-ink-soft">
            Try “turn the source audio down” or ask me to find a Moment in the footage.
          </p>
        ) : (
          (conversation?.messages ?? []).map((message) => (
            <div
              key={message.message_id}
              className={`mb-2 flex gap-2 ${
                message.role === "user" ? "justify-end" : "justify-start"
              }`}
            >
              {message.role === "assistant" && (
                <Bot size={14} className="mt-2 shrink-0 text-accent" />
              )}
              <div
                className={`max-w-[86%] whitespace-pre-wrap rounded-xl px-3 py-2 text-xs leading-5 ${
                  message.role === "user"
                    ? "bg-ink text-white"
                    : "border border-line bg-white text-ink"
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
                {message.content}
              </div>
              {message.role === "user" && (
                <UserRound size={14} className="mt-2 shrink-0 text-ink-faint" />
              )}
            </div>
          ))
        )}
        {progress && (
          <div className="mb-2 flex items-center gap-2 text-xs text-ink-soft">
            <LoaderCircle size={14} className="animate-spin text-accent" />
            {progress}…
          </div>
        )}
        <div ref={scrollEnd} />
      </div>

      {error && <p className="px-4 pb-2 text-[11px] leading-4 text-red-600">{error}</p>}

      <form onSubmit={send} className="px-3 pb-4 pt-1">
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
        <div className="flex items-end gap-2">
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
            rows={2}
            placeholder="Describe the next edit…"
            className="min-h-16 flex-1 resize-none rounded-[var(--radius-control)] border border-line-strong bg-white px-3 py-2 text-xs leading-5 outline-none transition focus:border-accent disabled:bg-canvas"
          />
          <button
            type="submit"
            aria-label="Send edit request"
            disabled={!conversation || !currentRevisionId || !draft.trim() || sending}
            className="flex h-9 w-9 shrink-0 items-center justify-center rounded-[var(--radius-control)] bg-accent text-white transition hover:bg-accent-hover disabled:cursor-not-allowed disabled:opacity-35"
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
