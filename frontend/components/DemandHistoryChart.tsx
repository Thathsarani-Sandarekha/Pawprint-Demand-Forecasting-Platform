type HistoryPoint = { date: string; units_sold: number; stockout_observed: boolean; promo_active: boolean };
type ForecastPoint = { date: string; predicted_units: number; lower_units?: number; upper_units?: number };

export default function DemandHistoryChart({ history, forecast, name }: {
  history: HistoryPoint[];
  forecast: ForecastPoint[];
  name: string;
}) {
  if (!history.length || !forecast.length) return <div className="empty-panel">No historical series and forecast are available for this selection.</div>;
  // Fixed, readable SVG with a truthful y-axis and a visible forecast boundary.
  const historical = history.slice(-60);
  const count = historical.length + forecast.length;
  const w = 920, h = 280, left = 51, right = 14, top = 20, bottom = 42;
  const plotW = w - left - right, plotH = h - top - bottom;
  const maxObserved = Math.max(...historical.map(r => r.units_sold), 0);
  const maxPredicted = Math.max(...forecast.map(r => r.upper_units ?? r.predicted_units), 0);
  const yMax = Math.max(1, Math.ceil(Math.max(maxObserved, maxPredicted) * 1.1));
  const x = (idx: number) => left + (idx / Math.max(1, count - 1)) * plotW;
  const y = (n: number) => top + plotH - (n / yMax) * plotH;
  const lines = (points: { x: number; y: number }[]) => points.map(p => `${p.x.toFixed(2)},${p.y.toFixed(2)}`).join(" ");
  const histPts = historical.map((p, i) => ({ x: x(i), y: y(p.units_sold) }));
  const forecastPts = forecast.map((p, i) => ({ x: x(historical.length + i), y: y(p.predicted_units) }));
  const connected = [{ x: x(historical.length - 1), y: y(historical.at(-1)!.units_sold) }, ...forecastPts];
  const upper = forecast.map((p, i) => ({ x: x(historical.length + i), y: y(p.upper_units ?? p.predicted_units) }));
  const lower = forecast.map((p, i) => ({ x: x(historical.length + i), y: y(p.lower_units ?? p.predicted_units) })).reverse();
  const lineCount = 4;
  const stockouts = historical.filter(p => p.stockout_observed).length;
  // Use fixed ISO-date parsing for identical SSR and browser axis labels, independent of local timezone.
  const fmt = (s: string) => {
    const month = Number(s.slice(5, 7));
    const day = Number(s.slice(8, 10));
    const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
    return month >= 1 && month <= 12 && day >= 1 && day <= 31 ? `${months[month - 1]} ${day}` : s;
  };
  const trend = forecast.reduce((sum, p) => sum + p.predicted_units, 0);
  return <section className="history-card" aria-label={`${name}: 60 days of observed sales and 28 days of forecast`}>
    <div className="history-heading"><div><strong>Sales history & demand forecast</strong><p>{historical.length} days of sales history, followed by {forecast.length} days of expected demand</p></div><span className="baseline-chip">28-day outlook · {Math.round(trend).toLocaleString("en-US")} units</span></div>
    <div className="chart-legend"><span><i className="legend-observed"/>Observed sales</span><span><i className="legend-forecast"/>Forecast</span><span><i className="legend-band"/>Nominal prediction interval</span><span><i className="legend-stockout"/>Zero-ending-stock flag</span></div>
    <div className="history-chart-scroll"><svg viewBox={`0 0 ${w} ${h}`} role="img" aria-label={`Observed sales line, then forecast line with nominal intervals. ${stockouts} recent stockout-flagged days. Max axis ${yMax} units.`}>
      {Array.from({ length: lineCount + 1 }, (_, i) => { const v = yMax * i / lineCount; return <g key={i}><line x1={left} x2={w-right} y1={y(v)} y2={y(v)} stroke="#e4e8f0" strokeDasharray="4 5"/><text x={left-9} y={y(v)+4} textAnchor="end" fontSize="11" fill="#63738a">{Math.round(v)}</text></g>; })}
      <polygon points={lines([...upper, ...lower])} fill="#a8b6ff" opacity="0.32"/>
      <polyline points={lines(histPts)} fill="none" stroke="#344568" strokeWidth="2.3" strokeLinejoin="round"/>
      <polyline points={lines(connected)} fill="none" stroke="#6255d8" strokeWidth="2.8" strokeLinejoin="round"/>
      <line x1={x(historical.length-1) + (x(1)-x(0))/2} x2={x(historical.length-1) + (x(1)-x(0))/2} y1={top} y2={top+plotH} stroke="#9da9be" strokeWidth="1.5" strokeDasharray="5 5"/>
      {historical.map((p, i) => p.stockout_observed ? (
        // React/Next can report hydration mismatches for nested SVG title text.
        // Keep the marker and its accessible description without a nested SVG title element.
        <circle
          key={p.date}
          cx={x(i)}
          cy={y(p.units_sold)}
          r="3.4"
          fill="#d07b24"
          role="img"
          aria-label={`${p.date}: ${p.units_sold} observed units, zero ending stock`}
        />
      ) : null)}
      <text x={x(0)} y={h-13} fill="#63738a" fontSize="11">{fmt(historical[0].date)}</text>
      <text x={x(historical.length-1)} y={h-13} textAnchor="middle" fill="#63738a" fontSize="11">{fmt(historical.at(-1)!.date)}</text>
      <text x={x(count-1)} y={h-13} textAnchor="end" fill="#63738a" fontSize="11">{fmt(forecast.at(-1)!.date)}</text>
      <text x={x(historical.length)+8} y={top+14} fill="#5145c7" fontSize="11">Forecast begins</text>
    </svg></div>
    <p className="history-footnote">{stockouts} zero-ending-stock days in the displayed history. Observed sales may understate true demand during stockouts. The shaded range is a nominal interval, not a guaranteed SKU-level bound or service level.</p>
  </section>;
}
