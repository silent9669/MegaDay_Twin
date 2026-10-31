import { useEffect, useRef, useState } from "react";
import { openMilo } from "../city/milo-contract";
import { Link } from "react-router";
import { ArrowRight, Check, RefreshCw, Target } from "lucide-react";
import type { Dist, ScenarioRun } from "../api/types";
import { viText } from "../lib/i18n";
import { sameAction, violationLabel } from "../pages/compare/compareLogic";
import { goalInterpretResponseSchema, type GoalDraft, type GoalKind, type GoalInterpretResponse } from "./goal-contract";
import type { PlannerContext, PlannerRunError, PlannerRunRequest, PlannerRunResponse } from "./planner-api";
import { ActionCalculation, CalculationEvidence } from "./CalculationEvidence";
import "./planner.css";

const kinds: Record<GoalKind, string> = {
  profit: "Tối ưu lợi nhuận", sell_target: "Đạt số lượng bán",
  clear_stock: "Ưu tiên xả hàng", efficiency: "Tối ưu hiệu quả chi",
};
const templates = [
  ["Lợi nhuận", "Tôi muốn tối ưu lợi nhuận của chiến dịch trong buổi sale."],
  ["Số lượng bán", "Tôi muốn bán ít nhất 100 sản phẩm, ngân sách quảng cáo tối đa 2 triệu đồng."],
  ["Xả hàng", "Tôi muốn xả hàng tồn, chấp nhận lỗ kỳ vọng tối đa 500 nghìn đồng."],
  ["Hiệu quả chi", "Tôi muốn tối ưu hiệu quả chi quảng cáo của chiến dịch."],
] as const;
const number = (value: number) => new Intl.NumberFormat("vi-VN", { maximumFractionDigits: 1 }).format(value);
const money = (value: number) => `${number(value)} ₫`;
const dateText = (value?: string | null) => value
  ? new Date(value).toLocaleString("vi-VN", { timeZone: "Asia/Ho_Chi_Minh" }) : "Chưa có";
// The planning clock is always Vietnam time, regardless of the browser timezone.
function localDate(value: string | null) {
  if (!value || !Number.isFinite(Date.parse(value))) return "";
  return new Date(Date.parse(value) + 7 * 3_600_000).toISOString().slice(0, 16);
}
const isoDate = (value: string) => value ? `${value}:00+07:00` : null;
function blankDraft(text: string): GoalDraft {
  return { text, kind: null, targetUnits: null, start: null, deadline: null,
    maxSpendVnd: null, maxLossVnd: null, sellTargetProbability: null,
    minMarginalReturn: null, unresolved: [] };
}
class PlannerError extends Error {
  constructor(message: string, readonly payload?: Partial<PlannerRunError>) { super(message); }
}
async function request<T>(path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
  // Intentionally bypasses the fixture-aware API client: results must be from the backend.
  const response = await fetch(`/api/sale-planner/${path}`, {
    method: body === undefined ? "GET" : "POST", cache: "no-store", signal,
    ...(body === undefined ? {} : { headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }),
  });
  const payload = await response.json().catch(() => null);
  if (!response.ok) throw new PlannerError(payload?.error?.message || `Không thể hoàn thành yêu cầu (${response.status}).`, payload ?? undefined);
  if (!payload) throw new PlannerError("Máy chủ trả về dữ liệu không hợp lệ.");
  return payload as T;
}
function draftIssues(draft: GoalDraft): string[] {
  const issues: string[] = [];
  if (!draft.text.trim()) issues.push("Nhập mô tả mục tiêu trước khi xác nhận.");
  if (!draft.kind) issues.push("Chọn mục tiêu chính cho chiến dịch.");
  if (draft.kind === "sell_target" && (!draft.targetUnits || !Number.isSafeInteger(draft.targetUnits) || draft.targetUnits < 1))
    issues.push("Số lượng mục tiêu phải là số nguyên dương.");
  if (draft.kind !== "sell_target" && (draft.targetUnits !== null || draft.sellTargetProbability !== null))
    issues.push("Số lượng và xác suất đạt chỉ áp dụng cho mục tiêu Đạt số lượng bán. Chọn lại mục tiêu hoặc xóa hai thông số này.");
  if (draft.kind !== "efficiency" && draft.minMarginalReturn !== null)
    issues.push("Hiệu quả biên chỉ áp dụng cho mục tiêu Tối ưu hiệu quả chi. Chọn lại mục tiêu hoặc xóa thông số này.");
  const start = Date.parse(draft.start ?? ""), end = Date.parse(draft.deadline ?? "");
  if (!Number.isFinite(start) || !Number.isFinite(end)) issues.push("Chọn thời điểm bắt đầu và kết thúc.");
  else if (start % 3_600_000 || end % 3_600_000 || end <= start || end - start > 72 * 3_600_000)
    issues.push("Chọn giờ tròn, thời lượng từ 1 đến 72 giờ.");
  if ([draft.maxSpendVnd, draft.maxLossVnd, draft.minMarginalReturn].some((v) => v !== null && (!Number.isFinite(v) || v < 0)))
    issues.push("Ngân sách, mức lỗ và hiệu quả biên phải lớn hơn hoặc bằng 0.");
  if (draft.sellTargetProbability !== null && (!Number.isFinite(draft.sellTargetProbability) || draft.sellTargetProbability <= 0 || draft.sellTargetProbability > 1))
    issues.push("Xác suất đạt mục tiêu phải lớn hơn 0% và không vượt quá 100%.");
  return issues;
}

