"use client";

/**
 * Polling a backend job from React.
 *
 * Long work is the normal case here, not an edge case: analysing a camera roll
 * or rendering a cut takes minutes. So progress reporting is part of the
 * contract rather than a nicety, and every screen that starts work needs to
 * survive a page that stays open for ten minutes.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, getJob, type Job, type JobStage } from "@/lib/api/client";

export interface JobState<T> {
  job: Job<T> | null;
  result: T | null;
  error: string | null;
  running: boolean;
}

const FINISHED = new Set(["succeeded", "failed"]);

export function useJob<T>(jobId: string | null, intervalMs = 1500) {
  const [state, setState] = useState<JobState<T>>({
    job: null,
    result: null,
    error: null,
    running: false,
  });
  // A ref, not state: the polling loop must see the current value without
  // being restarted, and restarting it would drop the interval.
  const cancelled = useRef(false);

  useEffect(() => {
    if (!jobId) return;
    cancelled.current = false;
    setState({ job: null, result: null, error: null, running: true });

    let timer: ReturnType<typeof setTimeout>;

    const tick = async () => {
      if (cancelled.current) return;
      try {
        const job = await getJob<T>(jobId);
        if (cancelled.current) return;

        setState({
          job,
          result: job.status === "succeeded" ? (job.result as T) : null,
          error: job.status === "failed" ? job.error : null,
          running: !FINISHED.has(job.status),
        });

        if (!FINISHED.has(job.status)) timer = setTimeout(tick, intervalMs);
      } catch (error) {
        if (cancelled.current) return;
        setState((previous) => ({
          ...previous,
          running: false,
          error:
            error instanceof ApiError ? error.detail : "could not reach the API",
        }));
      }
    };

    void tick();
    return () => {
      cancelled.current = true;
      clearTimeout(timer);
    };
  }, [jobId, intervalMs]);

  return state;
}

/** Human-readable stage labels. Kept next to the enum so a new backend stage
 * shows up as a missing label rather than as a blank progress bar. */
export const STAGE_LABELS: Record<JobStage, string> = {
  ingesting: "Reading your files",
  analyzing: "Understanding the footage",
  planning: "Planning the edit",
  matching: "Choosing shots",
  scoring: "Comparing versions",
  generating_proxy: "Preparing preview media",
  generating_thumbnail: "Creating thumbnails",
  rendering_proxy: "Rendering previews",
  rendering_hq: "Rendering the final cut",
  done: "Done",
  failed: "Failed",
};

export function useStartJob<TArgs extends unknown[]>(
  starter: (...args: TArgs) => Promise<{ job_id: string }>,
) {
  const [jobId, setJobId] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const start = useCallback(
    async (...args: TArgs) => {
      setStarting(true);
      setError(null);
      try {
        const { job_id } = await starter(...args);
        setJobId(job_id);
        return job_id;
      } catch (caught) {
        setError(
          caught instanceof ApiError ? caught.detail : "could not start the job",
        );
        return null;
      } finally {
        setStarting(false);
      }
    },
    [starter],
  );

  return { start, jobId, starting, error, reset: () => setJobId(null) };
}
