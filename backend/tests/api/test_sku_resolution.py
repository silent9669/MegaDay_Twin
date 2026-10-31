from fastapi.testclient import TestClient

from tests.api.helpers import VIEWER, ok


def test_inventory_code_resolves_only_unambiguous_accepted_aliases(api: TestClient) -> None:
    conn = api.app.state.db.write  # type: ignore[union-attr]
    conn.execute(
        "INSERT INTO sku_aliases (alias_id,source_type,raw_code,raw_name,norm_name,sku_id,status) "
        "VALUES ('resolve-test','inventory','RESOLVE-TEST','Cooker','cooker',"
        "'NOICHIEN2L','confirmed')"
    )
    try:
        path = "/api/v1/sku-resolution?code=RESOLVE-TEST"
        resolved = ok(api.get(path, headers=VIEWER))["data"]
        assert resolved == {"code": "RESOLVE-TEST", "sku": "NOICHIEN2L", "campaign_ids": ["CD002"]}
        for status in ("pending", "rejected"):
            conn.execute("UPDATE sku_aliases SET status=? WHERE alias_id='resolve-test'", [status])
            assert ok(api.get(path, headers=VIEWER))["data"]["sku"] is None
        conn.execute("UPDATE sku_aliases SET status='auto_accepted' WHERE alias_id='resolve-test'")
        assert ok(api.get(path, headers=VIEWER))["data"]["sku"] == "NOICHIEN2L"
        conn.execute(
            "INSERT INTO sku_aliases "
            "(alias_id,source_type,raw_code,raw_name,norm_name,sku_id,status) "
            "VALUES ('resolve-conflict','inventory','RESOLVE-TEST','Other','other',"
            "'GIAYLOT50','confirmed')"
        )
        assert ok(api.get(path, headers=VIEWER))["data"]["sku"] is None
    finally:
        conn.execute(
            "DELETE FROM sku_aliases WHERE alias_id IN ('resolve-test','resolve-conflict')"
        )


def test_canonical_and_missing_codes(api: TestClient) -> None:
    for code, expected in (("NOICHIEN2L", "NOICHIEN2L"), ("UNKNOWN-RESOLVE", None)):
        data = ok(api.get("/api/v1/sku-resolution", params={"code": code}, headers=VIEWER))["data"]
        assert data["sku"] == expected
        if expected is None:
            assert data["campaign_ids"] == []
