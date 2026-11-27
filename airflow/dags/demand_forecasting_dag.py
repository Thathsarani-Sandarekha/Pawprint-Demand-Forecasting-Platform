"""Optional Airflow 2.x adapter. Configure PAWPRINT_BACKEND_DIR in the worker.
Requires project dependencies in worker Python environment.
Candidate only: no automatic publication and no purchasing actions.
"""
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from airflow import DAG
from airflow.operators.python import PythonOperator


def run_candidate():
    backend = Path(os.environ['PAWPRINT_BACKEND_DIR']).resolve(strict=True)
    subprocess.run([sys.executable, '-m', 'app.mlops.pipeline', '--track'], cwd=backend, check=True)


with DAG(dag_id='pawprint_weekly_forecasting_candidate',
         start_date=datetime(2026, 11, 1, tzinfo=timezone.utc), schedule='@weekly',
         catchup=False, max_active_runs=1, default_args={'retries': 1, 'retry_delay': timedelta(minutes=5)},
         tags=['pawprint', 'forecasting', 'mlops']) as dag:
    train_evaluate_register = PythonOperator(task_id='train_evaluate_register_candidate',
                                              python_callable=run_candidate,
                                              execution_timeout=timedelta(hours=2))
