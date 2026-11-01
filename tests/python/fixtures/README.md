# TrueNAS getter shape fixture

`truenas_app_get_instance_direct.json` is synthetic. It preserves the observed
TrueNAS27 Custom App getter envelope, the five direct Compose top-level fields,
the managed service names and command-list types. It omits operational service
settings and uses a fixture-only LAN address and placeholder commands. Tests
insert deterministic RuntimePlan commands before checking full process identity.

This fixture contains no copied NAS instance identifiers, raw configuration hash,
credentials or market data. It is a response-shape regression, not an attestation
that an actual App was admitted or changed. The separate operator record retains
the original private getter response and canonical raw hash.
