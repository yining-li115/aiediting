import fs from "node:fs/promises";
import path from "node:path";
import process from "node:process";
import {bundle} from "@remotion/bundler";
import {renderStill, selectComposition} from "@remotion/renderer";

const ALLOWED_PACKAGES = new Set([
  "react",
  "react/jsx-runtime",
  "remotion",
  "@remotion/captions",
  "@remotion/fonts",
  "@remotion/layout-utils",
  "@remotion/lottie",
  "@remotion/paths",
  "@remotion/shapes",
  "@remotion/transitions",
]);

const [, , manifestPath, sourceRoot, outputRoot, chromeExecutable = ""] = process.argv;
if (!manifestPath || !sourceRoot || !outputRoot) {
  throw new Error(
    "usage: validate-candidate.mjs <manifest> <source-root> <output-root> [chrome]",
  );
}

const manifest = JSON.parse(await fs.readFile(manifestPath, "utf8"));
const importedPackages = await inspectImports(sourceRoot);
const disallowed = importedPackages.filter((name) => !ALLOWED_PACKAGES.has(name));
if (disallowed.length) {
  throw new Error(`disallowed package import(s): ${disallowed.join(", ")}`);
}

await fs.mkdir(outputRoot, {recursive: true});
const entrypoint = path.resolve(sourceRoot, manifest.entrypoint);
const relativeEntrypoint = path
  .relative(outputRoot, entrypoint)
  .split(path.sep)
  .join("/");
const importPath = relativeEntrypoint.startsWith(".")
  ? relativeEntrypoint
  : `./${relativeEntrypoint}`;
const generatedEntry = path.join(outputRoot, "candidate-entry.tsx");
const propsLiteral = JSON.stringify(manifest.example_props ?? {});
await fs.writeFile(
  generatedEntry,
  `import React from "react";
import {Composition, registerRoot} from "remotion";
import {${manifest.export_name} as CandidateComponent} from ${JSON.stringify(importPath)};
const CandidateHost = ({componentProps, aive}) => (
  <CandidateComponent {...componentProps} aive={aive} />
);
const calculateMetadata = ({props}) => {
  const runtime = props.__aiveRuntime;
  if (!runtime || !Number.isInteger(runtime.durationInFrames) ||
      !Number.isFinite(runtime.fps) || !Number.isInteger(runtime.width) ||
      !Number.isInteger(runtime.height)) {
    throw new Error("missing or invalid AIVE Remotion runtime metadata");
  }
  return {
    durationInFrames: runtime.durationInFrames,
    fps: runtime.fps,
    width: runtime.width,
    height: runtime.height,
  };
};
const Root = () => <Composition
  id="CandidatePreview"
  component={CandidateHost}
  durationInFrames={30}
  fps={30}
  width={640}
  height={360}
  defaultProps={{
    componentProps: ${propsLiteral},
    aive: {usage: ${JSON.stringify(manifest.usage)}, text: "AIVE Preview"},
    __aiveRuntime: {durationInFrames: 30, fps: 30, width: 640, height: 360},
  }}
  calculateMetadata={calculateMetadata}
/>;
registerRoot(Root);
`,
  "utf8",
);

const bundleDir = path.join(outputRoot, "bundle");
const serveUrl = await bundle({
  entryPoint: generatedEntry,
  outDir: bundleDir,
  rootDir: path.resolve(path.dirname(new URL(import.meta.url).pathname), ".."),
  publicDir: null,
  enableCaching: false,
  keyboardShortcutsEnabled: false,
  askAIEnabled: false,
  onProgress: () => undefined,
});
const inputProps = {
  componentProps: manifest.example_props ?? {},
  aive: {usage: manifest.usage, text: "AIVE Preview"},
  __aiveRuntime: {durationInFrames: 30, fps: 30, width: 640, height: 360},
};
const composition = await selectComposition({
  serveUrl,
  id: "CandidatePreview",
  inputProps,
  browserExecutable: chromeExecutable || null,
  logLevel: "error",
});
const previewPath = path.join(outputRoot, "preview.png");
await renderStill({
  composition,
  serveUrl,
  output: previewPath,
  inputProps,
  frame: 15,
  browserExecutable: chromeExecutable || null,
  logLevel: "error",
  timeoutInMilliseconds: 15_000,
});
await fs.writeFile(
  path.join(outputRoot, "result.json"),
  JSON.stringify({
    importedPackages,
    bundleDir,
    previewPath,
    remotionVersion: manifest.remotion_version,
  }),
  "utf8",
);

async function inspectImports(root) {
  const files = await walk(root);
  const packages = new Set();
  const patterns = [
    /(?:import|export)\s+(?:[^"']*?\s+from\s+)?["']([^"']+)["']/g,
    /import\s*\(\s*["']([^"']+)["']\s*\)/g,
    /require\s*\(\s*["']([^"']+)["']\s*\)/g,
  ];
  for (const file of files) {
    if (!/\.[cm]?[jt]sx?$/.test(file)) continue;
    const source = await fs.readFile(file, "utf8");
    for (const pattern of patterns) {
      pattern.lastIndex = 0;
      let match;
      while ((match = pattern.exec(source)) !== null) {
        const specifier = match[1];
        if (specifier.startsWith(".") || specifier.startsWith("/")) continue;
        if (specifier.startsWith("node:") || /^https?:/.test(specifier)) {
          packages.add(specifier);
          continue;
        }
        packages.add(packageName(specifier));
      }
    }
  }
  return [...packages].sort();
}

function packageName(specifier) {
  const parts = specifier.split("/");
  return specifier.startsWith("@") ? `${parts[0]}/${parts[1]}` : parts[0];
}

async function walk(root) {
  const result = [];
  for (const entry of await fs.readdir(root, {withFileTypes: true})) {
    const target = path.join(root, entry.name);
    if (entry.isDirectory()) result.push(...(await walk(target)));
    else if (entry.isFile()) result.push(target);
  }
  return result;
}
