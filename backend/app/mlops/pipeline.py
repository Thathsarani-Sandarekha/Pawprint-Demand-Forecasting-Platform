"""Offline candidate pipeline. Never overwrites the active UI's model/reports."""
from __future__ import annotations
import argparse
import json
import logging
import tempfile
from pathlib import Path
from app.data_pipeline.generate import ROOT
from app.forecasting.baselines import load_observed_sales, prepare_panel
from app.forecasting.lightgbm_model import run_ml, write_ml_artifacts, TrainConfig
from app.mlops.registry import ArtifactPaths, register, verify
from app.mlops.tracking import log_run

LOG = logging.getLogger(__name__)


def train_candidate(*, data_dir: Path | None = None, output: Path = ROOT / 'artifacts' / 'candidates', track: bool = False) -> dict:
    # SQL reader checks the imported dataset; prepare_panel rejects gaps/duplicates.
    frame, source = load_observed_sales(data_dir)
    prepare_panel(frame)
    output.mkdir(parents=True, exist_ok=True)
    folder = Path(tempfile.mkdtemp(prefix='candidate-', dir=output))
    LOG.info('training candidate dataset=%s source=%s', source['dataset_version'], source['source'])
    report, preview, model = run_ml(frame, source, data_dir=data_dir, config=TrainConfig())
    report_file, preview_file, model_file = write_ml_artifacts(report, preview, model, folder, folder)
    paths = ArtifactPaths(report_file, preview_file, model_file, folder / 'lightgbm_global_v1.manifest.json')
    register(paths)
    status = verify(paths)
    if status['status'] != 'ready':
        raise RuntimeError('Candidate failed integrity verification')
    result = {'status': 'candidate_ready', 'candidate_dir': str(folder), 'run_id': status['run_id'],
              'selected_model': report['selected_model'], 'promoted': False}
    if track:
        result['tracking'] = log_run(paths)
    LOG.info('candidate ready run_id=%s promoted=false', status['run_id'])
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path)
    parser.add_argument('--output', type=Path, default=ROOT / 'artifacts' / 'candidates')
    parser.add_argument('--track', action='store_true')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s %(message)s')
    print(json.dumps(train_candidate(data_dir=args.data_dir, output=args.output, track=args.track), indent=2))


if __name__ == '__main__':
    main()
