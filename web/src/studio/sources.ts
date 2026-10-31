import { encodeSource, sourceText, MAX_TABLE_BYTES, MAX_SOURCE_BYTES } from "./source-text";
import { z } from "zod";
import Papa from "papaparse";
import { previewVnd } from "./money-preview";

export const SOURCE_DEFS = [
  {
    kind: "orders",
    name: "Orders",
    file: "don_hang_seller_center.csv",
    path: "live_11_11/don_hang_seller_center.csv",
    required: [
      "Mã đơn hàng",
      "SKU phân loại hàng",
      "Số lượng",
      "Ngày đặt hàng",
    ],
  },
  {
    kind: "ads",
    name: "Ad performance",
    file: "bao_cao_ads.csv",
    path: "live_11_11/bao_cao_ads.csv",
    required: ["Mã chiến dịch", "Chi phí", "Lượt click", "Thời gian"],
  },
  {
    kind: "inventory",
    name: "Inventory",
    file: "ton_kho_google_sheet.csv",
    path: "live_11_11/ton_kho_google_sheet.csv",
    required: ["Mã nội bộ", "Tồn thực tế", "Tên hàng"],
  },
  {
    kind: "cost",
    name: "Product costs",
    file: "gia_von_nhap_tay.csv",
    path: "gia_von_nhap_tay.csv",
    required: ["tên sp", "giá vốn"],
  },
  {
    kind: "campaigns",
    name: "Campaign structure",
    file: "cau_truc_chien_dich.json",
    path: "cau_truc_chien_dich.json",
    required: ["ma_chien_dich", "sku"],
  },
  {
    kind: "sale_program",
    name: "Sale rules",
    file: "chuong_trinh_sale.json",
    path: "chuong_trinh_sale.json",
    required: ["cam_ket", "khung_flash_sale"],
  },
] as const;
export type SourceKind = (typeof SOURCE_DEFS)[number]["kind"] | "general";
export type Issue = {
  level: "error" | "warning" | "info";
  code: string;
  row?: number;
  column?: string;
  message: string;
  value?: string;
  normalizedVnd?: number;
};
export type Source = {
  kind: SourceKind;
  name: string;
  text: string;
  encoding?: "gzip-base64";
  replay?: { date: string; hours: number[]; lagHours: number };
  hash: string;
  origin: string;
  commit?: string;
};
export type SourcePack = { schema: "hackathon.sources.v1"; files: Source[] };
export type ParsedSource = {
  columns: string[];
  rows: Record<string, unknown>[];
  issues: Issue[];
  payload?: unknown;
};
export const MAX_BYTES = 20 * 1024 * 1024;
export const normalize = (s: string) =>
  s
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/đ/g, "d")
    .replace(/Đ/g, "D")
    .trim()
    .toLowerCase();
export function piiColumn(name: string) {
  const key = normalize(name);
  if (/ghi chu|note/.test(key)) return false;
  return /^(ten )?(nguoi mua|nguoi nhan|khach hang)$|^ho( va)? ten|^ten (nguoi mua|nguoi nhan|khach hang|dang nhap)|(so )?dien thoai|^sdt|phone|dia chi|address|e-?mail|^(buyer|customer|recipient)( name)?$|username|tai khoan nguoi mua|cmnd|cccd|so can cuoc/.test(
    key,
  );
}
function mask(value: string) {
  return value
    .replace(/[\w.+-]+@[\w-]+(?:\.[\w-]+)+/g, "***")
    .replace(/(?<!\d)(?:\+?84|0)(?:[\s.-]?\d){8,10}(?!\d)/g, "***");
}
function cleanJSON(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(cleanJSON);
  if (value && typeof value === "object")
    return Object.fromEntries(
      Object.entries(value)
        .filter(([k]) => !piiColumn(k))
        .map(([k, v]) => [
          k,
          /note|ghi_chu/i.test(k) && typeof v === "string"
            ? mask(v)
            : cleanJSON(v),
        ]),
    );
  return value;
}
const keyHas = (keys: string[], expected: readonly string[]) =>
  expected.every((k) => keys.some((v) => normalize(v) === normalize(k)));
