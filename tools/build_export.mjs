import fs from "node:fs/promises";
import path from "node:path";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const [inputPath, outputPath] = process.argv.slice(2);
if (!inputPath || !outputPath) throw new Error("Usage: build_export.mjs input.json output.xlsx");
const payload = JSON.parse(await fs.readFile(inputPath, "utf8"));
const results = payload.results || [];

const workbook = Workbook.create();
const resultsSheet = workbook.worksheets.add("回单结果");
const summarySheet = workbook.worksheets.add("统计摘要");
resultsSheet.showGridLines = false;
summarySheet.showGridLines = false;

const headers = [
  "文件名", "文档类型", "运单号", "客户订单号", "客户名称", "要求到货日期", "实际收货日期",
  "日期核验结论", "签章要求", "识别印章内容", "印章相似度", "印章核验结论",
  "整体结论", "复核状态", "人工备注", "处理时间"
];

function dateValue(value) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value || "")) return null;
  const parsed = new Date(`${value}T00:00:00`);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

function dateTimeValue(value) {
  const match = String(value || "").match(/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})/);
  if (match) {
    // artifact-tool writes the UTC components into the Excel serial. Build a UTC
    // date from the displayed local wall-clock fields so +08:00 is not shifted.
    return new Date(Date.UTC(+match[1], +match[2] - 1, +match[3], +match[4], +match[5], +match[6]));
  }
  const parsed = new Date(value || "");
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

const rows = results.map((item) => {
  const fields = item.fields || {};
  const date = item.date_check || {};
  const seal = item.seal_check || {};
  return [
    item.filename || "",
    item.document_type?.label || "旧记录未分类",
    String(fields["运单号"] || ""),
    String(fields["客户订单号"] || ""),
    fields["客户名称"] || "",
    dateValue(fields["要求到货"] || date.required),
    dateValue(date.actual),
    date.status || "",
    fields["签章要求"] || seal.requirement || "",
    seal.recognized || "",
    Number(seal.score || 0),
    seal.status || "",
    item.final_result || item.overall || "",
    item.review_status || "待复核",
    item.human_note || "",
    dateTimeValue(item.updated_at || item.created_at),
  ];
});

resultsSheet.getRange("A1:P1").values = [headers];
if (rows.length) resultsSheet.getRangeByIndexes(1, 0, rows.length, headers.length).values = rows;
const lastRow = Math.max(2, rows.length + 1);
const used = resultsSheet.getRange(`A1:P${lastRow}`);
used.format = {
  font: { name: "Microsoft YaHei", size: 10, color: "#1F2933" },
  verticalAlignment: "center",
};
resultsSheet.getRange("A1:P1").format = {
  fill: "#173F4F",
  font: { name: "Microsoft YaHei", bold: true, color: "#FFFFFF", size: 10 },
  horizontalAlignment: "center",
  verticalAlignment: "center",
  rowHeight: 30,
  wrapText: true,
  borders: { preset: "outside", style: "thin", color: "#173F4F" },
};
if (rows.length) {
  resultsSheet.getRange(`A2:P${lastRow}`).format.borders = {
    insideHorizontal: { style: "thin", color: "#E4E8E5" },
  };
  resultsSheet.getRange(`A2:E${lastRow}`).format.numberFormat = "@";
  resultsSheet.getRange(`F2:G${lastRow}`).format.numberFormat = "yyyy-mm-dd";
  resultsSheet.getRange(`K2:K${lastRow}`).format.numberFormat = "0.0%";
  resultsSheet.getRange(`P2:P${lastRow}`).format.numberFormat = "yyyy-mm-dd hh:mm:ss";
  resultsSheet.getRange(`A2:P${lastRow}`).format.rowHeight = 30;
  resultsSheet.getRange(`E2:E${lastRow}`).format.wrapText = true;
  resultsSheet.getRange(`I2:J${lastRow}`).format.wrapText = true;
  resultsSheet.getRange(`O2:O${lastRow}`).format.wrapText = true;
  // Long seal OCR evidence must remain inspectable in the exported workbook.
  // Estimate the required wrapped lines from the three narrative columns and
  // cap the height so noisy OCR cannot make one row dominate the sheet.
  rows.forEach((row, index) => {
    const narrativeLength = Math.max(
      String(row[8] || "").length,
      String(row[9] || "").length,
      String(row[14] || "").length,
    );
    const wrappedLines = Math.max(1, Math.ceil(narrativeLength / 34));
    resultsSheet.getRangeByIndexes(index + 1, 0, 1, headers.length).format.rowHeight =
      Math.min(90, Math.max(30, wrappedLines * 18));
  });
  resultsSheet.getRange(`M2:M${lastRow}`).conditionalFormats.add("containsText", {
    text: "不通过", format: { fill: "#FCE8E6", font: { color: "#A93226", bold: true } }
  });
  resultsSheet.getRange(`M2:M${lastRow}`).conditionalFormats.add("containsText", {
    text: "通过", format: { fill: "#E5F3EB", font: { color: "#216E51", bold: true } }
  });
  resultsSheet.getRange(`N2:N${lastRow}`).conditionalFormats.add("containsText", {
    text: "待复核", format: { fill: "#FFF2CC", font: { color: "#8A5A00", bold: true } }
  });
}
const widths = [20, 18, 21, 19, 32, 15, 15, 15, 42, 42, 13, 15, 14, 15, 32, 25];
widths.forEach((width, index) => resultsSheet.getRangeByIndexes(0, index, lastRow, 1).format.columnWidth = width);
resultsSheet.freezePanes.freezeRows(1);
resultsSheet.freezePanes.freezeColumns(1);
if (rows.length) {
  const table = resultsSheet.tables.add(`A1:P${lastRow}`, true, "ReceiptResultsTable");
  table.style = "TableStyleMedium2";
  table.showFilterButton = true;
}

