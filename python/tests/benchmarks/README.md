# Vendored: the swarm-routing/benchmarks run validator

`tools/validate_run.py`, `schemas/manifest.schema.json` and
`schemas/markers/v1.json`, copied unchanged from
[`swarm-routing/benchmarks`](https://github.com/swarm-routing/benchmarks) at
commit `7a70c5e1fb393e520599daa9018bf5ec05fbf6f7`: the head of swarm-routing/benchmarks#17, which accepts the
campaign-image tag with its `-optimized` suffix, on top of #16, the schema of
benchmarks#3. Refresh to #17's merge commit once it is merged, so `test_campaign.py` checks the run `ns3-campaign collect`
writes offline. The script is `MIT OR GPL-2.0-only` there and here; the
schemas are its data. Refresh all three from one benchmarks commit when the
schema changes, and update the commit above. `NS3_TOOL_BENCHMARKS=<checkout>`
runs a checkout's validator instead.
