import ClientWorkspace from "../../components/ClientWorkspace";

type Params = { sku_id?: string; warehouse_id?: string };
export default async function Page({searchParams}:{searchParams:Promise<Params>}) {
  const query=await searchParams;
  return <ClientWorkspace view="forecasts" skuId={query.sku_id} warehouseId={query.warehouse_id}/>;
}
