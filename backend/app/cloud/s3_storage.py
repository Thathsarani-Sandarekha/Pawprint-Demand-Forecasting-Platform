"""Explicit S3 sync for verified artifact bundles; never load pickle or promote automatically."""
from __future__ import annotations
import argparse
import json
import os
import re
import tempfile
from pathlib import Path
from app.mlops.registry import ArtifactPaths, verify
from app.data_pipeline.generate import DEFAULT_OUT
from app.data_pipeline.validate import validate_dataset
from app.db.import_dataset import operational_fingerprint
from app.db.models import IMPORT_FILES


def bundle_paths(folder: Path) -> ArtifactPaths:
    names = ArtifactPaths()
    return ArtifactPaths(*(folder / p.name for p in (names.report, names.preview, names.model, names.manifest)))


def _client(client):
    if client is not None:
        return client
    import boto3
    return boto3.client('s3')  # standard credential chain; no static credentials in code


def _prefix(prefix: str, run_id: str) -> str:
    if not re.fullmatch(r'[0-9a-f]{16}', run_id):
        raise ValueError('Expected a 16-character hexadecimal Pawprint run ID')
    return '/'.join(p for p in (prefix.strip('/'), 'runs', run_id) if p)


def upload_bundle(bucket: str, prefix: str = 'pawprint', paths: ArtifactPaths = ArtifactPaths(), *, client=None) -> dict:
    result = verify(paths)
    if result['status'] != 'ready':
        raise ValueError('Refusing to upload unverified artifacts')
    location = _prefix(prefix, result['run_id'])
    s3 = _client(client)
    # The manifest is uploaded last. Do not mutate local files while syncing.
    for file in (paths.report, paths.preview, paths.model, paths.manifest):
        s3.upload_file(str(file), bucket, f'{location}/{file.name}')
    return {'status': 'uploaded', 'run_id': result['run_id'], 'uri': f's3://{bucket}/{location}/', 'files': 4}


def download_bundle(bucket: str, run_id: str, destination: Path, prefix: str = 'pawprint', *, client=None) -> dict:
    location = _prefix(prefix, run_id)
    destination = Path(destination)
    if destination.exists():
        raise FileExistsError('Restore requires a new staging directory; no active-artifact overwrite')
    destination.parent.mkdir(parents=True, exist_ok=True)
    s3 = _client(client)
    with tempfile.TemporaryDirectory(prefix='.s3-download-', dir=destination.parent) as temp:
        folder = Path(temp) / 'bundle'
        folder.mkdir()
        paths = bundle_paths(folder)
        for file in (paths.manifest, paths.report, paths.preview, paths.model):
            s3.download_file(bucket, f'{location}/{file.name}', str(file))
        result = verify(paths)
        if result['status'] != 'ready' or result['run_id'] != run_id:
            raise ValueError('Downloaded bundle failed integrity/run ID verification')
        if destination.exists():
            raise FileExistsError('Restore destination already exists')
        folder.rename(destination)
    return {'status': 'downloaded_verified', 'run_id': run_id, 'destination': str(destination), 'promoted': False}


def upload_dataset(bucket: str, prefix: str = 'pawprint', data_dir: Path = DEFAULT_OUT, *, client=None) -> dict:
    quality = validate_dataset(data_dir, report_path=None)
    digest = operational_fingerprint(data_dir)
    location = '/'.join(p for p in (prefix.strip('/'), 'datasets', digest) if p)
    s3 = _client(client)
    names = [filename for _, filename in IMPORT_FILES] + ['metadata.json']
    for name in names:
        s3.upload_file(str(data_dir / name), bucket, f'{location}/{name}')
    return {'status': 'uploaded', 'dataset_version': quality['dataset_version'], 'dataset_sha256': digest,
            'uri': f's3://{bucket}/{location}/', 'files': len(names), 'evaluation_only_included': False}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=['upload', 'download', 'upload-dataset'])
    p.add_argument('--bucket', default=os.environ.get('PAWPRINT_S3_BUCKET'))
    p.add_argument('--prefix', default=os.environ.get('PAWPRINT_S3_PREFIX', 'pawprint'))
    p.add_argument('--run-id')
    p.add_argument('--directory', type=Path)
    args = p.parse_args()
    if not args.bucket:
        p.error('--bucket or PAWPRINT_S3_BUCKET is required')
    if args.action == 'upload':
        result = upload_bundle(args.bucket, args.prefix, bundle_paths(args.directory) if args.directory else ArtifactPaths())
    elif args.action == 'upload-dataset':
        result = upload_dataset(args.bucket, args.prefix, args.directory or DEFAULT_OUT)
    else:
        if not args.run_id or not args.directory:
            p.error('download requires --run-id and a new --directory')
        result = download_bundle(args.bucket, args.run_id, args.directory, args.prefix)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
