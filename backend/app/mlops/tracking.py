"""Log an already-verified forecasting run to MLflow, without fitting again."""
from __future__ import annotations
import argparse
import json
import math
import os
from pathlib import Path
from app.mlops.registry import ArtifactPaths, verify
from app.data_pipeline.generate import ROOT


def log_run(paths: ArtifactPaths = ArtifactPaths(), *, tracking_uri: str | None = None, client=None) -> dict:
    status = verify(paths)
    if status['status'] != 'ready':
        raise ValueError('Register and verify artifacts before tracking')
    if client is None:
        import mlflow as client
    local = ROOT / 'artifacts' / 'mlflow'
    default_local = not tracking_uri and not os.environ.get('MLFLOW_TRACKING_URI')
    if default_local:
        local.mkdir(parents=True, exist_ok=True)
        tracking_uri = 'sqlite:///' + (local / 'tracking.db').as_posix()
    if tracking_uri:
        client.set_tracking_uri(tracking_uri)
    if default_local and client.get_experiment_by_name('pawprint-demand-forecasting') is None:
        client.create_experiment('pawprint-demand-forecasting', artifact_location=(local / 'runs').resolve().as_uri())
    client.set_experiment('pawprint-demand-forecasting')
    report = json.loads(paths.report.read_text(encoding='utf-8'))
    with client.start_run(run_name=status['run_id']) as run:
        client.set_tags({'pawprint.run_id': status['run_id'], 'dataset_version': status['dataset_version'],
                         'model_version': status['model_version'], 'data_source': str(status['data_source']),
                         'dataset_sha256': str(status['dataset_sha256']), 'scope': 'synthetic_observed_sales',
                         'trained_through': status['trained_through']})
        client.log_params(report['config'])
        metrics = {}
        for section, models in [('rolling', report.get('backtest_summary', {})),
                                ('holdout', report.get('untouched_test', {}).get('metrics', {}))]:
            for model, values in models.items():
                for key, value in values.items():
                    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
                        metrics[f'{section}.{model}.{key}'] = value
        def collect(prefix, values):
            for key, value in values.items():
                if isinstance(value, dict):
                    collect(f'{prefix}.{key}', value)
                elif isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
                    metrics[f'{prefix}.{key}'] = value
        collect('holdout.coverage', report.get('prediction_intervals', {}).get('untouched_test_coverage', {}))
        if metrics:
            client.log_metrics(metrics)
        for path in (paths.model, paths.manifest, paths.report, paths.preview):
            client.log_artifact(str(path), artifact_path='verified_bundle')
        return {'status': 'logged', 'run_id': run.info.run_id, 'pawprint_run_id': status['run_id'], 'metrics_logged': len(metrics)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tracking-uri')
    args = parser.parse_args()
    print(json.dumps(log_run(tracking_uri=args.tracking_uri), indent=2))


if __name__ == '__main__':
    main()
