"use client";

import {
  AlertCircle,
  BookOpen,
  CheckCircle2,
  ChevronDown,
  CircleDashed,
  GitCommitHorizontal,
  LoaderCircle,
  ScanSearch,
  Search,
  Wrench,
} from "lucide-react";

import type { JobStage } from "@/lib/api/client";
import {
  type JobEvent,
  useJobStream,
} from "@/lib/hooks/useJobStream";

export interface RenderActivity {
  status: "running" | "succeeded" | "failed";
  label: string;
  progress?: number;
  error?: string;
}

interface AgentActivityProps {
  jobId: string | null;
  active: boolean;
  headline: string | null;
  error: string | null;
  render: RenderActivity | null;
  startedAt: number | null;
  completedAt: number | null;
  expanded: boolean;
  onExpandedChange: (expanded: boolean) => void;
}

type ActivityStatus = "running" | "completed" | "error";

interface ActivityEntry {
  key: string;
  label: string;
  detail?: string;
  status: ActivityStatus;
  icon: typeof BookOpen;
}

const STAGE_COPY: Partial<Record<JobStage, string>> = {
  ingesting: "Reading uploaded files",
  analyzing: "Understanding the footage",
  planning: "Preparing the Agent turn",
  matching: "Validating the candidate edit",
  scoring: "Comparing edit candidates",
  generating_proxy: "Preparing preview media",
  generating_thumbnail: "Creating thumbnails",
  rendering_proxy: "Rendering the new preview",
  rendering_hq: "Rendering the final export",
  reviewing: "Reviewing the rendered result",
  autofixing: "Repairing a review issue",
};

function stringValue(event: JobEvent, key: string) {
  const value = event[key];
  return typeof value === "string" ? value : null;
}

function numberValue(event: JobEvent, key: string) {
  const value = event[key];
  return typeof value === "number" ? value : null;
}

function booleanValue(event: JobEvent, key: string) {
  const value = event[key];
  return typeof value === "boolean" ? value : null;
}

function stringList(event: JobEvent, key: string) {
  const value = event[key];
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string")
    : [];
}

function countLabel(count: number, singular: string, plural = `${singular}s`) {
  return `${count} ${count === 1 ? singular : plural}`;
}

function quoted(value: string | null) {
  if (!value) return null;
  const compact = value.replace(/\s+/g, " ").trim();
  const shortened = compact.length > 72 ? `${compact.slice(0, 69)}…` : compact;
  return `“${shortened}”`;
}

function operationDetail(event: JobEvent) {
  const count = numberValue(event, "operation_count");
  const operations = stringList(event, "operation_types");
  const pieces = [
    count === null ? null : countLabel(count, "change"),
    operations.length > 0 ? operations.join(", ") : null,
  ];
  return pieces.filter(Boolean).join(" · ") || undefined;
}

function toolActivity(event: JobEvent, completed: boolean): ActivityEntry | null {
  const tool = stringValue(event, "tool");
  const step = numberValue(event, "step_index");
  if (!tool || step === null) return null;

  const failed = completed && booleanValue(event, "failed") === true;
  const query = quoted(stringValue(event, "query"));
  const summary = quoted(stringValue(event, "summary"));
  const resultCount = numberValue(event, "result_count");
  const targetCount = numberValue(event, "target_count");
  const trackCount = numberValue(event, "track_count");
  const durationFrames = numberValue(event, "duration_frames");
  const componentName = stringValue(event, "component_name");
  const candidateStatus = stringValue(event, "candidate_status");
  const error = stringValue(event, "error");
  const status: ActivityStatus = failed
    ? "error"
    : completed
      ? "completed"
      : "running";

  if (tool === "read_project") {
    const detail = completed
      ? [
          trackCount === null ? null : countLabel(trackCount, "track"),
          durationFrames === null ? null : `${durationFrames} frames`,
        ]
          .filter(Boolean)
          .join(" · ") || undefined
      : undefined;
    return {
      key: `tool-${step}`,
      label: completed ? "Read the current ProjectDoc" : "Reading the current ProjectDoc",
      detail: error ?? detail,
      status,
      icon: BookOpen,
    };
  }
  if (tool === "search_moments") {
    return {
      key: `tool-${step}`,
      label: completed
        ? failed
          ? "Moment search failed"
          : `Found ${countLabel(resultCount ?? 0, "Moment")}`
        : `Searching the footage${query ? ` for ${query}` : ""}`,
      detail: error ?? (completed ? query ?? undefined : undefined),
      status,
      icon: Search,
    };
  }
  if (tool === "inspect_media") {
    const count = completed ? resultCount : targetCount;
    return {
      key: `tool-${step}`,
      label: completed
        ? failed
          ? "Media inspection failed"
          : `Inspected ${countLabel(count ?? 0, "Moment")}`
        : `Inspecting ${countLabel(count ?? 0, "candidate Moment")}`,
      detail: error ?? undefined,
      status,
      icon: ScanSearch,
    };
  }
  if (tool === "search_components") {
    return {
      key: `tool-${step}`,
      label: completed
        ? failed
          ? "Component search failed"
          : `Found ${countLabel(resultCount ?? 0, "visual component")}`
        : `Searching visual components${query ? ` for ${query}` : ""}`,
      detail: error ?? (completed ? query ?? undefined : undefined),
      status,
      icon: Search,
    };
  }
  if (tool === "create_component_candidate") {
    return {
      key: `tool-${step}`,
      label: completed
        ? failed
          ? "Component candidate failed"
          : `Built ${componentName ?? "a component candidate"}`
        : `Building ${componentName ?? "a component candidate"} in the sandbox`,
      detail: error ?? candidateStatus ?? undefined,
      status,
      icon: Wrench,
    };
  }
  if (tool === "propose_patch") {
    return {
      key: `tool-${step}`,
      label: completed
        ? failed
          ? `Rejected ${summary ?? "the proposed edit"}`
          : `Validated ${summary ?? "the proposed edit"}`
        : `Checking ${summary ?? "the proposed edit"}`,
      detail: error ?? operationDetail(event),
      status,
      icon: failed ? AlertCircle : Wrench,
    };
  }
  return {
    key: `tool-${step}`,
    label: completed ? `Completed ${tool}` : `Running ${tool}`,
    detail: error ?? undefined,
    status,
    icon: Wrench,
  };
}

