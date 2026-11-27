"""Local-demo planner review persistence. No authentication or purchasing integration.

The caller must supply the exact current recommendation fingerprint. We re-read
all facts / model predictions before writing to reject stale browser previews.
Never interpret these records as sent purchase orders.
"""
from __future__ import annotations

from datetime import date
import hashlib
import json
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.db.connection import get_database_url, get_engine


class DecisionConflict(ValueError):
    """A newer/different planner decision exists for this snapshot."""


def fingerprint(recommendation: dict) -> str:
    """Bind approval to the material facts and calculated quantities shown."""
    material = {k: recommendation[k] for k in (
        'sku_id', 'warehouse_id', 'model_version', 'as_of_date',
        'review_period_days', 'lead_time_days', 'on_hand_units',
        'reserved_units', 'backorder_units', 'on_order_units',
        'on_order_arriving_within_window', 'demand_protection_window',
        'safety_stock_units', 'minimum_order_qty', 'case_pack',
        'recommended_order_units', 'requires_manual_review',
    )}
    # Forecast intervals and the exact vector also matter: changing forecasts
    # with an unchanged aggregate must not retain an old approval token.
    for k in ('forecast_signature', 'incoming_signature'):
        if k in recommendation:
            material[k] = recommendation[k]
    return hashlib.sha256(json.dumps(material, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def validate_choice(recommendation: dict, action: str, approved_units: int | None, reason: str | None) -> int:
    if action not in ('approve', 'override'):
        raise ValueError('Unknown planner action')
    recommended = recommendation['recommended_order_units']
    if action == 'approve':
        if approved_units is not None and approved_units != recommended:
            raise ValueError('Approval must match recommended units')
        if recommendation['requires_manual_review'] and not (reason and reason.strip()):
            raise ValueError('A review note is required when warnings demand manual review')
        return recommended
    if approved_units is None or isinstance(approved_units, bool) or approved_units < 0 or approved_units > 1000000:
        raise ValueError('Override requires an integer from 0 to 1,000,000')
    if approved_units == recommended:
        raise ValueError('Override must change the recommended quantity')
    if approved_units and (approved_units < recommendation['minimum_order_qty'] or approved_units % recommendation['case_pack']):
        raise ValueError('Override must respect the minimum order and case-pack multiple')
    if not reason or len(reason.strip()) < 8:
        raise ValueError('Override reason must contain at least eight characters')
    return approved_units


def _to_dict(row) -> dict:
    item = dict(row)
    item['as_of_date'] = item['as_of_date'].isoformat()
    item['created_at'] = item['created_at'].isoformat()
    item['record_type'] = 'planner_review_only_not_purchase_order'
    return item


def save_choice(recommendation: dict, action: str, approved_units: int | None, reason: str | None) -> dict:
    qty = validate_choice(recommendation, action, approved_units, reason)
    key = fingerprint(recommendation)
    url = get_database_url()
    if url is None:
        raise RuntimeError('PostgreSQL is required')
    values = {
        'id': str(uuid4()), 'sku': recommendation['sku_id'], 'wh': recommendation['warehouse_id'],
        'asof': date.fromisoformat(recommendation['as_of_date']),
        'model': recommendation['model_version'], 'review': recommendation['review_period_days'],
        'fingerprint': key, 'action': action, 'recommended': recommendation['recommended_order_units'],
        'qty': qty, 'reason': (reason or '').strip() or None,
        'reviewer': 'local-demo-planner',
    }
    with get_engine(url).begin() as conn:
        found = conn.execute(text('''
            SELECT * FROM planner_decisions WHERE sku_id=:sku AND warehouse_id=:wh
              AND as_of_date=:asof AND model_version=:model AND review_period_days=:review
            FOR UPDATE
        '''), values).mappings().first()
        if found:
            if (found['snapshot_hash'] == key and found['action'] == action and
                found['approved_units'] == qty and (found['reason'] or None) == values['reason']):
                return _to_dict(found)  # Repeating an identical request is idempotent.
            raise DecisionConflict('This SKU/warehouse snapshot has already been reviewed; no second decision was written')
        try:
            # Nested transaction isolates a unique-key race; transaction stays intact.
            with conn.begin_nested():
                conn.execute(text('''
                  INSERT INTO planner_decisions
                    (decision_id,sku_id,warehouse_id,as_of_date,model_version,review_period_days,
                     snapshot_hash,action,recommended_units,approved_units,reason,reviewer)
                  VALUES (:id,:sku,:wh,:asof,:model,:review,:fingerprint,:action,:recommended,:qty,:reason,:reviewer)
                '''), values)
        except IntegrityError:
            raise DecisionConflict('A concurrent planner review already exists; refresh to inspect it') from None
        row = conn.execute(text('SELECT * FROM planner_decisions WHERE decision_id=:id'), values).mappings().one()
        return _to_dict(row)


def list_choices(sku_id: str, warehouse_id: str) -> list[dict]:
    url = get_database_url()
    if url is None:
        raise RuntimeError('PostgreSQL is required')
    with get_engine(url).connect() as conn:
        rows = conn.execute(text('''
          SELECT * FROM planner_decisions WHERE sku_id=:sku AND warehouse_id=:wh
          ORDER BY created_at DESC, decision_id DESC LIMIT 20
        '''), {'sku': sku_id, 'wh': warehouse_id}).mappings().all()
    return [_to_dict(row) for row in rows]
