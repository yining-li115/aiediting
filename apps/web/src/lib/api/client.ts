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
  AgentTurn,
  AgentTurnTicket,
  AssetProcessingStatus,
  AssetUploadResponse,
  Conversation,
  ConversationTicket,
  CreateAgentTurnRequest,
  CreateConversationRequest,
  CreateRenderRequest,
  CreateFirstCutRequest,
  DirectorRun,
  CreateProjectRequest,
  EditPlanSpec,
  MaterialSegment,
  MomentEvidence,
  Patch,
  ProjectRevision,
  RenderArtifact,
  RenderTicket,
  FirstCutTicket,
  ReferenceAnalysis,
  RestoreRevisionRequest,
  RevisionMutationResponse,
  TimelineSpec,
  TranscriptEvidence,
  UploadAssetsRequest,
} from "./types";

export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE ?? "http://127.0.0.1:8000";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly detail: string,
    readonly code?: string,
    readonly retryable = false,
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
    let code: string | undefined;
    let retryable = response.status >= 500;
    try {
      const body = await response.json();
      if (typeof body?.detail === "string") {
        detail = body.detail;
      } else if (body?.detail && typeof body.detail === "object") {
        detail = body.detail.message ?? JSON.stringify(body.detail);
        code = body.detail.code;
        retryable = body.detail.retryable ?? retryable;
      } else {
        detail = JSON.stringify(body);
      }
    } catch {
      detail = (await response.text()) || detail;
    }
    throw new ApiError(response.status, detail, code, retryable);
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
  | "generating_proxy"
  | "generating_thumbnail"
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

export const createProject = (body: CreateProjectRequest) =>
  request<RevisionMutationResponse>("/projects", {
    method: "POST",
    body: JSON.stringify(body),
  });

/* --- authoritative project revisions ----------------------------------- */

const pathSegment = (value: string) => encodeURIComponent(value);

export const getCurrentProjectRevision = (
  projectId: string,
  signal?: AbortSignal,
) =>
  request<ProjectRevision>(
    `/projects/${pathSegment(projectId)}/revisions/current`,
    { signal },
  );

export const listProjectRevisions = (
  projectId: string,
  signal?: AbortSignal,
) =>
  request<ProjectRevision[]>(`/projects/${pathSegment(projectId)}/revisions`, {
    signal,
  });

export const getProjectRevision = (
  projectId: string,
  revisionId: string,
  signal?: AbortSignal,
) =>
  request<ProjectRevision>(
    `/projects/${pathSegment(projectId)}/revisions/${pathSegment(revisionId)}`,
    { signal },
  );

export const applyProjectPatch = (
  projectId: string,
  patch: Patch,
  requireReview = false,
) =>
  request<RevisionMutationResponse>(
    `/projects/${pathSegment(projectId)}/patches?require_review=${requireReview}`,
    { method: "POST", body: JSON.stringify(patch) },
  );

export const restoreProjectRevision = (
  projectId: string,
  body: RestoreRevisionRequest,
  requireReview = false,
) =>
  request<RevisionMutationResponse>(
    `/projects/${pathSegment(projectId)}/restores?require_review=${requireReview}`,
    { method: "POST", body: JSON.stringify(body) },
  );

export const acceptProjectCandidate = (projectId: string, commitId: string) =>
  request<RevisionMutationResponse>(
    `/projects/${pathSegment(projectId)}/commits/${pathSegment(commitId)}/accept`,
    { method: "POST" },
  );

/* --- material ------------------------------------------------------------ */

/**
 * Upload a complete media batch and commit its AssetRefs as one new revision.
 * Invalid batches fail as a whole; the backend never silently skips a file.
 */
export function uploadAssets(
  projectId: string,
  manifest: UploadAssetsRequest,
  files: File[],
  onProgress?: (fraction: number) => void,
): Promise<AssetUploadResponse> {
  const form = new FormData();
  form.append("manifest", JSON.stringify(manifest));
  files.forEach((file) => form.append("files", file));

  // fetch cannot report upload progress; XHR still can.
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `${API_BASE}/projects/${pathSegment(projectId)}/assets`);
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) onProgress?.(event.loaded / event.total);
    };
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(JSON.parse(xhr.responseText));
        return;
      }
      let detail = xhr.statusText || "media upload failed";
      let code: string | undefined;
      let retryable = xhr.status >= 500;
      try {
        const body = JSON.parse(xhr.responseText);
        if (typeof body?.detail === "string") {
          detail = body.detail;
        } else if (body?.detail && typeof body.detail === "object") {
          detail = body.detail.message ?? JSON.stringify(body.detail);
          const issues = body.detail.details?.issues;
          if (Array.isArray(issues) && issues.length > 0) {
            detail = issues
              .map((issue) => issue?.message)
              .filter((message): message is string => Boolean(message))
              .join("; ");
          }
          code = body.detail.code;
          retryable = body.detail.retryable ?? retryable;
        }
      } catch {
        detail = xhr.responseText || detail;
      }
      reject(new ApiError(xhr.status, detail, code, retryable));
    };
    xhr.onerror = () => reject(new ApiError(0, "network error", undefined, true));
    xhr.send(form);
  });
}

