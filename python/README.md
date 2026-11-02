# ns3-tool (Python)

Readers and paired statistics for the comparison harness's marker lines
(`##RUN##`, `##BENCH##`, `# paths`, ...), ported from AntHocNet's
`tools/bench/` (#3). Standard library only, `MIT OR GPL-2.0-only`.

| Module / command | What it does |
|---|---|
| `bench_parse` / `ns3-bench-parse` | parse a cell, per-seed 95% CIs, A/B deltas with the paired verdict (CI + Wilcoxon) |
| `stats_util` | the shared statistics (t half-widths, bootstrap, Wilcoxon signed-rank) |
| `native_view` / `ns3-native-view` | each family cell against its native baselines and the oracle bound |
| `comparator_view` / `ns3-comparator-view` | comparator arms against their anchors and a control generation |
| `pool_runs` / `ns3-pool-runs` | pool per-seed rows across runs of one cell |
| `sweep_summary` / `ns3-sweep-summary` | summarize campaign sweep CSVs |

```bash
pip install ./python                        # console scripts above
python3 python/ns3_tool/bench_parse.py CELL # or run a module as a script
for t in python/tests/test_*.py; do python3 "$t"; done
NS3_TOOL_CELLS=/path/to/AntHocNet/results/cells python3 python/tests/test_bench_parse_query.py
```

`tests/fixtures/cells/` holds 13 AntHocNet cells, one per scenario family
(`GPL-2.0-only` data, see `LICENSING.md`). CI also runs the query-equivalence
test on AntHocNet's whole `results/cells/` corpus.

Not ported yet: `scenario_check.py` (its anchor floors are AntHocNet's
`ns3/tools/anchors.yml`; it needs an `--anchors` input first) and
`campaign.py` (it dispatches AntHocNet's workflows, so it stays there).
