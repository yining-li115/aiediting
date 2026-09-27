"use client";

/**
 * Live job progress over Server-Sent Events.
 *
 * This replaces polling as the intended path. The difference is not only
 * latency - it is that a long job can report *what* it is doing (this segment,
 * that candidate) rather than only a percentage, which is what makes a
 * five-minute wait feel like work rather than a hang.
 *
 * Reconnection is the part worth being careful about. EventSource retries on
 * its own and sends the last event id back, so the server replays what was
 * missed; a page reloaded mid-job catches up instead of starting blank.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { API_BASE, type JobStage } from "@/lib/api/client";

export interface JobEvent {
  type: string;
  at: string;
  [key: string]: unknown;
}

export interface JobStreamState<T> {
  /** Every event received, in order. The transcript of what happened. */
  events: JobEvent[];
  stage: JobStage | null;
  progress: number;
  result: T | null;
  error: string | null;
  status: "idle" | "connecting" | "running" | "succeeded" | "failed";
}

const INITIAL: JobStreamState<never> = {
  events: [],
  stage: null,
  progress: 0,
  result: null,
  error: null,
  status: "idle",
};

export function useJobStream<T = unknown>(jobId: string | null) {
  const [snapshot, setSnapshot] = useState<{
    jobId: string | null;
    state: JobStreamState<T>;
  }>({ jobId: null, state: INITIAL as JobStreamState<T> });
  const source = useRef<EventSource | null>(null);
  const state =
    snapshot.jobId === jobId
      ? snapshot.state
      : ({
          ...(INITIAL as JobStreamState<T>),
          status: jobId ? "connecting" : "idle",
        } satisfies JobStreamState<T>);

  useEffect(() => {
    if (!jobId) return;

    const es = new EventSource(`${API_BASE}/jobs/${jobId}/stream`);
    source.current = es;

    const record = (type: string) => (message: MessageEvent<string>) => {
      const data = JSON.parse(message.data) as JobEvent;
      setSnapshot((previousSnapshot) => {
        const previous =
          previousSnapshot.jobId === jobId
            ? previousSnapshot.state
            : ({
                ...(INITIAL as JobStreamState<T>),
                status: "connecting",
              } satisfies JobStreamState<T>);
        const next: JobStreamState<T> = {
          ...previous,
          status: "running",
          events: [...previous.events, { ...data, type }],
        };
        if (typeof data.stage === "string") next.stage = data.stage as JobStage;
        if (typeof data.progress === "number") next.progress = data.progress;
        if (type === "done") {
          next.status = "succeeded";
          next.result = (data.result ?? null) as T | null;
          next.progress = 1;
        }
        if (type === "failed") {
          next.status = "failed";
          next.error = (data.error as string) ?? "the job failed";
        }
        return { jobId, state: next };
      });
      if (type === "done" || type === "failed") es.close();
    };

    // Named events, so a page can react to a segment differently from a
    // stage change without inspecting a discriminator.
    for (const type of [
      "started",
      "stage",
      "segment",
      "shot",
      "candidate",
      "asset_ready",
      "asset_failed",
      "asset_analyzed",
      "asset_analysis_failed",
      "shot_detected",
      "moment_analyzed",
      "transcript_segment",
      "style_reference_blueprint_ready",
      "audio_assets_analyzed",
      "director_context_ready",
      "director_decision",
      "first_cut_candidate_ready",
      "render_ready",
      "render_reviewed",
      "render_autofix_candidate_ready",
      "agent_turn_context_ready",
      "agent_model_started",
      "agent_tool_started",
      "agent_tool_completed",
      "preview_annotation_evidence_ready",
      "agent_patch_applying",
      "agent_turn_committed",
      "done",
      "failed",
    ]) {
      es.addEventListener(type, record(type) as EventListener);
    }

    es.onerror = () => {
      // EventSource reconnects by itself and the server replays from
      // Last-Event-ID, so a dropped connection is not an error to show the
      // user. Only a closed stream is.
      if (es.readyState === EventSource.CLOSED) {
        setSnapshot((previousSnapshot) => {
          const previous =
            previousSnapshot.jobId === jobId
              ? previousSnapshot.state
              : ({
                  ...(INITIAL as JobStreamState<T>),
                  status: "connecting",
                } satisfies JobStreamState<T>);
          return {
            jobId,
            state:
              previous.status === "succeeded" || previous.status === "failed"
                ? previous
                : {
                    ...previous,
                    status: "failed",
                    error: "lost the connection",
                  },
          };
        });
      }
    };

    return () => {
      es.close();
      source.current = null;
    };
  }, [jobId]);

  const cancel = useCallback(() => {
    source.current?.close();
    setSnapshot((previous) => {
      const current =
        previous.jobId === jobId
          ? previous.state
          : (INITIAL as JobStreamState<T>);
      return { jobId, state: { ...current, status: "idle" } };
    });
  }, [jobId]);

  return { ...state, cancel };
}

/** The events that carry an item, for building a live list. */
export const itemEvents = (events: JobEvent[], type: string) =>
  events.filter((event) => event.type === type);
