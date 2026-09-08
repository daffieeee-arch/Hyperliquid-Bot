import { PaperScreen } from "../../components/paper/paper-screen";
import { loadCockpitPageData, type CockpitSearchParams } from "../../lib/server-load";

export const dynamic = "force-dynamic";
export const revalidate = 0;

export default async function PaperPage({
  searchParams,
}: {
  searchParams: Promise<CockpitSearchParams>;
}) {
  const data = loadCockpitPageData(await searchParams);
  return <PaperScreen view={data.paper} identities={data.identities} />;
}
