"use client";

import { Check, Plus } from "lucide-react";
import { useState } from "react";

export type RemotionTemplateCategory =
  | "Captions"
  | "Titles"
  | "Graphics";

export interface RemotionPromptTemplate {
  id: string;
  category: RemotionTemplateCategory;
  title: string;
  prompt: string;
  preview:
    | "kinetic-caption"
    | "editorial-title"
    | "location-card"
    | "quote-card"
    | "chapter-marker"
    | "end-card";
}

export const REMOTION_PROMPT_TEMPLATES: readonly RemotionPromptTemplate[] = [
  {
    id: "kinetic-caption",
    category: "Captions",
    title: "Kinetic captions",
    prompt:
      "Add clean kinetic captions that follow the spoken words. Emphasize only the key phrase in each sentence with restrained scale and color animation, keep the text inside safe margins, and preserve readability over every shot.",
    preview: "kinetic-caption",
  },
  {
    id: "editorial-title",
    category: "Titles",
    title: "Editorial opening title",
    prompt:
      "Open with a refined editorial title treatment: a short headline, a quiet subtitle, generous spacing, and a smooth staged entrance. Keep it elegant and let the footage remain the focus.",
    preview: "editorial-title",
  },
  {
    id: "location-card",
    category: "Graphics",
    title: "Location story card",
    prompt:
      "Add a minimal animated location card when the setting is first established. Show the place and date with a subtle line reveal, then clear it before the important action begins.",
    preview: "location-card",
  },
  {
    id: "quote-card",
    category: "Graphics",
    title: "Pull-quote moment",
    prompt:
      "Find the strongest spoken line and turn it into one tasteful pull-quote moment. Animate the quote in phrase by phrase, keep the speaker visible, and avoid covering their face.",
    preview: "quote-card",
  },
  {
    id: "chapter-marker",
    category: "Titles",
    title: "Chapter markers",
    prompt:
      "Divide the story into a few meaningful chapters and introduce each with a compact numbered chapter marker. Use consistent typography and very short transitions so the markers support the pacing rather than interrupt it.",
    preview: "chapter-marker",
  },
  {
    id: "end-card",
    category: "Graphics",
    title: "Minimal end card",
    prompt:
      "Finish with a minimal animated end card that resolves the story and leaves room for one short closing line. Match the typography and color to the rest of the edit and keep the motion understated.",
    preview: "end-card",
  },
] as const;

const FILTERS = ["All", "Captions", "Titles", "Graphics"] as const;

interface RemotionTemplateGalleryProps {
  onUseTemplate: (template: RemotionPromptTemplate) => void;
}

export function RemotionTemplateGallery({
  onUseTemplate,
}: RemotionTemplateGalleryProps) {
  const [filter, setFilter] = useState<(typeof FILTERS)[number]>("All");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const visibleTemplates = REMOTION_PROMPT_TEMPLATES.filter(
    (template) => filter === "All" || template.category === filter,
  );

  return (
    <section id="remotion-templates" className="mt-16 scroll-mt-24 sm:mt-20">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <p className="text-xs font-medium text-ink-faint">Remotion recipes</p>
          <h2 className="mt-1 text-lg font-semibold tracking-tight text-ink">
            Start with a visual treatment
          </h2>
          <p className="mt-1 max-w-xl text-xs leading-5 text-ink-faint">
            These are prompt recipes, not fixed presets. The Agent adapts the
            typography, timing, and composition to your footage.
          </p>
        </div>
        <div className="flex flex-wrap gap-1.5" aria-label="Template filters">
          {FILTERS.map((item) => (
            <button
              key={item}
              type="button"
              onClick={() => setFilter(item)}
              className={`rounded-full border px-3 py-1.5 text-[11px] transition ${
                filter === item
                  ? "border-ink bg-ink text-white"
                  : "border-line bg-white text-ink-soft hover:border-line-strong hover:text-ink"
              }`}
            >
              {item}
            </button>
          ))}
        </div>
      </div>

      <div className="mt-5 grid gap-x-4 gap-y-7 sm:grid-cols-2 lg:grid-cols-3">
        {visibleTemplates.map((template) => {
          const selected = selectedId === template.id;
          return (
            <article key={template.id} className="group min-w-0">
              <div className="relative aspect-[16/10] overflow-hidden rounded-xl border border-line bg-white transition duration-300 group-hover:-translate-y-0.5 group-hover:border-line-strong group-hover:shadow-[0_16px_40px_rgba(0,0,0,0.08)]">
                <TemplatePreview kind={template.preview} />
                <button
                  type="button"
                  onClick={() => {
                    onUseTemplate(template);
                    setSelectedId(template.id);
                    window.setTimeout(() => setSelectedId(null), 1400);
                  }}
                  aria-label={`Add ${template.title} prompt`}
                  title="Add prompt to the composer"
                  className={`absolute right-3 top-3 flex h-8 w-8 items-center justify-center rounded-full border shadow-sm backdrop-blur transition ${
                    selected
                      ? "border-emerald-200 bg-emerald-50 text-emerald-700"
                      : "border-white/70 bg-white/90 text-ink hover:scale-105 hover:bg-white"
                  }`}
                >
                  {selected ? <Check size={14} /> : <Plus size={15} />}
                </button>
              </div>
              <div className="mt-2.5 flex items-start justify-between gap-3 px-0.5">
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <h3 className="truncate text-[13px] font-medium text-ink">
                      {template.title}
                    </h3>
                    <span className="shrink-0 text-[9px] uppercase tracking-[0.12em] text-ink-faint">
                      {template.category}
                    </span>
                  </div>
                  <p className="mt-1 line-clamp-2 text-[11px] leading-[1.55] text-ink-faint">
                    {template.prompt}
                  </p>
                </div>
              </div>
            </article>
          );
        })}
      </div>
    </section>
  );
}

