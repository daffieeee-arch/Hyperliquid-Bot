import { MarketsScreen } from "../../components/markets/markets-screen";
import {
  loadCockpitPageData,
  loadInitialMarketTape,
  type CockpitSearchParams,
} from "../../lib/server-load";

export const dynamic = "force-dynamic";
export const revalidate = 0;

export default async function MarketsPage({
  searchParams,
}: {
  searchParams: Promise<CockpitSearchParams>;
}) {
  const data = loadCockpitPageData(await searchParams);
  const tape = await loadInitialMarketTape(data.query);
  return (
    <MarketsScreen
      query={data.query}
      initialStrip={data.strip}
      initialTape={tape}
      soakPnl={data.snapshot?.pnl}
    />
  );
}
