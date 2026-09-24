export type JsonObject = Record<string, unknown>;

export type PaperRunPreflight = {
  strategy_class: string;
  order_quantity_btc: string;
  max_entry_notional_usdc: string;
  max_assumed_loss_usdc: string;
  same_d01_smoke_risk: boolean;
};

export type PaperRunClaim = {
  mode: "PAPER";
  run_id: string;
  schema: string;
  seconds?: number;
  feed?: string;
  state?: string;
  d22b_venue_authoritative_reconciliation?: boolean;
  run_identity?: string;
  config_sha256?: string;
  source_sha256?: string;
  websocket_url?: string;
  resume_policy?: string;
  preflight?: PaperRunPreflight;
};

export type PaperPosition = {
  kind: "paper-position";
  schema: string;
  path_contract: string;
  mode: "PAPER";
  instrument_id: string;
  final_position_btc: string;
  business_final_position_quantity: string;
  venue_authoritative: boolean;
  twenty_four_seven: boolean;
  limitations: string[];
};

export type PaperPnl = {
  kind: "paper-pnl";
  schema: string;
  path_contract: string;
  mode: "PAPER";
  assumed: boolean;
  venue_pnl: boolean;
  instrument_id: string;
  starting_cash_usdc_assumed: string;
  ending_cash_usdc_assumed: string;
  ending_equity_usdc_assumed: string;
  net_pnl_usdc_assumed: string;
  fee_cost_usdc: string;
  half_spread_cost_usdc: string;
  slippage_cost_usdc: string;
  funding_payment_usdc: string;
  mark_price: string;
  final_position_btc: string;
  limitations: string[];
};

export type CaptureHealth = {
  kind: "capture-health";
  schema: string;
  path_contract: string;
  feed: string;
  status: string;
  credentialless: boolean;
  twenty_four_seven: boolean;
  trade_count: number;
  bbo_count: number;
  adapter_rejected_count: number;
  risk_rejections: number;
  subscriptions_acknowledged: string[];
  limitations: string[];
};

export type PaperOrderIntent = {
  client_order_id: string;
  side: string;
  quantity: string;
  order_type: string;
  reason: string;
  reduce_only: boolean;
  risk_reasons?: string[];
};

export type PaperFillRow = {
  fill_ordinal: number;
  side: string;
  price: string;
  quantity: string;
  liquidity_side: string;
  position_after: string;
};

export type PaperOrders = {
  kind: "orders";
  schema: string;
  path_contract: string;
  mode: "PAPER";
  venue_orders_submitted: boolean;
  order_count: number;
  intents: PaperOrderIntent[];
  limitations: string[];
};

export type PaperFills = {
  kind: "fills";
  schema: string;
  path_contract: string;
  mode: "PAPER";
  fill_count: number;
  fills: PaperFillRow[];
  limitations: string[];
};

export type PaperRunSnapshot = {
  runDir: string;
  runId: string;
  source: "paper-run-dir" | "path-contract" | "default-fixture";
  claim: PaperRunClaim;
  position: PaperPosition;
  pnl: PaperPnl;
  health: CaptureHealth;
  orders: PaperOrders;
  fills: PaperFills;
};

export type Data1ACaptureClaim = {
  schema: string;
  path_contract: string;
  run_id: string;
  state: string;
  retained: boolean;
  twenty_four_seven: false;
  credentialless?: boolean;
  signing?: boolean;
  venue?: string;
  product?: string;
  feed?: string;
  duration_seconds?: number;
  resume_policy?: string;
};

export type TransportDisconnectFields = {
  exception_class?: string;
  close_code?: number;
  close_code_rcvd?: number;
  close_code_sent?: number;
  close_reason_rcvd?: string;
  close_reason_sent?: string;
  errno?: number;
};

export type TransportProfileRow = TransportDisconnectFields & {
  transport_profile: string;
  gaps?: number;
  reconnects?: number;
  reconnect_clusters?: number;
};

export type Data1ACaptureHealth = TransportDisconnectFields & {
  schema: string;
  kind: "capture-health";
  path_contract: string;
  run_id: string;
  status: string;
  retained: boolean;
  twenty_four_seven: false;
  credentialless?: boolean;
  gaps?: number;
  reconnects?: number;
  reconnect_clusters?: number;
  events?: number;
  parquet_files?: number;
  parquet_bytes?: number;
  transport_profiles?: TransportProfileRow[];
  limitations: string[];
};

export type Data1APartListing = {
  raw_dir_present: boolean;
  count: number | undefined;
  last_part_name: string | undefined;
  last_part_mtime_utc: string | undefined;
  bytes: number | undefined;
};

