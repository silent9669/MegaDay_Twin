import { useEffect, useRef, useState } from "react";
import { Link } from "react-router";
import { requestJSON } from "../studio/account";
import type { Envelope, ScenarioRun } from "../api/types";
import { openMilo } from "../city/milo-contract";
import { viText } from "../lib/i18n";
import { CampaignTables } from "./CampaignTables";
import { ReplayPanel } from "./ReplayPanel";
import { money, time, units } from "./format";
import type { CampaignContext, CampaignRun, Explanation } from "./types";
import "./campaign.css";

export function CampaignHQ({ watch }: { watch: { context: CampaignContext | null; error: string; acceptContext: (context: CampaignContext) => void } }) {
  const [result, setResult] = useState<CampaignRun | null>(null);
  const [previous, setPrevious] = useState<CampaignRun | null>(null);
  const [explanation, setExplanation] = useState<Explanation | null>(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [sku, setSku] = useState("NOICHIEN2L");
  const [tab, setTab] = useState("overview");
  const operation = useRef(0);
  useEffect(() => { const active = operation; return () => { active.current++; }; }, []);
  const current = watch.context;
  const stale = !!result && result.revision !== current?.revision;
  async function explain(run: CampaignRun, op: number) {
    setBusy("Milo đang chọn căn cứ cho đề xuất…");
    const response = await requestJSON<Explanation>("/api/campaign-hq/explain", {
      method: "POST", body: JSON.stringify({ scenarioId: run.scenario.scenario_id }),
    });
    if (operation.current === op && response.scenarioId === run.scenario.scenario_id) setExplanation(response);
  }
  async function refresh() {
    const op = ++operation.current;
    setBusy("Đang kiểm tra dữ liệu và mục tiêu…"); setError("");
    try {
      const latest = await requestJSON<CampaignContext>("/api/v1/campaign-analysis/CD002");
      if (op !== operation.current) return;
      watch.acceptContext(latest);
      if (!latest.goal) throw Error("Campaign chưa có mục tiêu được áp dụng. Mở phần mục tiêu, xem trước và chọn Apply để áp dụng.");
      setBusy("Đang tính các phương án theo dữ liệu mới…");
      const next = await requestJSON<CampaignRun>("/api/v1/campaign-analysis/CD002/refresh", {
        method: "POST", body: JSON.stringify({ revision: latest.revision, goal_id: latest.goal.goal_id }),
      });
      if (op !== operation.current) return;
      if (next.scenario.request.campaign_id !== "CD002" || next.scenario.request.goal_id !== latest.goal.goal_id ||
          next.scenario.calculation_trace?.input_hash !== next.scenario.input_hash) throw Error("Kết quả không khớp campaign/mục tiêu. Giữ nguyên bản trước.");
      setPrevious(result); setResult(next); setExplanation(null); setTab("analysis");
      await explain(next, op);
    } catch (e) {
      if (op === operation.current) setError(e instanceof Error ? e.message : "Không thể cập nhật. Bản trước được giữ nguyên.");
    } finally { if (op === operation.current) setBusy(""); }
  }
  async function openHistory(id: string) {
    const op = ++operation.current; setBusy("Đang mở bản lưu…"); setError("");
    try {
      const { data: scenario } = await requestJSON<Envelope<ScenarioRun>>(`/api/v1/scenarios/${encodeURIComponent(id)}`);
      if (operation.current !== op) return;
      if (scenario.request.campaign_id !== "CD002" || !scenario.calculation_trace) throw Error("Bản lưu thiếu bằng chứng.");
      const params = scenario.calculation_trace.goal.params;
      setPrevious(result); setExplanation(null);
      setResult({ scenario, revision: String(params.hq_revision ?? "historical"),
        goal: { goal_id: scenario.request.goal_id, version: Number(params.hq_goal_version ?? 0),
          kind: scenario.calculation_trace.goal.kind, deadline: String(params.hq_deadline ?? ""),
          target_units: scenario.calculation_trace.goal.target_units, params, confirmed_by: "Bản lưu" },
        end: new Date(Date.parse(scenario.calculation_trace.start) + scenario.calculation_trace.horizon_hours * 3600000).toISOString(),
        progress: savedProgress(params.hq_progress) }); setTab("analysis");
    } catch (e) { if (operation.current === op) setError(e instanceof Error ? e.message : "Không mở được bản lưu."); }
    finally { if (operation.current === op) setBusy(""); }
  }
  const rec = result?.scenario.recommendation;
  const best = rec?.status === "recommend" && rec.best ? result?.scenario.results.find(r => r.key === actionKey(rec.best!)) : null;
  const goalNames: Record<string, string> = { profit: "Tối ưu chỉ số kinh tế J", sell_target: "Đạt số lượng bán", clear_stock: "Xả hàng", efficiency: "Hiệu quả chi" };
  return <div className="campaign-hq">
    <header className="campaign-heading"><div><Link to="/city-demo?district=hq">← MegaDay HQ</Link><p className="campaign-eyebrow">CAMPAIGN / CD002</p>
      <h1>{current?.campaign.name ?? "Nồi chiên mini + giấy lót"}</h1><p>Ngân sách dùng chung · Quyết định cho toàn chiến dịch</p></div>
      <button className="campaign-primary" disabled={!!busy || !current?.goal || !current.canRun || !!watch.error} onClick={() => void refresh()}>Cập nhật & đề xuất lại</button>
    </header>
    {busy && <p role="status" className="campaign-banner">{busy}</p>}
    {(error || watch.error) && <div role="alert" className="campaign-banner">{error || watch.error}<p>Kết quả đã lưu được giữ nguyên. Dữ liệu mới cần được ingest vào backend trước khi phân tích.</p></div>}
    {stale && <p className="campaign-banner">Có dữ liệu hoặc mục tiêu mới chưa được tính vào bản đang xem.</p>}
    <nav className="campaign-tabs" aria-label="Campaign">{[["overview", "Tổng quan"], ["analysis", "Phân tích"], ["advice", "Đề xuất"]].map(([key, label]) => <button key={key} aria-pressed={tab === key} onClick={() => setTab(key!)}>{label}</button>)}</nav>
    <ReplayPanel />
    {tab === "overview" && <>
      <section className="campaign-card"><h2>Mục tiêu đang áp dụng</h2>{current?.goal ? <>
        <p><strong>{goalNames[current.goal.kind] ?? current.goal.kind}</strong> · Hạn {time(current.goal.deadline)}</p>
        <p>{current.goal.target_units != null && <>Chỉ tiêu: {units(current.goal.target_units)} sản phẩm · </>}Phiên bản {current.goal.version} · Xác nhận bởi {current.goal.confirmed_by}</p>
      </> : <p>Chưa có mục tiêu được áp dụng. Mở phần lập kế hoạch, xem trước rồi chọn Apply. Bản xem trước chưa thay mục tiêu đang chạy.</p>}
        <Link to="/sale-planner?campaign=CD002">Xem / chỉnh mục tiêu →</Link>
      </section>
      <section className="campaign-card"><h2>Sản phẩm trong campaign</h2><p>Ngân sách ngày hiện tại: {money(current?.campaign.daily_budget_vnd)} · Chi ads đã ghi nhận hôm nay: {money(current?.campaign.spend_today_vnd)}</p>
        <div className="campaign-table-scroll"><table><thead><tr><th>Sản phẩm</th><th>Giá bán</th><th>Tồn nguồn</th><th>Chi tiết</th></tr></thead><tbody>{current?.campaign.sku_detail.map(s => <tr key={s.sku}><td>{s.name}<small>{s.sku}</small></td><td>{money(s.price_effective_vnd)}</td><td>{s.is_bundle ? "Dùng tồn thành phần" : units(s.stock_available)}</td><td><button onClick={() => { setSku(s.sku); setTab("analysis"); }}>Xem bảng tính</button></td></tr>)}</tbody></table></div>
        {!current && <p>Đang chờ dữ liệu campaign từ backend; không dùng số mẫu thay thế.</p>}
      </section>
    </>}
    {tab === "analysis" && (result ? <CampaignTables scenario={result.scenario} selectedSku={sku} onSelect={setSku} /> : <section className="campaign-card"><h2>Chưa có lần tính</h2><p>Nhấn Cập nhật & đề xuất lại sau khi xác nhận mục tiêu và nhập dữ liệu.</p></section>)}
    {(tab === "analysis" || tab === "advice") && result && <section className="campaign-card">
      <h2>Đề xuất cho mục tiêu của bạn</h2><p>{goalNames[result.goal.kind] ?? result.goal.kind} · Mục tiêu {result.goal.goal_id} · Phiên bản {result.goal.version}</p>
      {result.progress && <p>Đã ghi nhận trong kỳ mục tiêu: {units(result.progress.units)} sản phẩm và {money(result.progress.spend)} tiền ads{!result.progress.ads_complete && " (ads chưa đầy đủ)"}. Xác suất đạt chỉ tiêu xét cả lượng đã ghi nhận và lượng dự báo thêm; đơn chưa hoàn tất còn có thể hủy/trả.</p>}
      <h3>{best ? viText.action(best.action) : rec?.status === "none_meets_goal" ? "Chưa có phương án đạt mục tiêu" : "Chưa đủ bằng chứng chọn phương án tốt hơn"}</h3>
      {best && <p>Áp dụng từ {time(result.scenario.request.start)} đến {time(result.end)}. Chi ads mới dự kiến: <strong>{money(best.spend.mean)}</strong>. {best.p_target != null && <>Xác suất đạt chỉ tiêu: {units(best.p_target * 100)}%.</>}</p>}
      <p>J có tính giá trị tồn cuối kỳ và phạt thiếu hàng; khác dòng “kết quả từ hàng đã bán” trong bảng.</p>
      <small>{explanation?.method === "ai" ? "Milo đã chọn căn cứ từ kết quả mô phỏng; số và phương án do backend tính." : "Căn cứ từ bộ mô phỏng; chưa phải diễn giải AI."}</small>
      <ul>{(explanation?.reasons ?? rec?.reasons ?? []).map((r, i) => <li key={i}>{r}</li>)}</ul>
      {explanation?.notice && <p>{explanation.notice}</p>}
      <details><summary>Khi nào cần xem lại?</summary><ul>{(explanation?.cautions ?? rec?.flip_conditions.map(c => c.text) ?? []).map((r, i) => <li key={i}>{r}</li>)}</ul></details>
      {previous && <p>Bản trước: {time(previous.scenario.request.start)} · {previous.scenario.recommendation?.best ? viText.action(previous.scenario.recommendation.best) : "Chưa có đề xuất"}. Bản này: {rec?.best ? viText.action(rec.best) : "Chưa có đề xuất"}. Hai bản có thể khác khoảng thời gian; không trừ trực tiếp lợi nhuận để đánh giá cải thiện.</p>}
      <button disabled={!!busy} onClick={() => openMilo({ screen: "campaign-hq", campaignId: "CD002", scenarioId: result.scenario.scenario_id })}>Trao đổi với Milo về lần tính này</button>
    </section>}
    {tab === "advice" && !result && <p>Chưa có kết quả mô phỏng để đề xuất.</p>}
    <section className="campaign-card"><h2>Nguồn và lịch sử</h2><p>Mốc backend: {time(current?.dataAsOf)}. Các mốc dưới đây là cận cuối, không gồm giờ kế tiếp.</p>
      <p>Đơn hàng: {time(current?.sources.orders)} · Tồn kho: {time(current?.sources.inventory)} · Ads: {time(current?.sources.ads)}</p>
      <p>Ads có thể đến trễ; giờ chưa có báo cáo không được coi là chi 0đ. Đồng hồ City không tự cập nhật dữ liệu Python.</p>
      {current?.history?.map(h => <button disabled={!!busy} key={h.scenario_id} onClick={() => void openHistory(h.scenario_id)}>Bản lưu {time(h.created_at)}</button>)}
      <Link to="/mock-data">Kiểm tra và gửi nguồn vào backend →</Link>
    </section>
  </div>;
}
function actionKey(a: { kind: string; pct?: number | null; windows_local?: number[][] | null }) {
  return a.kind === "reduce_budget" ? `${a.kind}:${a.pct}` : a.kind === "schedule_hours" ? `${a.kind}:${a.windows_local?.map(w => w.join("-")).join(",")}` : a.kind;
}
function savedProgress(value: unknown): CampaignRun["progress"] {
  if (!value || typeof value !== "object") return null;
  const p = value as Record<string, unknown>;
  return typeof p.units === "number" && typeof p.spend === "number" && typeof p.ads_complete === "boolean"
    ? { units: p.units, spend: p.spend, ads_complete: p.ads_complete } : null;
}
