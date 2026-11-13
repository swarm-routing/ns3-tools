# ns3-tool (Python)

Readers and paired statistics for the comparison harness's marker lines
(`##RUN##`, `##BENCH##`, `# paths`, ...), ported from AntHocNet's
`tools/bench/` (#3). Standard library only, `MIT OR GPL-2.0-only`.

`ns3-tool` is the one entry point (#39): `ns3-tool compare` (bench_parse,
byte for byte), `ns3-tool sweep` (a port of AntHocNet's `run-scenarios.py`:
the scenario matrix and the paper's sweeps through the harness;
`--program`, default `routing-compare`; `--dry-run` prints the command lines),
`ns3-tool diagnose CELL [--check ...]` (the drop book per arm, then
scenario_check's `results`) and `ns3-tool scaffold NAME` (an arm module that
registers with routing-bench; CI builds it, `ns3.yml` job `scaffold`). The
console scripts below stay, as aliases of the same modules.

| Module / command | What it does |
|---|---|
| `bench_parse` / `ns3-bench-parse` | parse a cell, per-seed 95% CIs, A/B deltas with the paired verdict (CI + Wilcoxon); `--get FIELD --from perf` reads a `##PERF##` cost field (#91, `docs/markers.md`) |
| `stats_util` | the shared statistics (t half-widths, bootstrap, Wilcoxon signed-rank) |
| `native_view` / `ns3-native-view` | each family cell against its native baselines and the oracle bound |
| `comparator_view` / `ns3-comparator-view` | comparator arms against their anchors and a control generation |
| `pool_runs` / `ns3-pool-runs` | pool per-seed rows across runs of one cell |
| `sweep_summary` / `ns3-sweep-summary` | summarize campaign sweep CSVs |
| `scenario_check` / `ns3-scenario-check` | `preflight` a scenario's knobs before a dispatch; `results`: plausibility of a cell, its anchor floor from an `--anchors` file (#28) |
| `campaign` / `ns3-campaign` | multi-protocol campaigns: a plan (`campaigns/*.toml`) built into one image, every cell run in it by digest, collected into a swarm-routing/benchmarks run with its manifest, paired verdicts (#66); a cell that already ran (same key: image digest, commits, program, sorted flags, arms, seeds, marker version) is reused instead of dispatched, from the local cache, a benchmarks checkout or a workflow artifact (#94) |
| `change_plan` (through `ns3-campaign`) | change-test campaigns in a protocol repository, AntHocNet's plan format (cells x arms, a `generation` key; YAML needs PyYAML, TOML and JSON are stdlib): each cell dispatched to the repository's caller of swarm-routing/.github's `reusable-benchmark-cell.yml`, pinned to the plan's `source_sha` and image digest (per arm with `pins`); `collect` rejects a cell whose `##PROV##` disagrees and lists it for re-dispatch; `verdicts` byte for byte AntHocNet's `campaign.py` (#81); `plan` prints each job's ns-3 release (an image arm's from its labels; `--offline` skips that), image and `source_sha`, warns when a cell's arms run different releases, and refuses a plan declaring `isolates: image\|ns3\|source_sha\|args` whose arms differ in more (`allow_confounds: true` to override) (#89) |

```bash
pip install ./python                        # console scripts above
python3 python/ns3_tool/bench_parse.py CELL # or run a module as a script
for t in python/tests/test_*.py; do python3 "$t"; done
NS3_TOOL_CELLS=/path/to/AntHocNet/results/cells python3 python/tests/test_bench_parse_query.py
```

`tests/fixtures/cells/` holds 14 AntHocNet cells, one per scenario family plus
`anchor-bee-p1-v270a.txt`, the control `test_change_plan.py` pairs with its
`-ringoff` arm,
(`GPL-2.0-only` data, see `LICENSING.md`). CI also runs the query-equivalence
test on AntHocNet's whole `results/cells/` corpus.

The CI gates in `tools/checks/` read marker lines through this reader
(`bench_parse --get FIELD --from run|table|csv|paths --proto ARM -`), never
by awk column position (#6): `check-stock-arms.sh` takes each arm's delivery
verdict from `tools/checks/arm-delivers.sh`, which asks `--get pdr`. A marker
change the reader absorbs cannot make a gate read the wrong number, and a
contract bump the reader rejects stops the gates too.
`tests/test_bench_parse_query.py` checks the reader against the awk it
replaced on every cell, and runs `arm-delivers.sh` on the fixtures and on
broken inputs.

The readers are strict by default (#58): a `##RUN##`, `##BENCH##` or
`# stddev` field, or a required campaign-CSV value (`sweep_summary`), that is
present but not a number fails with the file, the line and the field (exit 2),
instead of being skipped, so no verdict runs on fewer seeds than it says.
`inf` stays allowed where the harness prints it (the offered-load
percentiles). `--lenient` (`bench_parse`, `sweep_summary`) restores the old
skipping for exploring partial logs and reports on stderr how many fields and
rows it dropped; its standard output is the old output byte for byte. Nine
older cells in AntHocNet's `results/cells/` (the `grid-*` cells without a
generation suffix, `static-mesh-jitter-cap1s.txt`, `fanet-main.txt` and
`fanet-main-oracle100ms.txt`) carry a monitor's `##RSS##` line written into a
`##RUN##` row, so they read only with `--lenient`. A line that starts
`##RUN##` but is not `<run> <arm> <fields>` (such a cut row) fails too. `tests/test_strict.py` covers each case.

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
