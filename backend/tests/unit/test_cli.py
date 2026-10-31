from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from app.cli import EXIT_NOT_IMPLEMENTED, cli
from app.warehouse.db import set_db

runner = CliRunner()


@pytest.fixture(autouse=True)
def _reset_db() -> None:
    yield
    set_db(None)


def test_migrate_and_tables(tmp_path: Path) -> None:
    db = tmp_path / "cli.duckdb"
    r = runner.invoke(cli, ["migrate", "--db", str(db)])
    assert r.exit_code == 0, r.output
    assert "bảng" in r.output
    assert db.exists()
    r = runner.invoke(cli, ["tables", "--db", str(db)])
    assert r.exit_code == 0
    assert "bundle_components" in r.output


def test_reset_yes(tmp_path: Path) -> None:
    db = tmp_path / "cli.duckdb"
    assert runner.invoke(cli, ["migrate", "--db", str(db)]).exit_code == 0
    r = runner.invoke(cli, ["reset", "--db", str(db), "--yes"])
    assert r.exit_code == 0, r.output


@pytest.mark.parametrize(
    ("args", "task"),
    [],  # every command is implemented; keep the test for future stubs
)
def test_stub_commands_report_owner_task(args: list[str], task: str) -> None:
    r = runner.invoke(cli, args)
    assert r.exit_code == EXIT_NOT_IMPLEMENTED
    assert "chưa làm" in r.output
    assert task in r.output


@pytest.mark.integration
def test_ingest_rebuild_panel_and_live(tmp_path: Path) -> None:
    from tests.mockdata import mock_dir, require

    require("nho")
    db = tmp_path / "cli.duckdb"
    assert runner.invoke(cli, ["reset", "--db", str(db), "--yes"]).exit_code == 0
    r = runner.invoke(cli, ["ingest", "--db", str(db), "--dir", str(mock_dir("nho"))])
    assert r.exit_code == 0, r.output
    assert "Đã nạp 6/6 file" in r.output
    assert "hourly_panel" in r.output and "86.400" in r.output
    assert "DUP_ORDER" in r.output and "chờ xác nhận" in r.output
    r = runner.invoke(cli, ["ingest", "--db", str(db), "--dir", str(mock_dir("nho"))])
    assert r.exit_code == 0 and "Không có file mới" in r.output
    r = runner.invoke(cli, ["rebuild-panel", "--db", str(db)])
    assert r.exit_code == 0 and "86.400 dòng" in r.output, r.output
    r = runner.invoke(
        cli, ["ingest-live", "--db", str(db), "--dir", str(mock_dir("nho") / "live_11_11")]
    )
    assert r.exit_code == 0, r.output
    assert "Đã nạp phần live mới" in r.output


def test_ingest_missing_dir(tmp_path: Path) -> None:
    r = runner.invoke(cli, ["ingest", "--db", str(tmp_path / "x.duckdb"), "--dir", "nope"])
    assert r.exit_code != 0
