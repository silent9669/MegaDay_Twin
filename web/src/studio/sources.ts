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
export type SourceKind = (typeof SOURCE_DEFS)[number]["kind"];
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
      throw Error(`${name}: expected a campaign array or sale-rules object.`);
    columns = Object.keys(first);
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
    rows = parsed.data.map((row) =>
      Object.fromEntries(
        columns.map((k) => [
          k,
          /ghi chu|note/.test(normalize(k))
            ? mask(row[k] ?? "")
            : (row[k] ?? ""),
        ]),
      ),
    );
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
  if (!kind)
    throw Error(
      `${name}: unknown source format. Required headers: ${SOURCE_DEFS.map((d) => `${d.name} (${d.required.join(", ")})`).join("; ")}.`,
    );
  const definition = SOURCE_DEFS.find((d) => d.kind === kind)!;
  const seen = new Set<string>();
  rows.forEach((row, i) => {
    const rowNumber = i + (payload === undefined ? 2 : 1);
    for (const key of definition.required)
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
      const key = JSON.stringify(row);
      if (seen.has(key))
        addIssues({
          level: "warning",
          code: "DUP_ORDER",
          row: rowNumber,
          message: "Repeated order line; backend ingestion deduplicates.",
        });
      seen.add(key);
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
      level: "warning",
      code: "TIMEZONE_REVIEW",
      column: "Thời gian",
      message:
        "Some ads timestamps have no explicit timezone. Confirm the export timezone before combining ads and orders; do not assume local time.",
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
    name: SOURCE_DEFS.find((d) => d.kind === result.kind)!.file,
    text: sanitized,
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
    input.files.length !== 6
  )
    throw Error("A data pack needs exactly the six CSV/JSON source files.");
  let size = 0;
  const files: Source[] = [];
  for (const entry of input.files) {
    if (
      !entry ||
      typeof entry.name !== "string" ||
      typeof entry.text !== "string" ||
      /[/\\]/.test(entry.name)
    )
      throw Error("Invalid source entry.");
    size += new TextEncoder().encode(entry.text).length;
    if (size > MAX_BYTES) throw Error("Sources exceed 20 MiB.");
    files.push(
      await makeSource(
        entry.name,
        entry.text,
        typeof entry.origin === "string" ? entry.origin : "upload",
        typeof entry.commit === "string" && /^[a-f0-9]{40}$/.test(entry.commit)
          ? entry.commit
          : undefined,
      ),
    );
  }
  if (new Set(files.map((f) => f.kind)).size !== 6)
    throw Error(
      "Duplicate source type: include one of each of the six sources.",
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
    const live = candidates.filter((f) => f.path.includes("/live_11_11/"));
    if (live.length) candidates = live;
    if (candidates.length > 1)
      throw Error(`Multiple ${def.name} sources. Choose one shop folder.`);
    if (candidates[0]) result.push(candidates[0].i);
  }
  if (result.length !== 6)
    throw Error(
      "Select the shop folder containing live_11_11 and the three parent reference files.",
    );
  return result;
}
