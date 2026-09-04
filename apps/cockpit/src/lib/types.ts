export type JsonObject = Record<string, unknown>;

export type PaperRunClaim = {
  mode: "PAPER";
  run_id: string;
  schema: string;
  seconds?: number;
  feed?: string;
  state?: string;
  d22b_venue_authoritative_reconciliation?: boolean;
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
