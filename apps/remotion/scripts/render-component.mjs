import fs from "node:fs/promises";
import process from "node:process";
import {renderMedia, selectComposition} from "@remotion/renderer";

const [
  ,
  ,
  bundleDir,
  inputPath,
  outputPath,
  chromeExecutable = "",
] = process.argv;

if (!bundleDir || !inputPath || !outputPath || !chromeExecutable) {
  throw new Error(
    "usage: render-component.mjs <bundle-dir> <input-json> <output-mov> <chrome>",
  );
}

const inputProps = JSON.parse(await fs.readFile(inputPath, "utf8"));
const composition = await selectComposition({
  serveUrl: bundleDir,
  id: "CandidatePreview",
  inputProps,
  browserExecutable: chromeExecutable,
  logLevel: "error",
});

await renderMedia({
  composition,
  serveUrl: bundleDir,
  outputLocation: outputPath,
  inputProps,
  browserExecutable: chromeExecutable,
  codec: "prores",
  proResProfile: "4444",
  imageFormat: "png",
  pixelFormat: "yuva444p10le",
  muted: true,
  concurrency: 1,
  overwrite: true,
  timeoutInMilliseconds: 20_000,
  logLevel: "error",
});

const stat = await fs.stat(outputPath);
if (!stat.isFile() || stat.size === 0) {
  throw new Error("Remotion component render produced no media");
}
