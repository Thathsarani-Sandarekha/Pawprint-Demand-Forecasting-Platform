"""Synthetic-data quality, repeatability, API and corruption tests."""
import gzip
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.data_pipeline.generate import build_dataset
from app.data_pipeline.validate import DatasetValidationError, validate_dataset
from app.main import app


@pytest.fixture
def dataset(tmp_path: Path):
    path = tmp_path / "synthetic"
    report = tmp_path / "quality.json"
    build_dataset(n_skus=6, days=35, seed=42, end_date="2026-11-19", out=path)
    return path, report


def test_dataset_passes_reconciliation(dataset):
    path, report = dataset
    result = validate_dataset(path, report)
    assert result["status"] == "passed"
    assert result["daily_sales_rows"] == 6 * 3 * 35
    assert result["sku_count"] == 6
    assert result["issues"] == []
    assert report.is_file()
    assert (path / "evaluation_only" / "latent_demand_truth.csv.gz").exists()


def test_generator_is_repeatable(tmp_path):
    one, two = tmp_path / "one", tmp_path / "two"
    build_dataset(n_skus=4, days=35, seed=17, out=one)
    build_dataset(n_skus=4, days=35, seed=17, out=two)
    df1 = pd.read_csv(one / "daily_sales.csv.gz")
    df2 = pd.read_csv(two / "daily_sales.csv.gz")
    pd.testing.assert_frame_equal(df1, df2)


def test_validation_rejects_incorrect_stock_balance(dataset):
    path, report = dataset
    df = pd.read_csv(path / "daily_sales.csv.gz")
    df.loc[0, "closing_stock"] += 10
    df.to_csv(path / "daily_sales.csv.gz", index=False, compression="gzip")
    with pytest.raises(DatasetValidationError, match="stock balance"):
        validate_dataset(path, report)


def test_data_api_summary_products_history(dataset, monkeypatch):
    path, report = dataset
    validate_dataset(path, report)
    from app.api.routes import data as data_route
    monkeypatch.setattr(data_route, "DEFAULT_OUT", path)
    monkeypatch.setattr(data_route, "REPORT", report)
    monkeypatch.setattr(data_route, "_db_ready", lambda: False)
    with TestClient(app) as client:
        summary = client.get("/api/v1/data/summary")
        assert summary.status_code == 200
        assert summary.json()["status"] == "ready"
        assert summary.json()["sku_count"] == 6
        products = client.get("/api/v1/data/products", params={"warehouse_id": "TX-DAL", "limit": 2})
        assert products.status_code == 200
        assert len(products.json()["items"]) == 2
        history = client.get("/api/v1/data/history", params={"warehouse_id": "TX-DAL", "sku_id": "DOG-FOOD-15LB-001", "days": 7})
        assert history.status_code == 200
        assert len(history.json()["records"]) == 7
        assert "latent_demand" not in history.text
        assert client.get("/api/v1/data/history", params={"sku_id": "BAD-SKU"}).status_code == 404


def test_product_browser_can_request_entire_catalog(dataset, monkeypatch):
    """UI explorer must not silently truncate a 150-product catalog at 100."""
    path, report = dataset
    validate_dataset(path, report)
    from app.api.routes import data as data_route
    monkeypatch.setattr(data_route, "DEFAULT_OUT", path)
    monkeypatch.setattr(data_route, "REPORT", report)
    monkeypatch.setattr(data_route, "_db_ready", lambda: False)
    with TestClient(app) as client:
        response = client.get("/api/v1/data/products", params={"warehouse_id": "TX-DAL", "limit": 200})
        assert response.status_code == 200
        assert response.json()["total"] == 6
        assert len(response.json()["items"]) == 6
        assert all("sku_id" in item and "product_name" in item for item in response.json()["items"])
        assert client.get("/api/v1/data/products", params={"limit": 201}).status_code == 422
        assert client.get("/api/v1/data/products", params={"warehouse_id": "BAD-WH"}).status_code == 422
