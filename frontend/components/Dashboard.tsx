import PlannerReview from "./PlannerReview";
import ProductWarehouseSelector from "./ProductWarehouseSelector";
import DemandHistoryChart from "./DemandHistoryChart";
import { PawMark, PetArtwork } from "./PetArtwork";
type ProductItem = { sku_id: string; product_name: string; category?: string };
type ProductsPayload = { items: ProductItem[]; total: number; warehouse_id: string };
type HistoryPayload = { sku_id: string; warehouse_id: string; records: { date: string; units_sold: number; closing_stock: number; stockout_observed: boolean; promo_active: boolean }[] };
type RegistryInfo = { status: string; run_id?: string; model_version?: string; dataset_version?: string; trained_through?: string; dataset_sha256?: string; data_source?: string };
type BackendStatus = "connected" | "offline";
type BaselineMetrics = { wape_pct: number | null; bias_pct?: number | null; mase?: number | null; };
type ForecastStatus = {
  status: "ready" | "not_run" | "stale" | "invalid" | "unavailable";
  selected_baseline?: string;
  model_type?: string;
  source?: string;
  dataset_version?: string;
  horizon_days?: number;
  backtest_summary?: Record<string, BaselineMetrics>;
  untouched_test?: { test_start: string; test_end: string; metrics: Record<string, BaselineMetrics> };
  future?: { cutoff: string; forecast_start: string; forecast_end: string };
  message?: string;
};
type CoverageMetric = { coverage_pct: number; nonstockout_coverage_pct?: number | null; observations: number; mean_width_units: number };
type MLStatus = ForecastStatus & {
  selected_model?: string;
  model_version?: string;
  feature_importance?: Record<string, number>;
  prediction_intervals?: {
    nominal_coverage_pct: number;
    validation_coverage: { daily: CoverageMetric; weekly: CoverageMetric };
    untouched_test_coverage: { daily: CoverageMetric; weekly: CoverageMetric };
  };
};
type ForecastSeries = {
  sku_id: string;
  warehouse_id: string;
  model: string;
  forecast: { date: string; predicted_units: number; lower_units?: number; upper_units?: number }[];
  weekly_forecast?: { week: number; start_date: string; end_date: string; predicted_units: number; lower_units: number; upper_units: number }[];
  nominal_coverage_pct?: number;
};
type InventoryScenario = {
  shortage_days: number;
  first_shortage_date: string | null;
  estimated_unmet_units: number;
  ending_available_units: number;
};
type InventoryDecision = {
  status: string;
  sku_id: string;
  warehouse_id: string;
  product_name: string;
  supplier_name: string;
  model_version: string;
  as_of_date: string;
  new_order_expected_arrival: string;
  lead_time_days: number;
  review_period_days: number;
  protection_end: string;
  on_hand_units: number;
  reserved_units: number;
  backorder_units: number;
  on_order_units: number;
  on_order_arriving_within_window: number;
  inventory_position_for_window: number;
  demand_next_28_days: number;
  demand_protection_window: number;
  safety_stock_units: number;
  target_stock_units: number;
  unconstrained_order_units: number;
  minimum_order_qty: number;
  case_pack: number;
  recommended_order_units: number;
  expiry_status: string;
  expiring_current_lot_units: number;
  point_without_new_order: InventoryScenario;
  point_with_recommendation: InventoryScenario;
  stress_25pct_with_recommendation: InventoryScenario;
  requires_manual_review: boolean;
  snapshot_hash: string;
  decision_state: string;
  warnings: string[];
};
type PolicyMetric = {
  fill_rate_pct: number | null; stockout_day_pct: number;
  average_ending_stock_units_per_series: number; holding_cost_usd: number;
  units_ordered: number; orders_placed: number; series_count: number;
};
type PolicyReplay = {
  status: string; evaluation_start?: string; evaluation_end?: string;
  policy_comparison?: Record<string, PolicyMetric>;
  assumptions?: Record<string, string | number>;
  limitations?: string[];
};
type DatabaseStatus = {
  status: "ready" | "not_configured" | "not_migrated" | "empty" | "unavailable" | "invalid_config";
  configured: boolean;
  source: string;
};
type WarehouseMetric = {
  warehouse_id: string;
  city: string;
  state: string;
  units_sold: number;
  on_hand_units: number;
  stockout_flag_rate: number;
  promo_day_rate: number;
};
type DataSummary = {
  status: "ready" | "not_generated" | "unavailable";
  source: string;
  dataset_version?: string;
  sku_count?: number;
  warehouse_count?: number;
  daily_sales_rows?: number;
  sku_warehouse_series?: number;
  stockout_flag_rate?: number;
  promotion_day_rate?: number;
  warehouse_snapshot_units?: number;
  data_as_of_date?: string;
  warehouse_metrics?: WarehouseMetric[];
};

export const dynamic = "force-dynamic";

