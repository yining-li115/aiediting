/**
 * HTTP client for the aive backend.
 *
 * Two things shape this layer:
 *
 * 1. **Everything slow is a job.** Analysis and rendering take minutes, so the
 *    API accepts work and returns a job id; the client polls. Any code that
 *    assumes a request returns a finished result will hang or time out.
 * 2. **Types come from the backend specs.** `types.ts` is generated from the
 *    Pydantic models by `npm run types`. Never hand-edit it, and never define
 *    a parallel interface here - a duplicate drifts the moment a field is
 *    renamed, and the failure surfaces in the browser weeks later.
 */

import type {
  Asset,
  EditPlanSpec,
  MaterialSegment,
  ReferenceAnalysis,
  TimelineSpec,
} from "./types";

export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE ?? "http://127.0.0.1:8000";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly detail: string,
  ) {
    super(`${status}: ${detail}`);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      ...(init?.body instanceof FormData
        ? {}
        : { "Content-Type": "application/json" }),
      ...init?.headers,
    },
  });

  if (!response.ok) {
    // FastAPI puts the message in `detail`; fall back to the raw body so a
    // proxy error or a crash is still readable rather than "[object Object]".
    let detail = response.statusText;
    try {
      const body = await response.json();
      detail = body?.detail ?? JSON.stringify(body);
    } catch {
      detail = (await response.text()) || detail;
    }
    throw new ApiError(response.status, detail);
  }

  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

/* --- jobs ---------------------------------------------------------------- */

export type JobStage =
  | "ingesting"
  | "analyzing"
  | "planning"
  | "matching"
  | "scoring"
  | "rendering_proxy"
  | "rendering_hq"
  | "done"
  | "failed";

export interface Job<TResult = unknown> {
  job_id: string;
  kind: string;
  status: "queued" | "running" | "succeeded" | "failed";
  stage: JobStage | null;
  progress: number;
  error: string | null;
  result: TResult | null;
}

export const getJob = <T>(jobId: string) => request<Job<T>>(`/jobs/${jobId}`);

/**
 * Poll a job to completion.
 *
 * Rejects on failure rather than resolving with an error field: a caller that
 * forgets to check `status` would otherwise render a broken result as a
 * successful one.
 */
export async function waitForJob<T>(
  jobId: string,
  options: {
    onProgress?: (job: Job<T>) => void;
    intervalMs?: number;
    timeoutMs?: number;
    signal?: AbortSignal;
  } = {},
): Promise<T> {
  const { onProgress, intervalMs = 1500, timeoutMs = 20 * 60_000 } = options;
  const deadline = Date.now() + timeoutMs;

  for (;;) {
    if (options.signal?.aborted) throw new Error("cancelled");
    const job = await getJob<T>(jobId);
    onProgress?.(job);

    if (job.status === "succeeded") return job.result as T;
    if (job.status === "failed") {
      throw new ApiError(500, job.error ?? "job failed without a reason");
    }
    if (Date.now() > deadline) {
      throw new ApiError(504, `job ${jobId} still ${job.status} after timeout`);
    }
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
  }
}

/* --- projects ------------------------------------------------------------ */

export interface Project {
  project_id: string;
  mode: "auto" | "reference_remake";
  prompt: string | null;
  status: string;
  created_at: string;
}

export const createProject = (body: { prompt?: string; mode?: string }) =>
  request<Project>("/projects", { method: "POST", body: JSON.stringify(body) });

export const getProject = (id: string) => request<Project>(`/projects/${id}`);

/* --- material ------------------------------------------------------------ */

export interface UploadResult {
  assets: Asset[];
  skipped: { name: string; reason: string }[];
}

/**
 * Upload material. Accepts video and photos together.
 *
 * `skipped` must be surfaced in the UI. Silently dropping files the backend
 * did not recognise is how a user ends up believing all 45 of their clips were
 * imported when only 30 were.
 */
export function uploadAssets(
  projectId: string,
  files: File[],
  onProgress?: (fraction: number) => void,
): Promise<UploadResult> {
  const form = new FormData();
  files.forEach((file) => form.append("files", file));

  // fetch cannot report upload progress; XHR still can.
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `${API_BASE}/projects/${projectId}/assets`);
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) onProgress?.(event.loaded / event.total);
    };
    xhr.onload = () =>
      xhr.status >= 200 && xhr.status < 300
        ? resolve(JSON.parse(xhr.responseText))
        : reject(new ApiError(xhr.status, xhr.responseText));
    xhr.onerror = () => reject(new ApiError(0, "network error"));
    xhr.send(form);
  });
}

export const listSegments = (projectId: string, includeRejected = false) =>
  request<MaterialSegment[]>(
    `/projects/${projectId}/segments?include_rejected=${includeRejected}`,
  );

export const startAnalysis = (projectId: string) =>
  request<{ job_id: string }>(`/projects/${projectId}/analyze`, {
    method: "POST",
  });

/* --- references ---------------------------------------------------------- */

/**
 * Add a reference video. File upload only - there is deliberately no URL
 * option anywhere in this product (see TECHNICAL_PLAN section 16.1).
 */
export const uploadReference = (projectId: string, file: File) => {
  const form = new FormData();
  form.append("file", file);
  return request<{ reference_id: string; job_id: string }>(
    `/projects/${projectId}/references`,
    { method: "POST", body: form },
  );
};

export const getReference = (referenceId: string) =>
  request<ReferenceAnalysis>(`/references/${referenceId}`);

/* --- generation ---------------------------------------------------------- */

export interface GenerateRequest {
  prompt?: string;
  target_duration?: number;
  candidates?: number;
  keep_original_sound?: boolean;
  reference_id?: string;
}

export interface Candidate {
  timeline_id: string;
  variant: string;
  score: number;
  dimensions: Record<string, number>;
  duration: number;
  clip_count: number;
  preview_url: string | null;
}

export interface GenerateResult {
  best: TimelineSpec;
  plan: EditPlanSpec;
  candidates: Candidate[];
  render_url: string | null;
  notes: string[];
}

export const startGeneration = (projectId: string, body: GenerateRequest) =>
  request<{ job_id: string }>(`/projects/${projectId}/generate`, {
    method: "POST",
    body: JSON.stringify(body),
  });

/* --- media --------------------------------------------------------------- */

/** Files are served by the API, never by a path into storage. */
export const mediaUrl = (key: string) =>
  `${API_BASE}/media/${encodeURIComponent(key)}`;
