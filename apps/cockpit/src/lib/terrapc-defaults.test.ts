import { describe, expect, it } from "vitest";

import {
  TERRAPC_ACTIVE_RETAIN_RUN_IDS,
  TERRAPC_BINANCE_ACTIVE_RETAIN_RUN_ID,
  TERRAPC_BINANCE_STOPPED_RETAIN_RUN_ID,
  TERRAPC_SHARED_RETAIN_RUN_ID,
  isTerrapcActiveRetain,
} from "./terrapc-defaults";

describe("TerraPC retain defaults", () => {
  it("documents the current HL/BV/KR share and the post-#58 Binance retain", () => {
    expect(TERRAPC_SHARED_RETAIN_RUN_ID).toBe("20260905t232635z-live-retained");
    expect(TERRAPC_BINANCE_ACTIVE_RETAIN_RUN_ID).toBe("20260906t101559z-live-retained");
    expect(TERRAPC_BINANCE_STOPPED_RETAIN_RUN_ID).toBe("20260905t235830z-live-retained");
    expect(TERRAPC_ACTIVE_RETAIN_RUN_IDS).toEqual({
      hl: TERRAPC_SHARED_RETAIN_RUN_ID,
      binance: TERRAPC_BINANCE_ACTIVE_RETAIN_RUN_ID,
      bitvavo: TERRAPC_SHARED_RETAIN_RUN_ID,
      kraken: TERRAPC_SHARED_RETAIN_RUN_ID,
    });
    expect(isTerrapcActiveRetain("binance", TERRAPC_BINANCE_ACTIVE_RETAIN_RUN_ID)).toBe(true);
    expect(isTerrapcActiveRetain("binance", TERRAPC_BINANCE_STOPPED_RETAIN_RUN_ID)).toBe(false);
    expect(isTerrapcActiveRetain("hl", TERRAPC_SHARED_RETAIN_RUN_ID)).toBe(true);
  });
});
