import fs from "node:fs/promises";
import path from "node:path";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const [inputPath, outputDir = "tmp/export-verification"] = process.argv.slice(2);
if (!inputPath) throw new Error("Usage: verify_export.mjs input.xlsx [output-dir]");

await fs.mkdir(outputDir, { recursive: true });
const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(inputPath));
const sheets = JSON.parse((await workbook.inspect({
  kind: "sheet",
  include: "id,name",
  maxChars: 5000,
})).ndjson.split("\n").filter(Boolean).at(-1) || "{}");
const table = await workbook.inspect({
  kind: "table",
  range: "回单结果!A1:P10",
  include: "values,formulas",
  tableMaxRows: 10,
  tableMaxCols: 16,
  maxChars: 12000,
});
const errors = await workbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
  options: { useRegex: true, maxResults: 100 },
  summary: "final formula error scan",
  maxChars: 5000,
});
const formats = await workbook.inspect({
  kind: "computedStyle",
  sheetId: "回单结果",
  range: "C2:P4",
  maxChars: 8000,
});

for (const [sheetName, range] of [
  ["回单结果", "A1:P10"],
  ["统计摘要", "A1:F14"],
]) {
  const preview = await workbook.render({
    sheetName,
    range,
    scale: 1,
    format: "png",
  });
  await fs.writeFile(
    path.join(outputDir, `${sheetName}.png`),
    new Uint8Array(await preview.arrayBuffer()),
  );
}

console.log(JSON.stringify({
  sheets,
  table: table.ndjson,
  formats: formats.ndjson,
  errors: errors.ndjson,
  previews: ["回单结果.png", "统计摘要.png"].map(name => path.resolve(outputDir, name)),
}, null, 2));
