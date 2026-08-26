"use client";

import { ChevronDown } from "lucide-react";

interface Option {
  readonly value: string | number;
  readonly label: string;
}

interface Props {
  value: string | number;
  onChange: (value: string) => void;
  options: readonly Option[];
  icon?: React.ReactNode;
}

export function Select({ value, onChange, options, icon }: Props) {
  return (
    <div className="relative">
      {icon && (
        <span className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-ink-faint">
          {icon}
        </span>
      )}
      <select
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className={`h-[38px] w-full rounded-[var(--radius-control)] border border-line-strong bg-surface text-[13px] text-ink outline-none focus:border-accent ${
          icon ? "pl-9 pr-8" : "px-3 pr-8"
        }`}
      >
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
      <ChevronDown
        size={15}
        className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-ink-faint"
      />
    </div>
  );
}
