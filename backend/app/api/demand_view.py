"""Local-time API views of immutable demand evidence and calculation traces."""

from datetime import datetime
from typing import Any

from app.core.timeutil import iso_local


def demand_view(value: dict[str, Any]) -> dict[str, Any]:
    """Copy known evidence fields; never rewrite provenance text or stored hashes."""
    result = dict(value)
    for key in ("forecast_origin", "completed_at", "evidence_through"):
        stamp = result.get(key)
        if isinstance(stamp, str):
            stamp = datetime.fromisoformat(stamp)
        if isinstance(stamp, datetime):
            result[key] = iso_local(stamp)
    for key in ("label_evidence", "model_selection"):
        nested = result.get(key)
        if isinstance(nested, dict):
            result[key] = demand_view(nested)
    return result


def trace_view(value: dict[str, Any] | None) -> dict[str, Any] | None:
    """Project both new and saved traces without mutating their audit snapshots."""
    if value is None:
        return None
    result = dict(value)
    sources = result.get("sources")
    if isinstance(sources, dict):
        sources = dict(sources)
        forecasts = sources.get("forecasts")
        if isinstance(forecasts, list):
            sources["forecasts"] = [demand_view(forecast) for forecast in forecasts]
        result["sources"] = sources
    return result