summarySheet.getRange("A1:F1").merge();
summarySheet.getRange("A1").values = [["三星回单识别与复核统计"]];
summarySheet.getRange("A1:F1").format = {
  fill: "#173F4F", font: { name: "Microsoft YaHei", bold: true, color: "#FFFFFF", size: 18 },
  rowHeight: 42, verticalAlignment: "center", horizontalAlignment: "left",
};
summarySheet.getRange("A3:B9").values = [
  ["指标", "数值"], ["文档总数", null], ["最终通过", null], ["最终不通过", null],
  ["待人工复核", null], ["已确认通过", null], ["已确认不通过", null],
];
const resultEnd = Math.max(2, lastRow);
summarySheet.getRange("B4:B9").formulas = [
  [`=COUNTA('回单结果'!$A$2:$A$${resultEnd})`],
  [`=COUNTIF('回单结果'!$M$2:$M$${resultEnd},"通过")`],
  [`=COUNTIF('回单结果'!$M$2:$M$${resultEnd},"不通过")`],
  [`=COUNTIF('回单结果'!$N$2:$N$${resultEnd},"待复核")`],
  [`=COUNTIF('回单结果'!$N$2:$N$${resultEnd},"确认通过")`],
  [`=COUNTIF('回单结果'!$N$2:$N$${resultEnd},"确认不通过")`],
];
const accuracy = payload.report?.accuracy || {};
summarySheet.getRange("D3:E12").values = [
  ["样单准确率", "数值"],
  ["表单字段准确率", Number(accuracy.field_accuracy || 0)],
  ["日期识别准确率", Number(accuracy.date_accuracy || 0)],
  ["日期检出率", Number(accuracy.date_detection_rate || 0)],
  ["日期自动决策覆盖率", Number(accuracy.date_decision_coverage || 0)],
  ["日期自动决策准确率", Number(accuracy.date_decision_accuracy || 0)],
  ["印章结论准确率", Number(accuracy.seal_conclusion_accuracy || 0)],
  ["印章自动决策覆盖率", Number(accuracy.seal_decision_coverage || 0)],
  ["印章自动决策准确率", Number(accuracy.seal_decision_accuracy || 0)],
  ["印章平均相似度", Number(accuracy.seal_average_similarity || 0)],
];
summarySheet.getRange("E4:E12").format.numberFormat = "0.0%";
const summaryHeaderFormat = {
  fill: "#E9EFEC", font: { name: "Microsoft YaHei", bold: true, color: "#173F4F" },
  borders: { preset: "outside", style: "thin", color: "#CBD5D0" }, rowHeight: 28,
};
summarySheet.getRange("A3:B3").format = summaryHeaderFormat;
summarySheet.getRange("D3:E3").format = summaryHeaderFormat;
summarySheet.getRange("A4:B9").format.borders = { insideHorizontal: { style: "thin", color: "#E4E8E5" } };
summarySheet.getRange("D4:E12").format.borders = { insideHorizontal: { style: "thin", color: "#E4E8E5" } };
summarySheet.getRange("A14:F14").merge();
summarySheet.getRange("A14").values = [["说明：订单号以文本保存，日期以真正日期类型保存；黄色为待复核，红色为不通过。自动决策指标只统计系统可靠判定的样本。"]];
summarySheet.getRange("A14:F14").format = { fill: "#FFF8E1", font: { name: "Microsoft YaHei", color: "#765600" }, wrapText: true, rowHeight: 42 };
summarySheet.getRange("A1:A14").format.columnWidth = 25;
summarySheet.getRange("B1:B14").format.columnWidth = 16;
summarySheet.getRange("C1:C14").format.columnWidth = 6;
summarySheet.getRange("D1:D14").format.columnWidth = 25;
summarySheet.getRange("E1:E14").format.columnWidth = 16;
summarySheet.getRange("F1:F14").format.columnWidth = 12;
summarySheet.freezePanes.freezeRows(1);

await fs.mkdir(path.dirname(outputPath), { recursive: true });
const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(outputPath);