function TemplatePreview({
  kind,
}: {
  kind: RemotionPromptTemplate["preview"];
}) {
  if (kind === "kinetic-caption") {
    return (
      <div className="flex h-full items-center justify-center bg-[radial-gradient(circle_at_70%_25%,#44515f_0,#182027_45%,#090b0d_100%)] px-8 text-white">
        <div className="w-full text-center">
          <p className="text-[10px] uppercase tracking-[0.22em] text-white/45">
            A quiet morning
          </p>
          <p className="mt-2 text-xl font-semibold tracking-[-0.04em] sm:text-2xl">
            Find your <span className="rounded bg-[#f4dc72] px-1.5 py-0.5 text-black transition-transform duration-300 group-hover:scale-110">own pace</span>
          </p>
          <div className="mx-auto mt-5 h-px w-24 overflow-hidden bg-white/20">
            <div className="h-full w-1/2 bg-white transition-all duration-500 group-hover:w-full" />
          </div>
        </div>
      </div>
    );
  }
  if (kind === "editorial-title") {
    return (
      <div className="relative flex h-full items-center justify-center overflow-hidden bg-[#e8e2d8] text-[#292721]">
        <div className="absolute inset-y-0 left-[18%] w-px bg-black/10" />
        <div className="absolute inset-y-0 right-[18%] w-px bg-black/10" />
        <div className="text-center transition-transform duration-500 group-hover:-translate-y-1">
          <p className="text-[9px] uppercase tracking-[0.34em] text-black/45">
            Visual journal · 2026
          </p>
          <p className="mt-3 font-serif text-3xl italic tracking-[-0.04em]">
            Elsewhere
          </p>
          <p className="mx-auto mt-3 max-w-40 text-[9px] leading-4 text-black/45">
            Small observations from a long way home
          </p>
        </div>
      </div>
    );
  }
  if (kind === "location-card") {
    return (
      <div className="relative h-full overflow-hidden bg-[linear-gradient(155deg,#9db1b0_0%,#506a65_40%,#1f2827_100%)] text-white">
        <div className="absolute -bottom-14 -left-8 h-40 w-[120%] rotate-[-5deg] rounded-[50%] bg-[#273a32] shadow-[0_-18px_50px_rgba(0,0,0,0.2)]" />
        <div className="absolute bottom-5 left-5 border-l border-white/70 pl-3 transition-all duration-500 group-hover:pl-5">
          <p className="text-[9px] uppercase tracking-[0.2em] text-white/60">
            Day 03
          </p>
          <p className="mt-1 text-lg font-medium tracking-tight">Kyoto</p>
          <p className="text-[9px] text-white/55">35.0116° N · 135.7681° E</p>
        </div>
      </div>
    );
  }
  if (kind === "quote-card") {
    return (
      <div className="relative flex h-full items-center bg-[#111217] px-8 text-white">
        <span className="absolute left-6 top-4 font-serif text-6xl leading-none text-[#b9d5ff]/25">“</span>
        <div className="relative border-l border-[#b9d5ff]/55 pl-5">
          <p className="max-w-[15rem] text-lg font-medium leading-snug tracking-[-0.025em]">
            The best part was never in the plan.
          </p>
          <p className="mt-4 text-[9px] uppercase tracking-[0.2em] text-white/40">
            Field notes · 04:18
          </p>
        </div>
      </div>
    );
  }
  if (kind === "chapter-marker") {
    return (
      <div className="relative flex h-full items-center justify-center overflow-hidden bg-[#f3f1eb] text-[#20201e]">
        <span className="absolute -bottom-10 right-2 text-[9rem] font-semibold leading-none tracking-[-0.1em] text-black/[0.04] transition-transform duration-500 group-hover:-translate-x-3">02</span>
        <div className="relative flex items-center gap-4">
          <span className="font-mono text-[11px] text-black/35">02</span>
          <span className="h-10 w-px bg-black/20" />
          <div>
            <p className="text-[9px] uppercase tracking-[0.24em] text-black/35">Chapter</p>
            <p className="mt-1 text-xl font-medium tracking-tight">Into the hills</p>
          </div>
        </div>
      </div>
    );
  }
  return (
    <div className="relative flex h-full items-center justify-center overflow-hidden bg-[#d6e0ff] text-[#162044]">
      <div className="absolute -left-10 top-1/2 h-40 w-40 -translate-y-1/2 rounded-full bg-[#ff8f70]/80 blur-[1px] transition-transform duration-500 group-hover:translate-x-3" />
      <div className="absolute -right-12 top-1/2 h-48 w-48 -translate-y-1/2 rounded-full border-[18px] border-white/50 transition-transform duration-500 group-hover:-translate-x-3" />
      <div className="relative rounded-xl border border-white/70 bg-white/75 px-7 py-5 text-center shadow-[0_16px_45px_rgba(31,47,103,0.12)] backdrop-blur">
        <p className="text-lg font-semibold tracking-[-0.04em]">Keep exploring.</p>
        <p className="mt-1 text-[9px] uppercase tracking-[0.18em] text-[#162044]/45">
          More stories soon
        </p>
      </div>
    </div>
  );
}
