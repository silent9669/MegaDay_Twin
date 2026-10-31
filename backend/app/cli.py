"""Command line: ``uv run python -m app.cli <command>`` (01 §5, INF-04).

Implemented: ``migrate``, ``reset``, ``seed``, ``serve``, ``tables``, ``ingest``,
``ingest-live``, ``rebuild-panel``, ``eval`` (runs ``python -m eval.eval_data`` in a
subprocess: ``app`` never imports ``eval``).
``estimate-ads`` (iROAS, plan 04). Stubs (exit code 3, message names the owning task): none
left; ``_todo`` stays for future commands.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, NoReturn

import typer

from app.core import messages_vi as vi
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.core.timeutil import iso_local
from app.warehouse import repo
from app.warehouse.db import Database, get_db, set_db

if TYPE_CHECKING:  # the pipeline is imported lazily to keep the CLI start fast
    from app.ingest.pipeline import IngestReport, LiveDeltaReport

EXIT_NOT_IMPLEMENTED = 3

cli = typer.Typer(
    name="megaday",
    help="MegaDay Twin backend CLI.",
    no_args_is_help=True,
    add_completion=False,
)

DbOpt = Annotated[
    Path | None,
    typer.Option("--db", help="Đường dẫn DuckDB (mặc định DUCKDB_PATH trong .env)."),
]


def _db(path: Path | None) -> Database:
    if path is not None:
        set_db(Database(path))
    return get_db()


@cli.callback()
def _main(
    log_level: Annotated[str | None, typer.Option(help="Ghi đè LOG_LEVEL.")] = None,
) -> None:
    s = get_settings()
    configure_logging(log_level or s.log_level, json=s.log_json)


# ------------------------------------------------------------------ implemented
@cli.command()
def migrate(
    db: DbOpt = None,
    seed: Annotated[bool, typer.Option(help="Nạp khai báo mẫu (combo) sau khi migrate.")] = True,
) -> None:
    """Tạo/cập nhật mọi bảng DuckDB (idempotent)."""
    database = _db(db)
    tables = database.migrate()
    typer.echo(vi.CLI_MIGRATE_DONE.format(path=database.path, n_tables=len(tables)))
    if seed:
        n = repo.seed_bundles(database.write)
        typer.echo(vi.CLI_SEED_DONE.format(n=n))
    database.close()


@cli.command()
def reset(
    db: DbOpt = None,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Không hỏi lại.")] = False,
    seed: Annotated[bool, typer.Option(help="Nạp khai báo mẫu sau khi tạo lại.")] = True,
) -> None:
    """Xóa file DuckDB rồi migrate lại từ đầu."""
    database = _db(db)
    if not yes and not typer.confirm(vi.CLI_RESET_CONFIRM.format(path=database.path)):
        typer.echo(vi.CLI_RESET_ABORTED)
        raise typer.Exit(1)
    database.reset()
    typer.echo(vi.CLI_RESET_DONE.format(path=database.path))
    if seed:
        typer.echo(vi.CLI_SEED_DONE.format(n=repo.seed_bundles(database.write)))
    database.close()


@cli.command()
def seed(db: DbOpt = None) -> None:
    """Nạp khai báo onboarding mẫu (combo COMBO-NOICHIEN, WH-02)."""
    database = _db(db)
    database.migrate()
    typer.echo(vi.CLI_SEED_DONE.format(n=repo.seed_bundles(database.write)))
    database.close()


@cli.command()
def tables(db: DbOpt = None) -> None:
    """Liệt kê bảng và số dòng."""
    database = _db(db)
    conn = database.write
    for name in database.migrate():
        typer.echo(f"{name:24s} {repo.count_rows(conn, name):>10d}")
    database.close()


@cli.command()
def serve(
    host: Annotated[str, typer.Option()] = "127.0.0.1",
    port: Annotated[int, typer.Option()] = 8000,
    reload: Annotated[bool, typer.Option(help="Tự nạp lại khi sửa code.")] = False,
) -> None:
    """Chạy API (uvicorn app.main:app)."""
    import uvicorn

    uvicorn.run("app.main:app", host=host, port=port, reload=reload)


# ------------------------------------------------------------------ ingest
def _n(value: int) -> str:
    """Vietnamese thousands separator."""
    return f"{value:,}".replace(",", ".")


def _print_ingest(report: IngestReport, path: Path) -> None:
    files = report.files
    loaded = [f for f in files if f.status == "loaded"]
    skipped = [f for f in files if f.status == "skipped"]
    if not loaded and not [f for f in files if f.status == "failed"]:
        typer.echo(vi.INGEST_NOTHING_NEW.format(path=path))
    else:
        typer.echo(
            vi.INGEST_DONE.format(
                loaded=len(loaded),
                total=len(files),
                path=path,
                seconds=f"{report.duration_ms / 1000:.1f}".replace(".", ","),
                skipped=len(skipped),
            )
        )
    for f in files:
        extra = ""
        if f.issues:
            extra = "  [" + ", ".join(f"{c}×{n}" for c, n in f.issues.items()) + "]"
        typer.echo(
            vi.INGEST_FILE_LINE.format(
                name=f.file_name[:34],
                source=f.source_type or "?",
                status=f.batch_status if f.status != "skipped" else "đã nạp",
                rows=_n(f.rows_in),
                ms=_n(f.duration_ms),
                extra=extra,
            )
        )
        if f.rows_held:
            typer.echo(vi.INGEST_FILE_HELD.format(n=_n(f.rows_held)))
    typer.echo(vi.INGEST_TABLES)
    for table, n in report.table_rows.items():
        typer.echo(f"  {table:24s} {_n(n):>10s}")
    auto = sum(s.get("auto_accepted", 0) for s in report.aliases.values())
    confirmed = sum(s.get("confirmed", 0) for s in report.aliases.values())
    detail = ", ".join(
        f"{src}: {s.get('pending', 0)}/{sum(s.values())}" for src, s in report.aliases.items()
    )
    typer.echo(
        vi.INGEST_ALIASES.format(
            auto=_n(auto), confirmed=_n(confirmed), pending=_n(report.pending_aliases),
            detail=detail,
        )
    )  # fmt: skip
    if report.issues_by_code:
        typer.echo(vi.INGEST_ISSUES)
        for code, n in report.issues_by_code.items():
            typer.echo(f"  {code:24s} {_n(n):>8s}")
    else:
        typer.echo(vi.INGEST_NO_ISSUES)
    if report.panel is not None:
        typer.echo(
            vi.INGEST_PANEL.format(
                rows=_n(report.panel.rows_written),
                skus=report.panel.n_skus,
                ms=_n(report.panel.duration_ms),
            )
        )
    if report.timings_ms:
        typer.echo(
            vi.INGEST_TIMINGS.format(
                detail=", ".join(f"{k} {_n(v)}" for k, v in report.timings_ms.items())
            )
        )


@cli.command()
def ingest(
    dir: Annotated[Path, typer.Option("--dir", help="Thư mục chứa file của shop.")] = Path(
        "../mock-data/out/demo"
    ),
    db: DbOpt = None,
    force: Annotated[bool, typer.Option(help="Nạp lại cả file đã nạp (cùng mã băm).")] = False,
    panel: Annotated[bool, typer.Option(help="Dựng hourly_panel sau khi nạp.")] = True,
) -> None:
    """Nạp file lịch sử (nhận diện → adapter → ghép SKU → warehouse → kiểm lỗi → panel)."""
    from app.ingest.pipeline import ingest_files

    database = _db(db)
    database.migrate()
    report = ingest_files(database.write, dir, force=force, build_panel=panel)
    _print_ingest(report, dir)
    database.close()


def _print_live(report: LiveDeltaReport, path: Path) -> None:
    if report.status == "unchanged":
        typer.echo(vi.LIVE_UNCHANGED.format(path=path))
        return
    if report.status == "empty":
        typer.echo(vi.LIVE_EMPTY.format(path=path))
    else:
        typer.echo(
            vi.LIVE_DONE.format(
                seconds=f"{report.duration_ms / 1000:.1f}".replace(".", ","),
                detail=", ".join(f"{k} +{_n(v)}" for k, v in report.new_rows.items()),
                restart=vi.LIVE_RESTARTED if report.restarted else "",
            )
        )
    if report.tick is not None:
        typer.echo(
            vi.LIVE_TICK.format(
                orders=iso_local(report.tick.orders_inventory_until) or "—",
                ads=iso_local(report.tick.ads_until) or "—",
            )
        )
    if report.ingest is not None:
        _print_ingest(report.ingest, path)


@cli.command("ingest-live")
def ingest_live(
    dir: Annotated[
        Path, typer.Option("--dir", help="Thư mục live (live_stream/ của replay.js).")
    ] = Path("../mock-data/out/demo/live_stream"),
    db: DbOpt = None,
    panel: Annotated[bool, typer.Option(help="Dựng lại hourly_panel cho SKU bị chạm.")] = True,
) -> None:
    """Nạp phần mới của thư mục live (đọc trang_thai.json, con trỏ live_cursor)."""
    from app.ingest.pipeline import ingest_live_delta

    database = _db(db)
    database.migrate()
    report = ingest_live_delta(database.write, dir, build_panel=panel)
    _print_live(report, dir)
    database.close()


@cli.command("rebuild-panel")
def rebuild_panel(db: DbOpt = None) -> None:
    """Dựng lại toàn bộ hourly_panel từ bảng chuẩn."""
    from app.panel.build import build_hourly_panel

    database = _db(db)
    database.migrate()
    res = build_hourly_panel(database.write)
    typer.echo(
        vi.PANEL_REBUILT.format(rows=_n(res.rows_written), skus=res.n_skus, ms=_n(res.duration_ms))
    )
    database.close()


# ------------------------------------------------------------------ stubs
def _todo(command: str, task: str, plan: str) -> NoReturn:
    typer.echo(vi.CLI_NOT_IMPLEMENTED.format(command=command, task=task, plan=plan), err=True)
    raise typer.Exit(EXIT_NOT_IMPLEMENTED)


@cli.command()
def backtest(db: DbOpt = None) -> None:
    """Chạy backtest 6 mốc, ghi model_scores, chọn champion."""
    from app.forecast.service import run_backtests

    database = _db(db)
    database.migrate()
    res = run_backtests(database.write)
    typer.echo(
        vi.BACKTEST_DONE.format(
            scores=_n(len(res.scores)), champions=len(res.champions), s=res.seconds
        )
    )
    database.close()


@cli.command()
def fit(
    db: DbOpt = None,
    horizon: Annotated[int, typer.Option(help="Số giờ dự báo tới.")] = 72,
) -> None:
    """Dự báo cho mọi SKU từ cuối dữ liệu đã nạp, ghi bảng forecasts (cần chạy backtest trước)."""
    from collections import Counter

    from app.forecast.service import forecast_skus

    database = _db(db)
    database.migrate()
    results = forecast_skus(database.write, horizon_hours=horizon)
    labels = Counter(str(r.confidence) for r in results)
    typer.echo(
        vi.FIT_DONE.format(
            n=len(results),
            horizon=horizon,
            labels=", ".join(f"{k} {v}" for k, v in sorted(labels.items())),
        )
    )
    database.close()


def _num(x: float | None, digits: int = 2) -> str:
    return "—" if x is None else f"{x:.{digits}f}".replace(".", ",")


@cli.command("estimate-ads")
def estimate_ads(
    db: DbOpt = None,
    campaign: Annotated[
        list[str] | None, typer.Option("--campaign", help="Chỉ ước lượng các chiến dịch này.")
    ] = None,
    no_stock_rules: Annotated[
        bool,
        typer.Option(
            "--no-stock-rules", help="Không loại giờ sắp hết hàng (chỉ để so sánh, 04 §9)."
        ),
    ] = False,
    write: Annotated[bool, typer.Option(help="Ghi kết quả vào iroas_estimates.")] = True,
) -> None:
    """Ước lượng iROAS cho mọi chiến dịch (04), ghi bảng iroas_estimates."""
    from app.ads_effect.iroas import estimate_all, write_estimates

    database = _db(db)
    database.migrate()
    run = estimate_all(database.write, campaign, apply_stock_rules=not no_stock_rules)
    if not run.estimates:
        typer.echo(vi.ADS_NONE)
        database.close()
        return
    if write:
        write_estimates(database.write, run.estimates)
    no_ads = run.rhythm_source == "no_ads_skus"
    typer.echo(
        vi.ADS_DONE.format(
            n=len(run.estimates),
            s=run.seconds,
            delta=_num(run.delta, 1),
            rhythm=vi.ADS_RHYTHM_NO_ADS if no_ads else vi.ADS_RHYTHM_ALL,
            labels=", ".join(f"{k} {v}" for k, v in sorted(run.labels.items())),
        )
    )
    for e in run.estimates:
        typer.echo(
            vi.ADS_LINE.format(
                cid=e.campaign_id,
                roas=_num(e.platform_roas),
                mean=_num(e.iroas_mean),
                p10=_num(e.iroas_p10),
                p90=_num(e.iroas_p90),
                marg=_num(e.marginal_iroas),
                be=_num(e.breakeven_iroas),
                label=e.confidence,
            )
        )
    database.close()


@cli.command("eval")
def eval_(
    size: Annotated[str, typer.Option("--size", help="Cỡ mock-data: nho | demo.")] = "demo",
    kind: Annotated[
        str, typer.Option("--kind", help="data | forecast | ads | all: lớp cần chấm.")
    ] = "data",
    live_hours: Annotated[int, typer.Option(help="Số giờ live 11.11 để đo thời gian.")] = 3,
) -> None:
    """Chấm bằng đáp án (chạy ``python -m eval.…`` ở tiến trình riêng: code trong app/
    không import eval/)."""
    backend_dir = Path(__file__).resolve().parents[1]
    cmds = {
        "data": [
            sys.executable,
            "-m",
            "eval.eval_data",
            "--size",
            size,
            "--live-hours",
            str(live_hours),
        ],
        "forecast": [sys.executable, "-m", "eval.eval_forecast", "--size", size],
        "ads": [sys.executable, "-m", "eval.eval_ads", "--size", size],
    }
    if kind not in (*cmds, "all"):
        raise typer.BadParameter("--kind phải là data, forecast, ads hoặc all")
    code = 0
    for name in cmds if kind == "all" else (kind,):
        cmd = cmds[name]
        typer.echo(vi.EVAL_RUNNING.format(cmd=" ".join(cmd[1:])))
        code = code or subprocess.call(cmd, cwd=backend_dir)
    raise typer.Exit(code)


def _utf8_console() -> None:
    """Windows consoles/pipes default to cp1252; Vietnamese output needs UTF-8."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def main() -> None:
    _utf8_console()
    cli()


if __name__ == "__main__":
    main()
