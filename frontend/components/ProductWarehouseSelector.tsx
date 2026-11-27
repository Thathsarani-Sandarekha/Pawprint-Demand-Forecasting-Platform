"use client";

import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";

type Product = { sku_id: string; product_name: string; category?: string };
type Warehouse = { warehouse_id: string; city: string; state: string };

export default function ProductWarehouseSelector({
  view, skuId, warehouseId, products, warehouses,
}: {
  view: "forecasts" | "inventory";
  skuId: string;
  warehouseId: string;
  products: Product[];
  warehouses: Warehouse[];
}) {
  const router = useRouter();
  const [pending, startTransition] = useTransition();
  const [selectedSku, setSelectedSku] = useState(skuId);
  const [selectedWarehouse, setSelectedWarehouse] = useState(warehouseId);
  const submit = () => startTransition(() => {
    const params = new URLSearchParams({ sku_id: selectedSku, warehouse_id: selectedWarehouse });
    router.push(`/${view}?${params.toString()}`);
  });
  return <section className="explorer-filter" aria-label="Choose product and fulfillment warehouse">
    <div className="explorer-filter-title"><div><strong>Choose your pet product &amp; location</strong><p>Every product has a story. Choose a SKU and a regional fulfillment hub to explore its actual saved forecast or replenishment math.</p></div>
      <span className="baseline-chip">{products.length} SKUS &nbsp; / &nbsp; {warehouses.length} HUBS</span></div>
    <form onSubmit={e => { e.preventDefault(); submit(); }} className="explorer-fields">
      <label htmlFor={`sku-${view}`}>PET PRODUCT
        <select id={`sku-${view}`} value={selectedSku} onChange={e => setSelectedSku(e.target.value)} disabled={pending}>
          {products.map(p => <option key={p.sku_id} value={p.sku_id}>{p.product_name} ({p.sku_id})</option>)}
        </select>
      </label>
      <label htmlFor={`warehouse-${view}`}>FULFILLMENT HUB
        <select id={`warehouse-${view}`} value={selectedWarehouse} onChange={e => setSelectedWarehouse(e.target.value)} disabled={pending}>
          {warehouses.map(w => <option key={w.warehouse_id} value={w.warehouse_id}>{w.city}, {w.state} ({w.warehouse_id})</option>)}
        </select>
      </label>
      <button disabled={pending || !selectedSku || !selectedWarehouse} type="submit">{pending ? "Loading…" : "Explore this pair  ↗"}</button>
    </form>
    <div className="explorer-context">Switching selections reads existing forecasts and live synthetic stock data. It never places an order.</div>
  </section>;
}
