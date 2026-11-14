# Explicit pre-label engineering amendment

This does not replace the original registration. Original commit
`24afaa11a9970d264d623b633a696aeeb1d3f902`, protocol SHA256
`024b5080277fa5ebef06e5d5b42f31aaed73181cde3717c36c6a459946677296`,
and private v2/v3 designs remain unchanged. No financial labels preceded this
amendment. The coordinator explicitly accepted the following two clarifications.

1. The source ceiling becomes **20,000,000 physical raw rows**, including LOCAL,
   outbound and excluded profiles. Fixed-pair metadata reports 13,992,494 rows;
   the original 10M ceiling would fail on that same previously selected pair.
   There is no change to campaign, full capture scope, financial window, alpha,
   native validation, 4MiB part bound, 4GiB private source projection or 32MiB
   unresolved linkage bound. No alternative window is selected to fit a limit.
2. The 10-USDC minimum applies to the **submitted entry order**. A nonzero IOC
   partial below that amount is still an owned position, with its actual entry
   fees, quantity and initial reference margin. It immediately starts the fixed
   reduce-only liquidity-exit procedure; it cannot become a flat nonfill. Exit
   retries retain the original band/deadline and each retry's execution delay.

Private source projections use deterministic streaming gzip NDJSON (mtime=0),
with a hard 4GiB actual-byte limit including headers and footers, bounded 128KiB
decoded lines, 32GiB decoded-stream ceiling and unchanged 32MiB pending-link
limit. This avoids redundant expanded RAW/WITNESS text exceeding the local
budget; no source content or financial selection is changed. The whole
compressed projection is hash-verified before/after the four serial cases.

The initial source index has the exact schema documented in
`research.fast_catchup_io.load`; expected SHA256 is mandatory. Its independent
acceptance receipt must bind the complete same `captures` object, including
physical WORK parts and all twelve WORK/HDD metadata references per lane.
The raw funding response and explicitly complete padded request bounds are
bound separately. An incomplete template is not a runnable index.

Decoder engineering is fail closed: native USD-M IDs must be nondecreasing,
identical repeated IDs have identical payload signatures and are not new flow
or fresh quotes. A regression or conflict fails admission rather than silently
sorting, imputing or allocating an unbounded native-ID dictionary. Missing
`nq` is allowed and never imputed; observed `st=1` is an epoch-specific product
guard, not a universal historical schema claim. The prelaunch UTC-minus-mono
offset is fixed before native inspection; drift beyond 100ms fails this
conditional clock model instead of being fit away. Missing exact historical
precision remains the explicit BTC size-decimals=5 epoch assumption.

Producer `589372720d81c4524b47cc7d89d1b4923b68f27c` has byte-identical
`binance_public_research.py` and `hyperliquid_capture.py` to pilot producer
`758393e20cf2495274638030d1bf4774a100b533`. Thus the raw prequeue clock,
append-lock ordinals, linked LOCAL re-clocking and next-HL-inbound receiver
witness semantics checked for the pilot have unchanged function lineage.
Full fixed-pair integrity/native qualification is still separately required;
the pilot does not qualify the economic population.

Source/protocol review and merge alone admit **no financial run**. Only C100,
four fixed chains and the original two margin targets are in scope. 500/2500
remain unregistered NOT_RUN. No live risk, runtime, collector or broker changes.
