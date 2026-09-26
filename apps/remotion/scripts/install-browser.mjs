import {ensureBrowser} from "@remotion/renderer";

const status = await ensureBrowser({
  chromeMode: "headless-shell",
  logLevel: "info",
});

if (!("path" in status)) {
  throw new Error(`Remotion browser installation failed: ${status.type}`);
}

process.stdout.write(`${status.path}\n`);
