"""Phase 2B offline unit tests; live database loading is a separate manual smoke test."""
from __future__ import annotations

import gzip
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app
from app.db.connection import get_database_url
from app.db.import_dataset import _copy_table, operational_fingerprint
from app.db.models import IMPORT_FILES, metadata


def test_operational_schema_has_expected_tables_and_keys():
    assert {name for name, _ in IMPORT_FILES} == {
        "suppliers", "warehouses", "products", "promotions", "daily_sales",
        "purchase_orders", "inventory_snapshot", "stock_lots",
    }
    assert "dataset_imports" in metadata.tables
    sales_keys = {col.name for col in metadata.tables["daily_sales"].primary_key}
    assert sales_keys == {"date", "sku_id", "warehouse_id"}
    assert {c.name for c in metadata.tables["inventory_snapshot"].primary_key} == {
        "as_of_date", "sku_id", "warehouse_id",
    }
    assert "latent_demand" not in repr(metadata.tables).lower()
    assert all("evaluation_only" not in filename for _, filename in IMPORT_FILES)


def test_fingerprint_ignores_evaluation_only_and_gzip_timestamp(tmp_path: Path):
    (tmp_path / "metadata.json").write_text('{"test":true}', encoding="utf8")
    for _, filename in IMPORT_FILES:
        file = tmp_path / filename
        if filename.endswith(".gz"):
            with gzip.GzipFile(filename=str(file), mode="wb", mtime=2) as handle:
                handle.write(b"header\nvalue\n")
        else:
            file.write_bytes(b"header\nvalue\n")
    first = operational_fingerprint(tmp_path)
    eval_dir = tmp_path / "evaluation_only"
    eval_dir.mkdir()
    (eval_dir / "latent_demand_truth.csv.gz").write_bytes(b"Never import this")
    assert operational_fingerprint(tmp_path) == first
    file = tmp_path / "daily_sales.csv.gz"
    with gzip.GzipFile(filename=str(file), mode="wb", mtime=15) as handle:
        handle.write(b"header\nvalue\n")
    assert operational_fingerprint(tmp_path) == first
    (tmp_path / "products.csv").write_bytes(b"changed\n")
    assert operational_fingerprint(tmp_path) != first


def test_copy_whitelisted_columns_streams_input_without_dataframe(tmp_path):
    csv_file = tmp_path / "suppliers.csv"
    csv_file.write_bytes(b"supplier_id,supplier_name,lead_time_days,min_order_qty,case_pack\nSUP-1,Example,5,12,6\n")

    class Writer:
        def __init__(self):
            self.output = bytearray()
        def __enter__(self):
            return self
        def __exit__(self, exc_type, exc, tb):
            return False
        def write(self, payload):
            self.output.extend(payload)

    class Cursor:
        def __init__(self):
            self.writer = Writer()
        def copy(self, command):
            assert command.startswith('COPY "suppliers" ("supplier_id"')
            assert "FROM STDIN" in command
            return self.writer
    cursor = Cursor()
    _copy_table(cursor, "suppliers", csv_file)
    assert b"SUP-1,Example,5,12,6" in cursor.writer.output


def test_database_status_nonconfigured(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with TestClient(app) as client:
        response = client.get("/api/v1/database/status")
        assert response.status_code == 200
        assert response.json() == {
            "status": "not_configured", "configured": False, "source": "postgresql"
        }
        # Database configuration should not break Phase 2A's CSV endpoints.
        resp = client.get("/api/v1/health")
        assert resp.status_code == 200


def test_connection_url_rejects_unexpected_schemes(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:////tmp/test.db")
    try:
        get_database_url()
    except ValueError as exc:
        assert "postgresql+psycopg" in str(exc)
    else:
        assert False, "Expected invalid scheme to be rejected"
