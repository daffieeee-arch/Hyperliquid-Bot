import { SystemScreen } from "../../components/system/system-screen";
import { buildRiskView } from "../../lib/risk";
import { loadCockpitPageData, type CockpitSearchParams } from "../../lib/server-load";

export const dynamic = "force-dynamic";
export const revalidate = 0;

export default async function SystemPage({
  searchParams,
}: {
  searchParams: Promise<CockpitSearchParams>;
}) {
  const data = loadCockpitPageData(await searchParams);
  const risk = buildRiskView(data.snapshot, data.snapshotError, data.strip);
  return (
    <SystemScreen
      query={data.query}
      initialStrip={data.strip}
      initialData1A={data.data1a}
      secondRow={data.secondRow}
      risk={risk}
    />
  );
}