export const listProjectAssets = (
  projectId: string,
  signal?: AbortSignal,
) =>
  request<AssetProcessingStatus[]>(
    `/projects/${pathSegment(projectId)}/assets`,
    { signal },
  );

export const listProjectMoments = (
  projectId: string,
  signal?: AbortSignal,
) =>
  request<MomentEvidence[]>(
    `/projects/${pathSegment(projectId)}/moments`,
    { signal },
  );

export const listProjectTranscripts = (
  projectId: string,
  signal?: AbortSignal,
) =>
  request<TranscriptEvidence[]>(
    `/projects/${pathSegment(projectId)}/transcripts`,
    { signal },
  );

export const assetProxyUrl = (projectId: string, assetId: string) =>
  `${API_BASE}/projects/${pathSegment(projectId)}/assets/${pathSegment(assetId)}/proxy`;

export const assetThumbnailUrl = (projectId: string, assetId: string) =>
  `${API_BASE}/projects/${pathSegment(projectId)}/assets/${pathSegment(assetId)}/thumbnail`;

/* --- autonomous first cut ---------------------------------------------- */

export const createFirstCut = (
  projectId: string,
  body: CreateFirstCutRequest,
) =>
  request<FirstCutTicket>(`/projects/${pathSegment(projectId)}/first-cuts`, {
    method: "POST",
    body: JSON.stringify(body),
  });

export const getLatestFirstCut = (
  projectId: string,
  signal?: AbortSignal,
) =>
  request<DirectorRun | null>(
    `/projects/${pathSegment(projectId)}/first-cuts/latest`,
    { signal },
  );

/* --- continuous editing conversation ---------------------------------- */

export const createProjectConversation = (
  projectId: string,
  body: CreateConversationRequest,
) =>
  request<ConversationTicket>(
    `/projects/${pathSegment(projectId)}/conversations`,
    { method: "POST", body: JSON.stringify(body) },
  );

export const getProjectConversation = (
  projectId: string,
  conversationId: string,
  signal?: AbortSignal,
) =>
  request<Conversation>(
    `/projects/${pathSegment(projectId)}/conversations/${pathSegment(conversationId)}`,
    { signal },
  );

export const createConversationTurn = (
  projectId: string,
  conversationId: string,
  body: CreateAgentTurnRequest,
) =>
  request<AgentTurnTicket>(
    `/projects/${pathSegment(projectId)}/conversations/${pathSegment(conversationId)}/turns`,
    { method: "POST", body: JSON.stringify(body) },
  );

export const getConversationTurn = (
  projectId: string,
  conversationId: string,
  turnId: string,
  signal?: AbortSignal,
) =>
  request<AgentTurn>(
    `/projects/${pathSegment(projectId)}/conversations/${pathSegment(conversationId)}/turns/${pathSegment(turnId)}`,
    { signal },
  );

/* --- revision-bound rendering ------------------------------------------ */

export const createRevisionRender = (
  projectId: string,
  body: CreateRenderRequest,
) =>
  request<RenderTicket>(`/projects/${pathSegment(projectId)}/renders`, {
    method: "POST",
    body: JSON.stringify(body),
  });

export const listRevisionRenders = (
  projectId: string,
  revisionId: string,
  signal?: AbortSignal,
) =>
  request<RenderArtifact[]>(
    `/projects/${pathSegment(projectId)}/revisions/${pathSegment(revisionId)}/renders`,
    { signal },
  );

export const getRevisionRender = (
  projectId: string,
  artifactId: string,
  signal?: AbortSignal,
) =>
  request<RenderArtifact>(
    `/projects/${pathSegment(projectId)}/renders/${pathSegment(artifactId)}`,
    { signal },
  );

export const renderContentUrl = (projectId: string, artifactId: string) =>
  `${API_BASE}/projects/${pathSegment(projectId)}/renders/${pathSegment(artifactId)}/content`;

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
