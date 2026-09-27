"use client";

import {
  ArrowUp,
  Captions,
  Film,
  ImageIcon,
  Layers3,
  LoaderCircle,
  Music2,
  Paperclip,
  SlidersHorizontal,
  Video,
  X,
} from "lucide-react";
import { useRef, useState } from "react";

import { Select } from "@/components/ui/Select";
import { Toggle } from "@/components/ui/Toggle";
import {
  ASPECT_RATIOS,
  formatTargetDuration,
  PROMPT_MAX_CHARS,
  PROMPT_PLACEHOLDER,
  QUALITIES,
  TARGET_DURATION_INPUT,
  UPLOAD_LIMITS,
  VIBE_STYLES,
} from "@/lib/config";

export type UploadKind = "materials" | "styles" | "remake" | "music";

export interface UploadedFile {
  id: string;
  name: string;
  file: File;
  thumbnail?: string;
}

export interface ProjectSettings {
  length: number;
  vibe: string;
  aspectRatio: string;
  quality: string;
  autoCaptions: boolean;
  backgroundMusic: boolean;
  soundEffects: boolean;
}

interface Props {
  prompt: string;
  onPromptChange: (value: string) => void;
  settings: ProjectSettings;
  onSettingsChange: (settings: ProjectSettings) => void;
  uploads: Record<UploadKind, UploadedFile[]>;
  onAdd: (kind: UploadKind, files: File[]) => void;
  onRemove: (kind: UploadKind, id: string) => void;
  onCreate: () => void;
  creating: boolean;
  progress: number | null;
}

const UPLOAD_ACTIONS = [
  {
    kind: "materials" as const,
    label: "Footage & photos",
    description: "The material the Agent will edit",
    icon: Video,
    limits: UPLOAD_LIMITS.material,
    multiple: true,
  },
  {
    kind: "styles" as const,
    label: "Style references",
    description: "Examples for look, pace, and motion",
    icon: Layers3,
    limits: UPLOAD_LIMITS.style,
    multiple: true,
  },
  {
    kind: "remake" as const,
    label: "Remake reference · Later",
    description: "Shot-by-shot remake will return as an extension",
    icon: Film,
    limits: UPLOAD_LIMITS.remake,
    multiple: false,
    available: false,
  },
  {
    kind: "music" as const,
    label: "Licensed music",
    description: "An owned or licensed track for the mix",
    icon: Music2,
    limits: UPLOAD_LIMITS.music,
    multiple: false,
  },
] as const;

const KIND_LABEL: Record<UploadKind, string> = {
  materials: "Footage",
  styles: "Style",
  remake: "Remake",
  music: "Music",
};

