/**
 * Decode upstream Pixel Agents sprite/tile PNGs into the JSON the webview
 * bundle fetches at runtime. See pixelagents/infrastructure/webview_build.py,
 * which invokes this via `tsx` against a runtime clone of vendor/pixel-agents
 * (cloned into Red's per-cog data directory, not a path relative to this
 * file), so upstream's modules are resolved from an argv-supplied checkout
 * root through a dynamic import rather than a static relative import.
 *
 * The production bundle never decodes PNGs itself (`initBrowserMock()` is
 * DEV-gated in upstream's main.tsx), so the cog serves pre-decoded pixel
 * arrays instead. Rather than port upstream's decoders to Python, this
 * script runs upstream's own decoder functions and writes their output next
 * to the Vite build, mirroring what its dev-server middleware serves on
 * demand.
 *
 * Usage: tsx emit_decoded_assets.ts <vendorCheckoutRoot> <assetsDir> <outDir>
 */

import * as fs from "node:fs";
import * as path from "node:path";
import { pathToFileURL } from "node:url";

function moduleUrl(vendorRoot: string, relativeToVendorRoot: string): string {
  return pathToFileURL(path.join(vendorRoot, relativeToVendorRoot)).href;
}

// Wrapped in an async function rather than using top-level await: this repo
// has no package.json to declare `"type": "module"`, and without one both
// Node and tsx's underlying esbuild transform default an extensionless
// `.ts` file to CJS output, where top-level await is a syntax error.
async function main(): Promise<void> {
  const [, , vendorRootArg, assetsDirArg, outDirArg] = process.argv;
  if (!vendorRootArg || !assetsDirArg || !outDirArg) {
    console.error("usage: emit_decoded_assets.ts <vendorCheckoutRoot> <assetsDir> <outDir>");
    process.exit(1);
  }

  const vendorRoot = path.resolve(vendorRootArg);
  const assetsDir = path.resolve(assetsDirArg);
  const outDir = path.resolve(outDirArg);

  const { buildFurnitureCatalog } = await import(
    moduleUrl(vendorRoot, "core/src/assets/build.ts")
  );
  const {
    decodeAllCarpets,
    decodeAllCharacters,
    decodeAllFloors,
    decodeAllFurniture,
    decodeAllWalls,
  } = await import(moduleUrl(vendorRoot, "core/src/assets/loader.ts"));

  fs.mkdirSync(outDir, { recursive: true });

  const catalog = buildFurnitureCatalog(assetsDir);
  const decoded: Record<string, unknown> = {
    "characters.json": decodeAllCharacters(assetsDir),
    "floors.json": decodeAllFloors(assetsDir),
    "walls.json": decodeAllWalls(assetsDir),
    "carpets.json": decodeAllCarpets(assetsDir),
    "furniture.json": decodeAllFurniture(assetsDir, catalog),
  };

  for (const [filename, data] of Object.entries(decoded)) {
    fs.writeFileSync(path.join(outDir, filename), JSON.stringify(data));
    console.log(`wrote ${path.join(outDir, filename)}`);
  }
}

main().catch((err: unknown) => {
  console.error(err);
  process.exit(1);
});
