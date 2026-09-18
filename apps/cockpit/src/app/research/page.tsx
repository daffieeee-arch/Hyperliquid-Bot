import { ResearchScreen } from "../../components/research/research-screen";
import { loadCockpitPageData, type CockpitSearchParams } from "../../lib/server-load";

export const dynamic = "force-dynamic";
export const revalidate = 0;

export default async function ResearchPage({
  searchParams,
}: {
  searchParams: Promise<CockpitSearchParams>;
}) {
  const data = loadCockpitPageData(await searchParams);
  return <ResearchScreen query={data.query} initialResearch={data.research} />;
}
