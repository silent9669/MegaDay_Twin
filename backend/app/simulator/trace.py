"""Immutable, finite calculation evidence captured from the exact simulated arrays.

This is not a reconstruction from today's warehouse. Batch references are supporting
source records, not a claim of complete CSV row lineage or forecast training lineage.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import date, datetime
from typing import Any

import duckdb
import numpy as np

from app.core.timeutil import to_local
from app.simulator.constraints import UNLIMITED
from app.simulator.engine import SimRun
from app.simulator.recommend import quantity_sku
from app.simulator.schemas import Dist
from app.warehouse.repo import fetch_all


def _json(value: Any) -> Any:
    if isinstance(value, datetime):
        return to_local(value).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    raise TypeError(type(value).__name__)


def build_trace(conn: duckdb.DuckDBPyConnection, run: SimRun, input_hash: str) -> dict[str, Any]:
    inp = run.inputs
    ids = [s.sku_id for s in inp.skus]
    # Only batches attached to relevant SKU orders/inventory or this campaign's ads,
    # with observations before the simulated start. Never guess cost/sale-file lineage.
    batches = fetch_all(
        conn,
        "SELECT batch_id, source_type, file_name, file_sha256, as_of FROM ingest_batches"
        " WHERE batch_id IN ("
        " SELECT o.batch_id FROM orders o JOIN order_lines l USING(order_id)"
        " WHERE list_contains(?, l.sku_id) AND o.ordered_at < ?"
        " UNION SELECT batch_id FROM inventory_snapshots"
        " WHERE list_contains(?, sku_id) AND snapshot_at < ?"
        " UNION SELECT batch_id FROM ad_hourly WHERE campaign_id = ? AND hour_utc < ?)"
        " ORDER BY received_at DESC, batch_id LIMIT 201",
        [ids, inp.start, ids, inp.start, inp.campaign_id, inp.start],
    )
    forecasts = fetch_all(
        conn,
        "SELECT forecast_id, sku_id, model_id, origin, data_as_of, panel_version, payload"
        " FROM forecasts WHERE list_contains(?, forecast_id) ORDER BY forecast_id",
        [inp.forecast_ids],
    )
    for forecast in forecasts:
        payload = forecast.pop("payload")
        saved = json.loads(payload) if isinstance(payload, str) else payload
        forecast["model_selection"] = saved.get("model_selection", {})
    params = []
    for name in list(inp.params.versions):
        key, _, sku = name.partition("@")
        params.append(asdict(inp.params.resolve(key, sku or None)))
    focus = quantity_sku(inp)
    actions = []
    for action in run.runs:
        penalty = inp.penalty * action.lost_units
        component_sum = action.contribution - action.spend + action.leftover_value - penalty
        actions.append(
            {
                "key": action.action.key,
                "action": action.action.model_dump(mode="json"),
                "metrics": {
                    "objective": Dist.of(action.j).model_dump(),
                    "contribution": Dist.of(action.contribution).model_dump(),
                    "spend": Dist.of(action.spend).model_dump(),
                    "leftover_value": Dist.of(action.leftover_value).model_dump(),
                    "stockout_penalty": Dist.of(penalty).model_dump(),
                    "units_sold": Dist.of(action.units_sold).model_dump(),
                    "focus_units_sold": Dist.of(action.sold_by_sku[focus]).model_dump()
                    if focus
                    else None,
                },
                "reconciliation": {
                    "sum_components_mean": float(component_sum.mean()),
                    "objective_mean": float(action.j.mean()),
                    "residual_vnd": float((action.j - component_sum).mean()),
                },
                "spend_hourly": action.spend_hourly.tolist(),
                "per_sku": [
                    {
                        "sku_id": sku_id,
                        "units_sold": Dist.of(action.sold_by_sku[sku_id]).model_dump(),
                        "contribution": Dist.of(contribution).model_dump(),
                    }
                    for sku_id, contribution in action.contribution_by_sku.items()
                ],
            }
        )
    skus = []
    for s in inp.skus:
        economics = []
        if inp.economics is not None:
            economics = [
                asdict(e) for (sku_id, _), e in inp.economics.by_key.items() if sku_id == s.sku_id
            ]
        skus.append(
            {
                "sku_id": s.sku_id,
                "in_campaign": s.in_campaign,
                "is_bundle": s.is_bundle,
                "stock_start": None if s.is_bundle or s.stock0 >= UNLIMITED else s.stock0,
                "stock_unlimited": s.stock0 >= UNLIMITED,
                "unit_cost_vnd": s.unit_cost,
                "price_hourly": s.price.tolist(),
                "contribution_per_unit_hourly": s.m.tolist(),
                "economics_by_day": economics,
                "forecast_mean_hourly": s.lam_sq.tolist(),
                "forecast_id": s.forecast_id,
                "forecast_label": s.label,
                "dispersion_k": s.k,
                "sigma": s.sigma,
                "leftover_value_per_unit": s.leftover_value,
                "restock": [{"hour": hour, "units": units} for hour, units in inp.arrivals(s)],
                "safety_stock": s.safety,
                "flash_reserve": s.reserve,
                "components": [{"sku_id": sku, "units": units} for sku, units in s.components],
            }
        )
    trace = {
        "version": 1,
        "input_hash": input_hash,
        "campaign_id": inp.campaign_id,
        "focus_sku": inp.goal.params.get("focus_sku"),
        "quantity_scope": "sku" if focus else "campaign",
        "economic_scope": "campaign",
        "start": inp.start,
        "horizon_hours": inp.T,
        "samples": run.samples,
        "seed": run.seed,
        "data_as_of": inp.data_as_of,
        "panel_version": inp.panel_version,
        "goal": {
            "kind": inp.goal.kind,
            "target_units": inp.goal.target_units,
            "params": dict(inp.goal.params),
        },
        "equations": [
            {
                "id": "objective",
                "formula": "J = contribution - spend + leftover_value - penalty * lost_units",
                "explanation": "Tính trên từng mẫu rồi lấy trung bình/phân vị."
                " J có giá trị tồn cuối kỳ, không phải lợi nhuận kế toán.",
            },
            {
                "id": "contribution",
                "formula": "contribution = sum_sku,hour(m[sku,hour] * sold[sku,hour])",
                "explanation": "m = giá bán - giá vốn - tỷ lệ phí * giá bán - đóng gói."
                " Combo dùng tổng giá vốn thành phần.",
            },
            {
                "id": "demand",
                "formula": "lambda = forecast_mean * (1 + uplift(action)) / (1 + uplift(continue))"
                " * rank; demand ~ Poisson(lambda * Lmult * Gamma)",
                "explanation": "Mô phỏng nhu cầu có bất định; dùng cùng các mẫu ngẫu nhiên giữa"
                " phương án. Nhu cầu lớn dùng xấp xỉ chuẩn của phân vị Poisson.",
            },
            {
                "id": "target_probability",
                "formula": "P(target) = count(goal_units >= target_units) / samples",
                "explanation": "goal_units là số bán trực tiếp của SKU đã chọn khi có mục tiêu"
                " số lượng theo SKU; không cộng bán SKU khác hoặc tiêu hao qua combo.",
            },
        ],
        "sources": {
            "batches": batches[:200],
            "batches_truncated": len(batches) > 200,
            "forecasts": forecasts,
            "parameters": params,
            "iroas": {
                "source": inp.iroas_source,
                "estimate_id": inp.iroas.estimate_id,
                "confidence": inp.iroas.confidence,
                "delta": inp.iroas.delta,
                "spend_scale": inp.iroas.spend_scale,
            },
        },
        "assumptions": {
            "stockout_penalty_per_unit": inp.penalty,
            "rank_loss": inp.rank_loss,
            "adstock_start": inp.adstock0,
            "budget_source": inp.budget_source,
            "daily_budget": inp.spend.budget_day.tolist(),
            "must_run_hours": np.flatnonzero(inp.must_run).tolist(),
        },
        "skus": skus,
        "actions": actions,
        "limitations": [
            "Đây là ảnh chụp đầu vào và phép tính của lần mô phỏng; dữ liệu nguồn sửa sau đó"
            " không thay đổi bản này.",
            "Batch là nguồn hỗ trợ có bản ghi liên quan, không phải truy vết từng dòng CSV hay"
            " toàn bộ dữ liệu huấn luyện dự báo. Chưa lưu liên kết batch chính xác cho giá vốn,"
            " cam kết sale và cấu trúc chiến dịch.",
            "Không cộng/trừ các phân vị để suy ra phân vị J; chỉ kỳ vọng của các thành phần"
            " có thể đối soát bằng phép cộng.",
            "Chi quảng cáo, J và giới hạn lỗ thuộc toàn bộ chiến dịch, gồm cả nhu cầu thành phần"
            " combo ngoài chiến dịch khi có; không phân bổ chi phí quảng cáo riêng SKU.",
            "P10/P50/P90 là phân vị mô phỏng theo giả định hiện tại, không bảo đảm kết quả thực tế."
            " Giới hạn lỗ áp dụng kỳ vọng J.",
            "Giá vốn/giá bán theo quy tắc mô hình có thể dùng nguồn được nhập sau mốc mô phỏng"
            " khi dữ liệu lịch sử thiếu; không xem đây là backtest không rò rỉ thông tin.",
            *(
                ["Không có bảng phân rã giá vốn/phí theo ngày trong đầu vào lần tính này."]
                if inp.economics is None
                else []
            ),
            *inp.data_reasons,
        ],
    }
    # Validate serializability and finite numbers before any persistence/mutation.
    return json.loads(json.dumps(trace, default=_json, allow_nan=False))  # type: ignore[no-any-return]
