"use client";

import { HelpCircle, Monitor, Sparkles, SlidersHorizontal } from "lucide-react";

import { Select } from "@/components/ui/Select";
import { Toggle } from "@/components/ui/Toggle";
import {
  ASPECT_RATIOS,
  CREDITS,
  QUALITIES,
  UNWIRED_SETTINGS,
  VIBE_STYLES,
  VIDEO_LENGTHS,
} from "@/lib/config";

export interface Settings {
  length: number;
  vibe: string;
  aspectRatio: string;
  quality: string;
  autoCaptions: boolean;
  backgroundMusic: boolean;
  soundEffects: boolean;
}

interface Props {
  settings: Settings;
  onChange: (settings: Settings) => void;
  onGenerate: () => void;
  generating?: boolean;
  disabled?: boolean;
}

const unwired = (key: string) =>
  (UNWIRED_SETTINGS as readonly string[]).includes(key);

export function VideoSettings({
  settings,
  onChange,
  onGenerate,
  generating,
  disabled,
}: Props) {
  const set = <K extends keyof Settings>(key: K, value: Settings[K]) =>
    onChange({ ...settings, [key]: value });

  return (
    <section className="flex flex-col rounded-[var(--radius-card)] border border-line bg-surface p-5">
      <div className="flex items-center justify-between">
        <h3 className="text-[14px] font-semibold">Video Settings</h3>
        <SlidersHorizontal size={15} className="text-ink-faint" />
      </div>

      <div className="mt-5 flex flex-col gap-4">
        <Field label="Video Length">
          <Select
            value={settings.length}
            onChange={(value) => set("length", Number(value))}
            options={VIDEO_LENGTHS.map((option) => ({
              value: option.value,
              label: option.label,
            }))}
          />
        </Field>

        <Field label="Vibe / Style">
          <Select
            value={settings.vibe}
            onChange={(value) => set("vibe", value)}
            options={VIBE_STYLES}
          />
        </Field>

        <Field label="Aspect Ratio">
          <Select
            value={settings.aspectRatio}
            onChange={(value) => set("aspectRatio", value)}
            options={ASPECT_RATIOS}
            icon={<Monitor size={14} />}
          />
        </Field>

        <Field label="Quality">
          <Select
            value={settings.quality}
            onChange={(value) => set("quality", value)}
            options={QUALITIES}
          />
        </Field>
      </div>

      <div className="mt-5 flex flex-col gap-3.5">
        <Switch
          label="Auto Captions"
          checked={settings.autoCaptions}
          onChange={(value) => set("autoCaptions", value)}
          unwired={unwired("autoCaptions")}
        />
        <Switch
          label="Background Music"
          checked={settings.backgroundMusic}
          onChange={(value) => set("backgroundMusic", value)}
        />
        <Switch
          label="Sound Effects"
          checked={settings.soundEffects}
          onChange={(value) => set("soundEffects", value)}
          unwired={unwired("soundEffects")}
        />
      </div>

      <button
        type="button"
        onClick={onGenerate}
        disabled={disabled || generating}
        className="mt-6 flex h-[46px] items-center justify-center gap-2 rounded-[var(--radius-control)] bg-accent text-[14px] font-medium text-white transition-colors hover:bg-accent-hover disabled:cursor-not-allowed disabled:opacity-50"
      >
        <Sparkles size={16} />
        {generating ? "Generating..." : "Generate Draft"}
      </button>

      {/* TODO 硬编码: there is no billing system, so this cost is fixed and
          not derived from anything the job will actually consume. */}
      <p className="mt-3 flex items-center justify-center gap-1.5 text-[12px] text-ink-faint">
        Estimated cost: {CREDITS.estimatedCost} credits
        <HelpCircle size={12} />
      </p>
    </section>
  );
}

function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <label className="mb-2 block text-[12.5px] text-ink-soft">{label}</label>
      {children}
    </div>
  );
}

function Switch({
  label,
  checked,
  onChange,
  unwired,
}: {
  label: string;
  checked: boolean;
  onChange: (value: boolean) => void;
  unwired?: boolean;
}) {
  return (
    <div className="flex items-center justify-between">
      <span className="text-[13px] text-ink-soft">{label}</span>
      <Toggle
        checked={checked}
        onChange={onChange}
        label={label}
        unwired={unwired}
      />
    </div>
  );
}
