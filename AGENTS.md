# AGENTS.md — guide for AI coding agents

Operational guide for LLM agents working in `swarm-routing/ns3-tools`. Read this
before changing anything. Org-wide conventions:
[`swarm-routing/.github` CONTRIBUTING.md](https://github.com/swarm-routing/.github/blob/main/CONTRIBUTING.md). Shape and rules mirror
[AntHocNet's AGENTS.md](https://github.com/danieljoppi/AntHocNet/blob/main/AGENTS.md), the source repository.

## What this repo is

**Status: being populated (Phase 4).** Moved from AntHocNet so far: the `routing-bench` comparison harness (`ns3/routing-bench/`, #2) and the result readers (`python/`, #3). Probes, arenas and the `ns3-tool` subcommands below are still the plan; update this file in the PR that adds each part.

Protocol-agnostic diagnostics and comparison tooling: non-intrusive C++20
probes (`TopologyOracle` for BFS-reachable PDR and path stretch,
`DropCauseClassifier` for PHY/MAC/ARP/TTL/loop root causes, the
`##RUN##`/`##BENCH##`/`##DIAG##` emitters), universal arenas
(`manet-arena.cc`, `leo-walker-arena.cc`), and the `ns3-tool` Python CLI
(`sweep`, `compare`, `diagnose`, `scaffold`) ported from AntHocNet's
`run-scenarios.py` / `bench_parse.py`.

```
ns3/routing-bench/  the comparison harness module `routing-bench` (moved from AntHocNet as-is; routing-compare + the anthocnet-compare alias) — GPL-2.0-only
ns3/arms/<name>/    (planned) non-swarm comparator arms, one contrib module each (epic #15) — GPL-2.0-only
ns3/                (planned) probes, emitters, arenas — GPL-2.0-only
tools/checks/       CI scripts (check-stock-arms.sh) — MIT OR GPL-2.0-only
python/ns3_tool/    the readers and statistics (bench_parse, stats_util, native/comparator views, pool_runs, sweep_summary), stdlib only — MIT OR GPL-2.0-only
python/tests/       their self-tests (MIT OR GPL-2.0-only); fixtures/cells/: 13 AntHocNet cells (data, GPL-2.0-only)
docs/               marker-line contract, arena reference
```

## Build & verify

CI is [`reusable-ns3-ci.yml`](https://github.com/swarm-routing/.github/blob/main/.github/workflows/reusable-ns3-ci.yml):
the module builds inside `ghcr.io/swarm-routing/ns3-dev:latest-{debug,sanitized}`
(warm ns-3 tree; only the module compiles) and runs its test.py suites.
`latest` is the release ns-3 supports (it maintains its latest only), so
automatic CI follows ns-3 with no edit; an older ns-3 release runs only on
demand, through a manual `workflow_dispatch` passing `ns3-versions`, a JSON list such as `["3.48"]` (org
CONTRIBUTING invariant 7). The
same locally:

```bash
docker run --rm -v "$PWD:/src:ro" ghcr.io/swarm-routing/ns3-dev:latest-debug bash -c \
    'ns3-build-contrib --test routing-bench=/src/ns3/routing-bench &&
     bash /src/tools/checks/check-stock-arms.sh /opt/ns-3 "olsr dsdv hwmp"'
```

The module is named `routing-bench`, as in AntHocNet: every arm module that
ships `helper/<module>-bench-arms.cc` keeps registering against it. CI
(`.github/workflows/ns3.yml`) also checks that each stock arm delivers.
**Temporary exception (ns3-containers#35, option A):** ns-3.49's stock AODV
livelocks, so the `aodv` arm's check runs on `ns3-dev:3.48`; move it back to
`latest` when a fixed ns-3 release reaches it. Formatting is not checked yet:
the harness keeps AntHocNet's style until a restyle PR adopts the org
`.clang-format`.

Python (no pytest dependency; each test exits non-zero on its first failure):

```bash
for t in python/tests/test_*.py; do python3 "$t"; done
NS3_TOOL_CELLS=/path/to/AntHocNet/results/cells python3 python/tests/test_bench_parse_query.py  # the full corpus, as CI
pip install ./python   # console scripts: ns3-bench-parse, ns3-native-view, ns3-comparator-view, ns3-pool-runs, ns3-sweep-summary
```

A reader change must keep its output byte-identical on the fixtures unless the
change is the point (then say so in the PR and update AntHocNet's callers).

## Golden rules (invariants — do not break)

Org-wide (1–9), then this repo's own.

<!-- org-sync:begin org-rules -->
1. **Licensing per directory, as AntHocNet** ([`LICENSING.md`](LICENSING.md);
   AntHocNet [LICENSING.md](https://github.com/danieljoppi/AntHocNet/blob/main/LICENSING.md),
   [ADR-0028](https://github.com/danieljoppi/AntHocNet/blob/main/docs/adr/0028-core-is-dual-licensed.md)). A
   simulator-free `core/` and the standalone tooling are `MIT OR GPL-2.0-only`;
   ns-3 modules, the site, docs and data are `GPL-2.0-only`. Every original
   `.h`/`.hpp`/`.cc`/`.cpp`/`.py`/`.sh` starts with
   `// Copyright (c) 2026 Daniel Joppi (https://github.com/swarm-routing)` +
   `// SPDX-License-Identifier: <MIT OR GPL-2.0-only | GPL-2.0-only>` (`#`
   comments where needed), matching its directory in `LICENSING.md`. CI
   (`reusable-licence-check.yml`) checks it. A new directory gets its row in
   `LICENSING.md` in the same PR.
2. **The licence boundary only goes one way.** GPL code may use the dual
   parts; dual code never pulls GPL-only code in: a `core/` file includes only
   the C++ standard library and other `core/` files (CI checks it), and a dual
   script imports no GPL-only code. Code coming from AntHocNet keeps its
   licence: its `core/`/`tools/` (dual) may land in dual or GPL paths here; its
   `ns3/` modules and `web/` (GPL-2.0-only) only in GPL paths. Third-party code
   (AntHocNet's vendored `aomdv`/`gpsr`, IITP RAS) lands only in GPL paths with
   its provenance recorded. Write clean implementations against public ns-3
   interfaces rather than copying ns-3's `src/aodv`/`src/olsr`.
3. **Wire formats are POD + versioned.** Control headers are
   simulator-independent POD structs with bounds-checked little-endian
   readers/writers, a leading version byte, and deterministic roundtrip + fuzz
   tests. Changing a field or its units bumps the version byte.
4. **No contrib module named `core`** (collides with ns-3's `src/core`). The
   shared module is `swarm-core` (`#include "ns3/swarm-core-module.h"`).
5. **Determinism.** A fixed `--seed=<N>` gives byte-identical output. All
   randomness through the simulator's seeded streams or an injected RNG port;
   never libc `rand()`, wall-clock time, or unordered-container iteration order
   in anything that reaches an output.
6. **Comparisons are paired.** Per-seed deltas across >= 20 seeds, emitting the
   canonical `##CONFIG##`, `##RUN##`, `##BENCH##`, `##DIAG##` lines. A
   comparator arm (a protocol we did not design) counts only after its three
   gates: a fidelity sheet from its paper, the per-PR delivery smoke, and an
   anchor reproducing the paper's own trend.
7. **Keep bounded structures bounded** (visited paths, dedup histories, queues
   carry a configured cap).
8. **CI hygiene.** Actions pinned by full commit SHA with a `# vX.Y.Z` comment;
   buildx caches scoped per profile; transient artifacts `retention-days` <= 7.
9. **Nothing shipped depends on `.claude/`.** Skills hold procedures; scripts
   that anything shipped needs live in `tools/` (AntHocNet ADR-0014).
<!-- org-sync:end org-rules -->
10. **Probes are non-intrusive.** They read trace sources and callbacks only; attaching a probe never changes a simulation's output (same seed, byte-identical `##RUN##` with and without it — test it).
11. **The marker lines are a versioned contract.** `##CONFIG##`/`##RUN##`/`##BENCH##`/`##DIAG##` fields are only appended; a removal or meaning change bumps the schema version and updates the reader and `benchmarks`' parsers in the same change (AntHocNet #128, #771).
12. **Protocol-agnostic.** No protocol is special-cased in an arena or the harness; protocols register as arms (AntHocNet ADR-0027). The comparator arms that are not swarm protocols (secure, learned, DTN: epic #15) live here by maintainer decision (2026-11-02), each as its **own** contrib module under `ns3/arms/<name>/` that registers through `helper/<name>-bench-arms.cc` exactly as an out-of-repo arm does; no code outside its directory hard-codes a reference to it or branches on its name (registration and docs may name it). Swarm-based variants live with their protocol (ARA, BeeAdHoc). Every arm clears the three gates (rule 6) before a number counts.
13. **Statistics stay scripted.** Noise verdicts and paired tests come from the CLI, never from eyeballing numbers (AntHocNet ADR-0014).

## Conventions

<!-- org-sync:begin conventions -->
- Issues follow the org taxonomy ([CONTRIBUTING.md](https://github.com/swarm-routing/.github/blob/main/CONTRIBUTING.md#issues), from
  [AntHocNet ADR-0013](https://github.com/danieljoppi/AntHocNet/blob/main/docs/adr/0013-track-bugs-and-findings-as-issues.md)): one type label
  (`bug`/`enhancement`/`chore`/`documentation`/`verification`, plus `epic` for
  umbrellas), area label(s) (`protocol`/`adapter`/`ns3`/`benchmark`/
  `observability`/`packaging`/`learn-site`/`literature`), one `model:*`
  recommendation, one `priority:P1|P2|P3` on non-epic issues, a
  `release:vX.Y.Z` label only when a roadmap gates the issue. Labels come from
  `labels.json` via the `sync-labels` workflow.
- Issue bodies: **Context** (evidence, numbers, links) · **Scope** ·
  **Acceptance criteria** (checkboxes) · **Depends on** (cross-repo form
  `swarm-routing/<repo>#N`) · **References**. Children of an epic say "Part of
  #N" and are linked as sub-issues. Comments you post end with the Claude Code
  footer.
- Issues migrated from AntHocNet carry `migrated` and start with *Migrated from
  `danieljoppi/AntHocNet#N`*.
- Epics are listed where contributors look. In a public repository, this
  README's `## Epics` section and the org
  [ROADMAP](https://github.com/swarm-routing/.github/blob/main/ROADMAP.md#epics)
  are generated from the issues labelled `epic` by `swarm-routing/.github`'s
  `tools/epics/epics.py`. An open epic is Planned (no sub-issue closed) or In
  progress (some closed); it becomes Done or Dropped only when you close the
  epic itself (as completed or as not planned), even if every sub-issue is
  closed. Whenever you change an epic (open, close, rename or re-scope it, add
  or remove its `epic` label) or one of its sub-issues (close, reopen, add,
  unlink), refresh the lists in that PR or the next one. You need
  `swarm-routing/.github` checked out next to this repository and `gh` logged
  in (or `GH_TOKEN` set). Run `python3 ../.github/tools/epics/epics.py readme .`
  in the repository that owns the epic (for a sub-issue of another
  repository's epic, that repository, not this one), then
  `python3 tools/epics/epics.py roadmap ROADMAP.md` and
  `python3 tools/epics/epics.py profile profile/README.md` in `.github`. Never
  edit a generated section by hand; the weekly `epics` workflow in `.github`
  only reports drift. Private repositories (such as `papers`) are never listed
  and keep no generated section.
- Make minimal, reviewable changes; update the relevant doc/ADR when you change
  a documented decision.
<!-- org-sync:end conventions -->

## Git workflow

<!-- org-sync:begin git-workflow -->
- Branch from `main` (`claude/<short-topic>`), one focused change per PR.
- **Always open a PR to `main`** once the branch is pushed — every change is
  tracked through one.
- **PRs are squash-merged** (the only merge method enabled). The PR title is
  the commit on `main`, so it is a lowercase Conventional Commit
  (`feat:`/`fix:`/`docs:`/`chore:`/`ci:`…, optional scope).
- Push with `git push -u origin <branch>`; merge when CI is green.
<!-- org-sync:end git-workflow -->

<!-- org-sync:begin skills -->
## Agent skills

The org's procedures ship as the Claude Code plugin **`swarm-routing`**, from
[`swarm-routing/skills`](https://github.com/swarm-routing/skills). This
repository's `.claude/settings.json` enables it, so Claude Code offers a skill
when the task matches its description. Other agents (Codex, Cursor, Copilot, …)
install the same skills with `npx skills add swarm-routing/skills`
([skills.sh](https://skills.sh)). A skill is the procedure; the script does the
work and runs without it (golden rule 9), so an agent without the skill starts
from the script; so does a session that does not read this repository's
settings (some hosted sessions with several repositories do not). If Claude
Code says the plugin is missing (it was called
`org-repo` before 2026-11-09, and a cached marketplace keeps the old list), run
`claude plugin marketplace update swarm-routing` and start a new session.

| Task | Script (any agent) | Skill |
|------|--------------------|-------|
| Bring this repository onto the org templates, or fix their drift | `swarm-routing/.github` `tools/org-sync/org_sync.py` | `org-repo` |
| Write or review a protocol's paper-fidelity sheet (`docs/fidelity.md`) | `swarm-routing/.github` `tools/fidelity/fidelity.py` | `fidelity-sheet` |
| Check a diff against the org golden rules before a PR or a review | `swarm-routing/.github` `tools/protocol-review/check_invariants.py` | `protocol-review` |
| Bring the README, AGENTS/CLAUDE/CONTEXT and docs/ to the org documentation standard | `swarm-routing/.github` `tools/docs-review/check_docs.py` | `docs-review` |

A skill only this repository uses lives in its own `.claude/skills/`; a skill
two repositories need moves to the plugin. Skills that manage the private papers
library (finding, citing and extracting papers) stay in `swarm-routing/papers`;
a public skill may point to them, never carry library content. This section is managed by org-sync: add
a row in `swarm-routing/.github` `templates/agents/skills.md`, not here (an edit
here is undone by the next `org_sync.py apply`).
<!-- org-sync:end skills -->

## Where to look

| You want to… | Go to |
|--------------|-------|
| Python sources to port (MIT OR GPL-2.0-only → `python/`) | AntHocNet `tools/bench/` (`bench_parse.py`, `stats_util.py`, `campaign.py`, `scenario_check.py`), `ns3/tools/run-scenarios.py` |
| Harness to port into `ns3/` (GPL-2.0-only, so it lands as-is in a GPL path) | AntHocNet `ns3/routing-bench/`, `docs/comparison-harness.md`, ADR-0027 |
| Comparator arms (SAODV, SEAD, TAODV, DRL, DTN) and their gates | epic #15; the arm contract: AntHocNet `docs/comparison-harness.md`, ADR-0027 |
| Open issues being migrated here | AntHocNet #764, #771, #772, #773, #791, #431, #294 |
| The org's invariants, labels, issue shape | [`swarm-routing/.github` CONTRIBUTING.md](https://github.com/swarm-routing/.github/blob/main/CONTRIBUTING.md) |
| The reference implementation and its history | [`danieljoppi/AntHocNet`](https://github.com/danieljoppi/AntHocNet) (`AGENTS.md`, `CONTEXT.md`, `docs/adr/`) |
| Pick up open work | this repo's issues — highest open `priority:P*` first; newest open `handoff` issue for work in flight |
