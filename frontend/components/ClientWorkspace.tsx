import WorklistTable, { type WorklistItem } from "./WorklistTable";
import DemandHistoryChart from "./DemandHistoryChart";
import PlannerReview from "./PlannerReview";
import { PawMark } from "./PetArtwork";
import { CategoryGlyph, Glyph } from "./WorkspaceIcons";

export type ClientView = "overview" | "inventory" | "forecasts" | "evaluation";
type Worklist = { status: "ready" | "partial"; as_of_date: string; model_version: string; synthetic_data: boolean; summary: {
  sku_locations_evaluated: number; requires_attention: number; expedite_count: number; review_count: number;
  replenishment_count: number; total_suggested_units: number; expiry_review_count: number; recorded_reviews:number; awaiting_review:number;
}; rows: WorklistItem[]; issues: {sku_id:string;warehouse_id:string;issue:string}[] };
type Summary = { status:string; sku_count?:number; warehouse_count?:number; data_as_of_date?: string; warehouse_metrics?:{ warehouse_id:string; city:string; state:string; on_hand_units:number; stockout_flag_rate:number}[] };
type ForecastPoint = {date:string;predicted_units:number;lower_units?:number;upper_units?:number};
type MLSeries = { train_through:string; nominal_coverage_pct:number; sku_id:string; warehouse_id:string; model_version:string; forecast:ForecastPoint[]; weekly_forecast:{week:number;start_date:string;end_date:string;predicted_units:number;lower_units:number;upper_units:number}[] };
type InventoryRecommendation = {status:string;sku_id:string;warehouse_id:string;product_name:string;supplier_name:string;model_version:string;as_of_date:string;forecast_start:string;new_order_expected_arrival:string;lead_time_days:number;review_period_days:number;protection_end:string;on_hand_units:number;reserved_units:number;backorder_units:number;on_order_units:number;on_order_arriving_within_window:number;inventory_position_for_window:number;demand_next_28_days:number;demand_protection_window:number;safety_stock_units:number;target_stock_units:number;unconstrained_order_units:number;minimum_order_qty:number;case_pack:number;recommended_order_units:number;expiry_status:string;expiring_current_lot_units:number;point_without_new_order:{shortage_days:number;estimated_unmet_units:number;first_shortage_date:string|null};point_with_recommendation:{shortage_days:number;estimated_unmet_units:number};stress_25pct_with_recommendation:{shortage_days:number;estimated_unmet_units:number};requires_manual_review:boolean;snapshot_hash:string;warnings:string[]};
type Product = {sku_id:string;product_name:string;category?:string};
type History = { records:{date:string;units_sold:number;stockout_observed:boolean;promo_active:boolean}[]};
type MLStatus = {status:string;model_version?:string;model_type?:string;dataset_version?:string;untouched_test?:{test_start:string;test_end:string;metrics:Record<string,{wape_pct:number;bias_pct?:number;mase?:number}>};prediction_intervals?:{nominal_coverage_pct:number;untouched_test_coverage:{daily:{coverage_pct:number};weekly:{coverage_pct:number}}}};
type Replay = {status:string;evaluation_start?:string;evaluation_end?:string;policy_comparison?:Record<string,{fill_rate_pct:number;stockout_day_pct:number;average_ending_stock_units_per_series:number;holding_cost_usd:number;units_ordered:number}>;limitations?:string[]};


const API = process.env.API_BASE_URL ?? "http://127.0.0.1:8000";
const num = (v:number) => new Intl.NumberFormat("en-US",{maximumFractionDigits:0}).format(v);
const dec = (v:number|undefined, digits=1) => v == null ? "—" : v.toFixed(digits);
async function read<T>(path:string):Promise<T|null> {
  try {
    const response = await fetch(`${API}/api/v1/${path}`,{cache:"no-store",signal:AbortSignal.timeout(15000)});
    return response.ok ? await response.json() as T : null;
  } catch {return null;}
}
const hubs = [{warehouse_id:"TX-DAL",name:"Dallas, TX"},{warehouse_id:"CA-RIV",name:"Riverside, CA"},{warehouse_id:"PA-ALL",name:"Allentown, PA"}];
const paths: {key:ClientView;href:string;title:string;sub:string;icon:string}[] = [
  {key:"overview",href:"/",title:"Overview",sub:"Planning overview",icon:"overview"},
  {key:"inventory",href:"/inventory",title:"Replenishment",sub:"Purchasing worklist",icon:"box"},
  {key:"forecasts",href:"/forecasts",title:"Demand planning",sub:"Product outlook",icon:"trend"},
  {key:"evaluation",href:"/evaluation",title:"Planning reports",sub:"Planning insights",icon:"report"},
];


