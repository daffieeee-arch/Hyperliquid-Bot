import { PaperScreen } from "../../components/paper/paper-screen";
import { NativePaperScreen } from "../../components/paper/native-paper-screen";
import { loadNativePaperProjection } from "../../lib/native-paper";
import { requirePaperTradingMode } from "../../lib/paths";
import { loadCockpitPageData, type CockpitSearchParams } from "../../lib/server-load";

export const dynamic = "force-dynamic";
export const revalidate = 0;

export default async function PaperPage({
  searchParams,
}: {
  searchParams: Promise<CockpitSearchParams>;
}) {
  requirePaperTradingMode(process.env.TRADING_MODE);
  if (process.env.COCKPIT_NATIVE_PAPER_RUN_DIR !== undefined) {
    return <NativePaperScreen projection={loadNativePaperProjection()} />;
  }
  const data = loadCockpitPageData(await searchParams);
  return <PaperScreen view={data.paper} identities={data.identities} />;
}
