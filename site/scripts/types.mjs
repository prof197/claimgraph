// Generates src/types/export.ts from the export's JSON Schema; --check fails if it is stale.
import { readFile, writeFile } from "node:fs/promises";
import { compile } from "json-schema-to-typescript";

const schemaPath = new URL("../../schema/export.schema.json", import.meta.url);
const target = new URL("../src/types/export.ts", import.meta.url);
const banner =
  "// Generated from schema/export.schema.json by `npm run types`; do not edit by hand.";

// Pydantic titles every field; only the models' own titles should name a type.
function untitleFields(node) {
  if (Array.isArray(node)) {
    node.forEach(untitleFields);
  } else if (node && typeof node === "object") {
    for (const [key, value] of Object.entries(node)) {
      if (key === "properties") {
        for (const field of Object.values(value)) {
          delete field.title;
          untitleFields(field);
        }
      } else {
        untitleFields(value);
      }
    }
  }
}

const schema = JSON.parse(await readFile(schemaPath, "utf8"));
untitleFields(schema);
const generated = await compile(schema, "Export", {
  bannerComment: banner,
  additionalProperties: false,
  style: { printWidth: 100 },
});

if (process.argv.includes("--check")) {
  const current = await readFile(target, "utf8").catch(() => "");
  if (current !== generated) {
    console.error("src/types/export.ts is out of date with the schema; run npm run types");
    process.exit(1);
  }
} else {
  await writeFile(target, generated);
}
