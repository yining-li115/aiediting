"use client";

import { Plus, Upload, X } from "lucide-react";
import { useRef, useState } from "react";

import { THUMBNAIL_PREVIEW_COUNT } from "@/lib/config";

export interface UploadedFile {
  id: string;
  name: string;
  file: File;
  /** Object URL for a local preview, or a backend media URL once uploaded. */
  thumbnail?: string;
  durationLabel?: string;
  resolutionLabel?: string;
}

interface Props {
  icon: React.ReactNode;
  iconClass: string;
  title: string;
  optional?: boolean;
  description: string;
  dropzoneTitle: string;
  supportsLabel: string;
  maxSizeLabel: string;
  accept: string;
  multiple: boolean;
  files: UploadedFile[];
  onAdd: (files: File[]) => void;
  onRemove: (id: string) => void;
  /** "12 files uploaded" / "4 style references" */
  countLabel?: (count: number) => string;
  /** A single file shows as a detail row rather than a thumbnail strip. */
  variant?: "grid" | "single";
}

export function UploadCard({
  icon,
  iconClass,
  title,
  optional,
  description,
  dropzoneTitle,
  supportsLabel,
  maxSizeLabel,
  accept,
  multiple,
  files,
  onAdd,
  onRemove,
  countLabel,
  variant = "grid",
}: Props) {
  const input = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);

  const take = (list: FileList | null) => {
    if (!list?.length) return;
    onAdd(Array.from(list));
  };

  const shown = files.slice(0, THUMBNAIL_PREVIEW_COUNT + 1);
  const overflow = files.length - shown.length;

  return (
    <section className="flex flex-col rounded-[var(--radius-card)] border border-line bg-surface p-5">
      <div className="flex items-center gap-2.5">
        <span
          className={`flex h-6 w-6 items-center justify-center rounded-md ${iconClass}`}
        >
          {icon}
        </span>
        <h3 className="text-[14px] font-semibold">
          {title}
          {optional && (
            <span className="ml-1.5 font-normal text-ink-faint">(Optional)</span>
          )}
        </h3>
      </div>
      <p className="mt-2 text-[12.5px] leading-[1.55] text-ink-soft">
        {description}
      </p>

      <button
        type="button"
        onClick={() => input.current?.click()}
        onDragOver={(event) => {
          event.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(event) => {
          event.preventDefault();
          setDragging(false);
          take(event.dataTransfer.files);
        }}
        className={`mt-4 flex flex-1 flex-col items-center justify-center rounded-[12px] border border-dashed py-9 transition-colors ${
          dragging
            ? "border-accent bg-accent-soft/40"
            : "border-line-strong bg-canvas/60 hover:bg-canvas"
        }`}
      >
        <Upload size={19} className="text-ink-soft" />
        <span className="mt-2.5 text-[13px] font-medium">{dropzoneTitle}</span>
        <span className="mt-1 text-[12px] text-ink-faint">or drag and drop</span>
        <span className="mt-3.5 text-[11.5px] text-ink-faint">
          {supportsLabel}
        </span>
        <span className="text-[11.5px] text-ink-faint">{maxSizeLabel}</span>
      </button>

      <input
        ref={input}
        type="file"
        accept={accept}
        multiple={multiple}
        hidden
        onChange={(event) => {
          take(event.target.files);
          event.target.value = "";
        }}
      />

      {files.length > 0 && variant === "grid" && (
        <div className="mt-3.5 rounded-[12px] border border-line bg-canvas/60 p-3">
          <p className="text-[12px] text-ink-soft">
            {countLabel?.(files.length) ?? `${files.length} files`}
          </p>
          <div className="mt-2.5 flex gap-2">
            {shown.map((file) => (
              <div
                key={file.id}
                className="group relative h-[52px] w-[52px] shrink-0 overflow-hidden rounded-lg border border-line-strong bg-[#e9e9ed]"
              >
                {file.thumbnail && (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img
                    src={file.thumbnail}
                    alt={file.name}
                    className="h-full w-full object-cover"
                  />
                )}
                <button
                  type="button"
                  onClick={() => onRemove(file.id)}
                  aria-label={`Remove ${file.name}`}
                  className="absolute right-0.5 top-0.5 hidden h-4 w-4 items-center justify-center rounded-full bg-black/60 text-white group-hover:flex"
                >
                  <X size={9} />
                </button>
              </div>
            ))}
            {overflow > 0 ? (
              <div className="flex h-[52px] w-[52px] shrink-0 items-center justify-center rounded-lg border border-line-strong bg-surface text-[12px] font-medium text-ink-soft">
                +{overflow}
              </div>
            ) : (
              <button
                type="button"
                onClick={() => input.current?.click()}
                className="flex h-[52px] w-[52px] shrink-0 items-center justify-center rounded-lg border border-dashed border-line-strong bg-surface text-ink-soft transition-colors hover:bg-canvas"
                aria-label="Add more"
              >
                <Plus size={16} />
              </button>
            )}
          </div>
        </div>
      )}

      {files.length > 0 && variant === "single" && (
        <div className="mt-3.5 flex items-center gap-3 rounded-[12px] border border-line bg-canvas/60 p-3">
          <div className="h-[46px] w-[62px] shrink-0 overflow-hidden rounded-lg bg-[#e9e9ed]">
            {files[0].thumbnail && (
              // eslint-disable-next-line @next/next/no-img-element
              <img
                src={files[0].thumbnail}
                alt={files[0].name}
                className="h-full w-full object-cover"
              />
            )}
          </div>
          <div className="min-w-0 flex-1">
            <p className="truncate text-[13px] font-medium">{files[0].name}</p>
            <p className="mt-1 flex gap-3 text-[11.5px] text-ink-faint">
              {files[0].durationLabel && <span>{files[0].durationLabel}</span>}
              {files[0].resolutionLabel && (
                <span>{files[0].resolutionLabel}</span>
              )}
            </p>
          </div>
          <button
            type="button"
            onClick={() => onRemove(files[0].id)}
            aria-label="Remove"
            className="text-ink-faint transition-colors hover:text-ink"
          >
            <X size={16} />
          </button>
        </div>
      )}
    </section>
  );
}