async function getSystemStatus(skuId: string, warehouseId: string): Promise<{ backend: BackendStatus; data: DataSummary | null; database: DatabaseStatus | null; forecasting: ForecastStatus | null; series: ForecastSeries | null; ml: MLStatus | null; mlSeries: ForecastSeries | null; inventory: InventoryDecision | null; inventoryError: string | null; replay: PolicyReplay | null; mlReplay: PolicyReplay | null }> {
  const baseUrl = process.env.API_BASE_URL ?? "http://127.0.0.1:8000";
  const empty = { backend: "offline" as BackendStatus, data: null, database: null, forecasting: null, series: null, ml: null, mlSeries: null, inventory: null, inventoryError: null, replay: null, mlReplay: null };
  try {
    const healthResponse = await fetch(`${baseUrl}/api/v1/health`, {
      cache: "no-store",
      signal: AbortSignal.timeout(2500),
    });
    if (!healthResponse.ok) return empty;
    const health = await healthResponse.json();
    if (health?.status !== "ok") return empty;
    try {
      const dataResponse = await fetch(`${baseUrl}/api/v1/data/summary`, {
        cache: "no-store",
        signal: AbortSignal.timeout(4000),
      });
      const dbResponse = await fetch(`${baseUrl}/api/v1/database/status`, {
        cache: "no-store",
        signal: AbortSignal.timeout(2500),
      }).catch(() => null);
      const forecastResponse = await fetch(`${baseUrl}/api/v1/forecasting/status`, {
        cache: "no-store", signal: AbortSignal.timeout(5000),
      }).catch(() => null);
      const forecasting = forecastResponse?.ok ? (await forecastResponse.json() as ForecastStatus) : null;
      let series: ForecastSeries | null = null;
      if (forecasting?.status === "ready") {
        const response = await fetch(`${baseUrl}/api/v1/forecasting/series?sku_id=${encodeURIComponent(skuId)}&warehouse_id=${encodeURIComponent(warehouseId)}`, {
          cache: "no-store", signal: AbortSignal.timeout(5000),
        }).catch(() => null);
        if (response?.ok) series = await response.json() as ForecastSeries;
      }
      const mlResponse = await fetch(`${baseUrl}/api/v1/forecasting/ml/status`, {
        cache: "no-store", signal: AbortSignal.timeout(4500),
      }).catch(() => null);
      const ml = mlResponse?.ok ? (await mlResponse.json() as MLStatus) : null;
      let mlSeries: ForecastSeries | null = null;
      if (ml?.status === "ready") {
        const response = await fetch(`${baseUrl}/api/v1/forecasting/ml/series?sku_id=${encodeURIComponent(skuId)}&warehouse_id=${encodeURIComponent(warehouseId)}`, {
          cache: "no-store", signal: AbortSignal.timeout(4500),
        }).catch(() => null);
        if (response?.ok) mlSeries = await response.json() as ForecastSeries;
      }
      const replayResponse = await fetch(`${baseUrl}/api/v1/inventory/simulation/status`, {
        cache: "no-store", signal: AbortSignal.timeout(4000),
      }).catch(() => null);
      const replay: PolicyReplay | null = replayResponse?.ok ? await replayResponse.json() as PolicyReplay : null;
      const mlReplayResponse = await fetch(`${baseUrl}/api/v1/inventory/simulation/ml/status`, {
        cache: "no-store", signal: AbortSignal.timeout(4000),
      }).catch(() => null);
      const mlReplay: PolicyReplay | null = mlReplayResponse?.ok ? await mlReplayResponse.json() as PolicyReplay : null;
      let inventory: InventoryDecision | null = null;
      let inventoryError: string | null = null;
      if (ml?.status === "ready" && dbResponse?.ok) {
        const dbStatus = await dbResponse.json() as DatabaseStatus;
        if (dbStatus.status === "ready") {
          const response = await fetch(`${baseUrl}/api/v1/inventory/recommendation?sku_id=${encodeURIComponent(skuId)}&warehouse_id=${encodeURIComponent(warehouseId)}`, {
            cache: "no-store", signal: AbortSignal.timeout(7000),
          }).catch(() => null);
          if (response?.ok) inventory = await response.json() as InventoryDecision;
          else if (response) {
            const detail = await response.json().catch(() => null);
            inventoryError = typeof detail?.detail === "string" ? detail.detail : `Recommendation API returned ${response.status}`;
          } else inventoryError = "Recommendation API timed out or is unreachable.";
        }
        // Reuse this response below instead of parsing it twice.
        return {
          backend: "connected",
          data: dataResponse.ok ? (await dataResponse.json() as DataSummary) : null,
          database: dbStatus,
          forecasting, series, ml, mlSeries, inventory, inventoryError, replay, mlReplay,
        };
      }
      return {
        backend: "connected",
        data: dataResponse.ok ? (await dataResponse.json() as DataSummary) : null,
        database: dbResponse?.ok ? (await dbResponse.json() as DatabaseStatus) : null,
        forecasting,
        series,
        ml,
        mlSeries,
        inventory, inventoryError, replay, mlReplay,
      };
    } catch {
      return { ...empty, backend: "connected" };
    }
  } catch {
    return empty;
  }
}

const fmt = (n: number) => new Intl.NumberFormat("en-US").format(n);
const pct = (n: number) => `${(n * 100).toFixed(1)}%`;

async function loadExplorerData(view: DashboardView, warehouseId: string, skuId: string) {
  const base = process.env.API_BASE_URL ?? "http://127.0.0.1:8000";
  const empty = { products: [] as ProductItem[], history: null as HistoryPayload | null, registry: null as RegistryInfo | null, error: "" };
  if (view === "overview") return empty;
  const safeGet = async (route: string) => {
    try {
      const r = await fetch(`${base}/api/v1/${route}`, { cache: "no-store", signal: AbortSignal.timeout(6500) });
      return r.ok ? await r.json() : null;
    } catch { return null; }
  };
  if (view === "evaluation") {
    return { ...empty, registry: await safeGet("mlops/model") as RegistryInfo | null };
  }
  const products = await safeGet(`data/products?limit=200&warehouse_id=${encodeURIComponent(warehouseId)}`) as ProductsPayload | null;
  if (!products || !Array.isArray(products.items)) return { ...empty, error: "Product catalog unavailable. Check PostgreSQL and the backend API." };
  if (!products.items.some(p => p.sku_id === skuId)) return { ...empty, products: products.items, error: "The selected product is not in this warehouse's catalog." };
  const history = view === "forecasts" ? await safeGet(`data/history?sku_id=${encodeURIComponent(skuId)}&warehouse_id=${encodeURIComponent(warehouseId)}&days=90`) as HistoryPayload | null : null;
  return { ...empty, products: products.items, history, error: view === "forecasts" && !history ? "Sales history could not be loaded." : "" };
}

export type DashboardView = "overview" | "forecasts" | "inventory" | "evaluation";

