# AGENTS.md — guide for AI coding agents

Operational guide for LLM agents working in `swarm-routing/ns3-tools`. Read this
before changing anything. Org-wide conventions:
[`swarm-routing/.github` CONTRIBUTING.md](https://github.com/swarm-routing/.github/blob/main/CONTRIBUTING.md). Shape and rules mirror
[AntHocNet's AGENTS.md](https://github.com/swarm-routing/AntHocNet/blob/main/AGENTS.md), the source repository.

## What this repo is

**Status: being populated (Phase 4).** Moved from AntHocNet so far: the `routing-bench` comparison harness (`ns3/routing-bench/`, #2) and the result readers (`python/`, #3), behind the `ns3-tool` CLI (`compare`, `sweep`, `diagnose`, `scaffold`, #39). Probes and arenas are still the plan; update this file in the PR that adds each part.

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
tools/checks/       CI scripts (check-stock-arms.sh; arm-delivers.sh, its verdict read through the reader, #6; check-perf-markers.sh, the ##PERF## line, #91; check-scaffold-arm.sh, the arm `ns3-tool scaffold` writes, #39) — MIT OR GPL-2.0-only
tools/release/      dist.sh: the release assets (routing-bench tarball, ns3-tool wheel + sdist), each checked; `make dist` — MIT OR GPL-2.0-only
.cz.toml            Commitizen config; releases run through .github/workflows/release.yml (the org reusable-release.yml, #26)
python/ns3_tool/    `ns3-tool` (cli: compare = bench_parse, sweep = a port of AntHocNet's run-scenarios.py, diagnose = the drop book + scenario_check results, scaffold = an arm module skeleton; #39), the readers and statistics (bench_parse, stats_util, native/comparator views, pool_runs, sweep_summary), the plausibility checker (scenario_check: ns3-scenario-check, anchors from an --anchors file) and the campaign driver (campaign: ns3-campaign; change_plan: the change-test plans it routes, #81), stdlib only (PyYAML only to read a .yaml plan) — MIT OR GPL-2.0-only
python/tests/       their self-tests (MIT OR GPL-2.0-only); fixtures/cells/: 14 AntHocNet cells (data, GPL-2.0-only); benchmarks/: swarm-routing/benchmarks' run validator, vendored at a pinned commit
campaigns/          campaign plans (TOML), the pre-registration of each multi-protocol campaign — GPL-2.0-only
.github/workflows/  campaign-image.yml (one image per plan, through reusable-ns3-image.yml), campaign-cell.yml (one cell in it by digest)
docs/               markers.md (the marker-line contract, ##PERF## included, #91), profiling.md (perf / heaptrack / massif on one cell); arena reference (planned)
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
    'ns3-build-contrib --strict-warnings --test routing-bench=/src/ns3/routing-bench &&
     bash /src/tools/checks/check-stock-arms.sh /opt/ns-3 "olsr dsdv hwmp"'
```

The module is named `routing-bench`, as in AntHocNet: every arm module that
ships `helper/<module>-bench-arms.cc` keeps registering against it. CI
(`.github/workflows/ns3.yml`) also checks that each stock arm delivers.
**Temporary exception (ns3-containers#35, option A):** ns-3.49's stock AODV
livelocks, so the `aodv` arm's check runs on `ns3-dev:3.48`; move it back to
`latest` when a fixed ns-3 release reaches it. C++ formatting is not checked
yet, in CI (`format: false` in `ns3.yml`) or by `make`: the harness keeps
AntHocNet's style until a restyle PR adopts the org `.clang-format` and adds
clang-format back to `make lint` and `make format`. Never run clang-format on
`ns3/` before that PR.

Before pushing, run `make check` (needs `ruff`, and network for the org
tooling): `lint` (`ruff check` and `ruff format --check` on `python/` with
the org `ruff.toml`, the required check `python-quality / python`), `test`
(the Python self-tests), `headers` and `org-sync-check`. `make format` runs
`ruff format python` only. The ns-3 build, its suites and the stock-arm
checks need the docker line above and run in CI.

Python (no pytest dependency; each test exits non-zero on its first failure):

```bash
for t in python/tests/test_*.py; do python3 "$t"; done
NS3_TOOL_CELLS=/path/to/AntHocNet/results/cells python3 python/tests/test_bench_parse_query.py  # the full corpus, as CI
NS3_TOOL_CELLS=/path/to/AntHocNet/results/cells NS3_TOOL_SCENARIO_CHECK=/path/to/AntHocNet/tools/bench/scenario_check.py \
    python3 python/tests/test_scenario_check_corpus.py   # scenario_check's verdicts = AntHocNet's, as CI
NS3_TOOL_ANTHOCNET=/path/to/AntHocNet python3 python/tests/test_change_plan.py   # change-plan verdicts = AntHocNet's campaign.py on every plan (PyYAML), as CI
pip install ./python   # ns3-tool {compare,sweep,diagnose,scaffold}; the older scripts stay: ns3-bench-parse, ns3-native-view, ns3-comparator-view, ns3-pool-runs, ns3-sweep-summary, ns3-scenario-check, ns3-campaign
ns3-tool sweep /opt/ns-3 --only discrete --runs 10 --time 120 --dry-run   # the harness command lines, nothing run
ns3-campaign plan campaigns/anthocnet-aodv-smoke.toml   # offline: the image and every cell; validate checks a plan alone
```

Releases (#26; `swarm-routing/.github` CONTRIBUTING "Cutting a release"):
the `release` environment is ungated for now (`[settings]
release_environment = false`, maintainer decision 2026-11-11, written when
`swarm-routing/.github`'s `repo-settings` runs in `apply` mode; `true`
would make the maintainer approve each publish). Dispatch `release.yml`
with `dry-run`, then `bump`; merging the bot's
`chore(release): bump version A to B` PR tags and publishes with no
approval step. `make dist`
builds the assets locally (`DIST_CHECK=0` skips the docker suite and the
wheel check).

A reader change must keep its output byte-identical on the fixtures unless the
change is the point (then say so in the PR and update AntHocNet's callers).

## Golden rules (invariants — do not break)

Org-wide (1–9), then this repo's own.

<!-- org-sync:begin org-rules -->
1. **Licensing per directory, as AntHocNet** ([`LICENSING.md`](LICENSING.md);
   AntHocNet [LICENSING.md](https://github.com/swarm-routing/AntHocNet/blob/main/LICENSING.md),
   [ADR-0028](https://github.com/swarm-routing/AntHocNet/blob/main/docs/adr/0028-core-is-dual-licensed.md)). A
   simulator-free `core/` and the standalone tooling are `MIT OR GPL-2.0-only`;
   ns-3 modules, the site, docs and data are `GPL-2.0-only`. Every original
   `.h`/`.hpp`/`.cc`/`.cpp`/`.py`/`.sh` starts with
   `// Copyright (c) 2026 Daniel Joppi (https://github.com/swarm-routing)` +
   `// SPDX-License-Identifier: <MIT OR GPL-2.0-only | GPL-2.0-only>` (`#`
   comments where needed), matching its directory in `LICENSING.md`; an
   outside contributor may add their own `Copyright (c) <year> <Name>` line
   beneath the org line, never replace it, and signs off every commit (DCO,
   CONTRIBUTING "Outside contributions"). CI (`reusable-licence-check.yml`)
   checks the SPDX line, and the org line's place when a header names more
   than one holder; a header with a single, other holder (a replaced org line,
   or third-party code) passes it, so review catches that. A new directory
   gets its row in `LICENSING.md` in the same PR.
2. **The licence boundary only goes one way.** GPL code may use the dual
   parts; dual code never pulls GPL-only code in: a `core/` file includes only
   the C++ standard library, other `core/` files and the dual-licensed `core/`
   of a dependency pinned by commit and declared as the `core-deps` input of
   every workflow that runs the licence check (one calling
   `reusable-licence-check.yml`, or one calling `reusable-ns3-ci.yml`, which
   forwards it; workflow file names differ per repo), each with the same pin,
   with `core-roots` naming the repo's own `core/`
   (without it the check skips core includes; a protocol's `core/` on
   swarm-core's; CI checks it, `check-headers.py --core-dep`), and a dual script imports no GPL-only code. Code coming from AntHocNet keeps its
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
   shared module is `swarm-core` (`#include "ns3/swarm-core-module.h"`), and
   every protocol builds on it, AntHocNet included: `core/` on its `core/`, the
   ns-3 adapter on `SwarmRoutingBase` (swarm-routing/ns3-swarm-core#14).
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
   Every job that runs steps has a `timeout-minutes` cap (a job that only
   calls a reusable workflow cannot take one: the called workflow's jobs carry it), and every apt, npm or installer step
   its own, sized from recent green runs; `apt-get update` runs with
   `--error-on=any` inside a shell retry. Ubuntu mirror outages are the usual
   cause of a red run.
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
  [AntHocNet ADR-0013](https://github.com/swarm-routing/AntHocNet/blob/main/docs/adr/0013-track-bugs-and-findings-as-issues.md)): one type label
  (`bug`/`enhancement`/`chore`/`documentation`/`verification`, plus `epic` for
  umbrellas), area label(s) (`protocol`/`adapter`/`ns3`/`benchmark`/
  `observability`/`packaging`/`learn-site`/`literature`), one `model:*`
  suggestion (a model on the Claude Pro plan), one `priority:P1|P2|P3` on
  non-epic issues (none on an epic), a
  `release:vX.Y.Z` label only when a release in the repository's ladder
  (`docs/roadmap.md`; until it lands, the org ROADMAP's release train)
  gates the issue: that label set is the version's scope,
  and the version is released when its issues are done (CONTRIBUTING
  "Cutting a release"). Labels come from `labels.json` via the `sync-labels`
  workflow, the shared `release:v0.1.0` to `release:v0.4.0` and
  `release:v1.0.0` among them; a version outside that set is added to
  `swarm-routing/.github`'s `labels.json` (and CONTRIBUTING's label table)
  when a ladder needs it, and reaches a repository when its `sync-labels` runs
  (monthly, or dispatched after the change merges),
  or, in a repository that keeps its own release labels (AntHocNet), created
  there with `gh label create release:vX.Y.Z` when its ladder adds the version.
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
  only reports drift. Private repositories are never listed
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
- **`main` takes no direct push**, in any repo: its ruleset requires a PR and
  the required checks, not an up-to-date branch. Bots push `bot/<name>` branches
  and open PRs; a release is the bump PR from `bot/release`, tagged after it
  merges (CONTRIBUTING "Cutting a release").
- A PR behind `main` may merge as it is: the ruleset does not require an
  up-to-date branch. Update it first
  (`gh api -X PUT "repos/<owner>/<repo>/pulls/<n>/update-branch"`, with
  `<owner>/<repo>` the PR's base repository and `<n>` its number) only when
  `main` changed what it touches (a template, a workflow, a required check),
  then merge after its CI on the updated branch. A
  repository with a merge queue (`merge_queue = true` in its `[settings]`)
  is not updated by hand: enable auto-merge and the queue tests each PR on
  the latest `main` and merges it (CONTRIBUTING "Merge when CI is green").
  Never force-push a branch someone else pushed. A behind `bot/release` PR is the exception:
  dispatch the release `bump` again, so the version and changelog are rebuilt
  on the new `main`.
- **Settings are code.** Repository settings, rulesets, required checks and the
  Actions allowlist change only by a PR to the repo's `[settings]` in
  `.github/org-sync.toml` or to `templates/rulesets/` in `swarm-routing/.github`,
  never in the UI or by a direct API write. A `templates/rulesets/` change
  applies on merge; a repo's `[settings]` change applies when `repo-settings`
  in `swarm-routing/.github` is dispatched with mode `apply` after the merge
  (the daily run only checks). A required check runs on every PR (no `paths:`
  filter; skip work inside a gate job instead) and is named exactly as its
  check run. A new third-party action goes into `defaults.json`
  `selected_actions` (`templates/rulesets/`) before or with the workflow that
  uses it: in the same PR in `swarm-routing/.github`, otherwise merged there
  first, or `repo-settings` refuses the repo.
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
| Find new papers on a topic, or confirm a reference (Crossref, Semantic Scholar, OpenAlex; free sources only) | `swarm-routing/.github` `tools/lit-search/lit_search.py` | `lit-search` |
| Give a repository its docs/references.bib, or review it: every paper and org release it relies on, confirmed from Crossref, DataCite or Zenodo (a work with no DOI, such as a thesis, from its publisher's or institution's record, with its url), DOI links first, the fidelity Sources table and CITATION.cff references generated from it | `swarm-routing/.github` `tools/fidelity/fidelity.py` (bib), `tools/zenodo/zenodo.py` | `references` |
| Take open pull requests from review to merge: CI, mergeable state and unresolved review threads in one view, each finding verified and fixed or answered, then merged when green (auto-merge where the repository has a merge queue) | `swarm-routing/.github` `tools/pr-status/pr_status.py` | `pr-shepherd` |
| Measure what a Claude Code session loads from the agent files (`CLAUDE.md` and its `@` imports, or `AGENTS.md` alone), or where a finished Claude Code session's tokens went, before cutting or adding agent text | `swarm-routing/skills` `tools/token-audit/token_audit.py` | `token-audit` |

A skill only this repository uses lives in its own `.claude/skills/`; a skill
two repositories need moves to the plugin. Skills that file, cite and extract
papers stay in a private library the maintainer keeps; finding
new papers and confirming a reference is public (`lit-search`). A public skill
may point to that library's skills, never carry its content. This section is managed by org-sync: add
a row in `swarm-routing/.github` `templates/agents/skills.md`, not here (an edit
here is undone by the next `org_sync.py apply`).
<!-- org-sync:end skills -->

<!-- org-sync:begin agent-defaults -->
## Working defaults

Condensed from [andrej-karpathy-skills](https://github.com/forrestchang/andrej-karpathy-skills).

- **Assumptions:** state the ones that matter. Ask only when the choice
  belongs to the maintainer: a default, a published number, a cell, the scope.
  Then give your recommendation and its reason with the question, never the
  options alone. Otherwise pick the sensible option, say which, and keep
  going. Long unattended runs, like campaigns, depend on that.
- **Simplicity:** the least code that solves the task. No speculative
  features, configurability or abstractions for one use.
- **Surgical diffs:** every changed line traces to the request. Match the
  surrounding style. Mention unrelated dead code rather than deleting it, and
  remove only the orphans your own change created.
- **Verifiable goals:** a bug gets a reproducing test first; a refactor passes
  before and after. Finish when the check passes, not when the code looks right.

## Session habits

- **Handoffs are always a ticket.** When a session ends with work in flight, or a
  handoff is asked for, open a GitHub issue from the *Handoff* template (label
  `handoff`); a session that continued a handoff updates that issue instead of
  opening another. Never write a handoff file in the repo. A session that
  continues handed-off work starts from the newest open `handoff` issue for
  that work; a new task starts from the request. Close the issue, and any
  older one it superseded, when the work it hands off is done ([AntHocNet ADR-0013](https://github.com/swarm-routing/AntHocNet/blob/main/docs/adr/0013-track-bugs-and-findings-as-issues.md) item 5).
- **Attach only the repositories the task needs.** Each attached repository's
  agent files are loaded into every request, whether the task touches it or
  not.
- **Start from the record, not from memory:** the matching `handoff` issue,
  then the issue's acceptance criteria, then `main`. Check a claim against its
  source before it goes into an issue or a PR.
- **Script what repeats.** A check or a lookup run more than a few times
  becomes a script in `tools/` that prints a summary; the model reads the
  verdict, not the raw data (AntHocNet ADR-0014).
- **Measure before cutting or adding agent text:** the `token-audit` skill
  (`swarm-routing/skills` `tools/token-audit/token_audit.py`) prints what a
  session loads and where a finished session's tokens went.

## Response style — caveman (token economy)

Answer in tight, low-filler prose: drop hedging, pleasantries, and preamble; keep
the substance. Fewer output tokens on every reply, same information.

**Carve-out (the record stays full).** Keep byte-for-byte exact — code,
commands, file paths, error text, benchmark numbers. And keep the *durable
record* detailed, never terse: commit messages, PR bodies, ADRs, `docs/`, and
issue / finding threads. Cross-session traceability depends on those being
complete — AntHocNet's AC_BE_NQOS / A2 investigation was only recoverable
because the issue threads were thorough. Caveman the chat, not the record.

## Agent pitfalls

- `gh api … -F body=@file` reads the file; `-f body=@file` posts the literal
  text `@file`.
- When the permission system refuses a command (a merge, say), stop and report
  it. Never retry it another way, and never ask another agent to run it.
- A PR body's `Closes #N` closes the issue on merge; write `Part of #N` while
  acceptance criteria remain open.
- Before saying a tool or check is missing, look at `main` (`git fetch`,
  `origin/main`), not a stale local checkout.
- In an issue, PR or comment, write every issue of another repository in
  full, `swarm-routing/<repo>#N`, also inside a list or a range. A bare `#N`
  links to the repository the text is in: in
  `swarm-routing/AntHocNet#65, #151` the second number points at this
  repository's #151 (swarm-routing/.github#277).
- A sentence in `ROADMAP.md` or an AGENTS.md that calls an issue done is not
  evidence: read the issue's acceptance criteria. On 2026-11-12 nine issues
  described as done had open criteria (swarm-routing/.github#274).
- Change text inside `org-sync:begin/end` markers only through the template
  in `swarm-routing/.github` `templates/`: one PR there, then org-bot's
  `bot/org-sync` refresh PR in each repository. A hand edit per repository
  stays red on `check / org-sync` (swarm-routing/.github#236).
- In a repository with a merge queue, merge by enabling auto-merge (squash);
  a direct merge (`PUT pulls/<n>/merge`) is refused with HTTP 405. Only the
  required checks (`gh api 'repos/{owner}/{repo}/rules/branches/main'`, which
  `gh` fills from the checkout) gate a merge: a red or pending report-only
  check (`codecov/patch`, Sonar) is triaged, not waited on. A red `dco` on an
  outside contributor's commit still holds the PR until that author signs
  off; on the maintainer's or an agent's commits it is expected
  (swarm-routing/.github#212). `dco` is not a required check and runs on
  `pull_request` only, so merge an outside contributor's PR, by auto-merge or
  directly, only after `dco` passed on the head commit that merges. Never push
  to an outside contributor's PR: ask them for the change, or make it in a
  follow-up PR after the merge (swarm-routing/.github#288). If the maintainer
  must push to such a PR, first disable auto-merge, or, if the PR is already
  in a merge queue, remove it from the queue (disabling auto-merge does not).
  Re-enable auto-merge, or enqueue it again, only after `dco` passed on the
  new head: GitHub keeps auto-merge on after a push by someone with write
  access, and `dco` is not a required check.
- `review / coderabbit` is a required check (the review gate,
  swarm-routing/.github#294): it passes once CodeRabbit has reviewed the PR,
  once it says it will not, for a draft, or 30 minutes after the PR opened
  (then with one comment). So auto-merge may be enabled at once: the merge
  waits for the gate, and the ruleset holds it while a CodeRabbit thread is
  unresolved. Still read every thread and fix or answer it before resolving.
  Where the gate is not required yet (`gh api
  'repos/{owner}/{repo}/rules/branches/main'` does not list it), wait first
  with `python3 -I ../.github/tools/pr-status/pr_status.py gate REPO#N`,
  which waits for CodeRabbit alone (swarm-routing/.github#238).
- Never add a `Signed-off-by` trailer, for the maintainer or anyone else: a
  DCO sign-off is a person's own certification (CONTRIBUTING "Outside
  contributions").
- Releases are cut on request only: dispatch the release `bump` when the
  maintainer asks, once the version's `release:vX.Y.Z` issues are done and
  its exit criteria in the ladder (`docs/roadmap.md`, or the org ROADMAP's
  release train until it lands) are met; never approve a gated publish. A
  failed publish is recovered with "Re-run all jobs", never "Re-run failed
  jobs" (CONTRIBUTING "Cutting a release"). Never type a release's DOI: read
  it from Zenodo.
- Cite a paper in a public issue, PR or doc by its `https://doi.org/<DOI>`
  link, read from its record (`lit-search` confirms it), or, for a work with
  no DOI, by its publisher's or institution's page; never point into or name
  the private paper library there.
<!-- org-sync:end agent-defaults -->

## Where to look

| You want to… | Go to |
|--------------|-------|
| Python sources to port (MIT OR GPL-2.0-only → `python/`) | AntHocNet `tools/bench/` (`bench_parse.py`, `stats_util.py`; `scenario_check.py` is ported, #28; `campaign.py` as `change_plan.py`, #81), `ns3/tools/run-scenarios.py` |
| Harness to port into `ns3/` (GPL-2.0-only, so it lands as-is in a GPL path) | AntHocNet `ns3/routing-bench/`, `docs/comparison-harness.md`, ADR-0027 |
| Run a multi-protocol campaign (one image, cells by digest, a benchmarks manifest) | `python/ns3_tool/campaign.py` (`ns3-campaign`: `validate`, `plan`, `build`, `dispatch`, `status`, `collect`, `verdicts`; its docstring is the plan format), a plan in `campaigns/`, `campaign-image.yml` + `campaign-cell.yml`; #66, swarm-routing/.github#107. Every cell has a key over every pinned input (`cell_key`; in `##PROV##` and the artifact name): `dispatch` reuses a cell already run, from the local cache (`~/.cache/ns3-campaign/`), a benchmarks checkout (`--benchmarks DIR`) or this repository's artifacts, and stops on any mismatch; `--no-reuse` runs everything (#94) |
| Run a protocol repository's change tests (AntHocNet's plans: cells x arms) | `python/ns3_tool/change_plan.py`, reached through `ns3-campaign` for any plan with a `generation` key (`plan`, `dispatch`, `status`, `collect [--redispatch]`, `verdicts`; `--repo`, `--workflow`; its docstring is the plan format): every cell goes to the repository's caller of swarm-routing/.github's `reusable-benchmark-cell.yml` pinned to the plan's `source_sha` and image (swarm-routing/.github#157); verdicts byte for byte AntHocNet's (`NS3_TOOL_ANTHOCNET=<checkout> python3 python/tests/test_change_plan.py`, as CI); `verdicts` pairs arms; a plan's `reference: <protocol>` (or a list) also pairs every other protocol of a cell against it by seed, an anchor's read (#152); `plan` shows each arm's ns-3 release, image and commit and flags a control whose arms differ in more than it `isolates` (#89); #81, epic swarm-routing/.github#156 |
| Comparator arms (SAODV, SEAD, TAODV, DRL, DTN) and their gates | epic #15; the arm contract: AntHocNet `docs/comparison-harness.md`, ADR-0027 |
| Measure what an arm costs, or read a protocol's internals | `##PERF##` (CPU, wall time and events per arm and seed; peak RSS only when the process runs one arm, and then the process peak up to that seed, `-` otherwise; `--perf`, default on; never compared between cells): `docs/markers.md`, `tools/checks/check-perf-markers.sh`, `bench_parse --from perf`, `docs/profiling.md` (#91, #97); a protocol's own counters come from swarm-core's router observer (`SwarmRoutingHelper::CollectStats`, swarm-routing/ns3-swarm-core#42), state footprint in the harness still open in #91 |
| Open issues being migrated here | AntHocNet #764, #771, #772, #773, #791, #431, #294 |
| The org's invariants, labels, issue shape | [`swarm-routing/.github` CONTRIBUTING.md](https://github.com/swarm-routing/.github/blob/main/CONTRIBUTING.md) |
| The reference implementation and its history | [`swarm-routing/AntHocNet`](https://github.com/swarm-routing/AntHocNet) (`AGENTS.md`, `CONTEXT.md`, `docs/adr/`) |
| Know which release a piece of work serves, or why something is not planned | [`docs/roadmap.md`](docs/roadmap.md): the ladder v0.1.0 → v1.0.0, per-release exit criteria, the non-goals; issues carry `release:` labels |
| Pick up open work | this repo's issues — highest open `priority:P*` first; newest open `handoff` issue for work in flight |
