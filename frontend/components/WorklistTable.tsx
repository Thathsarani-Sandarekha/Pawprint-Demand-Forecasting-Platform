"use client";

import { useMemo, useState } from "react";
import { CategoryGlyph } from "./WorkspaceIcons";

export type WorklistItem = {
  sku_id: string; warehouse_id: string; product_name: string; category: string;
  warehouse_name: string; supplier_name: string;
  priority: "expedite" | "review" | "reorder" | "covered";
  on_hand_units: number; on_order_units: number; demand_next_28_days: number;
  recommended_order_units: number; lead_time_days: number; new_order_expected_arrival: string;
  projected_shortage_days_no_order: number; projected_shortage_units_no_order: number;
  first_shortage_date_no_order: string | null; shortage_days_with_order: number;
  expiring_lot_units: number; manual_review_required: boolean;
  review_state: "unreviewed" | "approve" | "override"; reviewed_units: number | null;
};
const fmt = (n: number) => new Intl.NumberFormat("en-US", {maximumFractionDigits: 0}).format(n);
const labels: Record<WorklistItem["priority"], string> = {
  expedite: "Time-critical", review: "Review needed", reorder: "Reorder", covered: "Covered",
};
export default function WorklistTable({ rows, compact = false, initialWarehouse = "all", initialPriority = "all", initialReview = "all", initialExpiry = false, initialCategory = "all" }: { rows: WorklistItem[]; compact?: boolean; initialWarehouse?: string; initialPriority?: string; initialReview?: string; initialExpiry?: boolean; initialCategory?: string }) {
  const [query, setQuery] = useState("");
  const [warehouse, setWarehouse] = useState(initialWarehouse);
  const [priority, setPriority] = useState(Object.keys(labels).includes(initialPriority) ? initialPriority : "all");
  const [category, setCategory] = useState(rows.some(r=>r.category===initialCategory) ? initialCategory : "all");
  const [review, setReview] = useState(["all","pending","recorded"].includes(initialReview) ? initialReview : "all");
  const [expiry, setExpiry] = useState(initialExpiry);
  const [page, setPage] = useState(1);
  const [sort, setSort] = useState("priority");
  const categories = useMemo(() => Array.from(new Set(rows.map(r=>r.category))).sort(), [rows]);
  const warehouses = useMemo(() => Array.from(new Map(rows.map(r=>[r.warehouse_id, r.warehouse_name] as const)).entries()), [rows]);
  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    const match = rows.filter(r => (!q || `${r.product_name} ${r.sku_id} ${r.supplier_name}`.toLowerCase().includes(q)) &&
      (warehouse === "all" || r.warehouse_id === warehouse) &&
      (priority === "all" || r.priority === priority) &&
      (category === "all" || r.category === category) &&
      (review === "all" || (review === "pending" ? r.review_state === "unreviewed" && r.priority !== "covered" : r.review_state !== "unreviewed")) &&
      (!expiry || r.expiring_lot_units > 0));
    if (sort === "shortage") return [...match].sort((a,b) => b.projected_shortage_days_no_order-a.projected_shortage_days_no_order || b.recommended_order_units-a.recommended_order_units);
    if (sort === "quantity") return [...match].sort((a,b) => b.recommended_order_units-a.recommended_order_units);
    return match; // Server has already sorted using consistent priority rules.
  }, [rows, query, warehouse, priority, category, sort, review, expiry]);
  const pageSize = compact ? 5 : 20;
  const pageCount = Math.max(1, Math.ceil(filtered.length/pageSize));
  const current = Math.min(page,pageCount);
  const start = compact ? 0 : (current-1)*pageSize;
  function reset(){setQuery("");setWarehouse("all");setPriority("all");setCategory("all");setSort("priority");setReview("all");setExpiry(false);setPage(1)}
  function exportCsv() {
    const header = ["SKU", "Product", "Warehouse", "Category", "Priority", "On hand", "Incoming", "28d forecast", "Suggested units", "Projected shortage days without order", "Expiry units", "ETA", "Review state", "Reviewed units"];
    const cell = (x: string | number) => { const v = String(x); const safe = /^[\s]*[=+@-]/.test(v) && !/^-?\d+(\.\d+)?$/.test(v) ? `'${v}` : v; return `"${safe.replaceAll('"', '""')}"`; };
    const body = filtered.map(r => [r.sku_id, r.product_name, r.warehouse_name, r.category, labels[r.priority], r.on_hand_units, r.on_order_units, r.demand_next_28_days, r.recommended_order_units, r.projected_shortage_days_no_order, r.expiring_lot_units, r.new_order_expected_arrival, r.review_state, r.reviewed_units ?? ""].map(cell).join(","));
    const blob = new Blob([[header.map(cell).join(","), ...body].join("\r\n")], { type:"text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a"); a.href = url; a.download = "pawprint-replenishment-review.csv"; document.body.appendChild(a); a.click(); a.remove();
    URL.revokeObjectURL(url);
  }
  return <div className="ops-worklist">
    {!compact && <div className="worklist-tabs" aria-label="Review status">{[["all","All items"],["pending","Awaiting review"],["recorded","Reviewed"]].map(([id,label])=><button type="button" key={id} aria-pressed={review===id} className={review===id?"selected":""} onClick={()=>{setReview(id);setPage(1)}}>{label}</button>)}</div>}
    {!compact && <div className="ops-toolbar">
      <label className="ops-search"><span>Search products</span><input value={query} onChange={e=>{setQuery(e.target.value);setPage(1)}} placeholder="Name, SKU or supplier" aria-label="Search products, SKU or supplier"/></label>
      <label><span>Warehouse</span><select aria-label="Filter by warehouse" value={warehouse} onChange={e=>{setWarehouse(e.target.value);setPage(1)}}><option value="all">All locations</option>{warehouses.map(([id,name])=><option value={id} key={id}>{name}</option>)}</select></label>
      <label><span>Priority</span><select aria-label="Filter by priority" value={priority} onChange={e=>{setPriority(e.target.value);setPage(1)}}><option value="all">All priorities</option>{Object.entries(labels).map(([id,name])=><option value={id} key={id}>{name}</option>)}</select></label>
      {!compact && <label><span>Category</span><select aria-label="Filter by category" value={category} onChange={e=>{setCategory(e.target.value);setPage(1)}}><option value="all">All categories</option>{categories.map(c=><option key={c} value={c}>{c}</option>)}</select></label>}
      {!compact && <label><span>Sort by</span><select value={sort} onChange={e=>{setSort(e.target.value);setPage(1)}}><option value="priority">Attention first</option><option value="shortage">Projected shortage days</option><option value="quantity">Suggested order size</option></select></label>}
      {!compact && <button className="ops-button ops-button-ghost" type="button" onClick={exportCsv}>Export filtered CSV ↓</button>}
    </div>}
    <div className="ops-table-info"><span>{filtered.length ? `${start+1}–${Math.min(start+pageSize,filtered.length)} of ${filtered.length}` : "0"} matching product-locations</span>{compact ? <a href="/inventory?review=pending">Open full queue →</a> : <div className="filter-extra"><label><input type="checkbox" checked={expiry} onChange={e=>{setExpiry(e.target.checked);setPage(1)}}/> Expiring lots only</label><button type="button" onClick={reset}>Clear filters</button></div>}</div>
    <div className="ops-table-scroll"><table className="ops-table"><thead><tr><th>Item &amp; location</th><th>Priority</th><th>On hand / incoming</th><th>28-day forecast</th><th>Without order</th><th>Suggested order</th><th>Decision log</th><th aria-label="Open detail"/></tr></thead><tbody>
      {filtered.slice(start,start+pageSize).map(r => <tr key={`${r.sku_id}:${r.warehouse_id}`}>
        <td><div className="item-cell"><span className="item-icon" data-category={r.category.toLowerCase().replaceAll(" ","-")} aria-hidden="true"><CategoryGlyph category={r.category}/></span><div><strong>{r.product_name}</strong><small>{r.sku_id} · {r.warehouse_name}</small></div></div></td>
        <td><span className={`ops-priority ${r.priority}`}>{labels[r.priority]}</span>{r.expiring_lot_units > 0 && <small>{fmt(r.expiring_lot_units)} units in expiring lots</small>}</td>
        <td>{fmt(r.on_hand_units)} <span className="ops-soft">/ +{fmt(r.on_order_units)}</span></td>
        <td>{fmt(r.demand_next_28_days)}</td>
        <td>{r.projected_shortage_days_no_order ? <strong className="ops-risk">{r.projected_shortage_days_no_order} shortage days</strong> : <span className="ops-success">None projected</span>}</td>
        <td><strong className="ops-order">{fmt(r.recommended_order_units)} units</strong><small>{r.lead_time_days}d supplier lead</small></td>
        <td>{r.review_state === "unreviewed" ? <span className="ops-soft">Not reviewed</span> : <span className="ops-success">{r.review_state === "approve" ? "Approved" : "Overridden"} · {fmt(r.reviewed_units ?? 0)}</span>}</td>
        <td><a className="ops-rowlink" href={`/inventory?sku_id=${encodeURIComponent(r.sku_id)}&warehouse_id=${encodeURIComponent(r.warehouse_id)}`} aria-label={`Review ${r.product_name} in ${r.warehouse_name}`}>Review ↗</a></td>
      </tr>)}
      {filtered.length===0 && <tr><td colSpan={8} className="ops-empty">No items match these filters. Try another warehouse or clear your search.</td></tr>}
    </tbody></table></div>
    {!compact && <div className="table-pagination"><span>Page {current} of {pageCount}</span><div><button type="button" disabled={current===1} onClick={()=>setPage(current-1)}>← Previous</button><button type="button" disabled={current===pageCount} onClick={()=>setPage(current+1)}>Next →</button></div></div>}
    <div className="ops-table-disclaimer">Priorities and shortages are estimates from dated forecasts and deterministic simulations—not probabilities or confirmed lost sales. No orders are sent.</div>
  </div>;
}
