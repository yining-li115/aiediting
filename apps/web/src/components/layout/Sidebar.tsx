"use client";

import {
  Folder,
  Home,
  Images,
  LayoutTemplate,
  Plus,
  Settings,
  Sparkles,
  X,
} from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { APP_NAME, CREDITS, LAYOUT } from "@/lib/config";

// TODO 硬编码: navigation items. None of these routes exist yet - only the
// workspace page is built - so every entry other than Home is decorative.
const NAV = [
  { icon: Home, label: "Home", href: "/", active: true },
  { icon: Folder, label: "Projects", href: "/projects" },
  { icon: Images, label: "Assets", href: "/assets" },
  { icon: LayoutTemplate, label: "Templates", href: "/templates" },
  { icon: Settings, label: "Settings", href: "/settings" },
];

export function Sidebar() {
  const [showUpgrade, setShowUpgrade] = useState(true);
  const creditFraction = CREDITS.used / CREDITS.total;

  return (
    <aside
      // Hidden rather than squeezed on narrow screens: a 160px sidebar is
      // worse than no sidebar.
      className="hidden shrink-0 flex-col border-r border-line bg-surface lg:flex"
      style={{ width: LAYOUT.sidebarWidth }}
    >
      <div
        className="flex items-center gap-2.5 px-5"
        style={{ height: LAYOUT.topBarHeight }}
      >
        <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-ink">
          <Sparkles size={15} className="text-white" />
        </span>
        <span className="text-[15px] font-semibold tracking-tight">
          {APP_NAME}
        </span>
      </div>

      <div className="px-4 pb-1 pt-2">
        <Link
          href="/"
          className="flex h-10 w-full items-center justify-center gap-2 rounded-[var(--radius-control)] border border-line-strong text-[13.5px] font-medium text-ink transition-colors hover:bg-canvas"
        >
          <Plus size={16} />
          New Project
        </Link>
      </div>

      <nav className="flex flex-col gap-0.5 px-4 pt-3">
        {NAV.map(({ icon: Icon, label, active }) => (
          <button
            key={label}
            type="button"
            className={`flex h-10 items-center gap-3 rounded-[var(--radius-control)] px-3 text-[13.5px] transition-colors ${
              active
                ? "bg-canvas font-medium text-ink"
                : "text-ink-soft hover:bg-canvas"
            }`}
          >
            <Icon size={17} className={active ? "text-ink" : "text-ink-soft"} />
            {label}
          </button>
        ))}
      </nav>

      <div className="mt-auto p-4">
        {showUpgrade && (
          <div className="relative mb-3 rounded-[var(--radius-card)] border border-line bg-canvas p-4">
            <button
              type="button"
              onClick={() => setShowUpgrade(false)}
              className="absolute right-3 top-3 text-ink-faint hover:text-ink"
              aria-label="Dismiss"
            >
              <X size={14} />
            </button>
            <div className="flex items-start gap-2">
              <span className="mt-0.5 flex h-5 w-5 items-center justify-center rounded-md bg-accent-soft">
                <Sparkles size={11} className="text-accent" />
              </span>
              <p className="text-[13px] font-semibold leading-[1.35]">
                Upgrade for
                <br />
                more credits
              </p>
            </div>
            <p className="mt-3 text-[12px] leading-[1.5] text-ink-soft">
              More exports, longer videos, and priority generation.
            </p>
            <button
              type="button"
              className="mt-3.5 h-9 w-full rounded-[var(--radius-control)] bg-ink text-[13px] font-medium text-white transition-opacity hover:opacity-90"
            >
              Upgrade Plan
            </button>
          </div>
        )}

        {/* TODO 硬编码: credits are display-only. There is no billing system,
            so these numbers are fixed and must not be shown as real. */}
        <div className="rounded-[var(--radius-card)] border border-line p-4">
          <div className="flex items-baseline justify-between text-[12px]">
            <span className="text-ink-soft">Credits</span>
            <span className="font-medium">
              {CREDITS.used.toLocaleString()} / {CREDITS.total.toLocaleString()}
            </span>
          </div>
          <div className="mt-2.5 h-1.5 overflow-hidden rounded-full bg-[#ececee]">
            <div
              className="h-full rounded-full bg-accent"
              style={{ width: `${creditFraction * 100}%` }}
            />
          </div>
        </div>
      </div>
    </aside>
  );
}
