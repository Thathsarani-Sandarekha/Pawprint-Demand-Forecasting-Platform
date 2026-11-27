from datetime import date
import json
from pathlib import Path
from types import SimpleNamespace
from contextlib import contextmanager
import pytest
from app.mlops.registry import register
from app.mlops.tracking import log_run
from app.mlops.monitoring import monitor
from app.cloud.s3_storage import upload_bundle, download_bundle
from tests.test_mlops_registry import _artifacts


def registered(tmp_path):
    paths = _artifacts(tmp_path)
    report = json.loads(paths.report.read_text())
    report['backtest_summary'] = {'lightgbm_global': {'wape_pct': 29.78, 'bias_pct': None}}
    report['untouched_test'] = {'metrics': {'lightgbm_global': {'wape_pct': 30.45, 'mase': .836}}}
    report['prediction_intervals'] = {'untouched_test_coverage': {'daily': {'coverage_pct': 87.6}}}
    paths.report.write_text(json.dumps(report))
    paths.preview.write_text('date,sku_id,warehouse_id,predicted_units,lower_units,upper_units\n2026-11-20,S-1,TX-DAL,3,1,5\n2026-11-21,S-1,TX-DAL,6,3,9\n')
    return paths, register(paths)['run_id']


class MemoryS3:
    def __init__(self): self.objects = {}; self.writes = []
    def upload_file(self, filename, bucket, key):
        self.objects[bucket, key] = Path(filename).read_bytes(); self.writes.append(key)
    def download_file(self, bucket, key, filename): Path(filename).write_bytes(self.objects[bucket, key])


def test_s3_round_trip_and_manifest_last(tmp_path):
    paths, run_id = registered(tmp_path)
    client = MemoryS3()
    assert upload_bundle('test', paths=paths, client=client)['files'] == 4
    assert client.writes[-1].endswith('.manifest.json')
    dest = tmp_path / 'restored'
    result = download_bundle('test', run_id, dest, client=client)
    assert result['status'] == 'downloaded_verified' and not result['promoted']
    assert (dest / paths.model.name).read_bytes() == paths.model.read_bytes()
    with pytest.raises(FileExistsError): download_bundle('test', run_id, dest, client=client)


def test_s3_corruption_rejected(tmp_path):
    paths, run_id = registered(tmp_path)
    client = MemoryS3(); upload_bundle('test', paths=paths, client=client)
    client.objects['test', f'pawprint/runs/{run_id}/{paths.model.name}'] = b'corrupted'
    with pytest.raises(ValueError, match='integrity'): download_bundle('test', run_id, tmp_path/'restore', client=client)
    assert not (tmp_path/'restore').exists()
    paths.model.write_bytes(b'changed')
    with pytest.raises(ValueError, match='unverified'): upload_bundle('test', paths=paths, client=client)


class FakeMLflow:
    def __init__(self): self.metrics = {}; self.params = {}; self.tags = {}; self.files = []
    def set_tracking_uri(self, uri): self.uri = uri
    def set_experiment(self, name): self.experiment = name
    @contextmanager
    def start_run(self, run_name): yield SimpleNamespace(info=SimpleNamespace(run_id='mock-run'))
    def set_tags(self, tags): self.tags = tags
    def log_params(self, params): self.params = params
    def log_metrics(self, metrics): self.metrics = metrics
    def log_artifact(self, file, artifact_path): self.files.append(Path(file).name)


def test_tracking_report_mapping(tmp_path):
    paths, run_id = registered(tmp_path)
    fake = FakeMLflow()
    result = log_run(paths, tracking_uri='sqlite:///test.db', client=fake)
    assert result['pawprint_run_id'] == run_id
    assert fake.metrics['holdout.lightgbm_global.wape_pct'] == 30.45
    assert fake.metrics['holdout.coverage.daily.coverage_pct'] == 87.6
    assert 'rolling.lightgbm_global.bias_pct' not in fake.metrics
    assert fake.params['seed'] == 42 and len(fake.files) == 4
    paths.preview.write_text('broken')
    with pytest.raises(ValueError, match='verify'): log_run(paths, client=fake)


def test_monitor_maturity(tmp_path):
    paths, _ = registered(tmp_path)
    actual = tmp_path / 'new_sales.csv'
    actual.write_text('date,sku_id,warehouse_id,units_sold,stockout_observed\n2026-11-20,S-1,TX-DAL,4,0\n2026-11-21,S-1,TX-DAL,1,1\n')
    result = monitor(actual, paths, as_of=date(2026,11,20))
    assert result['matched_rows'] == 1 and result['observed_sales']['wape_pct'] == 25
    assert result['observed_sales']['bias_pct'] == -25 and result['observed_sales']['daily_coverage_pct'] == 100
    result = monitor(actual, paths, as_of=date(2026,11,19))
    assert result['status'] == 'no_matured_actuals' and result['observed_sales'] is None
    actual.write_text(actual.read_text() + '2026-11-20,S-1,TX-DAL,5,0\n')
    with pytest.raises(ValueError, match='duplicate'): monitor(actual, paths, as_of=date(2026,11,21))


def test_candidate_keeps_active_artifacts_untouched(tmp_path, monkeypatch):
    from app.mlops import pipeline
    paths, _ = registered(tmp_path)
    before = {p: p.read_bytes() for p in (paths.report, paths.preview, paths.model, paths.manifest)}
    monkeypatch.setattr(pipeline, 'load_observed_sales', lambda _: (None, {'dataset_version':'test', 'source':'test'}))
    monkeypatch.setattr(pipeline, 'prepare_panel', lambda _: None)
    monkeypatch.setattr(pipeline, 'run_ml', lambda *a, **k: ({'selected_model':'lightgbm_global'},None,None))
    def fake_write(report, preview, model, out, model_dir):
        assert out == model_dir and out != tmp_path
        result = []
        for p in (paths.report, paths.preview, paths.model):
            new = out/p.name; new.write_bytes(before[p]); result.append(new)
        return tuple(result)
    monkeypatch.setattr(pipeline, 'write_ml_artifacts', fake_write)
    result = pipeline.train_candidate(output=tmp_path/'candidates')
    assert result['status'] == 'candidate_ready' and result['promoted'] is False
    assert all(p.read_bytes() == content for p,content in before.items())