function eventEntry(event: JobEvent): ActivityEntry | null {
  if (event.type === "stage") {
    const stage = stringValue(event, "stage") as JobStage | null;
    if (!stage) return null;
    const label = STAGE_COPY[stage];
    if (!label) return null;
    return {
      key: `stage-${stage}`,
      label,
      status: "running",
      icon: LoaderCircle,
    };
  }
  if (event.type === "agent_turn_context_ready") {
    const moments = numberValue(event, "moment_count");
    const targets = Array.isArray(event.selected_target_ids)
      ? event.selected_target_ids.length
      : 0;
    return {
      key: "turn-context",
      label: "Loaded the frozen project context",
      detail: [
        moments === null ? null : countLabel(moments, "Moment"),
        targets > 0 ? countLabel(targets, "selected target") : null,
      ]
        .filter(Boolean)
        .join(" · ") || undefined,
      status: "completed",
      icon: BookOpen,
    };
  }
  if (event.type === "audio_assets_analyzed") {
    const assets = numberValue(event, "asset_count") ?? 0;
    const grounded = numberValue(event, "beat_grounded_asset_count") ?? 0;
    return {
      key: "audio-assets-analyzed",
      label: `Understood ${countLabel(assets, "audio Asset")}`,
      detail:
        grounded > 0
          ? `${countLabel(grounded, "track")} with measured beat timing`
          : "Energy and transient timing measured",
      status: "completed",
      icon: ScanSearch,
    };
  }
  if (event.type === "agent_model_started") {
    const step = numberValue(event, "step_index") ?? 0;
    return {
      key: `model-${step}`,
      label: step === 0 ? "Interpreting the request" : "Choosing the next editing action",
      status: "running",
      icon: CircleDashed,
    };
  }
  if (event.type === "agent_tool_started") return toolActivity(event, false);
  if (event.type === "agent_tool_completed") return toolActivity(event, true);
  if (event.type === "preview_annotation_evidence_ready") {
    const count = Array.isArray(event.annotation_ids)
      ? event.annotation_ids.length
      : 0;
    return {
      key: "annotation-evidence",
      label: `Prepared ${countLabel(count, "marked preview frame")}`,
      status: "completed",
      icon: ScanSearch,
    };
  }
  if (event.type === "agent_patch_applying") {
    const summary = quoted(stringValue(event, "summary"));
    return {
      key: "apply-patch",
      label: `Applying ${summary ?? "the validated edit"}`,
      detail: operationDetail(event),
      status: "running",
      icon: GitCommitHorizontal,
    };
  }
  if (event.type === "agent_turn_committed") {
    const summary = quoted(stringValue(event, "summary"));
    return {
      key: "apply-patch",
      label: `Saved ${summary ?? "the edit"}`,
      detail: operationDetail(event),
      status: "completed",
      icon: GitCommitHorizontal,
    };
  }
  if (event.type === "failed") {
    return {
      key: "agent-failed",
      label: "Agent turn failed",
      detail: stringValue(event, "error") ?? undefined,
      status: "error",
      icon: AlertCircle,
    };
  }
  return null;
}

function settleRunning(entries: ActivityEntry[]) {
  return entries.map((entry) =>
    entry.status === "running" ? { ...entry, status: "completed" as const } : entry,
  );
}

