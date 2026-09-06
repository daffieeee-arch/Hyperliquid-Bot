import { Data1ACapturePanel } from "../components/data1a-capture";
import { DeskBanner } from "../components/desk-banner";
import { DataRetainIdentityCardPanel, SoakIdentityCardPanel } from "../components/identity-cards";
import { MarketsPanel } from "../components/markets-panel";
import { PaperBotPanel } from "../components/paper-bot-panel";
import { ResearchP0Panel } from "../components/research-p0-panel";
import { RiskPanel } from "../components/risk-panel";
import { SecondRowPanels } from "../components/second-row";
import { VenueCaptureStrip } from "../components/venue-capture-strip";
import { loadData1ACaptureSnapshot } from "../lib/data1a-capture";
import { buildSeparateIdentityCards } from "../lib/identity-cards";
import { buildPaperBotView } from "../lib/paper-bot";
import { loadPaperRunSnapshot } from "../lib/paper-run";
import { firstQueryValue, findRepoRoot, type VenueCaptureQuery } from "../lib/paths";
import { buildResearchP0View } from "../lib/research-p0";
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
  const research = buildResearchP0View(venueStrip, process.env, findRepoRoot(), venueQuery);

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
        <section id="health" className="zone" aria-label="Health">
          <h2 className="zone-label">Health</h2>
          <p className="zone-kicker">
            Read-only capture strip + picker + DATA-1A. Start/stop is vetoed. D01 bind and BN
            usdm_public stay here — not on the COURSE-1 DESK bot card.
          </p>
          <SecondRowPanels view={secondRow} />
          <VenueCaptureStrip query={venueQuery} initial={venueStrip} />
          <Data1ACapturePanel queryRunId={data1aRunId} initial={data1a} />
        </section>
        <div id="markets">
          <MarketsPanel query={venueQuery} initial={venueStrip} soakPnl={snapshot?.pnl} />
        </div>
        <div id="research">
          <ResearchP0Panel view={research} />
        </div>
        <section id="paper" className="zone" aria-label="PAPER">
          <h2 className="zone-label">PAPER</h2>
          <p className="zone-kicker">
            DESK bot is COURSE-1 soak / paperbroker only. DATA retain stays a separate card.
            assumed_pnl is not venue-reconciled. D22-B is blocked.
          </p>
          {snapshotError !== undefined ? <p className="error">{snapshotError}</p> : null}
          <div className="zone-grid zone-grid-2">
            <SoakIdentityCardPanel card={identities.soak} />
            <DataRetainIdentityCardPanel card={identities.retain} />
          </div>
          <PaperBotPanel view={paperBot} />
        </section>
        <div id="risk">
          <RiskPanel
            snapshot={snapshot}
            snapshotError={snapshotError}
            query={venueQuery}
            initial={venueStrip}
          />
        </div>
      </main>
      <footer className="statusbar">
        <div className="statusbar-scan">
          <span>PAPER ONLY</span>
          <span>Fail-Closed Amber</span>
        </div>
        <details className="statusbar-constraints">
          <summary>Fail-closed constraints</summary>
          <div className="statusbar-constraints-list">
            <span>NO WALLET SIGNING</span>
            <span>NO LIVE CAPITAL</span>
            <span>NO CAPTURE START/STOP</span>
            <span>SOAK != RETAIN</span>
            <span>ASSUMED != VENUE RECONCILED</span>
            <span>RISK != INVENTED</span>
            <span>RESEARCH P0 · FAIL-CLOSED</span>
            <span>PUBLIC MID != RESEARCH</span>
            <span>PUBLIC MID != PAPER PNL</span>
          </div>
        </details>
      </footer>
    </div>
  );
}
