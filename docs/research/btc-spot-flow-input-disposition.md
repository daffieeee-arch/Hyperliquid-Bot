# Original BTC Spot-flow input disposition

Checkpoint: **2026-11-13**. Disposition: **INSUFFICIENT_DATA**; hypothesis
decision: **NOT_TESTED**. No economic runner or financial/OOS decoder was run.
This records the original input attempt; it is not a financial `no_edge` result.

## Frozen experiment

The [protocol](../experiments/exp_btc_spot_flow.md) and
[specification](../experiments/exp_btc_spot_flow.spec.yaml) still match preregistration commit
`811e5e9165430c613db23d669ee8625bcb3e1ef2` byte for byte. The registered window
remains **Nov2 00:00–Nov5 00:00 UTC, 2026**: 12 hours of history, 36 hours of
validation and the final 24 hours reserved as untouched OOS.

The data producer remains identified by source
`71771338c5daeff86c1fbac3b0ba63c9f72df6b7` and its original immutable image,
`sha256:59098068f67c2373d76a64bfd835a5620024b938e4eeb1ec9a769ff956420aa5`.
No source, window, cost, sample-floor or funding contract was changed to obtain
a score. Current continuous NAS collection is a separate dataset.

## Publication admission

An independent reader verified all twelve retained publications across WORK
and HDD: three chunks for each of two venues on each store. Every file's hash
and size, complete inventory, linked seals, destination proofs, original
claims, producer/prelaunch identities and stored clocks passed. Shared files
match across copies; expected WORK-only regenerated catalogs remain separate.
No archived database was executed.

| Venue | Complete chunks / planned | Raw Parquet files per copy | Native gap counts by chunk |
| --- | ---: | ---: | --- |
| Hyperliquid | 3 / 12 | 1,076 | 2, 2, 2 |
| Binance | 3 / 12 | 7,236 | 20, 6, 46 |

Only chunks `0000`–`0002` are complete. Chunks `0003`–`0011` are unpublished
on both stores, and there is no Nov4–5 OOS publication. The last complete
publications were sealed around Nov2 18:14 UTC. All retained native terminals
are COMPLETED with zero integrity events, but recorded gaps remain and every
publication retains `research_ready=false`.

The audit streamed **4,657,688,462 bytes** with bounded buffers and used only
Parquet footer row/receipt-clock metadata. Payloads, executable quotes, flow
features, funding values and trading outcomes were not decoded. Copy integrity
does not admit the missing 72-hour study window.

## Retained partial and funding

The original VM spool retains an unpublished fourth chunk of about 337 MB.
Its HL terminal reports OPERATOR_STOP after 8,899.8 seconds of the requested
21,600 seconds; the Binance terminal is absent. Both partials remain
unqualified and unchanged.

Selected metadata for the exact retained original VM container records exit 2,
`OOMKilled=false`, restart count zero, 8 GiB RAM and four CPU cores. Its old
cgroups and project units are unavailable. The precise stop cause remains
**UNPROVEN**; no historical memory event or peak was inferred from current
container state.

Both Nov6 funding publications independently pass
**PASS_TIMING_METADATA_ONLY** under the frozen
`hl-fundingHistory-api-clock-v1` model: 120 native hourly records and all 74
required padded study events. Inventories, hashes, HTTP receipts, stored clocks
and the frozen acquisition packet match. Rate/premium strings stayed opaque;
no endpoint was fetched again. This does not establish account/onchain cash
settlement, zero realized funding, S4 admission or missing market-data coverage.

## Preserved disposition

The runner's applicable status category is `not_enough_data`; it was not invoked
to manufacture a financial result. No missing outcome is imputed, no namespace
is resumed and no shorter or later period replaces the registered window. A
future confirmatory attempt requires its own preregistration and fresh OOS.

The private operator evidence bundle
`2026-11-12-spotflow-input-disposition` retains the reproducible metadata
verifiers, census, original stop/partial metadata and funding admission.
Its machine disposition SHA-256 is
`a2a7740c12050f2680066d10d489d64429810c1995357e7fbf1eee068bf236ac`;
the independent report SHA-256 is
`75a450981d3d1151ea9ee5902007bce61f60a648479205601b7e254ea931ad2d`.
The original input attempt in
[#192](https://github.com/daffieeee-arch/Hyperliquid-Bot/issues/192#issuecomment-6839899958)
is closed with this disposition. Closing the attempt makes no positive or
negative financial hypothesis decision.
