"""Register and verify *trusted, locally generated* ML training artifacts.

This is a checksum-based local model manifest, not an authorization mechanism,
cryptographic signature, MLflow server, or production model registry. Never
unpickle or execute model artifacts when verifying them.

From backend/:
    python -m app.mlops.registry register
    python -m app.mlops.registry verify
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from app.forecasting.lightgbm_model import ML_PREVIEW_FILE, ML_REPORT_FILE, MODEL_FILE

MANIFEST_PATH = MODEL_FILE.parent / "lightgbm_global_v1.manifest.json"
SCHEMA_VERSION = 1


@dataclass(frozen=True)
class ArtifactPaths:
    report: Path = ML_REPORT_FILE
    preview: Path = ML_PREVIEW_FILE
    model: Path = MODEL_FILE
    manifest: Path = MANIFEST_PATH


class ArtifactIntegrityError(ValueError):
    """A required artifact is missing, malformed or inconsistent."""


def _hash_file(path: Path) -> dict:
    if not path.is_file():
        raise ArtifactIntegrityError(f"Missing artifact: {path.name}")
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
            size += len(block)
    if not size:
        raise ArtifactIntegrityError(f"Empty artifact: {path.name}")
    return {"filename": path.name, "sha256": digest.hexdigest(), "bytes": size}


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ArtifactIntegrityError(f"Cannot parse {path.name}") from exc
    if not isinstance(value, dict):
        raise ArtifactIntegrityError(f"Invalid JSON object: {path.name}")
    return value


def _package_versions() -> dict[str, str | None]:
    result: dict[str, str | None] = {"python": platform.python_version()}
    for name in ("lightgbm", "numpy", "pandas", "scikit-learn", "joblib"):
        try:
            result[name] = version(name)
        except PackageNotFoundError:
            result[name] = None
    return result


def _validate_training_report(report: dict, model_hash: str) -> None:
    if report.get("status") != "ready" or report.get("model_type") != "trained_ml_comparison":
        raise ArtifactIntegrityError("Training report is not a completed ML training run")
    if report.get("model_artifact_sha256") != model_hash:
        raise ArtifactIntegrityError("Model file does not match the training report checksum")
    if not isinstance(report.get("config"), dict) or not isinstance(report.get("features"), list):
        raise ArtifactIntegrityError("Training report lacks reproducibility metadata")
    if not report.get("dataset_version") or not report.get("model_version") or not report.get("future", {}).get("cutoff"):
        raise ArtifactIntegrityError("Training report lacks model, dataset or training cutoff")


def register(paths: ArtifactPaths = ArtifactPaths()) -> dict:
    """Create a manifest for an *existing* trained model; never retrain here."""
    checksums = {key: _hash_file(getattr(paths, key)) for key in ("report", "preview", "model")}
    report = _read_json(paths.report)
    _validate_training_report(report, checksums["model"]["sha256"])
    lineage = {
        "model_version": report["model_version"],
        "selected_model": report.get("selected_model"),
        "dataset_version": report["dataset_version"],
        "dataset_sha256": report.get("source_sha256"),
        "data_source": report.get("source"),
        "trained_through": report["future"]["cutoff"],
        "training_config": report["config"],
        "features": report["features"],
    }
    run_fingerprint = hashlib.sha256(json.dumps({"files": checksums, "lineage": lineage},
                                             sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:16]
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_fingerprint,
        "registered_at_utc": datetime.now(timezone.utc).isoformat(),
        "fictional": True,
        "purpose": "Local artifact traceability; not a production model registry",
        "lineage": lineage,
        "environment": _package_versions(),
        "artifacts": checksums,
    }
    paths.manifest.parent.mkdir(parents=True, exist_ok=True)
    temp_path = paths.manifest.with_name(paths.manifest.name + ".tmp")
    temp_path.write_text(json.dumps(manifest, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    temp_path.replace(paths.manifest)
    return {"status": "registered", "run_id": run_fingerprint, "model_version": lineage["model_version"],
            "dataset_version": lineage["dataset_version"], "manifest_path": str(paths.manifest)}


def verify(paths: ArtifactPaths = ArtifactPaths()) -> dict:
    """Verify sizes, checksums and training lineage without loading .joblib."""
    if not paths.manifest.is_file():
        return {"status": "not_registered", "message": "Existing model can still run. Register it with python -m app.mlops.registry register"}
    try:
        manifest = _read_json(paths.manifest)
        if manifest.get("schema_version") != SCHEMA_VERSION:
            raise ArtifactIntegrityError("Unsupported manifest schema version")
        expected = manifest.get("artifacts")
        lineage = manifest.get("lineage")
        if not isinstance(expected, dict) or not isinstance(lineage, dict):
            raise ArtifactIntegrityError("Manifest missing artifact hashes or training lineage")
        for name in ("report", "preview", "model"):
            now = _hash_file(getattr(paths, name))
            if now != expected.get(name):
                raise ArtifactIntegrityError(f"{name} artifact changed since registration")
        report = _read_json(paths.report)
        _validate_training_report(report, expected["model"]["sha256"])
        for field, actual in (
            ("model_version", report["model_version"]),
            ("selected_model", report.get("selected_model")),
            ("dataset_version", report["dataset_version"]),
            ("dataset_sha256", report.get("source_sha256")),
            ("data_source", report.get("source")),
            ("trained_through", report["future"]["cutoff"]),
            ("training_config", report["config"]),
            ("features", report["features"]),
        ):
            if lineage.get(field) != actual:
                raise ArtifactIntegrityError(f"Manifest {field} does not match training report")
    except (ArtifactIntegrityError, OSError, KeyError, TypeError) as exc:
        return {"status": "invalid", "message": str(exc)}
    return {
        "status": "ready", "run_id": manifest.get("run_id"),
        "model_version": lineage["model_version"],
        "dataset_version": lineage["dataset_version"],
        "dataset_sha256": lineage.get("dataset_sha256"),
        "data_source": lineage.get("data_source"),
        "trained_through": lineage["trained_through"],
        "environment": manifest.get("environment", {}),
        "note": "Checksums match local files; this is not proof of ML accuracy, source authenticity, or deployment safety",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Local LightGBM artifact lineage and integrity checks")
    parser.add_argument("action", choices=("register", "verify"))
    parser.add_argument("--report", type=Path, default=ML_REPORT_FILE)
    parser.add_argument("--preview", type=Path, default=ML_PREVIEW_FILE)
    parser.add_argument("--model", type=Path, default=MODEL_FILE)
    parser.add_argument("--manifest", type=Path, default=MANIFEST_PATH)
    args = parser.parse_args()
    paths = ArtifactPaths(args.report, args.preview, args.model, args.manifest)
    try:
        result = register(paths) if args.action == "register" else verify(paths)
    except (ArtifactIntegrityError, OSError, KeyError, TypeError) as exc:
        result = {"status": "invalid", "message": str(exc)}
    print(json.dumps(result, indent=2))
    return 0 if result["status"] in ("ready", "registered") else 1


if __name__ == "__main__":
    sys.exit(main())
