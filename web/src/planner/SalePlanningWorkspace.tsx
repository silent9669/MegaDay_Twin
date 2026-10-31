import { useSearchParams } from "react-router";
import { Target } from "lucide-react";
import { SaleGoalPlanner } from "./SaleGoalPlanner";
import { SkuGoalAssistant } from "./SkuGoalAssistant";
import "./planner.css";

export function SalePlanningWorkspace() {
  const [params, setParams] = useSearchParams();
  const scope = params.get("scope") === "sku" || params.has("sku") ? "sku" : "campaign";
  function choose(next: string) {
    const value = new URLSearchParams();
    if (next === "sku") value.set("scope", "sku");
    setParams(value);
  }
  return <div className="sale-planning-workspace">
    <header className="planner-heading"><div><span className="planner-eyebrow">MEGADAY HQ</span>
      <h1><Target aria-hidden size={26} /> Lập kế hoạch sale</h1>
      <p>Trao đổi mục tiêu, mô phỏng thử và so sánh. Chỉ thay đổi mục tiêu đang áp dụng khi bạn xác nhận riêng.</p>
    </div></header>
    <fieldset className="planner-scope-choice"><legend>Bạn muốn lập kế hoạch cho phạm vi nào?</legend>
      <label><input type="radio" name="planning-scope" checked={scope === "campaign"} onChange={() => choose("campaign")} /> Cả chiến dịch</label>
      <label><input type="radio" name="planning-scope" checked={scope === "sku"} onChange={() => choose("sku")} /> Một sản phẩm (SKU)</label>
    </fieldset>
    {scope === "sku" ? <SkuGoalAssistant embedded /> : <SaleGoalPlanner hideHeading />}
  </div>;
}