type PlannerProps = {
  initialInterpretation?: GoalInterpretResponse;
  focusSku?: string;
  campaignIds?: string[];
  embedded?: boolean;
  allowRun?: boolean;
};
// The assistant remounts this review on a new interpretation or SKU, clearing approval/results.
export function SaleGoalPlanner({ initialInterpretation, focusSku, campaignIds, embedded = false, allowRun = true }: PlannerProps = {}) {
  const [context, setContext] = useState<PlannerContext | null>(null);
  const [contextError, setContextError] = useState("");
  const [reload, setReload] = useState(0);
  const [loading, setLoading] = useState(true);
  const [text, setText] = useState(initialInterpretation?.draft.text ?? "");
  const [draft, setDraft] = useState<GoalDraft | null>(initialInterpretation?.draft ?? null);
  const [method, setMethod] = useState<"ai" | "rules" | "manual">(initialInterpretation?.method ?? "manual");
  const [notices, setNotices] = useState<string[]>(initialInterpretation?.notices ?? []);
  const [campaignId, setCampaignId] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [acknowledged, setAcknowledged] = useState(false);
  const [busy, setBusy] = useState<"interpret" | "run" | null>(null);
  const [error, setError] = useState("");
  const [errorDetails, setErrorDetails] = useState<Record<string, unknown> | null>(null);
  const [goalId, setGoalId] = useState<string | null>(null);
  const [result, setResult] = useState<PlannerRunResponse | null>(null);
  const review = useRef<HTMLElement>(null), results = useRef<HTMLElement>(null);
  const activeRequest = useRef<AbortController | null>(null);
  const campaignScope = campaignIds?.join("\u0000");
  useEffect(() => () => activeRequest.current?.abort(), []);
  useEffect(() => {
    const controller = new AbortController();
    void request<PlannerContext>("context", undefined, controller.signal).then((value) => {
      if (!controller.signal.aborted) {
        const ids = campaignScope?.split("\u0000");
        const scoped = ids ? { ...value, campaigns: value.campaigns.filter((campaign) => ids.includes(campaign.id)) } : value;
        setContext(scoped); setContextError("");
        if (focusSku && scoped.campaigns.length === 1) setCampaignId(scoped.campaigns[0]!.id);
      }
    }).catch((e: unknown) => {
      if (!controller.signal.aborted) { setContext(null); setContextError(e instanceof Error ? e.message : "Không tải được chiến dịch."); }
    }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [reload, campaignScope, focusSku]);
  function invalidate() {
    setConfirmed(false); setGoalId(null); setResult(null); setError(""); setErrorDetails(null);
  }
  function changeText(value: string) {
    invalidate(); setAcknowledged(false); setText(value); setDraft(null); setNotices([]);
  }
  function edit(patch: Partial<GoalDraft>) {
    invalidate(); setAcknowledged(false); setDraft((old) => old ? { ...old, ...patch } : old);
  }
  function openReview() { requestAnimationFrame(() => review.current?.focus()); }
  async function interpret() {
    invalidate(); setAcknowledged(false); setBusy("interpret"); setDraft(null);
    const controller = new AbortController(); activeRequest.current = controller;
    try {
      const interpreted = goalInterpretResponseSchema.parse(await request("interpret", { text }, controller.signal));
      if (controller.signal.aborted) return;
      setDraft({ ...interpreted.draft, text }); setMethod(interpreted.method); setNotices(interpreted.notices); openReview();
    } catch (e) { if (!controller.signal.aborted) setError(e instanceof Error ? e.message : "Chưa phân tích được mục tiêu. Bạn có thể nhập thủ công."); }
    finally { if (!controller.signal.aborted) setBusy(null); }
  }
  const issues = draft ? draftIssues(draft) : [];
  const selected = context?.campaigns.find((c) => c.id === campaignId);
  const canConfirm = !!draft && !issues.length && (!draft.unresolved.length || acknowledged);
  const canRun = allowRun && canConfirm && confirmed && !!selected && !!context?.backendConfigured && context.canCreateGoals && !busy && !loading;
  async function run() {
    if (!canRun || !draft) return;
    setBusy("run"); setError(""); setErrorDetails(null);
    const controller = new AbortController(); activeRequest.current = controller;
    try {
      const body: PlannerRunRequest = { campaignId, draft, confirmed: true, acknowledgedUnresolved: acknowledged, ...(goalId ? { goalId } : {}), ...(focusSku ? { focusSku } : {}) };
      const response = await request<PlannerRunResponse>("run", body, controller.signal);
      if (controller.signal.aborted) return;
      setGoalId(response.goalId); setResult(response);
      requestAnimationFrame(() => results.current?.focus());
    } catch (e) {
      if (controller.signal.aborted) return;
      if (e instanceof PlannerError) {
        if (e.payload?.goalId) setGoalId(e.payload.goalId);
        setErrorDetails(e.payload?.error?.details ?? null);
      }
      setError(e instanceof Error ? e.message : "Mô phỏng chưa hoàn thành.");
    } finally { if (!controller.signal.aborted) setBusy(null); }
  }
  return (
    <div className={`sale-planner ${embedded ? "planner-embedded" : ""}`}>
      {!embedded && <>
      <header className="planner-heading">
        <div><span className="planner-eyebrow">MEGADAY HQ / LẬP PHƯƠNG ÁN</span><h1><Target aria-hidden size={26} /> Mục tiêu buổi sale</h1>
          <p>Nói điều bạn muốn đạt. Kiểm tra mục tiêu, rồi so sánh phương án dựa trên dữ liệu của chiến dịch.</p></div>
        <Link to="/mock-data">Kiểm tra dữ liệu <ArrowRight aria-hidden size={16} /></Link>
      </header>
      <ol className="planner-steps" aria-label="Các bước lập phương án">
        <li aria-current={!draft ? "step" : undefined}><span>1</span> Nhập mục tiêu</li>
        <li aria-current={draft && !result ? "step" : undefined}><span>2</span> Kiểm tra & xác nhận</li>
        <li aria-current={result ? "step" : undefined}><span>3</span> So sánh phương án</li>
      </ol>
      </>}
      <section className="planner-context" aria-label="Kết nối dữ liệu">
        <div role="status"><strong>{loading ? "Đang kiểm tra dữ liệu…" : context?.backendConfigured ? "Chiến dịch từ backend" : "Chưa kết nối backend tính toán"}</strong>
          <p>{contextError || context?.notice || (context?.backendConfigured ? `Dữ liệu đến: ${dateText(context.dataAsOf)} (giờ Việt Nam).` : "Bạn vẫn có thể soạn và kiểm tra mục tiêu. Cần kết nối backend để chạy mô phỏng.")}</p>
          {context?.backendConfigured && !context.canCreateGoals && <p>Tài khoản hiện tại chưa có quyền tạo mục tiêu và chạy mô phỏng.</p>}
          {context?.backendConfigured && !context.campaigns.length && <p>Chưa có chiến dịch. Import và gửi dữ liệu từ Data Studio sang backend trước.</p>}</div>
        <button type="button" disabled={loading || !!busy} onClick={() => { invalidate(); setLoading(true); setReload((n) => n + 1); }}><RefreshCw aria-hidden size={16} /> Kiểm tra lại</button>
      </section>
      <div className={`planner-layout ${embedded ? "planner-review-only" : ""}`}>
        {!embedded &&
        <section className="planner-card" aria-labelledby="planner-input-title">
          <span className="planner-eyebrow">BƯỚC 1</span><h2 id="planner-input-title">Buổi sale này, bạn muốn đạt điều gì?</h2>
          <fieldset disabled={!!busy}>
            <label htmlFor="sale-goal-text">Mô tả mục tiêu</label>
            <textarea id="sale-goal-text" rows={5} maxLength={4000} value={text} onChange={(e) => changeText(e.target.value)} placeholder="Ví dụ: Bán 100 sản phẩm trong 12 giờ, ngân sách quảng cáo tối đa 2 triệu đồng…" aria-describedby="sale-goal-text-hint" />
            <p id="sale-goal-text-hint" className="planner-help">Ghi rõ ưu tiên, thời gian, ngân sách và mức rủi ro bạn chấp nhận. {text.length}/4.000 ký tự.</p>
            <div className="planner-templates" aria-label="Mẫu mục tiêu">{templates.map(([label, value]) => <button type="button" key={label} onClick={() => changeText(value)}>{label}</button>)}</div>
            <div className="planner-actions"><button className="planner-primary" type="button" disabled={!text.trim()} onClick={() => void interpret()}>{busy === "interpret" ? "Đang phân tích…" : "Phân tích mục tiêu"}<ArrowRight aria-hidden size={16} /></button>
              <button type="button" disabled={!text.trim()} onClick={() => { invalidate(); setAcknowledged(false); setDraft(blankDraft(text)); setMethod("manual"); setNotices([]); openReview(); }}>Nhập thông số thủ công</button></div>
          </fieldset>
          <aside className="planner-scope"><strong>Phạm vi của lần lập phương án</strong><p>Một chiến dịch, trong 1–72 giờ. So sánh tiếp tục, tạm dừng, giảm ngân sách và lịch chạy quảng cáo theo bộ mô phỏng hiện có.</p><p>Chưa tối ưu ngân sách chung giữa nhiều chiến dịch; chưa tự thay đổi giá bán, voucher hoặc chạy quảng cáo.</p></aside>
        </section>}
        <section ref={review} tabIndex={-1} className="planner-card planner-review" aria-labelledby="planner-review-title">
          <span className="planner-eyebrow">BƯỚC 2</span><h2 id="planner-review-title">Kiểm tra cách hệ thống hiểu</h2>
          {!draft ? <div className="planner-empty"><Target aria-hidden size={30} /><p>Thông số sẽ xuất hiện ở đây sau khi phân tích. Bạn có thể chỉnh từng giá trị trước khi xác nhận.</p></div> : <>
            <p className="planner-help">{method === "ai" ? "Bản nháp do AI diễn giải" : method === "rules" ? "Bản nháp theo quy tắc nhận diện" : "Bản nháp nhập thủ công"}. Các thông số bên dưới là nội dung được dùng để tính.</p>
            {embedded && <details><summary>Nội dung mục tiêu đã diễn giải</summary><pre>{draft.text}</pre></details>}
            {!!notices.length && <ul className="planner-notices">{notices.map((notice, i) => <li key={i}>{notice}</li>)}</ul>}
            <fieldset disabled={!!busy}>
              {focusSku && <div className="planner-warning"><strong>Mục tiêu gắn với SKU {focusSku}</strong><p>Số lượng bán/xả hàng được tính riêng cho SKU này. Lợi nhuận J, ngân sách, mức lỗ và hiệu quả chi được tính cho toàn bộ chiến dịch đã chọn; thay đổi quảng cáo có thể ảnh hưởng các SKU khác.</p><p>Xác nhận bên dưới đồng nghĩa bạn đã kiểm tra phạm vi này. Đây chưa phải phép tính lợi nhuận riêng cho SKU.</p></div>}
              <label htmlFor="sale-campaign">Chiến dịch</label><select id="sale-campaign" value={campaignId} onChange={(e) => { invalidate(); setCampaignId(e.target.value); }}><option value="">Chọn chiến dịch từ backend</option>{context?.campaigns.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}</select>
              <label htmlFor="sale-kind">Mục tiêu chính</label><select id="sale-kind" value={draft.kind ?? ""} onChange={(e) => edit({ kind: (e.target.value || null) as GoalKind | null })}><option value="">Chọn mục tiêu</option>{Object.entries(kinds).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select>
              <div className="planner-fields">
                <label>Bắt đầu (giờ Việt Nam)<input type="datetime-local" step={3600} value={localDate(draft.start)} onChange={(e) => edit({ start: isoDate(e.target.value) })} /></label>
                <label>Kết thúc (giờ Việt Nam)<input type="datetime-local" step={3600} value={localDate(draft.deadline)} onChange={(e) => edit({ deadline: isoDate(e.target.value) })} /></label>
              </div>
              <p className="planner-help">Chọn giờ tròn, thời lượng tối đa 72 giờ. Hãy đối chiếu thời gian với dữ liệu đã import.</p>
              <div className="planner-fields"><label>Số sản phẩm mục tiêu<input type="number" min={1} step={1} value={draft.targetUnits ?? ""} onChange={(e) => edit({ targetUnits: e.target.value ? Number(e.target.value) : null })} /></label><label>Xác suất đạt tối thiểu (%)<input type="number" min={0.01} max={100} step="any" value={draft.sellTargetProbability === null ? "" : draft.sellTargetProbability * 100} placeholder="Theo cấu hình hệ thống" onChange={(e) => edit({ sellTargetProbability: e.target.value ? Number(e.target.value) / 100 : null })} /></label></div>
              <p className="planner-help">Hai trường trên chỉ áp dụng cho mục tiêu Đạt số lượng bán. Để trống nếu chọn mục tiêu khác.</p>
              <div className="planner-fields"><label>Ngân sách quảng cáo tối đa (₫)<input type="number" min={0} step="any" value={draft.maxSpendVnd ?? ""} placeholder="Không thêm giới hạn" onChange={(e) => edit({ maxSpendVnd: e.target.value ? Number(e.target.value) : null })} /></label><label>Mức lỗ kỳ vọng tối đa (₫)<input type="number" min={0} step="any" value={draft.maxLossVnd ?? ""} placeholder={draft.kind === "clear_stock" ? "Theo cấu hình hệ thống" : "Không thêm giới hạn"} onChange={(e) => edit({ maxLossVnd: e.target.value ? Number(e.target.value) : null })} /></label></div>
              <p className="planner-help">Ngân sách áp dụng cho toàn bộ khoảng mô phỏng. Mức lỗ giới hạn giá trị kỳ vọng của chỉ số J, không bảo đảm mọi kết quả thực tế đều nằm trong giới hạn.</p>
              <label>Hiệu quả biên tối thiểu<input type="number" min={0} step="any" value={draft.minMarginalReturn ?? ""} placeholder="Theo cấu hình hệ thống" onChange={(e) => edit({ minMarginalReturn: e.target.value ? Number(e.target.value) : null })} /><span className="planner-help">Chỉ áp dụng cho Tối ưu hiệu quả chi; để trống dùng cấu hình backend. Mục tiêu khác cần để trống trường này.</span></label>
              {draft.unresolved.length > 0 && <div className="planner-warning"><strong>Các yêu cầu cần làm rõ hoặc chưa được mô hình hỗ trợ</strong><ul>{draft.unresolved.map((item, i) => <li key={i}>{item}</li>)}</ul><p>Chỉnh mô tả rồi phân tích lại, hoặc xác nhận chỉ dùng những thông số đã kiểm tra ở trên.</p><label className="planner-check"><input type="checkbox" checked={acknowledged} onChange={(e) => { invalidate(); setAcknowledged(e.target.checked); }} /><span>Tôi đã kiểm tra và đồng ý loại các yêu cầu chưa được thể hiện trong thông số khỏi lần mô phỏng này.</span></label></div>}
              {!!issues.length && <ul className="planner-validation" aria-label="Thông tin cần bổ sung">{issues.map((issue) => <li key={issue}>{issue}</li>)}</ul>}
              <div className="planner-confirm"><label className="planner-check"><input type="checkbox" checked={confirmed} disabled={!canConfirm} onChange={(e) => setConfirmed(e.target.checked)} /><span>Tôi xác nhận các thông số trên. Khi chạy, hệ thống sẽ lưu mục tiêu mới đang áp dụng cho chiến dịch và tạo mô phỏng.</span></label><p>Mỗi lần chỉnh sửa cần xác nhận lại. Lệnh này chưa thực thi thay đổi quảng cáo.</p></div>
              {goalId && !result && <p className="planner-saved" role="status"><Check aria-hidden size={16} /> Đã lưu mục tiêu {goalId}. Thử lại sẽ tiếp tục mô phỏng với mục tiêu này.</p>}
              <button className="planner-primary planner-run" type="button" disabled={!canRun} onClick={() => void run()}>{busy === "run" ? "Đang chạy mô phỏng…" : goalId && !result ? "Thử lại mô phỏng" : "Lưu mục tiêu và chạy mô phỏng"}<ArrowRight aria-hidden size={16} /></button>
              {!selected && <p className="planner-help">Cần chọn một chiến dịch đã có trên backend để chạy.</p>}
            </fieldset>
          </>}
        </section>
      </div>
      {error && <div className="planner-error" role="alert"><strong>{error}</strong>{errorDetails && <details><summary>Chi tiết dữ liệu cần kiểm tra</summary><pre>{JSON.stringify(errorDetails, null, 2)}</pre></details>}{goalId && <p>Mục tiêu đã được lưu; bạn có thể thử lại mô phỏng.</p>}</div>}
      {result && <section ref={results} tabIndex={-1} className="planner-card planner-results" aria-labelledby="planner-results-title"><PlannerResults result={result} campaignName={selected?.name ?? campaignId} /></section>}
    </div>
  );
}

function Metric({ title, dist, currency = false, field }: { title: string; dist: Dist; currency?: boolean; field: string }) {
  const format = currency ? money : number;
  return <details className="planner-metric"><summary><span>{title} (kỳ vọng)</span><strong>{format(dist.mean)}</strong></summary><div><p>P10: {format(dist.p10)} · P50: {format(dist.p50)} · P90: {format(dist.p90)}</p><p>Trung bình và các phân vị từ mẫu mô phỏng backend; đây là ước lượng, không phải cam kết kết quả.</p><p>Trường dữ liệu: <code>{field}</code>. <a href="#planner-evidence">Xem nguồn và tham số</a></p></div></details>;
}
function PlannerResults({ result, campaignName }: { result: PlannerRunResponse; campaignName: string }) {
  const { data: scenario, meta } = result.scenario, rec = scenario.recommendation;
  const status: Record<NonNullable<ScenarioRun["recommendation"]>["status"], string> = { recommend: "Có phương án được đề xuất", undetermined: "Chưa phân biệt rõ phương án tốt hơn", none_meets_goal: "Chưa có phương án đáp ứng mục tiêu" };
  return <>
    <span className="planner-eyebrow">BƯỚC 3 / KẾT QUẢ TỪ BACKEND</span><h2 id="planner-results-title">Phương án cho {campaignName}</h2>
    <div className="planner-result-status" role="status"><strong>{scenario.blocked ? "Mô phỏng đang bị chặn bởi dữ liệu" : rec ? status[rec.status] : "Backend chưa trả về đề xuất"}</strong>
      {rec?.status === "recommend" && rec.best && <p>Đề xuất: {viText.action(rec.best)}. Cần người phụ trách xem xét trước khi thực thi.</p>}
      {rec?.reasons.length ? <ul>{rec.reasons.map((reason, i) => <li key={i}>{reason}</li>)}</ul> : null}
    </div>
    <p>Nhấn vào từng chỉ số để xem khoảng P10–P90 và nguồn tính. J = đóng góp − chi quảng cáo + giá trị tồn cuối kỳ − phạt thiếu hàng. Đây là chỉ số kinh tế của mô hình, có tính cả giá trị tồn kho.</p>
    <div className="planner-options">{scenario.results.map((option, i) => {
      const best = rec?.status === "recommend" && sameAction(option.action, rec.best);
      return <article className={`planner-option ${best ? "planner-option-best" : ""}`} key={option.key || i}>
        <header><h3>{viText.action(option.action)}</h3>{best && <span className="planner-badge">Được đề xuất</span>}</header>
        <p className="planner-option-status">{!option.feasible ? "Không khả thi" : !option.goal_eligible ? "Không đạt điều kiện mục tiêu" : "Đáp ứng điều kiện mô hình"}</p>
        {!!option.violations.length && <ul>{option.violations.map((v) => <li key={v}>{violationLabel(v)}</li>)}</ul>}
        {option.violation_details?.map((v, j) => <p key={j}>{v}</p>)}{option.goal_note && <p>{option.goal_note}</p>}
        <Metric title="Chỉ số kinh tế J" dist={option.objective} currency field={`results[${i}].objective`} />
        <Metric title="Chi quảng cáo" dist={option.spend} currency field={`results[${i}].spend`} />
        {option.goal_sku && option.goal_units_sold && <Metric title={`Sản phẩm bán · ${option.goal_sku}`} dist={option.goal_units_sold} field={`results[${i}].goal_units_sold`} />}
        <Metric title={option.goal_sku ? "Sản phẩm bán · toàn chiến dịch" : "Sản phẩm bán"} dist={option.units_sold} field={`results[${i}].units_sold`} />
        {option.p_target != null && <p>Xác suất đạt số lượng mục tiêu: <strong>{number(option.p_target * 100)}%</strong></p>}
        <details className="planner-components"><summary>Thành phần kinh tế và tồn kho</summary><Metric title="Đóng góp" dist={option.contribution} currency field={`results[${i}].contribution`} /><Metric title="Giá trị tồn cuối kỳ" dist={option.leftover_value} currency field={`results[${i}].leftover_value`} /><Metric title="Tồn cuối kỳ" dist={option.leftover_units} field={`results[${i}].leftover_units`} />{option.lost_units && <Metric title="Nhu cầu chưa đáp ứng" dist={option.lost_units} field={`results[${i}].lost_units`} />}{option.stockout_hour_p50 && <p>Hết hàng P50: {dateText(option.stockout_hour_p50)}</p>}</details>
        {option.duplicate_of && <p className="planner-help">Kết quả trùng với phương án: {option.duplicate_of}.</p>}
        {scenario.calculation_trace?.version === 1 && scenario.calculation_trace.actions.find((action) => action.key === option.key) && <ActionCalculation action={scenario.calculation_trace.actions.find((action) => action.key === option.key)!} index={i} />}
      </article>;
    })}</div>
    {!scenario.results.length && <p>Chưa có kết quả mô phỏng để so sánh. Kiểm tra nguồn dữ liệu và thông báo từ backend.</p>}
    <CalculationEvidence trace={scenario.calculation_trace} />
    <button type="button" onClick={() => openMilo({ screen: 'sale-planner',
      ...(scenario.calculation_trace?.focus_sku ? { sku: scenario.calculation_trace.focus_sku } : {}),
      campaignId: scenario.request.campaign_id, scenarioId: scenario.scenario_id,
    })}>Nhờ Milo giải thích kết quả</button>
    <details className="planner-evidence" id="planner-evidence"><summary>Nguồn dữ liệu và tham số của lần tính</summary><dl>
      <dt>Mã mô phỏng</dt><dd>{scenario.scenario_id}</dd><dt>Mục tiêu</dt><dd>{result.goalId}</dd>
      <dt>Dữ liệu đến</dt><dd>{dateText(rec?.inputs_ref.data_as_of ?? meta?.data_as_of)} (giờ Việt Nam)</dd>
      <dt>Thời gian mô phỏng</dt><dd>{dateText(scenario.request.start)} · {scenario.request.horizon_hours} giờ</dd>
      <dt>Số mẫu / seed</dt><dd>{scenario.request.samples} / {scenario.request.seed}</dd>
      <dt>Nguồn ước lượng iROAS</dt><dd>{rec?.inputs_ref.iroas_source === "prior" ? "Giả định prior của mô hình" : rec?.inputs_ref.iroas_source === "estimate" ? "Ước lượng từ dữ liệu" : "Chưa cung cấp"}</dd>
      <dt>Dự báo đầu vào</dt><dd>{rec?.inputs_ref.forecast_ids.join(", ") || "Chưa cung cấp"}</dd>
      <dt>Phiên bản tham số</dt><dd><pre>{JSON.stringify(rec?.inputs_ref.param_version ?? {}, null, 2)}</pre></dd>
      {scenario.input_hash && <><dt>Mã dữ liệu đầu vào</dt><dd>{scenario.input_hash}</dd></>}
    </dl>{rec?.flip_conditions.length ? <><h3>Khi nào cần xem lại đề xuất?</h3><ul>{rec.flip_conditions.map((item, i) => <li key={i}>{item.text}</li>)}</ul></> : null}
      <Link to="/mock-data">Mở Data Studio để kiểm tra nguồn nhập</Link>
    </details>
  </>;
}
