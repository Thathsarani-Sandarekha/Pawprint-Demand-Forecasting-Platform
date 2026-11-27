import ClientWorkspace from "../../components/ClientWorkspace";

type Params = { sku_id?: string; warehouse_id?: string; priority?: string; review?: string; expiry?: string; category?: string };
export default async function Page({searchParams}:{searchParams:Promise<Params>}) {
  const query=await searchParams;
  return <ClientWorkspace view="inventory" skuId={query.sku_id} warehouseId={query.warehouse_id} priority={query.priority} review={query.review} expiry={query.expiry} category={query.category}/>;
}