export function ProjectComposer({
  prompt,
  onPromptChange,
  settings,
  onSettingsChange,
  uploads,
  onAdd,
  onRemove,
  onCreate,
  creating,
  progress,
}: Props) {
  const [mediaMenuOpen, setMediaMenuOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [durationMinutes, setDurationMinutes] = useState(() =>
    String(settings.length / 60),
  );
  const inputs = useRef<Partial<Record<UploadKind, HTMLInputElement | null>>>({});
  const set = <K extends keyof ProjectSettings>(
    key: K,
    value: ProjectSettings[K],
  ) => onSettingsChange({ ...settings, [key]: value });
  const attachments = UPLOAD_ACTIONS.flatMap((action) =>
    uploads[action.kind].map((file) => ({
      ...file,
      kind: action.kind,
    })),
  );

  return (
    <section id="composer" className="mx-auto w-full max-w-3xl scroll-mt-24">
      <div className="relative rounded-2xl border border-line-strong bg-white shadow-[0_18px_60px_rgba(0,0,0,0.07)] transition-shadow focus-within:shadow-[0_22px_70px_rgba(0,0,0,0.09)]">
        <textarea
          value={prompt}
          onChange={(event) =>
            onPromptChange(event.target.value.slice(0, PROMPT_MAX_CHARS))
          }
          onKeyDown={(event) => {
            if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
              event.preventDefault();
              onCreate();
            }
          }}
          rows={4}
          autoFocus
          placeholder={PROMPT_PLACEHOLDER}
          aria-label="Describe the video you want to create"
          className="min-h-32 w-full resize-none rounded-t-2xl bg-transparent px-5 pb-3 pt-5 text-[15px] leading-6 text-ink outline-none placeholder:text-ink-faint sm:min-h-36"
        />

        {attachments.length > 0 && (
          <div className="flex max-h-28 flex-wrap gap-2 overflow-y-auto border-t border-line px-4 py-3">
            {attachments.map((attachment) => (
              <div
                key={attachment.id}
                className="flex min-w-0 max-w-[15rem] items-center gap-2 rounded-lg border border-line bg-canvas px-2 py-1.5"
              >
                <span className="flex h-7 w-7 shrink-0 items-center justify-center overflow-hidden rounded-md bg-white text-ink-faint">
                  {attachment.thumbnail ? (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img
                      src={attachment.thumbnail}
                      alt=""
                      className="h-full w-full object-cover"
                    />
                  ) : attachment.file.type.startsWith("video/") ? (
                    <Video size={13} />
                  ) : attachment.file.type.startsWith("audio/") ? (
                    <Music2 size={13} />
                  ) : (
                    <ImageIcon size={13} />
                  )}
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-[11px] font-medium text-ink">
                    {attachment.name}
                  </span>
                  <span className="block text-[9px] uppercase tracking-wide text-ink-faint">
                    {KIND_LABEL[attachment.kind]}
                  </span>
                </span>
                <button
                  type="button"
                  onClick={() => onRemove(attachment.kind, attachment.id)}
                  aria-label={`Remove ${attachment.name}`}
                  className="rounded p-1 text-ink-faint transition hover:bg-white hover:text-ink"
                >
                  <X size={12} />
                </button>
              </div>
            ))}
          </div>
        )}

        {settingsOpen && (
          <div className="border-t border-line px-4 py-4">
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              <CompactField label="Target length">
                <div className="relative">
                  <input
                    type="number"
                    min={TARGET_DURATION_INPUT.minMinutes}
                    step={TARGET_DURATION_INPUT.stepMinutes}
                    value={durationMinutes}
                    onChange={(event) => {
                      const next = event.target.value;
                      setDurationMinutes(next);
                      const minutes = Number(next);
                      if (Number.isFinite(minutes) && minutes > 0) {
                        set("length", Math.max(1, Math.round(minutes * 60)));
                      }
                    }}
                    onBlur={() => {
                      const minutes = Number(durationMinutes);
                      if (!Number.isFinite(minutes) || minutes <= 0) {
                        setDurationMinutes(String(settings.length / 60));
                      }
                    }}
                    aria-label="Target video length in minutes"
                    title="No maximum duration"
                    className="h-[38px] w-full rounded-[var(--radius-control)] border border-line-strong bg-surface px-3 pr-12 text-[13px] text-ink outline-none focus:border-accent"
                  />
                  <span className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-[11px] text-ink-faint">
                    min
                  </span>
                </div>
              </CompactField>
              <CompactField label="Style">
                <Select
                  value={settings.vibe}
                  onChange={(value) => set("vibe", value)}
                  options={VIBE_STYLES}
                />
              </CompactField>
              <CompactField label="Format">
                <Select
                  value={settings.aspectRatio}
                  onChange={(value) => set("aspectRatio", value)}
                  options={ASPECT_RATIOS}
                />
              </CompactField>
              <CompactField label="Quality">
                <Select
                  value={settings.quality}
                  onChange={(value) => set("quality", value)}
                  options={QUALITIES}
                />
              </CompactField>
            </div>
            <div className="mt-4 flex flex-wrap gap-x-6 gap-y-3 border-t border-line pt-4">
              <SettingToggle
                icon={<Captions size={14} />}
                label="Auto captions"
                checked={settings.autoCaptions}
                onChange={(value) => set("autoCaptions", value)}
              />
              <SettingToggle
                icon={<Music2 size={14} />}
                label="Background music"
                checked={settings.backgroundMusic}
                onChange={(value) => set("backgroundMusic", value)}
              />
            </div>
          </div>
        )}

        <div className="relative flex items-center justify-between gap-3 border-t border-line px-3 py-2.5">
          <div className="flex min-w-0 items-center gap-1">
            <button
              type="button"
              aria-expanded={mediaMenuOpen}
              onClick={() => setMediaMenuOpen((open) => !open)}
              className="flex h-8 items-center gap-1.5 rounded-lg px-2.5 text-xs font-medium text-ink-soft transition hover:bg-accent-soft hover:text-ink"
            >
              <Paperclip size={14} />
              <span className="hidden sm:inline">Add media</span>
              {attachments.length > 0 && (
                <span className="rounded-full bg-accent-soft px-1.5 text-[10px] text-ink">
                  {attachments.length}
                </span>
              )}
            </button>
            <button
              type="button"
              aria-expanded={settingsOpen}
              onClick={() => setSettingsOpen((open) => !open)}
              className={`flex h-8 items-center gap-1.5 rounded-lg px-2.5 text-xs transition ${
                settingsOpen
                  ? "bg-accent-soft font-medium text-ink"
                  : "text-ink-soft hover:bg-accent-soft hover:text-ink"
              }`}
            >
              <SlidersHorizontal size={14} />
              <span className="hidden sm:inline">
                {formatTargetDuration(settings.length)} · {settings.aspectRatio}
              </span>
            </button>
          </div>

          <div className="flex items-center gap-2">
            <span className="hidden text-[10px] tabular-nums text-ink-faint sm:inline">
              {prompt.length}/{PROMPT_MAX_CHARS}
            </span>
            <button
              type="button"
              onClick={onCreate}
              disabled={creating}
              aria-label="Create project"
              title="Create project · Command/Control + Enter"
              className="flex h-8 items-center gap-1.5 rounded-lg bg-ink px-3 text-xs font-medium text-white transition hover:bg-accent-hover disabled:cursor-not-allowed disabled:opacity-45"
            >
              {creating ? (
                <LoaderCircle size={14} className="animate-spin" />
              ) : (
                <ArrowUp size={14} />
              )}
              <span className="hidden sm:inline">
                {creating
                  ? progress === null
                    ? "Creating"
                    : `Uploading ${Math.round(progress * 100)}%`
                  : "Create"}
              </span>
            </button>
          </div>

          {mediaMenuOpen && (
            <div className="absolute bottom-12 left-3 z-20 w-[19rem] overflow-hidden rounded-xl border border-line-strong bg-white p-1.5 shadow-[0_16px_45px_rgba(0,0,0,0.14)]">
              {UPLOAD_ACTIONS.map((action) => {
                const Icon = action.icon;
                const count = uploads[action.kind].length;
                const available = !("available" in action) || action.available;
                return (
                  <button
                    key={action.kind}
                    type="button"
                    disabled={!available}
                    onClick={() => {
                      if (!available) return;
                      inputs.current[action.kind]?.click();
                      setMediaMenuOpen(false);
                    }}
                    className="flex w-full items-center gap-3 rounded-lg px-3 py-2.5 text-left transition hover:bg-accent-soft disabled:cursor-not-allowed disabled:opacity-45 disabled:hover:bg-transparent"
                  >
                    <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-line bg-canvas text-ink-soft">
                      <Icon size={15} />
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="flex items-center gap-2 text-xs font-medium text-ink">
                        {action.label}
                        {count > 0 && (
                          <span className="text-[10px] font-normal text-ink-faint">
                            {count}
                          </span>
                        )}
                      </span>
                      <span className="mt-0.5 block text-[10px] text-ink-faint">
                        {action.description}
                      </span>
                    </span>
                  </button>
                );
              })}
            </div>
          )}
        </div>

        {creating && (
          <div className="h-0.5 overflow-hidden rounded-b-2xl bg-line">
            <div
              className="h-full bg-ink transition-[width] duration-500"
              style={{ width: `${Math.max(4, (progress ?? 0.08) * 100)}%` }}
            />
          </div>
        )}
      </div>

      {UPLOAD_ACTIONS.map((action) => (
        <input
          key={action.kind}
          ref={(node) => {
            inputs.current[action.kind] = node;
          }}
          type="file"
          accept={action.limits.accept}
          multiple={action.multiple}
          hidden
          disabled={"available" in action && !action.available}
          onChange={(event) => {
            const selected = Array.from(event.target.files ?? []);
            if (selected.length > 0) onAdd(action.kind, selected);
            event.target.value = "";
          }}
        />
      ))}
    </section>
  );
}

function CompactField({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <label>
      <span className="mb-1.5 block text-[10px] font-medium uppercase tracking-wide text-ink-faint">
        {label}
      </span>
      {children}
    </label>
  );
}

function SettingToggle({
  icon,
  label,
  checked,
  onChange,
}: {
  icon: React.ReactNode;
  label: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
}) {
  return (
    <div className="flex items-center gap-2.5 text-xs text-ink-soft">
      <span className="text-ink-faint">{icon}</span>
      <span>{label}</span>
      <Toggle checked={checked} onChange={onChange} label={label} />
    </div>
  );
}
