"""Separate what a product is from the evidence available to forecast it."""

from __future__ import annotations

from typing import Any

from app.forecast.classify import name_hint
from app.forecast.context import FitContext
from app.forecast.demand import POLICY_VERSION, PRIMARY_FROM_HISTORY
from app.forecast.registry import ChampionInfo, get_model

PREFERRED = {"Normal": "profile_ewma", "Trendy": "trend_damped", "Seasonal": "seasonal_calendar"}


def select_model(
    ctx: FitContext,
    i: int,
    champion: ChampionInfo,
    label: dict[str, Any] | None,
    *,
    drift: bool = False,
    min_history: float = 14,
    sparse_adi: float = 1.32,
) -> tuple[ChampionInfo, dict[str, Any]]:
    group = ctx.groups[i]
    sku = ctx.sku_ids[i]
    features = ctx.features.get(sku, {})
    primary = label["label"] if label else PRIMARY_FROM_HISTORY.get(group)
    new = ctx.history_days(i) < min_history
    sparse = float(features.get("ADI", 0)) > sparse_adi
    insufficient = int(ctx.first_day[i]) < 0 or "peak_ratio" not in features
    notes = []
    chosen = champion

    def use(name: str, reason: str) -> None:
        nonlocal chosen
        # Scores belong to a model + history group + context, not to its replacement.
        chosen = (
            champion
            if name == champion.model_id.split("@", 1)[0]
            else ChampionInfo(get_model(name).model_id, None, False, False)
        )
        notes.append(reason)

    if ctx.group_source.get(sku) == "user":
        notes.append("Giữ lựa chọn hồ sơ dự báo thủ công; không đổi nhãn nhu cầu chính.")
    elif champion.from_backtest and champion.scope is not None:
        notes.append("Chọn mô hình đã được kiểm thử trên cùng hồ sơ lịch sử và bối cảnh.")
    elif new:
        use(
            "analog",
            "Lịch sử còn ngắn: dùng sản phẩm tương tự, không coi nhãn là bằng chứng doanh số.",
        )
    elif sparse:
        use("tsb", "Nhiều ngày không có đơn: ưu tiên mô hình bán thưa, giữ nguyên nhãn nhu cầu.")
    elif insufficient:
        use("profile_ewma", "Chưa đủ ngày hợp lệ để xác minh hình dạng nhu cầu; dùng mô hình nền.")
    elif primary:
        preferred = PREFERRED[primary]
        kind = ctx.kinds[i] or name_hint(ctx.names.get(sku))
        if preferred == "seasonal_calendar" and kind is None:
            use(
                "profile_ewma",
                "Nhãn Mùa vụ chưa xác định được lịch mùa được hỗ trợ; dùng mô hình nền.",
            )
        else:
            if preferred == "seasonal_calendar":
                ctx.kinds[i] = kind
            use(
                preferred,
                "Nhãn nhu cầu định hướng mô hình mặc định; chưa có kiểm thử phù hợp để xác nhận.",
            )
    if chosen.model_id.startswith("seasonal_calendar") and not (
        ctx.kinds[i] or name_hint(ctx.names.get(sku))
    ):
        use("profile_ewma", "Không tự suy đoán lịch quà tặng cho một nhãn mùa vụ chưa rõ loại.")
    if drift and not new and not insufficient:
        use(
            "nowcast",
            "Nhu cầu lệch mạnh: cập nhật giờ bán gần nhất; không dùng điểm kiểm thử mô hình cũ.",
        )
    if label and PRIMARY_FROM_HISTORY.get(group) not in (None, primary):
        notes.append(
            "Nhãn chính và hình dạng lịch sử khác nhau; giữ nhãn và ghi rõ căn cứ chọn mô hình."
        )
    return chosen, {
        "policy_version": POLICY_VERSION,
        "primary_label": primary,
        "label_source": ("warehouse_manual" if label.get("manual") else "warehouse_ai")
        if label
        else "history"
        if primary
        else None,
        "label_evidence": label,
        "history_profile": group,
        "data_status": {
            "new": new,
            "sparse": sparse,
            "insufficient": insufficient,
            "history_days": ctx.history_days(i),
        },
        "model_id": chosen.model_id,
        "from_backtest": chosen.from_backtest,
        "thresholds": {"min_history_days": min_history, "sparse_adi": sparse_adi},
        "reasons": notes,
    }
