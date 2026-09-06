import { Data1ACapturePanel } from "../components/data1a-capture";
import { DeskBanner } from "../components/desk-banner";
import { DataRetainIdentityCardPanel, SoakIdentityCardPanel } from "../components/identity-cards";
import { MarketsPanel } from "../components/markets-panel";
import { PaperBotPanel } from "../components/paper-bot-panel";
import { ResearchStub } from "../components/research-stub";
import { RiskPanel } from "../components/risk-panel";
import { SecondRowPanels } from "../components/second-row";
import { VenueCaptureStrip } from "../components/venue-capture-strip";
import { loadData1ACaptureSnapshot } from "../lib/data1a-capture";
import { buildSeparateIdentityCards } from "../lib/identity-cards";
import { buildPaperBotView } from "../lib/paper-bot";
import { loadPaperRunSnapshot } from "../lib/paper-run";
import { firstQueryValue, findRepoRoot, type VenueCaptureQuery } from "../lib/paths";
import { buildSecondRowView } from "../lib/second-row";
import type {
  Data1ACaptureResponse,
  PaperRunSnapshot,
  VenueCaptureStripResponse,
} from "../lib/types";
import { loadVenueCaptureStrip } from "../lib/venue-capture";

export const dynamic = "force-dynamic";
export const revalidate = 0;

function loadVenueStripResponse(query: VenueCaptureQuery): VenueCaptureStripResponse {
  try {
    return {
      ok: true,
      strip: loadVenueCaptureStrip(process.env, findRepoRoot(), query),
    };
  } catch (error: unknown) {
    return {
      ok: false,
      error: error instanceof Error ? error.message : "Multi-venue capture health is unavailable.",
    };
  }
}

function loadData1AResponse(queryRunId: string | undefined): Data1ACaptureResponse {
  try {
    return {
      ok: true,
      snapshot: loadData1ACaptureSnapshot(process.env, findRepoRoot(), {
        data1a_run_id: queryRunId,
      }),
    };
  } catch (error: unknown) {
    return {
      ok: false,
      error: error instanceof Error ? error.message : "DATA-1A capture health is unavailable.",
    };
  }
}

export default async function FirstPaperScreen({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const params = await searchParams;
  const venueQuery: VenueCaptureQuery = {
    data1a_run_id: firstQueryValue(params.data1a_run_id),
    data1b_run_id: firstQueryValue(params.data1b_run_id),
    data1e_run_id: firstQueryValue(params.data1e_run_id),
    data1f_run_id: firstQueryValue(params.data1f_run_id),
  };
  const data1aRunId = venueQuery.data1a_run_id;
  const venueStrip = loadVenueStripResponse(venueQuery);
  const data1a = loadData1AResponse(data1aRunId);

  let snapshotError: string | undefined;
  let snapshot: PaperRunSnapshot | undefined;
  try {
    snapshot = loadPaperRunSnapshot();
  } catch (error: unknown) {
    snapshotError = error instanceof Error ? error.message : "PAPER run data is unavailable.";
  }

  const identities = buildSeparateIdentityCards(snapshot, snapshotError, venueStrip);
  const secondRow = buildSecondRowView(snapshot, venueStrip);
  const paperBot = buildPaperBotView(snapshot, snapshotError);

  return (
    <div className="terminal">
      <div className="watermark" aria-hidden="true">
        PAPER
      </div>
      <DeskBanner
        snapshot={snapshot}
        snapshotError={snapshotError}
        query={venueQuery}
        initial={venueStrip}
      />
      <main>
        <section className="zone" aria-label="Run identity">
          <h2 className="zone-label">Run identity</h2>
          <p className="zone-kicker">
            COURSE-1 soak and DATA retain stay separate cards. They are never one blended identity
            and never one blended PnL.
          </p>
          <div className="zone-grid zone-grid-2">
            <SoakIdentityCardPanel card={identities.soak} />
            <DataRetainIdentityCardPanel card={identities.retain} />
          </div>
        </section>
        <section className="zone" aria-label="Second row">
          <h2 className="zone-label">Bind / capture / Binance</h2>
          <SecondRowPanels view={secondRow} />
        </section>
        <MarketsPanel query={venueQuery} initial={venueStrip} soakPnl={snapshot?.pnl} />
        <ResearchStub />
        {snapshotError !== undefined ? <p className="error">{snapshotError}</p> : null}
        <PaperBotPanel view={paperBot} snapshot={snapshot} />
        <RiskPanel
          snapshot={snapshot}
          snapshotError={snapshotError}
          query={venueQuery}
          initial={venueStrip}
        />
        <VenueCaptureStrip query={venueQuery} initial={venueStrip} />
        <Data1ACapturePanel queryRunId={data1aRunId} initial={data1a} />
      </main>
      <footer className="statusbar">
        <span>PAPER ONLY</span>
        <span>NO WALLET SIGNING</span>
        <span>NO LIVE CAPITAL</span>
        <span>NO CAPTURE START/STOP</span>
        <span>SOAK != RETAIN</span>
        <span>ASSUMED != VENUE RECONCILED</span>
        <span>RISK != INVENTED</span>
        <span>RESEARCH UNAVAILABLE</span>
        <span>PUBLIC MID != PAPER PNL</span>
        <span>DEFAULT C HYBRID</span>
      </footer>
    </div>
  );
}