/** Transport inspection only. Domain normalization and SKU matching remain Python-owned. */
export function inspect(
  name: string,
  text: string,
): ParsedSource & { kind: SourceKind } {
  const buckets: Record<Issue["level"], Issue[]> = { error: [], warning: [], info: [] };
  let issueCount = 0;
  const addIssues = (...items: Issue[]) => {
    for (const issue of items) {
      issueCount++;
      if (buckets[issue.level].length < 500) buckets[issue.level].push(issue);
    }
  };
  let columns: string[],
    rows: Record<string, unknown>[],
    kind: SourceKind | undefined,
    payload: unknown;
  if (/\.json$/i.test(name)) {
    try {
      payload = cleanJSON(JSON.parse(text.replace(/^\uFEFF/, "")));
    } catch {
      throw Error(`${name}: invalid JSON syntax.`);
    }
    const first = Array.isArray(payload) ? payload[0] : payload;
    if (!first || typeof first !== "object" || Array.isArray(first))
      throw Error(`${name}: expected an object or an array of records.`);
    columns = [...new Set((Array.isArray(payload) ? payload : [first]).flatMap(row => row && typeof row === "object" ? Object.keys(row) : []))];
    kind = SOURCE_DEFS.find(
      (d) =>
        ["campaigns", "sale_program"].includes(d.kind) &&
        keyHas(columns, d.required),
    )?.kind;
    if (
      (kind === "campaigns" && !Array.isArray(payload)) ||
      (kind === "sale_program" && Array.isArray(payload))
    )
      throw Error(`${name}: incorrect JSON container.`);
    rows = Array.isArray(payload)
      ? (payload as Record<string, unknown>[])
      : [first as Record<string, unknown>];
    if (rows.some((r) => !r || typeof r !== "object" || Array.isArray(r)))
      throw Error(`${name}: each record must be an object.`);
  } else {
    const parsed = Papa.parse<Record<string, string>>(text, {
      header: true,
      skipEmptyLines: "greedy",
      transformHeader: (s) => s.replace(/^\uFEFF/, "").trim(),
    });
    if (parsed.errors.length) {
      const issue = parsed.errors[0]!;
      throw Error(
        `${name}: ${issue.code} at row ${(issue.row ?? 0) + 2}: ${issue.message}`,
      );
    }
    const original = parsed.meta.fields ?? [];
    columns = original.filter((k) => !piiColumn(k));
    const removed = original.filter(piiColumn);
    if (removed.length)
      addIssues({
        level: "info",
        code: "PII_DROPPED",
        message: `Removed contact columns: ${removed.join(", ")}.`,
      });
    kind = SOURCE_DEFS.find(
      (d) =>
        !["campaigns", "sale_program"].includes(d.kind) &&
        keyHas(columns, d.required),
    )?.kind;
    rows = parsed.data;
    for (const row of rows) {
      for (const column of removed) delete row[column];
      for (const column of columns)
        if (/ghi chu|note/.test(normalize(column))) row[column] = mask(String(row[column] ?? ""));
    }
    addIssues(
      ...parsed.errors
        .slice(0, 100)
        .map((e) => ({
          level: "error" as const,
          code: e.code,
          row: (e.row ?? 0) + 2,
          message: e.message,
        })),
    );
  }
  kind ??= "general";
  if (!columns.length || columns.length > 100 || columns.some(c => !c || c.length > 200))
    throw Error(`${name}: expected 1–100 named columns (at most 200 characters each).`);
  const definition = SOURCE_DEFS.find((d) => d.kind === kind);
  const seen = new Map<string, Record<string, unknown>[]>();
  rows.forEach((row, i) => {
    const rowNumber = i + (payload === undefined ? 2 : 1);
    for (const key of definition?.required ?? [])
      if (!(key in row))
        addIssues({
          level: "error",
          code: "MISSING_FIELD",
          row: rowNumber,
          column: key,
          message: "Required field is missing.",
        });
    for (const [column, value] of Object.entries(row)) {
      if (typeof value !== "string") continue;
      if (
        /ghi chu|note/.test(normalize(column)) &&
        /ignore.*instructions|system prompt|bo qua.*huong dan|tat (toan bo|het|tat ca)/.test(
          normalize(value),
        )
      )
        addIssues({
          level: "warning",
          code: "UNTRUSTED_NOTE",
          row: rowNumber,
          column,
          message: "Instruction-like note; treated only as untrusted data.",
        });
      if (column === "Tồn thực tế" && value !== "" && Number(value) < 0)
        addIssues({
          level: "error",
          code: "NEG_STOCK",
          row: rowNumber,
          column,
          message:
            "Negative stock. Review the source; do not treat as available inventory.",
        });
      if (
        ["giá vốn", "phí đóng gói", "Chi phí", "Tổng tiền hàng"].includes(
          column,
        )
      ) {
        if (!value.trim() && column === "giá vốn")
          addIssues({
            level: "error",
            code: "MISSING_COST",
            row: rowNumber,
            column,
            message: "Unknown cost; economics must wait for a value.",
          });
        else if (value.trim() && !/^\d+$/.test(value)) {
          const amount = previewVnd(value);
          const readable = amount !== null && amount >= 0;
          addIssues({
            level: readable ? "info" : "warning",
            code: readable ? "MONEY_NORMALIZATION" : "MONEY_FORMAT",
            row: rowNumber,
            column,
            value,
            normalizedVnd: amount ?? undefined,
            message: readable
              ? "Recognized VND format. Preview only; the source value is unchanged."
              : "This amount needs review before calculations; confirm the value and currency.",
          });
        }
      }
    }
    if (kind === "orders") {
      const key = JSON.stringify([row["Mã đơn hàng"], row["SKU phân loại hàng"]]);
      const previous = seen.get(key) ?? [];
      if (previous.some(prior => JSON.stringify(prior) === JSON.stringify(row)))
        addIssues({
          level: "warning",
          code: "DUP_ORDER",
          row: rowNumber,
          message: "Repeated order line; backend ingestion deduplicates.",
        });
      previous.push(row); seen.set(key, previous);
    }
    if (kind === "campaigns" && !Array.isArray(row.sku))
      addIssues({
        level: "error",
        code: "INVALID_SKUS",
        row: rowNumber,
        column: "sku",
        message: "Campaign sku must be an array.",
      });
  });
  if (
    kind === "ads" &&
    rows.some((row) => {
      const timestamp = String(row["Thời gian"] ?? "").trim();
      return !/(?:T|\s)\d{2}:\d{2}.*(?:Z|[+-]\d{2}:?\d{2})$/i.test(timestamp);
    })
  )
    addIssues({
      level: "info",
      code: "TIMEZONE_REVIEW",
      column: "Thời gian",
      message:
        "Original advertising clock times are preserved. Confirm a timezone only when comparing absolute times across exports.",
    });
  // Scan all records; repeated formatting notices must not hide a later error.
  const issues = [...buckets.error, ...buckets.warning, ...buckets.info].slice(0, 500);
  if (issueCount > issues.length)
    issues.push({
      level: "warning",
      code: "LOG_LIMIT",
      message: `Showing ${issues.length} of ${issueCount} checks, with errors and warnings first. Use the backend ingestion report for the full detail.`,
    });
  return { kind, columns, rows, issues, payload };
}
export async function hashText(text: string) {
  return Array.from(
    new Uint8Array(
      await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text)),
    ),
    (v) => v.toString(16).padStart(2, "0"),
  ).join("");
}
export async function makeSource(
  name: string,
  text: string,
  origin: string,
  commit?: string,
): Promise<Source> {
  const result = inspect(name, text);
  const sanitized =
    result.payload === undefined
      ? Papa.unparse({ fields: result.columns, data: result.rows })
      : JSON.stringify(result.payload, null, 2);
  return {
    kind: result.kind,
    name: SOURCE_DEFS.find((d) => d.kind === result.kind)?.file ?? name.replace(/[/\\]/g, "__"),
    ...encodeSource(sanitized),
    hash: await hashText(sanitized),
    origin: origin.slice(0, 300),
    commit,
  };
}
export async function validatePack(input: unknown): Promise<SourcePack> {
  if (
    !input ||
    typeof input !== "object" ||
    !("schema" in input) ||
    !["hackathon.sources.v1", "megaday.sources.v1"].includes(
      String(input.schema),
    ) ||
    !("files" in input) ||
    !Array.isArray(input.files) ||
    input.files.length > 24
  )
    throw Error("A data pack accepts up to 24 CSV/JSON tables.");
  let size = 0;
  const files: Source[] = [];
  for (const entry of input.files) {
    if (
      !entry ||
      typeof entry.name !== "string" ||
      typeof entry.text !== "string" ||
      !/\.(csv|json)$/i.test(entry.name) || entry.name.length > 1000 ||
      /[/\\]/.test(entry.name)
    )
      throw Error("Invalid source entry.");
    if (entry.encoding !== undefined && entry.encoding !== "gzip-base64") throw Error("Unsupported source encoding.");
    const text = sourceText(entry);
    const bytes = new TextEncoder().encode(text).length;
    size += bytes;
    if (bytes > MAX_TABLE_BYTES || size > MAX_SOURCE_BYTES) throw Error("Sources exceed 48 MiB, or one table exceeds 32 MiB.");
    const source = await makeSource(
        entry.name,
        text,
        typeof entry.origin === "string" ? entry.origin : "upload",
        typeof entry.commit === "string" && /^[a-f0-9]{40}$/.test(entry.commit)
          ? entry.commit
          : undefined,
      );
    if (entry.replay !== undefined) {
      const replay = z.object({ date: z.iso.date(), hours: z.array(z.number().int().min(-1).max(23)), lagHours: z.number().int().min(0).max(24) }).strict().parse(entry.replay);
      if (!["orders", "ads", "inventory"].includes(source.kind) || replay.hours.length !== inspect(source.name, text).rows.length) throw Error("Replay must match every business table row.");
      source.replay = replay;
    }
    files.push(source);
  }
  if (new Set(files.map(sourceKey)).size !== files.length)
    throw Error(
      "Duplicate table: choose one file per business source or general table name.",
    );
  return { schema: "hackathon.sources.v1", files };
}
export function selectUploads(
  files: { name: string; webkitRelativePath?: string }[],
) {
  const result: number[] = [];
  for (const def of SOURCE_DEFS) {
    let candidates = files
      .map((f, i) => ({ path: f.webkitRelativePath || f.name, i }))
      .filter(
        (f) =>
          f.path.split("/").pop() === def.file &&
          !/ground_truth|_replay|manifest/.test(f.path),
      );
    const root = candidates.filter(f => !f.path.includes("/live_11_11/"));
    if (root.length) candidates = root;
    if (candidates.length > 1)
      throw Error(`Multiple ${def.name} sources. Choose one shop folder.`);
    if (candidates[0]) result.push(candidates[0].i);
  }
  if (!result.length)
    throw Error(
      "No supported business files found in this folder.",
    );
  return result;
}

/** General tables are independent; business tables retain their existing canonical identity. */
export const sourceKey = (source: Source) => source.kind === "general" ? `general:${source.name}` : source.kind;
export function mergeSources(existing: Source[], incoming: Source[]) {
  const merged = new Map(existing.map(source => [sourceKey(source), source]));
  for (const source of incoming) merged.set(sourceKey(source), source);
  if (merged.size > 24) throw Error("A workspace accepts up to 24 tables.");
  return [...merged.values()];
}
export const DEMAND_COLUMN = "Demand label";
export async function withoutDemandLabels(pack: SourcePack): Promise<SourcePack> {
  return { ...pack, files: await Promise.all(pack.files.map(async source => {
    if (source.kind !== "inventory") return source;
    const parsed = inspect(source.name, sourceText(source));
    const columns = parsed.columns.filter(c => c !== DEMAND_COLUMN);
    const text = Papa.unparse({ fields: columns, data: parsed.rows });
    return { ...source, encoding: undefined, ...encodeSource(text), hash: await hashText(text) };
  })) };
}
