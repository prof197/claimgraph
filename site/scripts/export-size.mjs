// Measures the page's export as a reader downloads it, and fails when its gzip size is over the
// budget. GitHub Pages serves JSON gzip-compressed, so gzip is what a reader's phone transfers;
// brotli is reported for comparison. Usage: node scripts/export-size.mjs [path/to/export.json]
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { brotliCompressSync, constants, gzipSync } from "node:zlib";

// Lighthouse's mobile throttling, "slow 4G": 1,638.4 kbit/s down with a 150 ms round trip.
const SLOW_4G_BYTES_PER_SECOND = (1638.4 * 1000) / 8;

// 1 MB compressed: about 5 s on slow 4G and about 1 s on typical 4G. The page draws nothing
// until the export has loaded, so this is the wait before the first chart; past that a reader on
// a phone is likely to give up. The export then parses on the phone at its raw size, which runs
// about fourteen times larger.
const BUDGET_GZIP_BYTES = 1_000_000;

function megabytes(bytes) {
  return bytes < 100_000 ? `${(bytes / 1000).toFixed(1)} kB` : `${(bytes / 1_000_000).toFixed(2)} MB`;
}

function measure(buffer) {
  return {
    raw: buffer.length,
    // Level 6 is zlib's default, and what a server compressing on the fly commonly uses.
    gzip: gzipSync(buffer, { level: 6 }).length,
    brotli: brotliCompressSync(buffer, {
      params: { [constants.BROTLI_PARAM_QUALITY]: 11, [constants.BROTLI_PARAM_SIZE_HINT]: buffer.length },
    }).length,
  };
}

const path = process.argv[2] ?? fileURLToPath(new URL("../public/export.json", import.meta.url));
let buffer;
try {
  buffer = await readFile(path);
} catch (error) {
  console.error(`no export to measure at ${path}: ${error.code ?? error}`);
  process.exit(1);
}
const size = measure(buffer);
const used = Math.round((100 * size.gzip) / BUDGET_GZIP_BYTES);
const seconds = (size.gzip / SLOW_4G_BYTES_PER_SECOND).toFixed(1);
console.log(
  `${path}: ${megabytes(size.raw)} raw, ${megabytes(size.gzip)} gzip, ${megabytes(size.brotli)} brotli; ` +
    `${used}% of the ${megabytes(BUDGET_GZIP_BYTES)} gzip budget, about ${seconds} s on slow 4G`,
);
if (size.gzip > BUDGET_GZIP_BYTES) {
  console.error(
    `the export is ${megabytes(size.gzip - BUDGET_GZIP_BYTES)} over its gzip budget; ` +
      "trim what it carries before deploying it",
  );
  process.exit(1);
}
