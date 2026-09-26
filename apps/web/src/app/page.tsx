"use client";

import { Film, Layers, Music2, Video } from "lucide-react";
import { useRouter } from "next/navigation";
import { useCallback, useRef, useState } from "react";

import { PromptCard } from "@/components/workspace/PromptCard";
import {
  UploadCard,
  type UploadedFile,
} from "@/components/workspace/UploadCard";
import {
  VideoSettings,
  type Settings,
} from "@/components/workspace/VideoSettings";
import { ApiError, createProject, uploadAssets } from "@/lib/api/client";
import type {
  CreateProjectRequest,
  UploadAssetsRequest,
} from "@/lib/api/types";
import {
  DEFAULT_SETTINGS,
  LAYOUT,
  OUTPUT_DIMENSIONS,
  UPLOAD_LIMITS,
} from "@/lib/config";

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
 * Project creation commits a minimal initial revision first. The selected
 * media is then registered in project storage and attached through one
 * server-generated Patch, producing the next immutable revision.
 */

function toUploaded(file: File): UploadedFile {
  return {
    id: `asset_${crypto.randomUUID()}`,
    name: file.name,
    file,
    thumbnail: file.type.startsWith("image/")
      ? URL.createObjectURL(file)
      : undefined,
    // Duration and resolution come from the backend's probe step.
    durationLabel: undefined,
    resolutionLabel: undefined,
  };
}

