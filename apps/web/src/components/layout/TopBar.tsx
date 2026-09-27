"use client";

import { Compass, Play, Plus, Sparkles } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { APP_NAME, LAYOUT } from "@/lib/config";

export function TopBar() {
  const pathname = usePathname();
  const projectMatch = pathname.match(/^\/projects\/([^/]+)/);

  return (
    <header
      className="flex shrink-0 items-center justify-between gap-3 border-b border-line bg-white px-3"
      style={{ height: LAYOUT.topBarHeight }}
    >
      <div className="flex min-w-0 items-center gap-3">
        <Link href="/" className="flex items-center gap-2 text-xs font-semibold">
          <span className="flex h-6 w-6 items-center justify-center rounded-md bg-ink text-white">
            <Sparkles size={12} />
          </span>
          <span className="hidden sm:inline">{APP_NAME}</span>
        </Link>
        <span className="h-4 w-px bg-line-strong" />
        <span className="max-w-52 truncate text-[11px] text-ink-faint">
          {projectMatch ? `Project ${projectMatch[1].slice(0, 12)}` : "Video Agent"}
        </span>
        {projectMatch && (
          <span className="hidden items-center gap-1.5 rounded-md bg-accent-soft px-2.5 py-1.5 text-[11px] font-medium text-ink-soft sm:flex">
            <Play size={11} fill="currentColor" /> Preview
          </span>
        )}
      </div>

      <nav className="flex items-center gap-1.5">
        {!projectMatch && (
          <a
            href="#remotion-templates"
            className="flex h-8 items-center gap-1.5 rounded-md px-2.5 text-xs text-ink-soft transition-colors hover:bg-accent-soft hover:text-ink"
          >
            <Compass size={14} />
            <span className="hidden sm:inline">Explore</span>
          </a>
        )}
        <a
          href={projectMatch ? "/" : "#composer"}
          className="flex h-8 items-center gap-1.5 rounded-md bg-ink px-3 text-xs font-medium text-white transition-colors hover:bg-accent-hover"
        >
          <Plus size={14} />
          <span className="hidden sm:inline">
            {projectMatch ? "New project" : "Start creating"}
          </span>
        </a>
      </nav>
    </header>
  );
}
