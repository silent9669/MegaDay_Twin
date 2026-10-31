import type { ScenarioRun } from "../api/types";
import { CalculationEvidence } from "../planner/CalculationEvidence";

import { money, units, time } from "./format";

export function CampaignTables({ scenario, selectedSku, onSelect }: {
  scenario: ScenarioRun; selectedSku: string; onSelect: (sku: string) => void;
}) {
  const trace = scenario.calculation_trace;
  if (!trace || trace.input_hash !== scenario.input_hash) return <p role="alert">Lần tính này chưa có bằng chứng hợp lệ. Cập nhật backend và tính lại.</p>;
  const product = trace.skus.find(s => s.sku_id === selectedSku) ?? trace.skus.find(s => s.in_campaign);
  const choices = ["continue", "pause"].map(key => ({
    trace: trace.actions.find(a => a.key === key), result: scenario.results.find(r => r.key === key),
  }));
  const cells = (value: (action: NonNullable<typeof choices[0]["trace"]>) => string) => choices.map((c, i) =>
    <td key={i}>{!c.result?.feasible ? "Không áp dụng" : !c.trace ? "Chưa có bằng chứng" : value(c.trace)}</td>);
  return <>
    <section className="campaign-card campaign-unit-economics">
      <h2>Kinh tế từng sản phẩm</h2>
      <label>Sản phẩm <select value={product?.sku_id ?? ""} onChange={e => onSelect(e.target.value)}>
        {trace.skus.filter(s => s.in_campaign).map(s => <option key={s.sku_id}>{s.sku_id}</option>)}
      </select></label>
      {!product?.economics_by_day?.length && <p>Chưa có phân rã giá vốn/phí của sản phẩm trong lần tính này.</p>}
      {product?.economics_by_day?.map(e => <div className="campaign-table-scroll" key={e.day}><table>
        <thead><tr><th>Mỗi {product.sku_id} / giả định ngày {e.day}</th><th>Số tiền</th></tr></thead>
        <tbody>
          <tr><td>Giá bán sau giảm giá do shop chịu</td><td>{money(e.eff_price)}</td></tr>
          <tr><td>Giá vốn{e.is_bundle ? " thành phần combo" : ""}</td><td>{money(e.unit_cost)}</td></tr>
          <tr><td>Phí sàn giả định {units(e.fee_rate * 100)}%</td><td>{money(e.eff_price * e.fee_rate)}</td></tr>
          <tr><td>Đóng gói</td><td>{money(e.packaging_cost)}</td></tr>
          <tr className="campaign-total"><td>Còn lại trước quảng cáo</td><td>{money(e.m)}</td></tr>
        </tbody></table></div>)}
      <p>Giá bán − giá vốn − phí sàn − đóng gói. Đây là lãi đóng góp, chưa trừ mọi chi phí vận hành.</p>
      {product && <p>Tồn đầu kỳ: {product.is_bundle ? "Dùng tồn thành phần, không có kho combo riêng" : moneyStock(product.stock_start)}.
        {product.restock.length > 0 && <> Hàng về dự kiến: {product.restock.map(r => `${units(r.units)} sản phẩm ở giờ thứ ${r.hour}`).join("; ")}.</>}</p>}
    </section>
    <section className="campaign-card">
      <h2>Tiếp tục hay tạm dừng quảng cáo?</h2>
      <p>Từ {time(trace.start)} đến {time(new Date(Date.parse(trace.start) + trace.horizon_hours * 3600000).toISOString())} · Giờ Việt Nam.</p>
      <p>Số kỳ vọng cho thời gian còn lại; tiền đã chi không được hoàn lại khi tạm dừng.</p>
      <div className="campaign-table-scroll"><table>
        <thead><tr><th>Minh họa từ mô phỏng</th><th>Tiếp tục quảng cáo</th><th>Tạm dừng trong kỳ</th></tr></thead>
        <tbody>
          <tr><td>Ràng buộc</td>{choices.map((c, i) => <td key={i}>{c.result?.feasible ? "Hợp lệ" : c.result?.violations.includes("campaign_must_run") ? "Không hợp lệ do cam kết flash sale" : "Chưa có phương án hợp lệ"}</td>)}</tr>
          {trace.skus.filter(s => s.in_campaign).map(s => <tr key={`sold-${s.sku_id}`}><td>{s.sku_id} bán dự kiến{s.is_bundle ? " (combo)" : " (bán riêng)"}</td>{cells(a => units(a.per_sku.find(p => p.sku_id === s.sku_id)?.units_sold.mean))}</tr>)}
          {trace.skus.filter(s => !s.is_bundle).map(s => <tr key={`stock-${s.sku_id}`}><td>{s.sku_id} còn lại, đã trừ tiêu hao combo</td>{cells(a => units(a.per_sku.find(p => p.sku_id === s.sku_id)?.leftover_units?.mean))}</tr>)}
          <tr><td>Doanh thu</td>{cells(a => money(a.metrics.revenue?.mean))}</tr>
          <tr><td>Giá vốn hàng đã bán + phí sàn + đóng gói</td>{cells(a => money(a.metrics.selling_cost?.mean))}</tr>
          <tr className="campaign-total"><td>Còn lại trước quảng cáo</td>{cells(a => money(a.metrics.contribution.mean))}</tr>
          <tr><td>Chi quảng cáo mới dự kiến</td>{cells(a => money(a.metrics.spend.mean))}</tr>
          <tr className="campaign-total"><td>Kết quả từ hàng đã bán sau các khoản trên</td>{cells(a => money(a.metrics.after_ads?.mean))}</tr>
          <tr><td>Mục tiêu đã xác nhận</td>{choices.map((c, i) => <td key={i}>{c.result?.goal_eligible ? "Đáp ứng theo mô hình" : c.result?.goal_note ?? "Không đạt / chưa có kết quả"}</td>)}</tr>
        </tbody></table></div>
      <p>Không cộng số combo vào tồn thành phần. Bảng tiền bao gồm phạm vi kinh tế của mô phỏng; chi ads dùng chung campaign.</p>
      <CalculationEvidence trace={trace} />
    </section>
  </>;
}
function moneyStock(n: number | null) { return n == null ? "chưa rõ" : `${units(n)} sản phẩm`; }