export default function WorkspacePage() {
  const router = useRouter();
  const [prompt, setPrompt] = useState("");
  const [autoEnhance, setAutoEnhance] = useState(true);
  const [settings, setSettings] = useState<Settings>({ ...DEFAULT_SETTINGS });

  const [materials, setMaterials] = useState<UploadedFile[]>([]);
  const [styles, setStyles] = useState<UploadedFile[]>([]);
  const [remake, setRemake] = useState<UploadedFile[]>([]);
  const [music, setMusic] = useState<UploadedFile[]>([]);
  const [creating, setCreating] = useState(false);
  const [uploadProgress, setUploadProgress] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const pendingCreate = useRef<{
    projectFingerprint: string;
    createRequest: CreateProjectRequest;
    mediaFingerprint?: string;
    uploadRequest?: UploadAssetsRequest;
  } | null>(null);

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

  const onGenerate = useCallback(async () => {
    const dimensions =
      OUTPUT_DIMENSIONS[settings.quality as keyof typeof OUTPUT_DIMENSIONS][
        settings.aspectRatio as keyof (typeof OUTPUT_DIMENSIONS)["1080p"]
      ];
    const projectFingerprint = JSON.stringify({
      prompt,
      settings,
      dimensions,
      taskMode: remake.length ? "reference_remake" : "autonomous",
    });
    const existing = pendingCreate.current;
    const createRequest: CreateProjectRequest =
      existing?.projectFingerprint === projectFingerprint
        ? existing.createRequest
        : {
            request_id: `request_${crypto.randomUUID()}`,
            project_id: `project_${crypto.randomUUID()}`,
            idempotency_key: `create_${crypto.randomUUID()}`,
            origin: "user",
            summary: "Project created from the Web workspace",
            intent: {
              task_mode: remake.length ? "reference_remake" : "autonomous",
              target_duration_frames: settings.length * 30,
              prompt: prompt.trim() || null,
              caption_policy: settings.autoCaptions ? "auto" : "none",
              music_policy: settings.backgroundMusic ? "auto" : "none",
              style_tags: [settings.vibe],
            },
            output: {
              ...dimensions,
              fps: { num: 30, den: 1 },
              audio_sample_rate: 48000,
              color_space: "bt709",
            },
          };
    let pending =
      existing?.projectFingerprint === projectFingerprint
        ? existing
        : { projectFingerprint, createRequest };
    pendingCreate.current = pending;
    setCreating(true);
    setUploadProgress(null);
    setError(null);
    let projectCreated = false;
    try {
      const created = await createProject(createRequest);
      const initialRevision = created.revision;
      const projectId = initialRevision?.document.project_id;
      if (!projectId || !initialRevision) {
        throw new Error("Project creation returned no initial revision");
      }
      projectCreated = true;
      const media = [
        ...materials.map((uploaded) => ({
          uploaded,
          roles: ["source"] as ["source"],
        })),
        ...styles.map((uploaded) => ({
          uploaded,
          roles: ["reference", "style_reference"] as [
            "reference",
            "style_reference",
          ],
        })),
        ...remake.map((uploaded) => ({
          uploaded,
          roles: ["reference", "remake_reference"] as [
            "reference",
            "remake_reference",
          ],
        })),
        ...music.map((uploaded) => ({
          uploaded,
          roles: ["music"] as ["music"],
        })),
      ];
      if (media.length) {
        const toUploadItem = ({
          uploaded,
          roles,
        }: (typeof media)[number]) => ({
          asset_id: uploaded.id,
          roles,
        });
        const uploadItems: UploadAssetsRequest["items"] = [
          toUploadItem(media[0]),
          ...media.slice(1).map(toUploadItem),
        ];
        const mediaFingerprint = JSON.stringify(
          media.map(({ uploaded, roles }) => ({
            id: uploaded.id,
            name: uploaded.name,
            size: uploaded.file.size,
            lastModified: uploaded.file.lastModified,
            roles,
          })),
        );
        const uploadRequest: UploadAssetsRequest =
          pending.mediaFingerprint === mediaFingerprint && pending.uploadRequest
            ? pending.uploadRequest
            : {
                request_id: `request_${crypto.randomUUID()}`,
                patch_id: `patch_${crypto.randomUUID()}`,
                project_id: projectId,
                base_revision_id: initialRevision.document.revision_id,
                idempotency_key: `upload_${crypto.randomUUID()}`,
                origin: "user",
                summary: "Add media uploaded from the Web workspace",
                items: uploadItems,
              };
        pending = {
          ...pending,
          mediaFingerprint,
          uploadRequest,
        };
        pendingCreate.current = pending;
        setUploadProgress(0);
        const uploaded = await uploadAssets(
          projectId,
          uploadRequest,
          media.map(({ uploaded: item }) => item.file),
          setUploadProgress,
        );
        if (!uploaded.revision) {
          throw new Error("Media upload returned no committed revision");
        }
      }
      pendingCreate.current = null;
      router.push(`/projects/${encodeURIComponent(projectId)}`);
    } catch (cause) {
      if (cause instanceof ApiError && !cause.retryable && !projectCreated) {
        pendingCreate.current = null;
      }
      setError(
        cause instanceof ApiError
          ? cause.detail
          : "Project setup did not complete. Retry to reuse the same request safely.",
      );
    } finally {
      setCreating(false);
      setUploadProgress(null);
    }
  }, [materials, music, prompt, remake, router, settings, styles]);

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

      {error && (
        <p className="rounded-[var(--radius-control)] border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          {error}
        </p>
      )}

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
          <div className="grid grid-cols-1 gap-5 md:grid-cols-2 2xl:grid-cols-4">
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

            <UploadCard
              icon={<Music2 size={13} className="text-[#16a34a]" />}
              iconClass="bg-[#e8f7ec]"
              title="Licensed Music"
              optional
              description="Upload one owned or licensed track for the Director to mix under the cut."
              dropzoneTitle="Add a music track"
              {...UPLOAD_LIMITS.music}
              multiple={false}
              files={music}
              onAdd={add(setMusic, true)}
              onRemove={remove(setMusic)}
              variant="single"
            />
          </div>
        </div>

        <div className="w-full shrink-0 xl:w-[var(--settings-width)]">
          <VideoSettings
            settings={settings}
            onChange={setSettings}
            onGenerate={onGenerate}
            generating={creating}
            actionLabel="Create Project"
            busyLabel={
              uploadProgress === null
                ? "Creating Project..."
                : `Uploading Media... ${Math.round(uploadProgress * 100)}%`
            }
          />
        </div>
      </div>
    </div>
  );
}