function chronologicalActivity(events: JobEvent[]) {
  let entries: ActivityEntry[] = [];
  for (const event of events) {
    if (event.type === "done" || event.type === "failed") {
      entries = settleRunning(entries);
    }
    const entry = eventEntry(event);
    if (!entry) continue;

    entries = settleRunning(entries);
    if (event.type === "agent_tool_started") {
      const step = numberValue(event, "step_index");
      entries = entries.filter((item) => item.key !== `model-${step}`);
    }
    const existing = entries.findIndex((item) => item.key === entry.key);
    if (existing >= 0) {
      entries[existing] = entry;
    } else {
      entries.push(entry);
    }
  }
  return entries;
}

function elapsedLabel(startedAt: number | null, completedAt: number | null) {
  if (startedAt === null || completedAt === null) return null;
  const seconds = Math.max(1, Math.round((completedAt - startedAt) / 1_000));
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  const remainder = seconds % 60;
  return remainder === 0 ? `${minutes}m` : `${minutes}m ${remainder}s`;
}

export function AgentActivity({
  jobId,
  active,
  headline,
  error,
  render,
  startedAt,
  completedAt,
  expanded,
  onExpandedChange,
}: AgentActivityProps) {
  const stream = useJobStream(jobId);
  const reportedError = error ?? stream.error ?? render?.error ?? null;
  let entries = chronologicalActivity(stream.events);

  if (render) {
    entries = settleRunning(entries);
    entries.push({
      key: "render-activity",
      label: render.label,
      detail:
        render.progress === undefined
          ? undefined
          : `${Math.round(render.progress * 100)}%`,
      status:
        render.status === "failed"
          ? "error"
          : render.status === "succeeded"
            ? "completed"
            : "running",
      icon:
        render.status === "failed"
          ? AlertCircle
          : render.status === "succeeded"
            ? CheckCircle2
            : LoaderCircle,
    });
  }

  if (!jobId && !headline && !reportedError && !render) return null;

  const streamRunning =
    stream.status === "connecting" || stream.status === "running";
  const reachedTerminalState =
    render?.status === "succeeded" || render?.status === "failed";
  const isRunning =
    !reachedTerminalState &&
    (active || streamRunning || render?.status === "running");
  const currentAction = [...entries]
    .reverse()
    .find((entry) => entry.status === "running");
  const latestAction = entries.at(-1);
  const duration = elapsedLabel(startedAt, completedAt);
  const summary = reportedError
    ? `${duration ? `Stopped after ${duration}` : "Stopped"} · ${reportedError}`
    : isRunning
      ? currentAction?.label ??
        latestAction?.label ??
        headline ??
        "Starting the AI editor"
      : `${duration ? `Worked for ${duration}` : "Completed"} · ${
          render?.status === "succeeded" ? "Preview updated" : "Edit completed"
        }`;

  return (
    <div className="mb-4 px-0.5">
      <button
        type="button"
        aria-expanded={expanded}
        onClick={() => onExpandedChange(!expanded)}
        className={`flex w-full items-center gap-2 py-1 text-left text-[11px] transition-colors ${
          reportedError
            ? "text-red-600"
            : "text-ink-faint hover:text-ink-soft"
        }`}
      >
        {isRunning && !reportedError ? (
          <LoaderCircle size={12} className="shrink-0 animate-spin" />
        ) : reportedError ? (
          <AlertCircle size={12} className="shrink-0" />
        ) : (
          <CheckCircle2 size={12} className="shrink-0" />
        )}
        <span className="min-w-0 flex-1 truncate font-medium">{summary}</span>
        <ChevronDown
          size={12}
          className={`shrink-0 transition-transform ${expanded ? "rotate-180" : ""}`}
        />
      </button>

      {expanded && (
        <div className="mt-2 space-y-2 border-l border-line pl-3">
          {entries.length === 0 && !reportedError && (
            <div className="flex items-center gap-2 text-[11px] text-ink-faint">
              <CircleDashed size={12} /> Waiting for the worker…
            </div>
          )}
          {entries.map((entry) => {
            const Icon = entry.icon;
            return (
              <div key={entry.key} className="flex items-start gap-2 text-[11px]">
                <Icon
                  size={12}
                  className={`mt-0.5 shrink-0 ${
                    entry.status === "running" ? "animate-spin" : ""
                  } ${
                    entry.status === "error" ? "text-red-500" : "text-ink-faint"
                  }`}
                />
                <span
                  className={`min-w-0 flex-1 ${
                    entry.status === "error" ? "text-red-600" : "text-ink-soft"
                  }`}
                >
                  {entry.label}
                  {entry.detail && (
                    <span className="ml-1 text-ink-faint">· {entry.detail}</span>
                  )}
                </span>
              </div>
            );
          })}
          {reportedError && !entries.some((entry) => entry.status === "error") && (
            <div className="rounded-md border border-red-200 bg-red-50 px-2.5 py-2 text-[11px] leading-4 text-red-700">
              {reportedError}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
