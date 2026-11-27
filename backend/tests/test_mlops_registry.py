"""Local MLOps manifest: checksums and lineage, no joblib deserialization."""
from __future__ import annotations

import hashlib
import json

import pytest
from fastapi.testclient import TestClient

from app.api.routes import mlops as mlops_api
from app.main import app
from app.mlops.registry import ArtifactIntegrityError, ArtifactPaths, register, verify


def _artifacts(tmp_path):
    report_file = tmp_path / "forecasting_ml_v1.json"
    preview_file = tmp_path / "forecasting_ml_preview_v1.csv"
    model_file = tmp_path / "lightgbm_global_v1.joblib"
    manifest_file = tmp_path / "lightgbm_global_v1.manifest.json"
    model_file.write_bytes(b"FAKE_TEST_MODEL_BYTES_DO_NOT_DESERIALIZE")
    preview_file.write_text("date,sku_id,warehouse_id,predicted_units\n2026-11-20,S-1,TX-DAL,3\n")
    report_file.write_text(json.dumps({
        "status": "ready", "model_type": "trained_ml_comparison",
        "model_artifact_sha256": hashlib.sha256(model_file.read_bytes()).hexdigest(),
        "model_version": "lightgbm-global-v1", "selected_model": "lightgbm_global",
        "dataset_version": "synthetic-pet-retail-v1", "source": "postgresql",
        "source_sha256": "abc123", "config": {"estimators": 140, "seed": 42},
        "features": ["lag_7", "sku_code"], "future": {"cutoff": "2026-11-19"},
    }))
    return ArtifactPaths(report_file, preview_file, model_file, manifest_file)


def test_manifest_can_be_created_without_retraining_or_deserializing(tmp_path, monkeypatch):
    files = _artifacts(tmp_path)
    import joblib
    monkeypatch.setattr(joblib, "load", lambda *_a, **_k: pytest.fail("must not deserialize model"))
    assert verify(files)["status"] == "not_registered"
    result = register(files)
    assert result["status"] == "registered"
    status = verify(files)
    assert status["status"] == "ready"
    assert status["run_id"] == result["run_id"]
    assert status["dataset_version"] == "synthetic-pet-retail-v1"
    manifest = json.loads(files.manifest.read_text())
    assert all("sha256" in item for item in manifest["artifacts"].values())
    assert manifest["environment"]["python"]
    assert manifest["lineage"]["training_config"]["seed"] == 42


def test_changing_model_breaks_integrity(tmp_path):
    files = _artifacts(tmp_path)
    register(files)
    files.model.write_bytes(files.model.read_bytes() + b"tampered")
    status = verify(files)
    assert status["status"] == "invalid" and "model artifact changed" in status["message"]


def test_changing_forecast_preview_breaks_integrity(tmp_path):
    files = _artifacts(tmp_path)
    register(files)
    files.preview.write_text(files.preview.read_text() + "2026-11-21,S-1,TX-DAL,999999\n")
    assert verify(files)["status"] == "invalid"


def test_training_report_mismatch_does_not_get_registered(tmp_path):
    files = _artifacts(tmp_path)
    report = json.loads(files.report.read_text())
    report["model_artifact_sha256"] = "bad"
    files.report.write_text(json.dumps(report))
    with pytest.raises(ArtifactIntegrityError, match="Model file does not match"):
        register(files)
    assert not files.manifest.exists()


def test_cannot_register_missing_artifact(tmp_path):
    files = _artifacts(tmp_path)
    files.model.unlink()
    with pytest.raises(ArtifactIntegrityError, match="Missing artifact"):
        register(files)


def test_tampered_manifest_lineage_is_detected(tmp_path):
    files = _artifacts(tmp_path)
    register(files)
    manifest = json.loads(files.manifest.read_text())
    manifest["lineage"]["dataset_version"] = "different"
    files.manifest.write_text(json.dumps(manifest))
    assert verify(files)["status"] == "invalid"


def test_read_only_mlops_api(tmp_path, monkeypatch):
    files = _artifacts(tmp_path)
    register(files)
    monkeypatch.setattr(mlops_api, "verify", lambda: verify(files))
    with TestClient(app) as client:
        response = client.get("/api/v1/mlops/model")
        assert response.status_code == 200
        assert response.json()["status"] == "ready"
        assert client.post("/api/v1/mlops/model").status_code == 405