export type CaptureBindingSource =
  "query" | "env" | "explicit-dir" | "auto-detect" | "default-fixture" | "unbound";

export type CaptureRunCandidate = {
  run_id: string;
  has_claim: boolean;
  has_health: boolean;
  live: boolean;
  last_part_mtime_utc?: string;
  part_count?: number;
};

export type VenueCaptureCatalog = {
  id: "hl" | "binance" | "bitvavo-std" | "bitvavo" | "kraken";
  chip: "HL" | "BINANCE" | "BV-STD" | "BITVAVO" | "KRAKEN";
  series: "DATA-1A" | "DATA-1B" | "DATA-1D" | "DATA-1E" | "DATA-1F";
  query_key:
    "data1a_run_id" | "data1b_run_id" | "data1d_run_id" | "data1e_run_id" | "data1f_run_id";
  candidates: CaptureRunCandidate[];
};

export type CaptureRunProvenanceRow = {
  id: "hl" | "binance" | "bitvavo-std" | "bitvavo" | "kraken";
  chip: "HL" | "BINANCE" | "BV-STD" | "BITVAVO" | "KRAKEN";
  series: "DATA-1A" | "DATA-1B" | "DATA-1D" | "DATA-1E" | "DATA-1F";
  run_id: string;
  binding_source: CaptureBindingSource;
  started_at_utc?: string;
};

export type CaptureRunProvenance = {
  rows: CaptureRunProvenanceRow[];
  distinct_run_ids: string[];
  shared_run_ids: string[];
  overlap_starts_utc?: string;
  overlap_note: string;
};

export type CaptureLiveFeedState = "fresh" | "recovering" | "definitive_outage" | "unknown";

export type CaptureLiveFeed = {
  name: string;
  role: "required" | "optional";
  state: CaptureLiveFeedState;
  last_market_utc: string | undefined;
  silence_bound_seconds: number;
};

export type CaptureLiveOutage = {
  name: string;
  error_class: string;
  error_message: string;
};

export type CaptureLiveStatus = {
  schema: "capture-live-v1";
  kind: "capture-live";
  run_id: string;
  file_mtime_utc: string;
  writer_pending_records: number | null;
  writer_published_parts: number | null;
  feeds: CaptureLiveFeed[];
  definitive_outage: CaptureLiveOutage | null;
};

export type Data1ACaptureSnapshot = {
  runDir: string;
  runId: string;
  source: "data1a-run-dir" | "venue-run-dir" | "path-contract" | "auto-detect" | "default-fixture";
  observed_at: string;
  fresh_max_s: number;
  path_contract: string;
  claim: Data1ACaptureClaim;
  health: Data1ACaptureHealth | undefined;
  health_missing: boolean;
  health_error: string | undefined;
  live: CaptureLiveStatus | undefined;
  live_error: string | undefined;
  parts: Data1APartListing;
  duckdb_present: boolean;
};

export type Data1ACaptureResponse =
  { ok: true; snapshot: Data1ACaptureSnapshot } | { ok: false; error: string };

export type VenueCaptureChipStatus =
  "RUNNING" | "STALE" | "DEGRADED" | "STOPPED" | "MISSING" | "UNKNOWN";

export type VenueCaptureChip = {
  id: "hl" | "binance" | "bitvavo-std" | "bitvavo" | "kraken";
  chip: "HL" | "BINANCE" | "BV-STD" | "BITVAVO" | "KRAKEN";
  series: "DATA-1A" | "DATA-1B" | "DATA-1D" | "DATA-1E" | "DATA-1F";
  venue: string;
  product: string;
  path_contract: string;
  status: VenueCaptureChipStatus;
  status_detail: string;
  tone: "ok" | "warn" | "down" | "neutral";
  live: boolean;
  reason?: string;
  part_count: number | undefined;
  last_part_age: string;
  last_part_mtime_utc: string | undefined;
  gaps: number | undefined;
  reconnects: number | undefined;
  reconnect_clusters?: number;
  exception_class?: string;
  close_code?: number;
  close_code_rcvd?: number;
  close_code_sent?: number;
  close_reason_rcvd?: string;
  close_reason_sent?: string;
  errno?: number;
  run_id: string | undefined;
  binding_source: CaptureBindingSource;
  observed_at: string;
  error: string | undefined;
};

export type VenueCaptureStrip = {
  observed_at: string;
  fresh_max_s: number;
  venues: VenueCaptureChip[];
  catalog: VenueCaptureCatalog[];
  provenance: CaptureRunProvenance;
};

export type VenueCaptureStripResponse =
  { ok: true; strip: VenueCaptureStrip } | { ok: false; error: string };
