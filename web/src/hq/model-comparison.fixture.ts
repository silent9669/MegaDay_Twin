import type { ScenarioRun } from "../api/types";
const dist = (mean: number) => ({mean,p10:mean,p50:mean,p90:mean});
export function comparisonRun(): ScenarioRun {
  return { scenario_id:"scn-live",input_hash:"hash",blocked:false,
    results:["continue","pause"].map((key,i)=>({key,feasible:true,goal_eligible:true,per_sku:[{sku_id:"A",demand:dist(i?63:83)}]})),
    calculation_trace:{input_hash:"hash",start:"2026-11-11T00:00:00+07:00",horizon_hours:24,limitations:["Model estimate"],
      skus:[{sku_id:"A",in_campaign:true,is_bundle:false,stock_start:83,forecast_mean_hourly:[75],price_hourly:[600000],economics_by_day:[{eff_price:600000,unit_cost:480000,packaging_cost:3000,fee_rate:.12}]},
        {sku_id:"B",in_campaign:true,forecast_mean_hourly:[25],price_hourly:[600000]}],
      actions:["continue","pause"].map((key,i)=>({key,metrics:{spend:dist(i?0:1600000)},per_sku:[{sku_id:"A",units_sold:dist(i?63:80),leftover_units:dist(i?20:3),revenue:dist(i?37800000:48000000),contribution:dist(i?2835000:3600000)}]}))}
  } as unknown as ScenarioRun;
}
