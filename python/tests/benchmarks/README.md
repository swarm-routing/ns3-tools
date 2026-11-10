# Vendored: the swarm-routing/benchmarks run validator

`tools/validate_run.py`, `schemas/manifest.schema.json` and
`schemas/markers/v1.json`, copied unchanged from
[`swarm-routing/benchmarks`](https://github.com/swarm-routing/benchmarks) at
commit `e7ef2d1c977cb4cc1800e82be53c69d5c9ae6bf7` (benchmarks#17: the schema of
#16 plus the campaign-image tag with its `-optimized` suffix), so
`test_campaign.py` checks the run `ns3-campaign collect` writes offline. The
script is `MIT OR GPL-2.0-only` there and here; the schemas are its data.
Refresh all three from one benchmarks commit when the schema changes, and
update the commit above. `NS3_TOOL_BENCHMARKS=<checkout>` runs a checkout's
validator instead.
