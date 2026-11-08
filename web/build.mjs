import { build } from "esbuild";
import { readFile, writeFile, mkdir } from "node:fs/promises";
import path from "node:path";

const result = await build({
  entryPoints: ["src/main.tsx"], bundle: true, minify: true, format: "esm",
  platform: "browser", target: "es2022", write: false, metafile: true,
  define: { "process.env.NODE_ENV": '"production"' }, legalComments: "inline",
});
const script = result.outputFiles[0].text.replace(/<\/script/gi, "<\\/script");
const css = (await readFile("src/style.css", "utf8")).replace(/<\/style/gi, "<\\/style");
const licenses = [];
const packages = new Set(Object.keys(result.metafile.inputs).filter(p => p.startsWith("node_modules/")).map(p => {
  const parts = p.split("/");
  return parts.slice(0, parts[1].startsWith("@") ? 3 : 2).join("/");
}));
for (const dir of [...packages].sort()) {
  const pkg = JSON.parse(await readFile(path.join(dir, "package.json"), "utf8"));
  let license = "";
  for (const name of ["LICENSE", "LICENSE.md", "LICENSE.txt", "license", "license.md"]) {
    try { license = await readFile(path.join(dir, name), "utf8"); break; } catch {}
  }
  if (!license) throw new Error(`Missing license notice for ${pkg.name}`);
  licenses.push(`${pkg.name} ${pkg.version} (${pkg.license})\n${license}`);
}
const notices = licenses.join("\n\n---\n\n");
const safeNotices = notices.replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;");
const html = `<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="color-scheme" content="light dark"><title>Chess Coach · Analysis board</title><style>${css}</style></head>
<body><div id="root"></div><details class="credits"><summary>Open-source credits</summary><pre>${safeNotices}</pre></details><script type="module">${script}</script></body></html>`;
await mkdir("dist", { recursive: true });
await writeFile("dist/board.html", html);
await writeFile("dist/THIRD_PARTY_NOTICES.txt", notices);
console.log(`Built self-contained board: ${Math.round(Buffer.byteLength(html) / 1024)} KiB; ${packages.size} license notices.`);
