"""
Usage, from backend/:
  python -m app.db.import_dataset
  python -m app.db.import_dataset --replace

A matching version+SHA import is skipped. A different dataset requires explicit
--replace. Only the whitelisted operational CSVs are ever imported. The
synthetic evaluation_only/latent_demand_truth.csv.gz file is deliberately ignored.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
from pathlib import Path

from sqlalchemy import select, text

from app.data_pipeline.generate import DEFAULT_OUT
from app.data_pipeline.validate import validate_dataset
from app.db.connection import get_database_url, get_engine
from app.db.models import IMPORT_FILES, dataset_imports, metadata


class ImportFailure(RuntimeError):
    pass


def operational_fingerprint(data_dir: Path) -> str:
    """Hash filenames and bytes, including source metadata, but exclude evaluation-only data."""
    digest = hashlib.sha256()
    for filename in ("metadata.json", *(filename for _, filename in IMPORT_FILES)):
        digest.update(filename.encode("utf-8") + b"\0")
        opener = gzip.open if filename.endswith(".gz") else open
        # Hash decompressed CSV content so gzip timestamp metadata doesn't affect idempotency.
        with opener(data_dir / filename, "rb") as source:
            for block in iter(lambda: source.read(256 * 1024), b""):
                digest.update(block)
    return digest.hexdigest()


def source_row_count(source: Path) -> int:
    opener = gzip.open if source.name.endswith(".gz") else open
    with opener(source, "rt", encoding="utf-8", newline="") as file:
        reader = csv.reader(file)
        next(reader)  # header
        return sum(1 for _ in reader)


def _copy_table(cursor, table_name: str, source: Path) -> None:
    """Postgres COPY streams compressed input without loading 328k rows into memory."""
    table = metadata.tables[table_name]  # fixed allowlist, never user-input SQL
    columns = ", ".join(f'"{column.name}"' for column in table.columns)
    command = f'COPY "{table_name}" ({columns}) FROM STDIN WITH (FORMAT CSV, HEADER TRUE, NULL \'\')'
    opener = gzip.open if source.name.endswith(".gz") else open
    with opener(source, "rb") as file, cursor.copy(command) as writer:
        for block in iter(lambda: file.read(256 * 1024), b""):
            writer.write(block)


def import_dataset(data_dir: Path = DEFAULT_OUT, replace: bool = False, *, engine=None) -> dict:
    data_dir = Path(data_dir)
    # Fail before entering a database transaction if the sources do not reconcile.
    report = validate_dataset(data_dir, report_path=None)
    fingerprint = operational_fingerprint(data_dir)
    source_counts = {name: source_row_count(data_dir / filename) for name, filename in IMPORT_FILES}
    if engine is None:
        url = get_database_url()
        if not url:
            raise ImportFailure("DATABASE_URL is missing. Configure project-root .env first.")
        engine = get_engine(url)

    with engine.begin() as connection:
        if not connection.dialect.name == "postgresql":
            raise ImportFailure("Importer requires PostgreSQL (psycopg 3), not SQLite.")
        if not connection.execute(text("SELECT to_regclass('public.dataset_imports') IS NOT NULL")).scalar_one():
            raise ImportFailure("Schema is missing. From backend/, run: python -m alembic upgrade head")
        previous = connection.execute(select(dataset_imports).where(dataset_imports.c.id == 1)).mappings().first()
        if previous and previous["source_sha256"] == fingerprint and previous["dataset_version"] == report["dataset_version"]:
            for name in ("products", "warehouses", "daily_sales", "inventory_snapshot"):
                actual = int(connection.execute(text(f'SELECT COUNT(*) FROM "{name}"')).scalar_one())
                if actual != source_counts[name]:
                    raise ImportFailure(f"Existing database count differs for {name}: {actual}; inspect data or explicitly --replace")
            return {"status": "skipped", "reason": "already_imported", "dataset_version": report["dataset_version"], "sha256": fingerprint}
        if previous and not replace:
            raise ImportFailure("Database already contains a different data snapshot. Use --replace explicitly to reload.")
        if not previous:
            if connection.execute(text("SELECT EXISTS (SELECT 1 FROM products LIMIT 1)")).scalar_one() and not replace:
                raise ImportFailure("Existing rows without import metadata. Review database; use --replace to reset scenario data.")
        if replace:
            connection.execute(text("TRUNCATE TABLE planner_decisions, stock_lots, inventory_snapshot, purchase_orders, daily_sales, promotions, products, warehouses, suppliers, dataset_imports"))

        raw_cursor = connection.connection.driver_connection.cursor()
        try:
            for name, filename in IMPORT_FILES:
                _copy_table(raw_cursor, name, data_dir / filename)
        finally:
            raw_cursor.close()
        table_counts = {}
        for name, wanted in source_counts.items():
            count = int(connection.execute(text(f'SELECT COUNT(*) FROM "{name}"')).scalar_one())
            table_counts[name] = count
            if count != wanted:
                raise ImportFailure(f"Imported count mismatch for {name}: {count} (expected {wanted}). Transaction rolled back.")
        connection.execute(dataset_imports.insert().values(
            id=1, dataset_version=report["dataset_version"], source_sha256=fingerprint,
            as_of_date=report["data_as_of_date"], quality_report=report,
        ))
        return {"status": "imported", "dataset_version": report["dataset_version"],
                "source": "postgresql", "sha256": fingerprint,
                "table_counts": table_counts, "evaluation_only_imported": False}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--replace", action="store_true", help="Clear and replace this demo's operational tables")
    args = parser.parse_args()
    try:
        result = import_dataset(data_dir=args.data_dir, replace=args.replace)
        print(json.dumps(result, indent=2))
    except (ImportFailure, ValueError, OSError) as exc:
        parser.exit(status=1, message=f"Import failed: {exc}\n")

if __name__ == "__main__":
    main()
