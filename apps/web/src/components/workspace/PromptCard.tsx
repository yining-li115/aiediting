"use client";

import { Lightbulb, MessageSquareQuote, Square, Wand2 } from "lucide-react";

import { Toggle } from "@/components/ui/Toggle";
import { PROMPT_MAX_CHARS, PROMPT_PLACEHOLDER } from "@/lib/config";

interface Props {
  value: string;
  onChange: (value: string) => void;
  autoEnhance: boolean;
  onAutoEnhanceChange: (value: boolean) => void;
}

export function PromptCard({
  value,
  onChange,
  autoEnhance,
  onAutoEnhanceChange,
}: Props) {
  return (
    <section className="rounded-[var(--radius-card)] border border-line bg-surface px-6 pb-4 pt-5">
      <div className="flex items-center gap-2.5">
        <MessageSquareQuote size={16} className="text-ink-soft" />
        <h2 className="text-[14px] font-semibold">Prompt</h2>
      </div>

      <div className="relative mt-3.5">
        <textarea
          value={value}
          onChange={(event) =>
            onChange(event.target.value.slice(0, PROMPT_MAX_CHARS))
          }
          placeholder={PROMPT_PLACEHOLDER}
          rows={2}
          className="w-full resize-none bg-transparent text-[14px] leading-relaxed text-ink outline-none placeholder:text-ink-faint"
        />
        <div className="absolute bottom-0 right-0 flex items-center gap-2 text-[12px] text-ink-faint">
          <span className="flex h-[22px] w-[22px] items-center justify-center rounded-full bg-[#10a37f]">
            <Square size={9} className="fill-white text-white" />
          </span>
          {value.length} / {PROMPT_MAX_CHARS}
        </div>
      </div>

      <div className="mt-3 flex items-center justify-between">
        {/* TODO 硬编码: "Enhance Prompt" has no backend. It would be a planner
            call that rewrites a short request into a fuller one; nothing like
            it exists yet. */}
        <button
          type="button"
          className="flex h-[34px] items-center gap-2 rounded-[var(--radius-control)] border border-line-strong px-3.5 text-[13px] font-medium transition-colors hover:bg-canvas"
        >
          <Wand2 size={14} />
          Enhance Prompt
        </button>

        <div className="flex items-center gap-6">
          {/* TODO 硬编码: "Inspiration" is decorative - there is no prompt
              gallery behind it. */}
          <button
            type="button"
            className="flex items-center gap-2 text-[13px] text-ink-soft transition-colors hover:text-ink"
          >
            <Lightbulb size={15} />
            Inspiration
          </button>
          <div className="flex items-center gap-2.5">
            <Square size={14} className="text-ink-soft" />
            <span className="text-[13px] text-ink-soft">Auto-Enhance</span>
            {/* TODO 硬编码: auto-enhance is not wired to anything. */}
            <Toggle
              checked={autoEnhance}
              onChange={onAutoEnhanceChange}
              label="Auto-Enhance"
              unwired
            />
          </div>
        </div>
      </div>
    </section>
  );
}
