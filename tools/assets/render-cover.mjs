import { readFile, writeFile } from "node:fs/promises";
import process from "node:process";

const [source, destination] = process.argv.slice(2);
if (!source || !destination || process.argv.length !== 4) {
  throw new Error("usage: node render-cover.mjs SOURCE.svg DESTINATION.png");
}

const { Resvg } = await import("@resvg/resvg-js");
const svg = await readFile(source);
const rendered = new Resvg(svg, {
  fitTo: { mode: "width", value: 1600 },
}).render();
if (rendered.width !== 1600 || rendered.height !== 900) {
  throw new Error(`unexpected rendered dimensions: ${rendered.width}x${rendered.height}`);
}
await writeFile(destination, rendered.asPng());
