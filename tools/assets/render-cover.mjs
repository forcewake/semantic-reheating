import { readFile, writeFile } from "node:fs/promises";
import process from "node:process";
import { fileURLToPath } from "node:url";

const [source, destination] = process.argv.slice(2);
if (!source || !destination || process.argv.length !== 4) {
  throw new Error("usage: node render-cover.mjs SOURCE.svg DESTINATION.png");
}

const { Resvg } = await import("@resvg/resvg-js");
const svg = await readFile(source);
const fontNames = ["DejaVuSans.ttf", "DejaVuSans-Bold.ttf"];
const fontFiles = fontNames.map((name) =>
  fileURLToPath(new URL(`./fonts/${name}`, import.meta.url)),
);
const rendered = new Resvg(svg, {
  fitTo: { mode: "width", value: 1600 },
  font: {
    defaultFontFamily: "DejaVu Sans",
    fontFiles,
    loadSystemFonts: false,
  },
}).render();
if (rendered.width !== 1600 || rendered.height !== 900) {
  throw new Error(`unexpected rendered dimensions: ${rendered.width}x${rendered.height}`);
}
await writeFile(destination, rendered.asPng());
