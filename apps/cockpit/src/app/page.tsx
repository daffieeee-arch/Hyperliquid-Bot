import { OverviewScreen } from "../components/overview/overview-screen";
import { loadCockpitPageData, type CockpitSearchParams } from "../lib/server-load";

export const dynamic = "force-dynamic";
export const revalidate = 0;

export default async function OverviewPage({
  searchParams,
}: {
  searchParams: Promise<CockpitSearchParams>;
}) {
  const data = loadCockpitPageData(await searchParams);
  return (
    <OverviewScreen
      query={data.query}
      initialStrip={data.strip}
      initialResearch={data.research}
      paper={data.paper}
    />
  );
}