export default async function Dashboard({ view, skuId = "DOG-FOOD-15LB-001", warehouseId = "TX-DAL" }: {
  view: DashboardView; skuId?: string; warehouseId?: string;
}) {
  // Reject malformed query strings before forwarding user-selected IDs to the API.
  const validSku = /^[A-Z0-9-]{2,80}$/.test(skuId);
  const validWarehouse = ["TX-DAL", "CA-RIV", "PA-ALL"].includes(warehouseId);
  const safeSku = validSku ? skuId : "INVALID-SELECTION";
  const safeWarehouse = validWarehouse ? warehouseId : "TX-DAL";
  const [system, explorer] = await Promise.all([
    getSystemStatus(safeSku, safeWarehouse),
    loadExplorerData(view, safeWarehouse, safeSku),
  ]);
  const { backend, data, database, forecasting, series, ml, mlSeries, inventory, inventoryError, replay, mlReplay } = system;
  const { products, history, registry } = explorer;
  const selectedProduct = products.find(product => product.sku_id === safeSku);
  const selectedWarehouse = data?.warehouse_metrics?.find(warehouse => warehouse.warehouse_id === safeWarehouse);
  const selectionLabel = `${selectedProduct?.product_name ?? safeSku} · ${selectedWarehouse ? `${selectedWarehouse.city}, ${selectedWarehouse.state}` : safeWarehouse}`;
  const explorerError = (!validSku || !validWarehouse) ? "Invalid product or warehouse selection." : explorer.error;
  const available = !!selectedProduct && !explorerError;
  const mlReady = ml?.status === "ready";
  const baselineReady = forecasting?.status === "ready";
  const testMetrics = forecasting?.untouched_test?.metrics;
  const chosen = forecasting?.selected_baseline;
  const weekly = [0, 1, 2, 3].map((week) => ({
    week: week + 1,
    units: (series?.forecast ?? []).slice(week * 7, week * 7 + 7).reduce((sum, item) => sum + item.predicted_units, 0),
  }));
  const peak = Math.max(1, ...weekly.map((w) => w.units));
  const mlWeekly = mlSeries?.weekly_forecast?.map((w) => ({
    week: w.week, units: w.predicted_units, lower: w.lower_units, upper: w.upper_units,
  })) ?? [0, 1, 2, 3].map((week) => ({
    week: week + 1,
    units: (mlSeries?.forecast ?? []).slice(week * 7, week * 7 + 7).reduce((sum, item) => sum + item.predicted_units, 0),
    lower: undefined as number | undefined,
    upper: undefined as number | undefined,
  }));
  const mlPeak = Math.max(1, ...mlWeekly.map((w) => w.units));
  const dbReady = database?.status === "ready";
  const ready = data?.status === "ready";
  const modules = [
    { number: "01", href: "/forecasts", title: "Demand forecasting", summary: "SKU-level predictions, regional demand and uncertainty intervals.", label: mlReady ? "LightGBM evaluated with forecast intervals" : baselineReady ? "Baselines evaluated · Next: LightGBM" : dbReady ? "Next: run baseline backtests" : "Next: PostgreSQL import" },
    { number: "02", href: "/inventory", title: "Inventory decisions", summary: "Supplier lead times, safety stock and explainable reorder quantities.", label: inventory ? "SQL-backed reorder + planner review" : "Planned" },
    { number: "03", href: "/evaluation", title: "Model evaluation", summary: "Rolling backtests, WAPE, MASE, bias and service-level simulation.", label: mlReplay?.status === "ready" ? "ML forecasting + historical-origin inventory replay" : mlReady ? "Rolling ML backtests + inventory policy replay" : baselineReady ? "Rolling baselines measured" : "Planned" },
  ];

  return (
    <div className="shell">
      <aside className="sidebar" aria-label="Project navigation">
        <a href="/" className="brand" aria-label="Pawprint Supply Studio — Overview">
          <span className="brand-icon"><PawMark width={27} height={27}/></span>
          <span className="brand-details"><span className="brand-name">pawprint<span className="brand-dot">.</span></span><span className="brand-subtitle">SUPPLY STUDIO</span></span>
        </a>
        <div className="sidebar-heading">THE WORKSPACE <span>01 / 04</span></div>
        <nav aria-label="Workspace pages" className="workspace-nav">
          {([
            ["overview", "/", "◫", "Overview", "The big picture"],
            ["forecasts", "/forecasts", "⌁", "Demand studio", "Future outlook"],
            ["inventory", "/inventory", "▣", "Stock planner", "The order desk"],
            ["evaluation", "/evaluation", "◈", "Model lab", "The evidence"],
          ] as const).map(([key, href, icon, title, sub]) =>
            <a key={key} href={(key === "forecasts" || key === "inventory") && (view === "forecasts" || view === "inventory") ? `${href}?sku_id=${encodeURIComponent(safeSku)}&warehouse_id=${encodeURIComponent(safeWarehouse)}` : href} aria-current={view === key ? "page" : undefined} className={view === key ? "nav-current" : "nav-upcoming nav-link"}>
              <span aria-hidden="true" className="nav-symbol">{icon}</span><span className="nav-title">{title}<small className="nav-description">{sub}</small></span><span className="nav-chevron" aria-hidden="true">↗</span>
            </a>
          )}
        </nav>
        <div className="sidebar-middle" aria-hidden="true"><PawMark width={26} height={26}/><span>Better care begins with<br/>better planning.</span></div>
        <div className="sidebar-bottom">
          <div className="sidebar-bottom-kicker"><span className="tiny-live-dot"/> LOCAL DEMONSTRATION</div>
          <div className="sidebar-bottom-title">Built for every bowl, box &amp; warehouse.</div>
          <div className="sidebar-bottom-copy">Fictional US pet retail data. Planner reviews are local records, not supplier purchase orders.</div>
        </div>
      </aside>
      <main className="main" id="main-content">
        <div className="topline">
          <span className="crumb">SUPPLY STUDIO <span className="crumb-sep">/</span> <strong>{view === "overview" ? "Overview" : view === "forecasts" ? "Demand studio" : view === "inventory" ? "Stock planner" : "Model lab"}</strong></span>
          <div className="topline-right"><span className="topline-zone">US · 3 FULFILLMENT REGIONS</span><span className="tag"><span className="tiny-live-dot"/> FICTIONAL RETAIL DEMO</span></div>
        </div>
        {view === "overview" && <>
        <header className="hero">
          <div className="hero-text">
            <div className="eyebrow"><PawMark width={17} height={17}/> THE PET SUPPLY COMMAND CENTER <span className="hero-edition">/ VOL. 01</span></div>
            <h1>Better stocked.<br/><em>Happier tails.</em></h1>
            <p>From the everyday bag of kibble to the next big holiday rush. Predict what pets need, know what to order, and keep every warehouse a step ahead.</p>
            <div className="hero-actions"><a href="/forecasts" className="hero-action-primary">Explore demand <span aria-hidden="true">↗</span></a><a href="/inventory" className="hero-action-secondary">Plan replenishment <span aria-hidden="true">→</span></a></div>
            <div className="hero-tags"><span>{ready ? `${data?.sku_count} PET PRODUCTS` : "150 PLANNED PRODUCTS"}</span><span>3 US WAREHOUSES</span><span>28-DAY OUTLOOK</span></div>
          </div>
          <PetArtwork />
        </header>
        {inventory && inventory.status === "ready" && (
          <section className="planning-spotlight" aria-label="Featured pet-stock replenishment decision">
            <div className="spotlight-left"><div className="spotlight-eyebrow">ON THE PLANNING DESK <span> / DALLAS, TEXAS</span></div>
              <h2>From demand to <em>delivery.</em></h2>
              <p>Our 15 lb adult dog food case study connects trained demand forecasts, supplier lead time, open orders and safety buffers into one explainable decision.</p>
              <a href="/inventory?sku_id=DOG-FOOD-15LB-001&warehouse_id=TX-DAL">Explore the reorder recipe <span aria-hidden="true">↗</span></a>
            </div>
            <div className="spotlight-math" aria-label="Live Dallas recommendation">
              <div><span>28-DAY OUTLOOK</span><b>{fmt(Math.round(inventory.demand_next_28_days))}</b><small>units forecast</small></div>
              <span className="spotlight-arrow" aria-hidden="true">→</span>
              <div className="spotlight-highlight"><span>RECOMMENDED ORDER</span><b>{fmt(inventory.recommended_order_units)}</b><small>units · not yet ordered</small></div>
            </div>
          </section>
        )}
        {ready && data && (
          <section className="section" aria-label="Synthetic dataset summary">
            <div className="section-head"><div><div className="section-kicker">02 / THE BIG PICTURE</div><h2>Meet the supply network.</h2></div><span className="section-subtext">Synthetic dataset as of {data.data_as_of_date} · {dbReady ? "PostgreSQL-backed" : "CSV-backed"}</span></div>
            <div className="metric-grid">
              <div className="metric-card"><span>Products in the pack</span><strong>{fmt(data.sku_count ?? 0)}</strong><small>Fictional pet SKUs</small></div>
              <div className="metric-card"><span>Sales signals</span><strong>{fmt(data.daily_sales_rows ?? 0)}</strong><small>{fmt(data.sku_warehouse_series ?? 0)} SKU–warehouse series</small></div>
              <div className="metric-card"><span>Zero-ending-stock days</span><strong>{pct(data.stockout_flag_rate ?? 0)}</strong><small>Inventory flag, not lost sales</small></div>
              <div className="metric-card"><span>Ready on shelves</span><strong>{fmt(data.warehouse_snapshot_units ?? 0)}</strong><small>Units at synthetic snapshot date</small></div>
            </div>
            <div className="warehouse-card">
              <div className="warehouse-title"><strong>Three hubs. One connected supply picture.</strong><span>Scenario data only · not real business performance</span></div>
              <div className="warehouse-scroll"><table className="warehouse-table"><thead><tr><th>Warehouse</th><th>Historical units sold</th><th>Current stock</th><th>Zero-stock day rate</th></tr></thead>
                <tbody>{(data.warehouse_metrics ?? []).map((w) => <tr key={w.warehouse_id}><td><strong>{w.city}, {w.state}</strong><small>{w.warehouse_id}</small></td><td>{fmt(w.units_sold)}</td><td>{fmt(w.on_hand_units)}</td><td>{pct(w.stockout_flag_rate)}</td></tr>)}</tbody></table></div>
            </div>
          </section>
        )}
        <section className="section">
          <div className="section-head"><div><div className="section-kicker">01 / SYSTEM PULSE</div><h2>Everything in its place.</h2></div><span className="section-subtext">PostgreSQL: {database?.status?.replaceAll("_", " ") ?? "not checked"} · live API</span></div>
          <div className="status-grid">
            <div className="status-card"><div className="status-icon status-icon-dark">API</div><div><div className="status-card-label">FastAPI backend</div><div className="status-card-value">{backend === "connected" ? "Connected" : "Offline"}</div><p>{backend === "connected" ? "GET /api/v1/health is responding" : "Start the backend on port 8000"}</p></div><span className={`status-pill ${backend === "connected" ? "status-connected" : "status-offline"}`}>{backend === "connected" ? "● LIVE" : "● OFFLINE"}</span></div>
            <div className="status-card"><div className="status-icon status-icon-teal">ML</div><div><div className="status-card-label">Forecast models</div><div className="status-card-value">{mlReady ? "LightGBM trained" : baselineReady ? "Baselines ready" : "Not trained"}</div><p>{mlReady ? `Model ${ml?.model_version} · rolling folds + final holdout scored` : baselineReady ? "Rolling-origin evaluation complete · LightGBM not trained" : "Run baselines before training LightGBM"}</p></div><span className={`status-pill ${mlReady || baselineReady ? "status-connected" : "status-planned"}`}>{mlReady ? "● TRAINED" : baselineReady ? "● EVALUATED" : "PLANNED"}</span></div>
            <div className="status-card"><div className="status-icon status-icon-blue">{dbReady ? "PG" : "CSV"}</div><div><div className="status-card-label">{dbReady ? "PostgreSQL retail dataset" : "Retail dataset"}</div><div className="status-card-value">{dbReady ? "Imported" : ready ? "Validated" : "Not loaded"}</div><p>{ready ? `${fmt(data?.daily_sales_rows ?? 0)} synthetic daily sales records · ${dbReady ? "SQL-backed" : "CSV-backed"}` : "Run dataset generation and validation"}</p></div><span className={`status-pill ${ready ? "status-connected" : "status-planned"}`}>{ready ? "● READY" : "PLANNED"}</span></div>
          </div>
        </section>
        </>}
        {view === "forecasts" && <>
          <div className="workspace-heading workspace-heading-forecast"><div className="heading-content"><div className="section-kicker">01 / DEMAND STUDIO</div><h1>See what&apos;s coming.<br/><em>Before it arrives.</em></h1><p>Discover patterns in pet essentials, see trained 28-day forecasts, and understand the uncertainty behind each projection.</p></div><div className="heading-emblem" aria-hidden="true">⌁<small>FORECAST / 28D</small></div></div>
          {products.length > 0 && <ProductWarehouseSelector key={`${safeSku}-${safeWarehouse}-forecast`} view="forecasts" skuId={available ? safeSku : products[0].sku_id} warehouseId={safeWarehouse} products={products} warehouses={data?.warehouse_metrics ?? []} />}
          {explorerError && <div className="empty-panel" role="alert">{explorerError}</div>}
          {available && mlSeries && history && <DemandHistoryChart history={history.records} forecast={mlSeries.forecast} name={selectionLabel} />}
          {available && mlReady && !mlSeries && <div className="empty-panel" role="status">No saved LightGBM forecast exists for the selected SKU and warehouse. Check the forecast artifacts before making inventory decisions.</div>}
          {!baselineReady && !mlReady && <div className="empty-panel"><strong>Forecast results are not available</strong><p>Run baseline evaluation and LightGBM training from the backend, then reload this page.</p></div>}
        {baselineReady && forecasting && (
          <section className="section" aria-label="Forecast baseline evaluation">
            <div className="section-head"><div><div className="section-kicker">FORECAST BENCHMARK / BASELINES</div><h2>The starting line</h2></div><span className="section-subtext">Historical holdout: {forecasting.untouched_test?.test_start} to {forecasting.untouched_test?.test_end}</span></div>
            <div className="baseline-card">
              <div className="baseline-heading">
                <div><strong>Untouched 28-day test · WAPE</strong><p>Lower is better · Synthetic observed sales only · Baseline results archived for comparison</p></div>
                <span className="baseline-chip">Selected: {chosen?.replaceAll("_", " ")}</span>
              </div>
              <div className="baseline-metric-grid">
                {Object.entries(testMetrics ?? {}).map(([name, result]) => (
                  <div className="baseline-metric" key={name}>
                    <span>{name === "seasonal_naive_7" ? "7-day seasonal naive" : "28-day moving average"}</span>
                    <strong>{result.wape_pct !== null ? `${result.wape_pct.toFixed(2)}%` : "Undefined"}</strong>
                    <small>Holdout WAPE {chosen === name ? "· selected by earlier rolling folds" : ""}</small>
                  </div>
                ))}
              </div>
              {available && series && (
                <div className="baseline-forecast">
                  <div className="baseline-heading"><div><strong>{selectionLabel}</strong><p>Next 4 weeks · Daily SKU–warehouse baseline summed by week</p></div><span className="baseline-chip">{forecasting.future?.forecast_start} onward</span></div>
                  <div className="weekly-bars" role="img" aria-label={`Four-week baseline forecast in units: ${weekly.map((w) => `week ${w.week}, ${Math.round(w.units)}`).join("; ")}`}>
                    {weekly.map((w) => <div key={w.week} className="weekly-column"><strong>{Math.round(w.units).toLocaleString("en-US")}</strong><div className="weekly-track"><span style={{height: `${Math.max(2, 100 * w.units / peak)}%`}} /></div><small>Week {w.week}</small></div>)}
                  </div>
                </div>
              )}
              <p className="baseline-disclaimer">Observed sales can understate true demand during stockouts. Baseline forecasts are not replenishment orders. Baseline-only projections have no intervals here. Use the Inventory page for a replenishment recommendation based on trained forecasts.</p>
            </div>
          </section>
        )}
        {mlReady && ml && (
          <section className="section" aria-label="Trained LightGBM forecasting evaluation">
            <div className="section-head"><div><div className="section-kicker">DEMAND MODELS / LIGHTGBM</div><h2>The model showdown</h2></div><span className="section-subtext">Held-out 28 days · {ml.untouched_test?.test_start} – {ml.untouched_test?.test_end}</span></div>
            <div className="baseline-card">
              <div className="baseline-heading">
                <div><strong>Three model comparison · holdout WAPE</strong><p>Trained global LightGBM with recursive daily predictions; benchmarked against simple baselines</p></div>
                <span className="baseline-chip">Production candidate: {ml.selected_model?.replaceAll("_", " ")}</span>
              </div>
              <div className="ml-metric-grid">
                {Object.entries(ml.untouched_test?.metrics ?? {}).map(([name, result]) => (
                  <div className="baseline-metric" key={name}>
                    <span>{name === "lightgbm_global" ? "Global LightGBM" : name === "moving_average_28" ? "28-day moving average" : "7-day seasonal naive"}</span>
                    <strong>{result.wape_pct == null ? "Undefined" : `${result.wape_pct.toFixed(2)}%`}</strong>
                    <small>Holdout WAPE · MASE {result.mase == null ? "n/a" : result.mase.toFixed(3)}</small>
                    <small>Rolling WAPE: {ml.backtest_summary?.[name]?.wape_pct?.toFixed(2) ?? "n/a"}%</small>
                  </div>
                ))}
              </div>
              {ml.prediction_intervals && (
                <div className="interval-summary" aria-label="Prediction interval validation metrics">
                  <div><span>Nominal interval target</span><strong>{ml.prediction_intervals.nominal_coverage_pct}%</strong><small>Not guaranteed for every SKU</small></div>
                  <div><span>Final holdout · daily coverage</span><strong>{ml.prediction_intervals.untouched_test_coverage.daily.coverage_pct.toFixed(1)}%</strong><small>Observed sales · 12,600 daily forecasts</small></div>
                  <div><span>Final holdout · weekly coverage</span><strong>{ml.prediction_intervals.untouched_test_coverage.weekly.coverage_pct.toFixed(1)}%</strong><small>Aggregated seven-day demand</small></div>
                </div>
              )}
              {available && mlSeries && (
                <div className="baseline-forecast">
                  <div className="baseline-heading"><div><strong>{selectionLabel}</strong><p>Next four weeks · {ml.selected_model === "lightgbm_global" ? "Trained LightGBM selected on rolling backtests" : "Baseline retained because its rolling WAPE was lower than LightGBM"}</p></div><span className="baseline-chip">{ml.future?.forecast_start} onward</span></div>
                  <div className={`weekly-bars ${mlSeries?.weekly_forecast ? "weekly-bars-interval" : ""}`} role="img" aria-label={`Four weekly forecasts: ${mlWeekly.map((w) => `week ${w.week}, ${Math.round(w.units)} units`).join("; ")}`}>
                    {mlWeekly.map((w) => <div className="weekly-column" key={w.week}>
                      <strong>{Math.round(w.units).toLocaleString("en-US")}</strong>
                      <div className="weekly-track"><span style={{height: `${Math.max(2, 100 * w.units / mlPeak)}%`}} /></div>
                      <small>Week {w.week}</small>
                      {w.lower !== undefined && w.upper !== undefined && (
                        <small className="weekly-interval-label">90% nominal: {Math.round(w.lower)}–{Math.round(w.upper)}</small>
                      )}
                    </div>)}
                  </div>
                </div>
              )}
              <p className="baseline-disclaimer">Model selection uses rolling validation only; the final holdout was not used to choose the production candidate. Synthetic observed sales can be censored by stockouts. Forecast bands target 90% marginal coverage, calibrated from earlier rolling folds; actual coverage is shown above. They are not guaranteed for each individual SKU or a measure of lost sales. Forecasts are not replenishment orders.</p>
            </div>
          </section>
        )}
        </>}
        {view === "inventory" && <>
          <div className="workspace-heading workspace-heading-inventory"><div className="heading-content"><div className="section-kicker">02 / THE ORDER DESK</div><h1>Right stock.<br/><em>Right on time.</em></h1><p>Go from forecast to replenishment: inspect lead times, safety buffers, stock position, and the numbers behind each recommendation.</p></div><div className="heading-emblem" aria-hidden="true">▣<small>INVENTORY / PLAN</small></div></div>
          {products.length > 0 && <ProductWarehouseSelector key={`${safeSku}-${safeWarehouse}-inventory`} view="inventory" skuId={available ? safeSku : products[0].sku_id} warehouseId={safeWarehouse} products={products} warehouses={data?.warehouse_metrics ?? []} />}
          {explorerError && <div className="empty-panel" role="alert">{explorerError}</div>}
          {!inventory && !explorerError && <div className="empty-panel"><strong>No current recommendation available</strong><p>{inventoryError ?? "Start PostgreSQL and the FastAPI backend and verify the trained LightGBM forecasts."} No purchase order has been placed.</p></div>}
        {available && inventory && inventory.status === "ready" && (
          <section className="section" id="inventory-decision" aria-label="reorder calculation">
            <div className="section-head">
              <div><div className="section-kicker">PURCHASING INTELLIGENCE / EXPLAINED</div><h2>The reorder recipe</h2></div>
              <span className="section-subtext">SQL-backed local review · {inventory.as_of_date} snapshot</span>
            </div>
            <div className="inventory-card">
              <div className="inventory-heading">
                <div><span className="inventory-overline">{selectionLabel}</span>
                  <h3>{fmt(inventory.recommended_order_units)} <small>units to replenish</small></h3>
                  <p>Order-up-to policy for {inventory.lead_time_days}-day supplier lead time + {inventory.review_period_days}-day review period. Arrival expected {inventory.new_order_expected_arrival}.</p>
                </div>
                <span className="baseline-chip">RECOMMENDATION · NOT ORDERED</span>
              </div>
              <div className="inventory-steps">
                <div><span>28-day predicted sales</span><strong>{Math.round(inventory.demand_next_28_days).toLocaleString("en-US")}</strong><small>Full forecast horizon, not the order quantity</small></div>
                <div><span>Protection-period demand</span><strong>{Math.round(inventory.demand_protection_window).toLocaleString("en-US")}</strong><small>Lead time + review, through {inventory.protection_end}</small></div>
                <div><span>Heuristic safety stock</span><strong>+{fmt(inventory.safety_stock_units)}</strong><small>Derived from weekly uncertainty margins</small></div>
                <div><span>Inventory position</span><strong>−{fmt(inventory.inventory_position_for_window)}</strong><small>Free on-hand + timely open POs</small></div>
              </div>
              <div className="inventory-breakdown">
                <div className="inventory-table-wrap"><h4>Order calculation</h4>
                  <table className="inventory-detail-table"><tbody>
                    <tr><td>Current stock on hand</td><td>{fmt(inventory.on_hand_units)}</td></tr>
                    <tr><td>Reserved / backordered</td><td>{fmt(inventory.reserved_units)} / {fmt(inventory.backorder_units)}</td></tr>
                    <tr><td>Open incoming orders</td><td>{fmt(inventory.on_order_units)}</td></tr>
                    <tr><td>Arriving within protection window</td><td>{fmt(inventory.on_order_arriving_within_window)}</td></tr>
                    <tr><td>Target stock incl. safety buffer</td><td>{fmt(inventory.target_stock_units)}</td></tr>
                    <tr><td>Raw additional demand to order</td><td>{fmt(inventory.unconstrained_order_units)}</td></tr>
                    <tr><td>Minimum order / case pack</td><td>{fmt(inventory.minimum_order_qty)} / {fmt(inventory.case_pack)}</td></tr>
                    <tr className="inventory-total"><td>Recommended order</td><td>{fmt(inventory.recommended_order_units)} units</td></tr>
                  </tbody></table>
                </div>
                <div className="inventory-risk"><h4>Demand scenarios · next 28 days</h4>
                  <div><span>No new order · point forecast</span><strong>{inventory.point_without_new_order.shortage_days} shortage days</strong><small>Estimated unfilled demand: {inventory.point_without_new_order.estimated_unmet_units} units</small></div>
                  <div><span>With recommendation · point forecast</span><strong>{inventory.point_with_recommendation.shortage_days} shortage days</strong><small>Estimated unfilled demand: {inventory.point_with_recommendation.estimated_unmet_units} units</small></div>
                  <div><span>With recommendation · +25% demand stress</span><strong>{inventory.stress_25pct_with_recommendation.shortage_days} shortage days</strong><small>Estimated unfilled demand: {inventory.stress_25pct_with_recommendation.estimated_unmet_units} units</small></div>
                  <p>These are deterministic scenarios, <b>not</b> estimated stockout probabilities or achieved service levels.</p>
                </div>
              </div>
              <div className="inventory-notes"><strong>{inventory.requires_manual_review ? "Manual purchasing review required" : "Purchasing review recommended"}</strong>
                <span>{inventory.expiry_status}. Reviews are saved separately and never place purchase orders.</span>
                {inventory.warnings.map((warning, index) => <span key={index}>• {warning}</span>)}
              </div>
            </div>
            {inventory.snapshot_hash && <PlannerReview key={`${inventory.sku_id}-${inventory.warehouse_id}`} recommendation={{
              sku_id: inventory.sku_id, warehouse_id: inventory.warehouse_id,
              review_period_days: inventory.review_period_days, snapshot_hash: inventory.snapshot_hash,
              recommended_order_units: inventory.recommended_order_units,
              requires_manual_review: inventory.requires_manual_review,
              minimum_order_qty: inventory.minimum_order_qty, case_pack: inventory.case_pack,
              model_version: inventory.model_version,
            }} />}
          </section>
        )}
        </>}
        {view === "evaluation" && <>
          <div className="workspace-heading workspace-heading-evaluation"><div className="heading-content"><div className="section-kicker">03 / THE MODEL LAB</div><h1>Proof over<br/><em>promises.</em></h1><p>Every model needs a benchmark. Every stocking decision needs a trade-off. Explore the measured evidence behind both.</p></div><div className="heading-emblem" aria-hidden="true">◎<small>BACKTEST / LAB</small></div></div>
          {mlReplay?.status === "ready" && mlReplay.policy_comparison?.lightgbm_buffered && mlReplay.policy_comparison?.buffered_moving_average && (
            <div className="evidence-spotlight" aria-label="Observed simulation trade-off summary">
              <div className="evidence-copy"><span className="spotlight-eyebrow">THE TAKEAWAY / SYNTHETIC POLICY REPLAY</span><h2>A little more stock.<br/><em>Fewer simulated shortages.</em></h2><p>Using historical-origin LightGBM forecasts improved observed-sales fill rate compared with the buffered moving-average policy, while carrying more inventory. Diagnostic simulation only—not a real-world service-level or savings claim.</p></div>
              <div className="evidence-metrics"><div><span>LIGHTGBM FILL RATE</span><strong>{mlReplay.policy_comparison.lightgbm_buffered.fill_rate_pct?.toFixed(2) ?? "n/a"}%</strong></div><div><span>BUFFERED MA FILL RATE</span><strong>{mlReplay.policy_comparison.buffered_moving_average.fill_rate_pct?.toFixed(2) ?? "n/a"}%</strong></div></div>
            </div>
          )}
          {ml?.status === "ready" && <section className="section"><div className="section-head"><div><div className="section-kicker">MODEL ACCURACY / HOLDOUT</div><h2>How well do we predict?</h2></div><span className="section-subtext">{ml.untouched_test?.test_start} – {ml.untouched_test?.test_end}</span></div>
            <div className="ml-metric-grid">
              {Object.entries(ml.untouched_test?.metrics ?? {}).map(([name, metric]) => <div className="baseline-metric" key={name}><span>{name.replaceAll("_", " ")}</span><strong>{metric.wape_pct == null ? "n/a" : `${metric.wape_pct.toFixed(2)}%`}</strong><small>Holdout WAPE · MASE {metric.mase?.toFixed(3) ?? "n/a"}</small></div>)}
            </div>
            <div className="evaluation-details" aria-label="Model accuracy and artifact provenance">
              {Object.entries(ml.untouched_test?.metrics ?? {}).map(([name, metric]) => <div key={name} className="evaluation-detail-card">
                <strong>{name.replaceAll("_", " ")}</strong>
                <span>Forecast bias: {metric.bias_pct == null ? "n/a" : `${metric.bias_pct.toFixed(2)}%`}</span>
                <span>MASE: {metric.mase == null ? "n/a" : metric.mase.toFixed(3)}</span>
                <span>Rolling WAPE: {ml.backtest_summary?.[name]?.wape_pct?.toFixed(2) ?? "n/a"}%</span>
              </div>)}
            </div>
            {ml.prediction_intervals && <div className="evaluation-details">
              <div className="evaluation-detail-card"><strong>Nominal interval target</strong><span>{ml.prediction_intervals.nominal_coverage_pct}% (marginal)</span></div>
              <div className="evaluation-detail-card"><strong>Daily holdout coverage</strong><span>{ml.prediction_intervals.untouched_test_coverage.daily.coverage_pct.toFixed(2)}%</span></div>
              <div className="evaluation-detail-card"><strong>Weekly holdout coverage</strong><span>{ml.prediction_intervals.untouched_test_coverage.weekly.coverage_pct.toFixed(2)}%</span></div>
            </div>}
            {registry?.status === "ready" && <div className="registry-detail"><strong>Registered artifact provenance</strong><span>Model: {registry.model_version} · Run ID: {registry.run_id} · Dataset: {registry.dataset_version} · Trained through: {registry.trained_through} · Source: {registry.data_source ?? "recorded in manifest"}</span><small>Checksums identify matching local artifacts; this does not certify model accuracy or real-world deployment.</small></div>}
            <p className="baseline-disclaimer">Model selection used earlier rolling folds; evaluation uses observed sales that can be suppressed by stockouts. Nominal 90% prediction intervals are not guaranteed service levels.</p>
          </section>}
          {replay?.status !== "ready" && mlReplay?.status !== "ready" && <div className="empty-panel"><strong>Policy simulation reports are unavailable</strong><p>Run the inventory-policy replay commands from the backend and refresh this page.</p></div>}
        {replay?.status === "ready" && replay.policy_comparison && <section className="section" aria-label="Retrospective inventory policy simulation">
          <div className="section-head"><div><div className="section-kicker">INVENTORY REPLAY / RULES</div><h2>Stocking strategies, replayed</h2></div>
            <span className="section-subtext">{replay.evaluation_start} – {replay.evaluation_end} · observed sales</span></div>
          <div className="warehouse-card"><div className="warehouse-title"><strong>Simple vs buffered moving-average inventory policy</strong><span>Retrospective simulation · 450 SKU–warehouse series</span></div>
            <div className="warehouse-scroll"><table className="replay-table"><thead><tr><th>Policy</th><th>Observed-sales fill rate</th><th>Shortage day rate</th><th>Mean stock (units / series)</th><th>Simulated holding cost</th></tr></thead>
              <tbody>{Object.entries(replay.policy_comparison).map(([name, metric]) => <tr key={name}><td><strong>{name.replaceAll("_", " ")}</strong></td><td>{metric.fill_rate_pct?.toFixed(2) ?? "n/a"}%</td><td>{metric.stockout_day_pct.toFixed(2)}%</td><td>{fmt(Math.round(metric.average_ending_stock_units_per_series))}</td><td>${metric.holding_cost_usd.toLocaleString("en-US", {maximumFractionDigits: 2})}</td></tr>)}</tbody></table></div>
            <div className="replay-context">This is a retrospective replay of two moving-average replenishment rules, <strong>not</strong> a LightGBM policy backtest. It uses stockout-censored observed sales, simplified supplier deliveries, and an illustrative 20% annual carrying rate. Fill rates are not true customer service levels; costs are modeled, not measured.</div>
          </div></section>}
        {mlReplay?.status === "ready" && mlReplay.policy_comparison && <section className="section" aria-label="Historical-origin ML inventory policy comparison">
          <div className="section-head"><div><div className="section-kicker">INVENTORY REPLAY / MACHINE LEARNING</div><h2>Better forecasts. Better stock decisions?</h2></div>
            <span className="section-subtext">{mlReplay.evaluation_start} – {mlReplay.evaluation_end} · diagnostic replay</span></div>
          <div className="warehouse-card"><div className="warehouse-title"><strong>Three policies · identical starting inventory and supplier rules</strong><span>Model fit before the 28-day evaluation</span></div>
            <div className="warehouse-scroll"><table className="replay-table"><thead><tr><th>Policy</th><th>Observed-sales fill rate</th><th>Shortage day rate</th><th>Mean stock (units / series)</th><th>Simulated holding cost</th><th>Units ordered</th></tr></thead>
              <tbody>{Object.entries(mlReplay.policy_comparison).map(([name, metric]) => <tr key={name}><td><strong>{name.replaceAll("_", " ")}</strong></td><td>{metric.fill_rate_pct?.toFixed(2) ?? "n/a"}%</td><td>{metric.stockout_day_pct.toFixed(2)}%</td><td>{fmt(Math.round(metric.average_ending_stock_units_per_series))}</td><td>${metric.holding_cost_usd.toLocaleString("en-US", {maximumFractionDigits: 2})}</td><td>{fmt(metric.units_ordered)}</td></tr>)}</tbody></table></div>
            <div className="replay-context"><strong>What this establishes:</strong> historical-origin LightGBM forecasts can drive actual ordering decisions in the simulator. The model is refit on pre-evaluation history and recursively predicts only from information known at each weekly review. This is a <strong>diagnostic, not new untouched validation</strong>; fill rates use stockout-censored observed sales, supplier arrivals are simplified, and holding costs are illustrative. No measured business savings or true service levels are claimed.</div>
          </div></section>}
        </>}
        {view === "overview" && <>
        <section className="section modules-section">
          <div className="section-head"><div><div className="section-kicker">03 / EXPLORE THE STUDIO</div><h2>Pick a trail. Follow the data.</h2></div><span className="section-subtext">Forecasts, decisions & the evidence behind them</span></div>
          <div className="module-grid">
            {modules.map((module) => <a className="module-card" href={module.href} key={module.number}><span className="module-number">FIELD GUIDE / {module.number} <span className="module-arrow" aria-hidden="true">↗</span></span><h3>{module.title}</h3><p>{module.summary}</p><span className="module-label">{module.label}</span></a>)}
          </div>
        </section>
        </>}
        <footer className="footer"><span><PawMark width={17} height={17}/> pawprint. Supply Studio · Fictional portfolio demonstration</span></footer>
      </main>
    </div>
  );
}