export default async function ClientWorkspace({view,skuId,warehouseId,priority,review,expiry,category}:{view:ClientView;skuId?:string;warehouseId?:string;priority?:string;review?:string;expiry?:string;category?:string}) {
  const validSku = skuId && /^[A-Z0-9-]{2,80}$/.test(skuId) ? skuId : undefined;
  const selectedWarehouse = hubs.some(h=>h.warehouse_id===warehouseId) ? warehouseId! : "TX-DAL";
  const selectedSku = validSku ?? "DOG-FOOD-15LB-001";
  const isDetail = view === "inventory" && Boolean(validSku);
  const [worklist,summary,ml,replay,products,inventory,history,series] = await Promise.all([
    view === "overview" || (view === "inventory" && !isDetail) ? read<Worklist>("inventory/worklist") : Promise.resolve(null),
    view === "overview" ? read<Summary>("data/summary") : Promise.resolve(null),
    view === "evaluation" ? read<MLStatus>("forecasting/ml/status") : Promise.resolve(null),
    view === "evaluation" ? read<Replay>("inventory/simulation/ml/status") : Promise.resolve(null),
    view === "forecasts" ? read<{items:Product[]}>(`data/products?limit=200&warehouse_id=${encodeURIComponent(selectedWarehouse)}`) : Promise.resolve(null),
    isDetail ? read<InventoryRecommendation>(`inventory/recommendation?sku_id=${encodeURIComponent(selectedSku)}&warehouse_id=${encodeURIComponent(selectedWarehouse)}`) : Promise.resolve(null),
    view === "forecasts" ? read<History>(`data/history?sku_id=${encodeURIComponent(selectedSku)}&warehouse_id=${encodeURIComponent(selectedWarehouse)}&days=90`) : Promise.resolve(null),
    view === "forecasts" ? read<MLSeries>(`forecasting/ml/series?sku_id=${encodeURIComponent(selectedSku)}&warehouse_id=${encodeURIComponent(selectedWarehouse)}`) : Promise.resolve(null),
  ]);
  const date = worklist?.as_of_date ?? summary?.data_as_of_date ?? inventory?.as_of_date ?? series?.train_through;
  const issues = worklist?.issues?.length ?? 0;
  const whName = hubs.find(h=>h.warehouse_id===selectedWarehouse)?.name;
  const selectedProduct = products?.items.find(p=>p.sku_id===selectedSku);
  const pageTitle = paths.find(p=>p.key===view)?.title;
  const pairQuery = `sku_id=${encodeURIComponent(selectedSku)}&warehouse_id=${encodeURIComponent(selectedWarehouse)}`;
  const maxWape = Math.max(1, ...Object.values(ml?.untouched_test?.metrics??{}).map(m=>m.wape_pct));
  const money = (v:number) => v.toLocaleString("en-US",{style:"currency",currency:"USD"});
  return <div className="ops-shell">
    <a className="skip-link" href="#main-content">Skip to content</a>
    <header className="workspace-header"><div className="header-inner">
      <a className="ops-brand" href="/" aria-label="Pawprint home"><span className="ops-brand-icon"><PawMark width={22} height={22}/></span><span><strong>pawprint<span>.</span></strong><small>Pet supply operations</small></span></a>
      <nav className="ops-nav" aria-label="Workspace navigation">{paths.map(p=><a key={p.key} href={p.href} className={view===p.key?"active":""} aria-current={view===p.key?"page":undefined}><Glyph name={p.icon}/>{p.title}</a>)}</nav>
      <div className="header-context"><span className="demo-label"><span/>Synthetic data</span><span className="region-label">US network</span></div>
    </div></header>
    <div className="workspace-context"><div><span>Supply planning</span><span className="breadcrumb-sep">/</span><strong>{pageTitle}</strong>{isDetail&&<><span className="breadcrumb-sep">/</span><span>Review</span></>}</div><div>{date ? <>Data as of <strong>{date}</strong></> : replay?.evaluation_end ? <>Report through <strong>{replay.evaluation_end}</strong></> : ""}</div></div>
    <main className="ops-main" id="main-content">
      {view==="overview" && <>
        <PageTitle eyebrow="NETWORK OPERATIONS" title="Inventory overview" subtitle="Prioritize replenishment and protect product availability across your fulfillment network." action={<a className="ops-button ops-button-main" href="/inventory">Open replenishment <Glyph name="arrow"/></a>}/>
        {!worklist ? <Unavailable label="Inventory overview"/> : <>
          {issues>0 && <div className="ops-warning" role="alert">{issues} product-location combinations could not be evaluated. Totals exclude these items.</div>}
          <div className="ops-kpis">
            <Kpi tone="orange" label="Awaiting review" value={num(worklist.summary.awaiting_review)} hint="Attention items without a decision" icon="clipboard" href="/inventory?review=pending"/>
            <Kpi tone="red" label="Urgent supply risks" value={num(worklist.summary.expedite_count)} hint="Shortage projected before standard arrival" icon="alert" href="/inventory?priority=expedite"/>
            <Kpi tone="green" label="Suggested purchase units" value={num(worklist.summary.total_suggested_units)} hint={`${num(worklist.summary.replenishment_count)} positive recommendations`} icon="box" href="/inventory"/>
            <Kpi tone="purple" label="Expiry checks" value={num(worklist.summary.expiry_review_count)} hint="Product-locations with expiring lots" icon="clock" href="/inventory?expiry=yes"/>
          </div>
          <section className="ops-section priority-section"><SectionHead title="Priority purchasing queue" subtitle="Unreviewed items that need attention, ordered by urgency" action={<a className="ops-button ops-button-ghost" href="/inventory?review=pending">View all <Glyph name="arrow"/></a>}/><WorklistTable rows={worklist.rows.filter(r=>r.priority!=="covered"&&r.review_state==="unreviewed")} compact/></section>
          <div className="overview-network">
            <section className="ops-card network-card"><SectionHead title="Fulfillment network" subtitle="Planning exposure by warehouse"/><div className="hub-list">{hubs.map(h=>{const entries=worklist.rows.filter(r=>r.warehouse_id===h.warehouse_id);const pending=entries.filter(r=>r.priority!=="covered"&&r.review_state==="unreviewed").length;const urgent=entries.filter(r=>r.priority==="expedite").length;return <a className="hub-row" key={h.warehouse_id} href={`/inventory?warehouse_id=${h.warehouse_id}`}><span className="hub-icon"><Glyph name="warehouse"/></span><span className="hub-label"><strong>{h.name}</strong><small>{entries.length} evaluated product-locations</small></span><span className="hub-metric"><strong>{num(pending)}</strong><small>To review</small></span><span className="hub-metric urgent"><strong>{num(urgent)}</strong><small>Urgent</small></span><Glyph name="chevron"/></a>})}</div></section>
            <section className="ops-card category-card"><SectionHead title="Pet category exposure" subtitle="Unreviewed attention items · share of each category’s locations"/><div className="category-list">{Array.from(new Set(worklist.rows.map(r=>r.category))).sort().map(c=>{const rows=worklist.rows.filter(r=>r.category===c);const n=rows.filter(r=>r.priority!=="covered"&&r.review_state==="unreviewed").length;return <a className="category-row" key={c} href={`/inventory?category=${encodeURIComponent(c)}&review=pending`}><span className="category-icon"><CategoryGlyph category={c}/></span><span className="category-name">{c}<span className="category-track"><i style={{width:`${rows.length?100*n/rows.length:0}%`}}/></span></span><strong>{num(n)}</strong></a>})}</div></section>
          </div>

          <div className="network-footer"><span><Glyph name="box"/> {num(worklist.summary.sku_locations_evaluated)} evaluated product-locations</span><span><Glyph name="clipboard"/> {num(worklist.summary.recorded_reviews)} decisions recorded</span></div>
        </>}
      </>}
      {view==="inventory"&&!isDetail&&<>
        <PageTitle eyebrow="PURCHASING" title="Replenishment worklist" subtitle="Review purchasing recommendations, supplier constraints and inventory risks."/>
        {!worklist?<Unavailable label="Replenishment worklist"/>:<>
          {issues>0&&<div className="ops-warning" role="alert">{issues} product-location combinations could not be evaluated. They are excluded, not considered covered.</div>}
          <div className="ops-kpis ops-kpis-three"><Kpi tone="orange" label="Awaiting review" value={num(worklist.summary.awaiting_review)} hint="Unreviewed attention items · entire network" icon="clipboard" href="/inventory?review=pending"/><Kpi tone="red" label="Urgent supply risks" value={num(worklist.summary.expedite_count)} hint="Projected pre-arrival shortages · entire network" icon="alert" href="/inventory?priority=expedite"/><Kpi tone="green" label="Suggested units" value={num(worklist.summary.total_suggested_units)} hint="All evaluated recommendations · entire network" icon="box" href="/inventory"/></div>
          <WorklistTable rows={worklist.rows} initialWarehouse={warehouseId&&hubs.some(h=>h.warehouse_id===warehouseId)?warehouseId:"all"} initialPriority={priority} initialReview={review} initialExpiry={expiry==="yes"} initialCategory={category}/>
        </>}
      </>}
      {view==="inventory"&&isDetail&&<>
        <div className="ops-back"><a href="/inventory">← Replenishment worklist</a></div>
        {!inventory?<Unavailable label="Selected replenishment calculation"/>:<>
          <PageTitle eyebrow={`${inventory.sku_id} / ${whName}`} title={inventory.product_name} subtitle={`Supplier: ${inventory.supplier_name}`} action={<a className="ops-button ops-button-ghost" href={`/forecasts?${pairQuery}`}>View demand forecast <Glyph name="arrow"/></a>}/>
          <div className="detail-layout"><section className="ops-card order-hero"><div className="order-hero-top"><span className="order-icon"><Glyph name="box"/></span><span className={`ops-priority ${inventory.requires_manual_review?"review":"reorder"}`}>{inventory.requires_manual_review?"Manual review required":"Recommendation"}</span></div><span className="order-label">Suggested purchase quantity</span><div className="ops-big-number">{num(inventory.recommended_order_units)} <small>units</small></div><div className="order-eta"><Glyph name="truck"/><div><span>Expected arrival</span><strong>{inventory.new_order_expected_arrival}</strong></div><div><span>Supplier lead time</span><strong>{inventory.lead_time_days} days</strong></div></div><p>Planning recommendation only. No order has been sent.</p></section><section className="ops-card stock-summary"><SectionHead title="Stock & demand" subtitle="Current planning snapshot"/><div className="stock-grid">{[["On hand",inventory.on_hand_units],["Incoming orders",inventory.on_order_units],["Reserved / backordered",`${num(inventory.reserved_units)} / ${num(inventory.backorder_units)}`],["28-day forecast",inventory.demand_next_28_days]].map(([l,v])=><div key={l}><span>{l}</span><strong>{typeof v==="number"?num(v):v}</strong></div>)}</div><div className="stock-caption">{inventory.expiry_status}</div></section></div>
          <div className="ops-detail-grid"><section className="ops-card"><SectionHead title="Order calculation" subtitle={`Protection period through ${inventory.protection_end}`}/><div className="ops-lines">{[["Forecast demand during protection period",inventory.demand_protection_window],["Safety stock buffer",inventory.safety_stock_units],["Target stock",inventory.target_stock_units],["Less inventory position (free stock + timely receipts)",inventory.inventory_position_for_window],["Suggested order after supplier constraints",inventory.recommended_order_units]].map(([l,v],i)=><div key={l} className={i===4?"ops-lines-final":""}><span>{l}</span><strong>{num(Number(v))}</strong></div>)}</div><div className="constraint-grid"><div><span>Review interval</span><strong>{inventory.review_period_days} days</strong></div><div><span>Minimum order</span><strong>{num(inventory.minimum_order_qty)}</strong></div><div><span>Case pack</span><strong>{num(inventory.case_pack)}</strong></div><div><span>Timely incoming units</span><strong>{num(inventory.on_order_arriving_within_window)}</strong></div></div></section>
          <section className="ops-card"><SectionHead title="Availability scenarios" subtitle="Projected shortages over the next 28 days"/><Scenario label="Without a new order" detail="Expected demand" value={inventory.point_without_new_order.shortage_days}/><Scenario label="With suggested order" detail="Expected demand" value={inventory.point_with_recommendation.shortage_days}/><Scenario label="With suggested order" detail="Demand 25% higher" value={inventory.stress_25pct_with_recommendation.shortage_days}/>{inventory.point_without_new_order.first_shortage_date&&<p className="ops-muted">First projected shortage without order: {inventory.point_without_new_order.first_shortage_date}</p>}<p className="ops-muted">Scenarios are estimates, not stockout probabilities or guaranteed service levels.</p></section></div>
          {inventory.warnings.length>0&&<section className="ops-card warning-panel"><SectionHead title="Purchasing notes"/><ul>{inventory.warnings.map(w=><li key={w}>{w.replace(" ","")}</li>)}</ul></section>}
          <PlannerReview key={`${inventory.sku_id}-${inventory.warehouse_id}-${inventory.snapshot_hash}`} recommendation={{sku_id:inventory.sku_id,warehouse_id:inventory.warehouse_id,review_period_days:inventory.review_period_days,snapshot_hash:inventory.snapshot_hash,recommended_order_units:inventory.recommended_order_units,requires_manual_review:inventory.requires_manual_review,minimum_order_qty:inventory.minimum_order_qty,case_pack:inventory.case_pack,model_version:inventory.model_version}}/>
        </>}
      </>}
      {view==="forecasts"&&<>
        <PageTitle eyebrow="DEMAND PLANNING" title="Demand forecast" subtitle="Explore product sales history and the next four weeks of expected demand."/>
        <section className="ops-card ops-picker"><form action="/forecasts" method="get"><label><span>Product</span><select name="sku_id" defaultValue={selectedSku} disabled={!products?.items.length}>{products?.items.map(p=><option key={p.sku_id} value={p.sku_id}>{p.product_name} · {p.sku_id}</option>)}</select></label><label><span>Fulfillment center</span><select name="warehouse_id" defaultValue={selectedWarehouse}>{hubs.map(h=><option key={h.warehouse_id} value={h.warehouse_id}>{h.name}</option>)}</select></label><button className="ops-button ops-button-main" disabled={!products?.items.length} type="submit">View forecast <Glyph name="arrow"/></button></form></section>
        {!series||!history||!selectedProduct?<Unavailable label="Demand forecast"/>:<>
          <section className="ops-card forecast-banner"><span className="product-icon"><CategoryGlyph category={selectedProduct.category??""}/></span><div><span className="ops-overline">{selectedProduct.category??"Pet supplies"} / {selectedSku}</span><h2>{selectedProduct.product_name}</h2><p>{whName} · {series.forecast[0]?.date} to {series.forecast.at(-1)?.date}</p></div><div className="forecast-total"><span>28-day expected demand</span><strong>{num(series.forecast.reduce((a,r)=>a+r.predicted_units,0))}<small> units</small></strong></div><a className="ops-button ops-button-ghost" href={`/inventory?${pairQuery}`}>Review replenishment <Glyph name="arrow"/></a></section>
          <DemandHistoryChart history={history.records} forecast={series.forecast} name={selectedProduct.product_name}/>
          <section className="ops-section"><SectionHead title="Weekly demand outlook" subtitle={`Expected units and nominal ${series.nominal_coverage_pct??90}% forecast ranges`}/><div className="ops-week-grid">{series.weekly_forecast.map(w=><div key={w.week}><span>Week {w.week}</span><small>{w.start_date} – {w.end_date}</small><strong>{num(w.predicted_units)} <small>units</small></strong><div className="week-track"><i style={{width:`${100*w.predicted_units/Math.max(1,...series.weekly_forecast.map(v=>v.predicted_units))}%`}}/></div><div className="ops-interval">Range: {num(w.lower_units)}–{num(w.upper_units)}</div></div>)}</div></section>
        </>}
      </>}
      {view==="evaluation"&&<>
        <PageTitle eyebrow="PLANNING REPORTS" title="Planning performance" subtitle="Assess forecast reliability and the service–inventory trade-off on historical sample data."/>
        {ml?.status!=="ready"?<Unavailable label="Forecast reliability report"/>:<>
          <div className="ops-kpis ops-kpis-three"><Kpi tone="green" label="Forecast error (WAPE)" value={pct(ml.untouched_test?.metrics?.lightgbm_global?.wape_pct,2)} hint="Lower is better · historical test" icon="trend"/><Kpi tone="orange" label="Forecast bias" value={pct(ml.untouched_test?.metrics?.lightgbm_global?.bias_pct,2)} hint="Positive = overforecasting" icon="report"/><Kpi tone="purple" label="Weekly range coverage" value={pct(ml.prediction_intervals?.untouched_test_coverage?.weekly?.coverage_pct,1)} hint={`Target ${ml.prediction_intervals?.nominal_coverage_pct??90}% nominal coverage`} icon="clock"/></div>
          <section className="ops-card report-card"><SectionHead title="Historical forecast comparison" subtitle={`${ml.untouched_test?.test_start??""} – ${ml.untouched_test?.test_end??""}`}/><div className="report-bars">{Object.entries(ml.untouched_test?.metrics??{}).map(([id,m])=><div className="report-bar-row" key={id}><span>{modelLabel(id)}</span><div className="report-track"><i className={id==="lightgbm_global"?"chosen":""} style={{width:`${100*Math.max(0,m.wape_pct)/maxWape}%`}}/></div><strong>{pct(m.wape_pct,2)}</strong></div>)}</div><p className="ops-muted">WAPE measures total absolute forecast error relative to observed sales. Sales may understate true demand on stockout days.</p></section>
        </>}
        {replay?.status==="ready"&&replay.policy_comparison?<section className="ops-section ops-card report-card"><SectionHead title="Inventory policy comparison" subtitle={`Historical simulation · ${replay.evaluation_start} – ${replay.evaluation_end}`}/><div className="ops-table-scroll"><table className="ops-table"><thead><tr><th>Planning approach</th><th>Observed-sales fill rate</th><th>Shortage-day rate</th><th>Avg. units held / location</th><th>Estimated holding cost</th></tr></thead><tbody>{Object.entries(replay.policy_comparison).map(([id,m])=><tr key={id}><td><strong>{modelLabel(id)}</strong></td><td>{pct(m.fill_rate_pct,2)}</td><td>{pct(m.stockout_day_pct,2)}</td><td>{num(m.average_ending_stock_units_per_series)}</td><td>{money(m.holding_cost_usd)}</td></tr>)}</tbody></table></div><p className="ops-muted">Retrospective simulation with fixed supplier lead times and illustrative carrying costs. These are not actual customer service levels or realized savings.</p></section>:<Unavailable label="Inventory policy report"/>}
      </>}
    </main>
    <footer className="ops-footer"><span>Pawprint · Supply operations</span><span>Synthetic planning data · Decisions are recorded locally; no supplier orders are transmitted.</span></footer>
  </div>;
}
function pct(v:number|undefined,d=1){return v==null?"—":`${dec(v,d)}%`}
function modelLabel(id:string){return ({seasonal_naive_7:"Same weekday baseline",moving_average_28:"Recent sales average",lightgbm_global:"ML demand forecast",simple_moving_average:"Basic replenishment",buffered_moving_average:"Replenishment with buffer",lightgbm_buffered:"ML forecast with buffer"} as Record<string,string>)[id]??id.replaceAll("_"," ")}
function PageTitle({eyebrow,title,subtitle,action}:{eyebrow:string;title:string;subtitle:string;action?:React.ReactNode}){return <div className="ops-page-title"><div><span className="ops-overline">{eyebrow}</span><h1>{title}</h1><p>{subtitle}</p></div>{action}</div>}
function SectionHead({title,subtitle,action}:{title:string;subtitle?:string;action?:React.ReactNode}){return <div className="ops-section-head"><div><h2>{title}</h2>{subtitle&&<p>{subtitle}</p>}</div>{action}</div>}
function Kpi({label,value,hint,tone,icon,href}:{label:string;value:string;hint:string;tone:string;icon:string;href?:string}){const content=<><div className="ops-kpi-top"><span className="kpi-icon"><Glyph name={icon}/></span>{href&&<Glyph name="chevron"/>}</div><span className="kpi-label">{label}</span><strong>{value}</strong><small>{hint}</small></>;return href?<a href={href} className={`ops-kpi ops-tone-${tone}`}>{content}</a>:<div className={`ops-kpi ops-tone-${tone}`}>{content}</div>}
function Scenario({label,detail,value}:{label:string;detail:string;value:number}){return <div className={`ops-scenario ${value?"has-shortage":"no-shortage"}`}><div><strong>{label}</strong><span>{detail}</span></div><b>{value} shortage days</b></div>}
function Unavailable({label}:{label:string}){return <div className="ops-unavailable" role="alert"><Glyph name="alert"/><div><strong>{label} unavailable</strong><p>We couldn’t load the current planning data. Refresh the page or contact your system administrator.</p></div></div>}
