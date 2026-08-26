"use client";

import { Film, Layers, Video } from "lucide-react";
import { useCallback, useState } from "react";

import { PromptCard } from "@/components/workspace/PromptCard";
import {
  UploadCard,
  type UploadedFile,
} from "@/components/workspace/UploadCard";
import {
  VideoSettings,
  type Settings,
} from "@/components/workspace/VideoSettings";
import { DEFAULT_SETTINGS, LAYOUT, UPLOAD_LIMITS } from "@/lib/config";

/**
 * The workspace: everything the user gives us before a cut exists.
 *
 * The three upload slots are not variations on one another - they map to
 * three different pipelines in the backend:
 *
 *   User Materials   the footage to cut from
 *   Style References several videos, aggregated into a style prior (mode B1)
 *   Remake Video     exactly one video, used as a shot-by-shot template (B2)
 *
 * That is why the third takes a single file: five videos have no shot-level
 * correspondence with each other, so they cannot form a template.
 *
 * There is deliberately no preview here. Generating starts a job that runs for
 * minutes, and what follows is a different surface - live progress, then an
 * editing view - rather than an empty box sitting on this page beforehand.
 *
 * Nothing talks to the backend yet: the upload and generate endpoints are not
 * implemented, so state is local and `onGenerate` is a no-op.
 */

let counter = 0;
const nextId = () => `f_${++counter}`;

function toUploaded(file: File): UploadedFile {
  return {
    id: nextId(),
    name: file.name,
    thumbnail: file.type.startsWith("image/")
      ? URL.createObjectURL(file)
      : undefined,
    // Duration and resolution come from the backend's probe step, so they
    // stay unknown until upload is wired.
    durationLabel: undefined,
    resolutionLabel: undefined,
  };
}

export default function WorkspacePage() {
  const [prompt, setPrompt] = useState("");
  const [autoEnhance, setAutoEnhance] = useState(true);
  const [settings, setSettings] = useState<Settings>({ ...DEFAULT_SETTINGS });

  const [materials, setMaterials] = useState<UploadedFile[]>([]);
  const [styles, setStyles] = useState<UploadedFile[]>([]);
  const [remake, setRemake] = useState<UploadedFile[]>([]);

  const add =
    (
      setter: React.Dispatch<React.SetStateAction<UploadedFile[]>>,
      single = false,
    ) =>
    (files: File[]) =>
      setter((current) =>
        single ? [toUploaded(files[0])] : [...current, ...files.map(toUploaded)],
      );

  const remove =
    (setter: React.Dispatch<React.SetStateAction<UploadedFile[]>>) =>
    (id: string) =>
      setter((current) => current.filter((file) => file.id !== id));

  const onGenerate = useCallback(() => {
    // TODO 硬编码: no-op. POST /projects/{id}/generate is not implemented.
    // When it is, this starts a job and the page hands over to the progress
    // and editing view, subscribing with useJobStream.
    console.warn("generate is not wired to the backend yet");
  }, []);

  return (
    <div
      className="mx-auto flex w-full flex-col gap-5 px-4 py-5 sm:px-6 lg:px-7"
      style={{ maxWidth: LAYOUT.contentMaxWidth }}
    >
      <PromptCard
        value={prompt}
        onChange={setPrompt}
        autoEnhance={autoEnhance}
        onAutoEnhanceChange={setAutoEnhance}
      />

      {/* Side by side on a wide screen, stacked below it. The settings panel
          gets a floor and a ceiling rather than a fixed width, so it stays
          proportional instead of being right at exactly one monitor size. */}
      <div
        className="flex flex-col gap-5 xl:flex-row xl:items-start"
        style={
          { "--settings-width": LAYOUT.settingsWidth } as React.CSSProperties
        }
      >
        {/* min-w-0 matters: without it a long filename inside a card sets the
            flex item's minimum width and the whole row stops shrinking. */}
        <div className="min-w-0 flex-1">
          {/* One card per row on a phone, two on a tablet, three only once
              there is room for three comfortable ones rather than three
              narrow ones. */}
          <div className="grid grid-cols-1 gap-5 md:grid-cols-2 2xl:grid-cols-3">
            <UploadCard
              icon={<Video size={13} className="text-[#3b82f6]" />}
              iconClass="bg-[#e7f0fe]"
              title="User Materials"
              description="Upload your video clips and images. Multiple files supported."
              dropzoneTitle="Upload files"
              {...UPLOAD_LIMITS.material}
              multiple
              files={materials}
              onAdd={add(setMaterials)}
              onRemove={remove(setMaterials)}
              countLabel={(count) => `${count} files uploaded`}
            />

            <UploadCard
              icon={<Layers size={13} className="text-[#8b5cf6]" />}
              iconClass="bg-[#f0ebfe]"
              title="Style References"
              description="Upload reference videos to match the look, feel, color, and motion."
              dropzoneTitle="Add style reference videos"
              {...UPLOAD_LIMITS.style}
              multiple
              files={styles}
              onAdd={add(setStyles)}
              onRemove={remove(setStyles)}
              countLabel={(count) => `${count} style references`}
            />

            <UploadCard
              icon={<Film size={13} className="text-[#f97316]" />}
              iconClass="bg-[#feeee2]"
              title="Remake Video"
              optional
              description="Upload one video to replicate its editing structure and pacing."
              dropzoneTitle="Add remake reference"
              {...UPLOAD_LIMITS.remake}
              multiple={false}
              files={remake}
              onAdd={add(setRemake, true)}
              onRemove={remove(setRemake)}
              variant="single"
            />
          </div>
        </div>

        <div className="w-full shrink-0 xl:w-[var(--settings-width)]">
          <VideoSettings
            settings={settings}
            onChange={setSettings}
            onGenerate={onGenerate}
            disabled={materials.length === 0}
          />
        </div>
      </div>
    </div>
  );
}
