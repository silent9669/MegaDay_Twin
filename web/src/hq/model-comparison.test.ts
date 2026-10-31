import { vi, expect, it } from "vitest";
import { comparisonRun } from "./model-comparison.fixture";
import { api } from "../api/client";
import { loadModelComparison, modelComparison } from "./model-comparison";
it("uses simulated SKU outcomes and allocates shared spend without assigning the entire campaign budget",()=>{
 const result=modelComparison(comparisonRun(),"A");
 expect(result.share).toBe(.75);
 expect(result.value).toEqual({stock:83,price:600000,cost:480000,packaging:3000,feePercent:12,continueUnits:83,pauseUnits:63,spend:1200000});
 expect(result.results[0]).toEqual({units:80,leftover:3,revenue:48000000,costs:44400000,beforeAds:3600000,spend:1200000,afterAds:2400000});
 expect(result.results[1]?.afterAds).toBe(2835000);
});
it("refuses unrelated or incomplete traces and exposes infeasible choices",()=>{
 const run=comparisonRun();expect(()=>modelComparison(run,"OTHER")).toThrow();
 run.input_hash="other";expect(()=>modelComparison(run,"A")).toThrow();
 const blocked=comparisonRun();blocked.results[1]!.feasible=false;blocked.results[1]!.violation_details=["Must run commitment"];
 expect(modelComparison(blocked,"A").violations).toEqual(["Pause: Does not satisfy current campaign constraints"]);
 const missing=comparisonRun();missing.calculation_trace!.actions.pop();expect(()=>modelComparison(missing,"A")).toThrow();
});

it("previews the existing goal with its deadline and constraints without applying it", async () => {
  const get = vi.spyOn(api,"get").mockImplementation(async path => {
    if(path.startsWith("/sale-plans/context")) return {data:{base_goal:{goal_id:"goal-1",version:3},active_goal:{kind:"profit",deadline:"2026-11-11T04:00:00Z",target_units:null,params:{max_spend_vnd:300000,max_loss_vnd:0}}}} as never;
    return {data:{sku:"A",as_of:"2026-11-11T00:00:00Z",campaign:{id:"CD002"}}} as never;
  });
  const post = vi.spyOn(api,"post").mockResolvedValue({data:{scenario:comparisonRun()}});
  try {
    await loadModelComparison("A","CD002");
    expect(post).toHaveBeenCalledOnce();
    expect(post).toHaveBeenCalledWith("/sale-plans/preview",expect.objectContaining({horizon_hours:4,expected_goal:{goal_id:"goal-1",version:3},focus_sku:null,goal:expect.objectContaining({params:{max_spend_vnd:300000,max_loss_vnd:0}})}));
  } finally { get.mockRestore(); post.mockRestore(); }
});
