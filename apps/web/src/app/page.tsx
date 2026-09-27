"use client";

import { Captions, Clapperboard, Film, Sparkles } from "lucide-react";
import { useRouter } from "next/navigation";
import { useCallback, useRef, useState } from "react";

import {
  ProjectComposer,
  type ProjectSettings,
  type UploadKind,
  type UploadedFile,
} from "@/components/workspace/ProjectComposer";
import {
  RemotionTemplateGallery,
  type RemotionPromptTemplate,
} from "@/components/workspace/RemotionTemplateGallery";
import { ApiError, createProject, uploadAssets } from "@/lib/api/client";
import type {
  CreateProjectRequest,
  UploadAssetsRequest,
} from "@/lib/api/types";
import {
  DEFAULT_SETTINGS,
  OUTPUT_DIMENSIONS,
  PROMPT_MAX_CHARS,
} from "@/lib/config";

/**
 * The workspace: everything the user gives us before a cut exists.
 *
 * The active upload slots are not variations on one another - they map to
 * different pipelines in the backend:
 *
 *   User Materials   the footage to cut from
 *   Style References videos whose immutable Blueprints condition the Director
 * Remake Reference is intentionally visible-but-disabled as a later extension.
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
  };
}

const QUICK_PROMPTS = [
  {
    title: "Travel story",
    prompt:
      "Create a cinematic travel story with a strong opening, clear visual progression, restrained captions, and music that builds toward the ending.",
    icon: Film,
  },
  {
    title: "Vertical highlights",
    prompt:
      "Create a fast-paced vertical highlight reel. Open with the strongest moment, remove pauses, keep the rhythm energetic, and add concise readable captions.",
    icon: Sparkles,
  },
  {
    title: "Interview cut",
    prompt:
      "Find the most compelling statements in this interview and shape them into a concise narrative with natural pacing, clean cuts, and accurate captions.",
    icon: Captions,
  },
  {
    title: "Visual essay",
    prompt:
      "Create a calm premium visual essay with longer confident shots, minimal typography, subtle music, and transitions that never distract from the material.",
    icon: Clapperboard,
  },
] as const;

export default function WorkspacePage() {
  const router = useRouter();
  const [prompt, setPrompt] = useState("");
  const [settings, setSettings] = useState<ProjectSettings>({
    ...DEFAULT_SETTINGS,
  });

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

  const setterFor = (kind: UploadKind) => {
    if (kind === "materials") return setMaterials;
    if (kind === "styles") return setStyles;
    if (kind === "remake") return setRemake;
    return setMusic;
  };

  const add = (kind: UploadKind, files: File[]) => {
    const setter = setterFor(kind);
    const single = kind === "remake" || kind === "music";
    setter((current) =>
      single ? [toUploaded(files[0])] : [...current, ...files.map(toUploaded)],
    );
  };

  const remove = (kind: UploadKind, id: string) =>
    setterFor(kind)((current) => current.filter((file) => file.id !== id));

  const useTemplate = (template: RemotionPromptTemplate) => {
    setPrompt((current) => {
      const existing = current.trim();
      const next = existing
        ? `${existing}\n\nVisual treatment: ${template.prompt}`
        : template.prompt;
      return next.slice(0, PROMPT_MAX_CHARS);
    });
    window.requestAnimationFrame(() => {
      document
        .getElementById("composer")
        ?.scrollIntoView({ behavior: "smooth", block: "center" });
    });
  };

  const onGenerate = useCallback(async () => {
    const dimensions =
      OUTPUT_DIMENSIONS[settings.quality as keyof typeof OUTPUT_DIMENSIONS][
        settings.aspectRatio as keyof (typeof OUTPUT_DIMENSIONS)["1080p"]
      ];
    const projectFingerprint = JSON.stringify({
      prompt,
      settings,
      dimensions,
      taskMode: "autonomous",
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
              task_mode: "autonomous",
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
  }, [materials, music, prompt, router, settings, styles]);

  return (
    <div className="mx-auto w-full max-w-6xl px-4 pb-20 pt-12 sm:px-6 sm:pt-20">
      <div className="mx-auto max-w-3xl text-center">
        <h1 className="text-4xl font-semibold tracking-[-0.045em] text-ink sm:text-5xl">
          What will we make?
        </h1>
        <p className="mx-auto mt-4 max-w-xl text-sm leading-6 text-ink-soft">
          Give the Agent your footage and a direction. It will build the first cut, explain its work, and keep every version safe.
        </p>
      </div>

      <div className="mt-9">
        <ProjectComposer
          prompt={prompt}
          onPromptChange={setPrompt}
          settings={settings}
          onSettingsChange={setSettings}
          uploads={{ materials, styles, remake, music }}
          onAdd={add}
          onRemove={remove}
          onCreate={onGenerate}
          creating={creating}
          progress={uploadProgress}
        />
        <div className="mx-auto mt-3 flex max-w-3xl flex-wrap justify-center gap-1.5">
          {QUICK_PROMPTS.map((item) => {
            const Icon = item.icon;
            return (
              <button
                key={item.title}
                type="button"
                onClick={() => setPrompt(item.prompt)}
                className="flex items-center gap-1.5 rounded-full border border-line bg-white px-3 py-1.5 text-[11px] text-ink-soft transition hover:border-line-strong hover:text-ink"
              >
                <Icon size={12} />
                {item.title}
              </button>
            );
          })}
        </div>
      </div>

      {error && (
        <p className="mx-auto mt-4 max-w-3xl rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          {error}
        </p>
      )}

      <RemotionTemplateGallery onUseTemplate={useTemplate} />
    </div>
  );
}
