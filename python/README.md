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
| `scenario_check` / `ns3-scenario-check` | `preflight` a scenario's knobs before a dispatch; `results`: plausibility of a cell, its anchor floor from an `--anchors` file (#28) |
| `campaign` / `ns3-campaign` | multi-protocol campaigns: a plan (`campaigns/*.toml`) built into one image, every cell run in it by digest, collected into a swarm-routing/benchmarks run with its manifest, paired verdicts (#66) |

```bash
pip install ./python                        # console scripts above
python3 python/ns3_tool/bench_parse.py CELL # or run a module as a script
for t in python/tests/test_*.py; do python3 "$t"; done
NS3_TOOL_CELLS=/path/to/AntHocNet/results/cells python3 python/tests/test_bench_parse_query.py
```

`tests/fixtures/cells/` holds 13 AntHocNet cells, one per scenario family
(`GPL-2.0-only` data, see `LICENSING.md`). CI also runs the query-equivalence
test on AntHocNet's whole `results/cells/` corpus.

`scenario_check` is AntHocNet's script with the protocol taken out (#28). The
anchor floors come from a file in the schema of AntHocNet's
`ns3/tools/anchors.yml` (`key: number` lines); `--anchor` names one of its
`_pdr_min` keys (the PDR floors; the file's other bounds are not checked
here), or the key's stem (`broch-low-mobility-aodv` for
`broch_low_mobility_aodv_pdr_min`). The arms a rule needs are arguments:
`--anchor-arms` (the rows an anchor floor judges; default every row),
`--single-path`, `--multipath`, `--oracle` (default: the arm of a cell's
`##ORACLE##` rows) and `--hold-ceiling [ARM=]MS`. AntHocNet's own rules stay
there: the `##REINJ##` books (AntHocNet#386/#402), the opspf LSA rule (#707),
and the per-arm preflight refusals. AntHocNet's verdicts, with its roles:

```bash
ns3-scenario-check results CELL --anchors anchors.yml --anchor broch-low-mobility-aodv \
    --anchor-arms aodv --single-path aodv,olsr,dsdv --multipath anthocnet --oracle oracle \
    --hold-ceiling ara=35000 --hold-ceiling termite=35000 \
    --hold-ceiling qrouting=35000 --hold-ceiling beeadhoc=35000
```

`tests/test_scenario_check_corpus.py` holds the port to that: line for line on
the fixture cells (AntHocNet's output recorded in
`tests/fixtures/scenario_check/`), and on the whole corpus in CI.

AntHocNet's
`tools/bench/campaign.py` is AntHocNet-only (it dispatches AntHocNet's own
`benchmark-cell.yml`); `campaign` here ports its shape (throttle, run log,
collect) for campaigns that pair several repositories' modules.
