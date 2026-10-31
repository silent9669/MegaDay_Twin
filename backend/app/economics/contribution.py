"""Per-unit contribution (ECO-01, plan 05 §2).

``m(sku, t) = eff_price(t) − unit_cost − platform_fee_rate · eff_price(t) − packaging_cost``

* ``eff_price``: the committed price of the day (``sale_commitments.committed_price``)
  when there is one, otherwise the latest *non-commitment* ``price_events`` row on or before
  the day, otherwise ``skus.list_price``. Prices are never assumed to go up during a
  programme (05 §2).
* ``unit_cost`` / ``packaging_cost``: latest ``cost_items`` row with ``valid_from ≤ t``.
  Missing cost **blocks** the computation: the caller gets the SKU in
  :attr:`ContributionTable.missing_cost` and must return a ``scenario`` result with the
  reason :data:`MISSING_COST_REASON` instead of inventing a margin.
* Combo: ``eff_price(combo) − Σ qty·unit_cost(component) − fee − packaging(combo)``.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime

import duckdb

from app.core.money import format_vnd

DEFAULT_FEE_RATE = 0.12
MISSING_COST_REASON = "Thiếu giá vốn"


def contribution(
    eff_price: float, unit_cost: float, packaging_cost: float, fee_rate: float
) -> float:
    """``m`` in VND per unit (can be negative)."""
    return float(eff_price - unit_cost - fee_rate * eff_price - packaging_cost)


def bundle_contribution(
    eff_price: float,
    components: Sequence[tuple[float, int]],
    packaging_cost: float,
    fee_rate: float,
) -> float:
    """Combo ``m``: ``components`` = ``[(unit_cost, qty), …]``."""
    cost = sum(c * q for c, q in components)
    return contribution(eff_price, cost, packaging_cost, fee_rate)


@dataclass(frozen=True)
class CostRow:
    sku_id: str
    unit_cost: int
    packaging_cost: int
    valid_from: datetime


@dataclass(frozen=True)
class SkuEconomics:
    """Contribution of one SKU on one VN day."""

    sku_id: str
    day: date
    eff_price: int
    price_source: str  # commitment|orders|carried_forward|list_price
    unit_cost: int  # combo: Σ qty·component cost
    packaging_cost: int
    fee_rate: float
    m: float
    is_bundle: bool = False

    def describe(self) -> str:
        return (
            f"{self.sku_id}: giá {format_vnd(self.eff_price)} − vốn {format_vnd(self.unit_cost)}"
            f" − phí {format_vnd(round(self.fee_rate * self.eff_price))}"
            f" − đóng gói {format_vnd(self.packaging_cost)} = {format_vnd(round(self.m))}/sp"
        )


@dataclass
class ContributionTable:
    by_key: dict[tuple[str, date], SkuEconomics] = field(default_factory=dict)
    missing_cost: list[str] = field(default_factory=list)
    missing_price: list[str] = field(default_factory=list)
    unit_cost: dict[str, int] = field(default_factory=dict)  # stock-holding cost (for V)

    @property
    def blocked(self) -> bool:
        return bool(self.missing_cost or self.missing_price)

    def m(self, sku_id: str, day: date) -> float:
        return self.by_key[(sku_id, day)].m

    def get(self, sku_id: str, day: date) -> SkuEconomics:
        return self.by_key[(sku_id, day)]

    def reasons(self) -> list[str]:
        out = []
        if self.missing_cost:
            out.append(f"{MISSING_COST_REASON}: {', '.join(sorted(set(self.missing_cost)))}")
        if self.missing_price:
            out.append(f"Thiếu giá bán: {', '.join(sorted(set(self.missing_price)))}")
        return out


# ---------------------------------------------------------------------- DB access
def load_costs(
    conn: duckdb.DuckDBPyConnection, sku_ids: Iterable[str], at: datetime
) -> dict[str, CostRow]:
    """Latest ``cost_items`` row with ``valid_from ≤ at`` per SKU.

    ``valid_from`` is the time the cost file was received. When every row of a SKU was
    received *after* ``at`` (simulating a past or future instant with today's cost file),
    the earliest row is used: it is the best cost the shop has told us (decision log M3)."""
    ids = sorted(set(sku_ids))
    if not ids:
        return {}
    rows = conn.execute(
        "SELECT sku_id, valid_from, unit_cost, packaging_cost FROM cost_items"
        " WHERE list_contains(?, sku_id) ORDER BY sku_id, valid_from",
        [ids],
    ).fetchall()
    out: dict[str, CostRow] = {}
    for sku, vf, uc, pc in rows:
        row = CostRow(str(sku), int(uc), int(pc or 0), vf)
        prev = out.get(row.sku_id)
        if prev is None or vf <= at:
            out[row.sku_id] = row
    return out


def bundle_map(
    conn: duckdb.DuckDBPyConnection, sku_ids: Iterable[str] | None = None
) -> dict[str, list[tuple[str, int]]]:
    """``{combo: [(component, qty), …]}`` from ``bundle_components``."""
    rows = conn.execute(
        "SELECT bundle_sku_id, component_sku_id, qty FROM bundle_components"
        " ORDER BY bundle_sku_id, component_sku_id"
    ).fetchall()
    wanted = set(sku_ids) if sku_ids is not None else None
    out: dict[str, list[tuple[str, int]]] = {}
    for b, c, q in rows:
        if wanted is None or str(b) in wanted:
            out.setdefault(str(b), []).append((str(c), int(q)))
    return out


def price_for_day(
    conn: duckdb.DuckDBPyConnection, sku_id: str, day: date
) -> tuple[int, str] | None:
    """Effective price planned for ``day`` (VN) and its source."""
    row = conn.execute(
        "SELECT value_int FROM sale_commitments WHERE kind = 'committed_price' AND sku_id = ?"
        " AND day_local = ? AND value_int IS NOT NULL LIMIT 1",
        [sku_id, day],
    ).fetchone()
    if row is not None:
        return int(row[0]), "commitment"
    row = conn.execute(
        "SELECT eff_price, source FROM price_events WHERE sku_id = ? AND day_local <= ?"
        " AND source <> 'commitment' ORDER BY day_local DESC LIMIT 1",
        [sku_id, day],
    ).fetchone()
    if row is not None:
        return int(row[0]), "orders" if day == row[1] else "carried_forward"
    row = conn.execute("SELECT list_price FROM skus WHERE sku_id = ?", [sku_id]).fetchone()
    if row is not None and row[0] is not None:
        return int(row[0]), "list_price"
    return None


def load_contributions(
    conn: duckdb.DuckDBPyConnection,
    sku_ids: Iterable[str],
    days: Iterable[date],
    at: datetime,
    fee_rate: float = DEFAULT_FEE_RATE,
) -> ContributionTable:
    """Contribution of every SKU (combos included) on every day of the horizon."""
    skus = sorted(set(sku_ids))
    bundles = bundle_map(conn, skus)
    stock_skus = set(skus) | {c for comps in bundles.values() for c, _ in comps}
    costs = load_costs(conn, stock_skus | set(bundles), at)
    table = ContributionTable()
    for s in stock_skus:
        if s in costs:
            table.unit_cost[s] = costs[s].unit_cost
    day_list = sorted(set(days))
    for sku in skus:
        comps = bundles.get(sku)
        if comps:
            missing = [c for c, _ in comps if c not in costs]
            if missing:
                table.missing_cost.append(sku)
                table.missing_cost.extend(missing)
                continue
            unit_cost = sum(costs[c].unit_cost * q for c, q in comps)
            own = costs.get(sku)
            packaging = (
                own.packaging_cost
                if own is not None
                else sum(costs[c].packaging_cost * q for c, q in comps)
            )
        else:
            if sku not in costs:
                table.missing_cost.append(sku)
                continue
            unit_cost = costs[sku].unit_cost
            packaging = costs[sku].packaging_cost
        for d in day_list:
            pr = price_for_day(conn, sku, d)
            if pr is None:
                table.missing_price.append(sku)
                break
            price, src = pr
            m = contribution(price, unit_cost, packaging, fee_rate)
            table.by_key[(sku, d)] = SkuEconomics(
                sku_id=sku,
                day=d,
                eff_price=price,
                price_source=src,
                unit_cost=unit_cost,
                packaging_cost=packaging,
                fee_rate=fee_rate,
                m=m,
                is_bundle=bool(comps),
            )
    return table
