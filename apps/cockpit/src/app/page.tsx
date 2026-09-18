import { OverviewScreen } from "../components/overview/overview-screen";
import {
  loadCockpitPageData,
  loadInitialMarketTape,
  type CockpitSearchParams,
} from "../lib/server-load";

export const dynamic = "force-dynamic";
export const revalidate = 0;

export default async function OverviewPage({
  searchParams,
}: {
  searchParams: Promise<CockpitSearchParams>;
}) {
  const data = loadCockpitPageData(await searchParams);
  const tape = await loadInitialMarketTape(data.query);
  return (
    <OverviewScreen
      query={data.query}
      initialStrip={data.strip}
      initialResearch={data.research}
      initialTape={tape}
      paper={data.paper}
    />
  );
}
