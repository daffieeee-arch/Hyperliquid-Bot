export const HYPERLIQUID_PUBLIC_INFO_URL = "https://api.hyperliquid.xyz/info";
export const BTC_PERP_COIN = "BTC";

export type PublicBtcPerpPrice = {
  coin: string;
  instrument: string;
  mid: string;
  source: "hyperliquid-public-info-allMids";
  endpoint: string;
  signing: false;
  credentialless: true;
  fetched_at: string;
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function extractBtcMid(payload: unknown): string {
  if (!isRecord(payload)) {
    throw new Error("Hyperliquid allMids response must be a JSON object.");
  }
  const mid = payload[BTC_PERP_COIN];
  if (typeof mid !== "string" || mid === "") {
    throw new Error("Hyperliquid allMids response is missing a BTC mid string.");
  }
  return mid;
}

export function parsePublicBtcPerpPayload(payload: unknown): PublicBtcPerpPrice {
  if (!isRecord(payload) || typeof payload.mid !== "string" || payload.mid === "") {
    throw new Error("Public price response did not include a BTC mid string.");
  }
  if (
    payload.source !== "hyperliquid-public-info-allMids" ||
    typeof payload.instrument !== "string" ||
    payload.instrument === "" ||
    typeof payload.fetched_at !== "string" ||
    payload.fetched_at === "" ||
    typeof payload.endpoint !== "string" ||
    payload.endpoint === "" ||
    typeof payload.coin !== "string" ||
    payload.coin === "" ||
    payload.signing !== false ||
    payload.credentialless !== true
  ) {
    throw new Error("Public price response is not a credentialless unsigned BTC mid.");
  }
  return {
    coin: payload.coin,
    instrument: payload.instrument,
    mid: payload.mid,
    source: "hyperliquid-public-info-allMids",
    endpoint: payload.endpoint,
    signing: false,
    credentialless: true,
    fetched_at: payload.fetched_at,
  };
}

export async function fetchPublicBtcPerpMid(
  fetchImpl: typeof fetch = fetch,
  now: () => string = () => new Date().toISOString(),
): Promise<PublicBtcPerpPrice> {
  const response = await fetchImpl(HYPERLIQUID_PUBLIC_INFO_URL, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ type: "allMids" }),
    cache: "no-store",
    signal: AbortSignal.timeout(8_000),
  });
  if (!response.ok) {
    throw new Error(`Hyperliquid public /info returned HTTP ${String(response.status)}.`);
  }
  const payload: unknown = await response.json();
  return {
    coin: BTC_PERP_COIN,
    instrument: "BTC-PERP",
    mid: extractBtcMid(payload),
    source: "hyperliquid-public-info-allMids",
    endpoint: HYPERLIQUID_PUBLIC_INFO_URL,
    signing: false,
    credentialless: true,
    fetched_at: now(),
  };
}
