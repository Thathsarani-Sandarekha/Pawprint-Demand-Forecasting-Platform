"use client";

import { useCallback, useEffect, useState } from "react";

type Recommendation = {
  sku_id: string;
  warehouse_id: string;
  review_period_days: number;
  snapshot_hash: string;
  recommended_order_units: number;
  requires_manual_review: boolean;
  minimum_order_qty: number;
  case_pack: number;
  model_version: string;
};
type Saved = {
  decision_id: string; snapshot_hash: string; action: "approve" | "override";
  model_version?: string; as_of_date?: string;
  recommended_units: number; approved_units: number;
  reason: string | null; created_at: string; reviewer: string;
};
const api = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000";

export default function PlannerReview({ recommendation }: { recommendation: Recommendation }) {
  const [reason, setReason] = useState("");
  const [override, setOverride] = useState(String(recommendation.recommended_order_units));
  const [history, setHistory] = useState<Saved[]>([]);
  const [historyLoading, setHistoryLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const saved = history.find(item => item.snapshot_hash === recommendation.snapshot_hash) ?? null;

  const refreshHistory = useCallback(async (signal?: AbortSignal) => {
    const response = await fetch(`${api}/api/v1/inventory/decisions?sku_id=${encodeURIComponent(recommendation.sku_id)}&warehouse_id=${encodeURIComponent(recommendation.warehouse_id)}`, { cache: "no-store", signal });
    if (!response.ok) throw new Error("Planner history is unavailable");
    const result = await response.json();
    if (!Array.isArray(result.records)) throw new Error("Unexpected planner history format");
    return result.records as Saved[];
  }, [recommendation.sku_id, recommendation.warehouse_id]);

  useEffect(() => {
    const controller = new AbortController();
    setHistoryLoading(true);
    setHistoryError(null);
    setHistory([]);
    setReason("");
    setOverride(String(recommendation.recommended_order_units));
    refreshHistory(controller.signal)
      .then(rows => { if (!controller.signal.aborted) setHistory(rows); })
      .catch(() => { if (!controller.signal.aborted) setHistoryError("Planner decision history is unavailable. Review submissions are disabled until the backend responds."); })
      .finally(() => { if (!controller.signal.aborted) setHistoryLoading(false); });
    return () => controller.abort();
  }, [refreshHistory, recommendation.recommended_order_units]);

  async function submit(action: "approve" | "override") {
    if (busy || saved || historyLoading || historyError) return;
    setError(null);
    if (action === "override" && !/^\d+$/.test(override)) {
      setError("Enter a nonnegative whole number of units."); return;
    }
    const quantity = action === "override" ? Number(override) : recommendation.recommended_order_units;
    if (action === "override" && (!Number.isSafeInteger(quantity) || quantity > 1_000_000 || quantity === recommendation.recommended_order_units)) {
      setError("Enter a different whole-number quantity of up to 1,000,000 units."); return;
    }
    if (action === "override" && quantity > 0 && (quantity < recommendation.minimum_order_qty || quantity % recommendation.case_pack !== 0)) {
      setError(`Overrides must be zero or at least ${recommendation.minimum_order_qty} and divisible by ${recommendation.case_pack}.`); return;
    }
    if (action === "approve" && recommendation.requires_manual_review && !reason.trim()) {
      setError("Add a review note explaining how you assessed the flagged warnings."); return;
    }
    if (action === "override" && reason.trim().length < 8) {
      setError("Overrides require a reason of at least eight characters."); return;
    }
    setBusy(true);
    try {
      const response = await fetch(`${api}/api/v1/inventory/decisions`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          sku_id: recommendation.sku_id, warehouse_id: recommendation.warehouse_id,
          review_period_days: recommendation.review_period_days,
          snapshot_hash: recommendation.snapshot_hash, action,
          approved_units: quantity, reason: reason.trim() || null,
        }),
      });
      const body = await response.json();
      if (!response.ok) {
        setError(typeof body.detail === "string" ? body.detail : "Review could not be recorded; please refresh.");
        // On conflicts, a different planner may have just reviewed the snapshot.
        if (response.status === 409) {
          const rows = await refreshHistory();
          setHistory(rows);
        }
      } else {
        // Read back the durable PostgreSQL row, do not rely solely on browser state.
        try { setHistory(await refreshHistory()); }
        catch { setHistory([body as Saved, ...history]); setHistoryError("Saved, but the full decision history could not be refreshed."); }
      }
    } catch {
      setError("Unable to reach the service; no review was confirmed. Reload history before retrying to avoid duplicate submissions.");
      setHistoryError("Review history needs to be refreshed before another submission.");
    } finally { setBusy(false); }
  }

  return <section className="planner-review" aria-label="Purchasing review">
    <div className="planner-top"><div><strong>Record purchasing decision</strong><p>Approve or override this recommendation. Your decision is saved in the planning record. No purchase order will be sent.</p></div><span className="baseline-chip">Planning only</span></div>
    {saved ? <div className="planner-saved" role="status"><strong>Review already recorded — {saved.action === "approve" ? "approved" : "overridden"}</strong>
      <div>{saved.approved_units.toLocaleString("en-US")} units · {saved.reviewer} · {new Date(saved.created_at).toLocaleString("en-US")}</div>
      {saved.reason && <p>Reason: {saved.reason}</p>}
      <small>No second decision is permitted for the same product, warehouse, model, and snapshot.</small></div> : <>
      <div className="planner-fields"><label>Decision quantity <span>Must respect supplier MOQ / case pack</span>
          <input inputMode="numeric" type="number" min="0" step={recommendation.case_pack} value={override} onChange={e => setOverride(e.target.value)} disabled={busy || historyLoading || !!historyError}/></label>
        <label>Review / override rationale <span>Required for overrides and flagged recommendations</span>
          <textarea maxLength={500} rows={3} value={reason} onChange={e => setReason(e.target.value)} disabled={busy || historyLoading || !!historyError} placeholder="Explain purchasing judgment, supplier or expiry review…"/></label></div>
      <div className="planner-actions"><button disabled={busy || historyLoading || !!historyError} onClick={() => submit("approve")}>Approve {recommendation.recommended_order_units} units →</button><button className="planner-secondary" disabled={busy || historyLoading || !!historyError} onClick={() => submit("override")}>Record override</button></div>
      {error && <div className="planner-error" role="alert">{error}</div>}
      <p className="planner-disclaimer">Shared local review workspace. Individual user identities are not verified.</p>
    </>}
    <div className="review-history">
      <div className="review-history-head"><div><strong>Decision history</strong><p>Most recent 20 reviews for this selected SKU and warehouse</p></div>
        <button className="history-refresh" type="button" disabled={busy || historyLoading} onClick={async () => {
          setHistoryLoading(true); setHistoryError(null); setError(null);
          try { setHistory(await refreshHistory()); } catch { setHistoryError("Unable to load planner history. Try again or contact your administrator."); }
          finally { setHistoryLoading(false); }
        }}>{historyLoading ? "Loading…" : "Refresh history"}</button>
      </div>
      {historyError && <div className="planner-error" role="alert">{historyError}</div>}
      {historyLoading ? <p className="review-muted">Loading saved decisions…</p> : history.length === 0 ? <p className="review-muted">No saved decisions for this product and location yet.</p> :
        <div className="warehouse-scroll"><table className="replay-table review-history-table"><thead><tr><th>Recorded</th><th>Action</th><th>Planning snapshot</th><th>Suggested</th><th>Saved units</th><th>Reason</th></tr></thead>
          <tbody>{history.map(item => <tr key={item.decision_id}><td>{new Date(item.created_at).toLocaleString("en-US")}</td><td><strong>{item.action === "approve" ? "Approved" : "Overridden"}</strong></td><td>{item.as_of_date ?? "Snapshot date unavailable"}</td><td>{item.recommended_units.toLocaleString("en-US")}</td><td>{item.approved_units.toLocaleString("en-US")}</td><td>{item.reason ?? "—"}</td></tr>)}</tbody></table></div>}
    </div>
  </section>;
}
