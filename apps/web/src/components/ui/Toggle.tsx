"use client";

interface Props {
  checked: boolean;
  onChange: (checked: boolean) => void;
  label?: string;
  /** Marks a control whose value the backend currently ignores. */
  unwired?: boolean;
}

export function Toggle({ checked, onChange, label, unwired }: Props) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      onClick={() => onChange(!checked)}
      // A control that does nothing should at least admit it on hover rather
      // than silently accepting a setting that never reaches the backend.
      title={unwired ? `${label ?? "This setting"} is not wired up yet` : label}
      className={`relative h-[22px] w-[38px] shrink-0 rounded-full transition-colors ${
        checked ? "bg-accent" : "bg-[#dcdce1]"
      }`}
    >
      <span
        className={`absolute left-0 top-[3px] h-4 w-4 rounded-full bg-white shadow-sm transition-transform ${
          checked ? "translate-x-[19px]" : "translate-x-[3px]"
        }`}
      />
    </button>
  );
}
