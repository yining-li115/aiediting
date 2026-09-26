"use client";

import { Bell, ChevronDown, History, LayoutTemplate } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";

import { LAYOUT } from "@/lib/config";

// TODO 硬编码: the Chat tab has no page behind it. It is where the
// conversational editing agent will live (TECHNICAL_PLAN section 11); until
// that exists, switching to it would show an empty screen.
const TABS = ["Chat", "Workspace"] as const;

// TODO 硬编码: signed-in user. No authentication exists yet.
const USER = { name: "Lena", initial: "L" };

export function TopBar() {
  const [active, setActive] = useState<(typeof TABS)[number]>("Workspace");
  const pathname = usePathname();
  const projectMatch = pathname.match(/^\/projects\/([^/]+)/);
  const historyHref = projectMatch
    ? `/projects/${projectMatch[1]}#history`
    : null;

  return (
    <header
      className="flex shrink-0 items-center justify-between gap-4 border-b border-line bg-surface px-4 sm:px-7"
      style={{ height: LAYOUT.topBarHeight }}
    >
      <nav className="flex h-full items-center gap-7">
        {TABS.map((tab) => (
          <button
            key={tab}
            type="button"
            onClick={() => setActive(tab)}
            className={`relative flex h-full items-center text-[14px] transition-colors ${
              active === tab
                ? "font-semibold text-ink"
                : "text-ink-soft hover:text-ink"
            }`}
          >
            {tab}
            {active === tab && (
              <span className="absolute inset-x-0 bottom-0 h-[2px] rounded-full bg-ink" />
            )}
          </button>
        ))}
      </nav>

      <div className="flex items-center gap-4 xl:gap-5">
        {/* Labels drop before the whole control does: the icon still works. */}
        <button
          type="button"
          className="flex items-center gap-2 text-[13.5px] text-ink-soft transition-colors hover:text-ink"
        >
          <LayoutTemplate size={16} />
          <span className="hidden md:inline">Templates</span>
        </button>
        {historyHref ? (
          <Link
            href={historyHref}
            className="flex items-center gap-2 text-[13.5px] text-ink-soft transition-colors hover:text-ink"
          >
            <History size={16} />
            <span className="hidden md:inline">History</span>
          </Link>
        ) : (
          <span
            aria-disabled="true"
            title="Open a project to view its history"
            className="flex cursor-not-allowed items-center gap-2 text-[13.5px] text-ink-faint"
          >
            <History size={16} />
            <span className="hidden md:inline">History</span>
          </span>
        )}
        <button
          type="button"
          className="text-ink-soft transition-colors hover:text-ink"
          aria-label="Notifications"
        >
          <Bell size={17} />
        </button>
        <button type="button" className="flex items-center gap-2">
          <span className="flex h-7 w-7 items-center justify-center rounded-full bg-accent-soft text-[12px] font-semibold text-accent">
            {USER.initial}
          </span>
          <span className="hidden text-[13.5px] sm:inline">{USER.name}</span>
          <ChevronDown size={14} className="text-ink-faint" />
        </button>
      </div>
    </header>
  );
}
