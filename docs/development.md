# Developer guide

ns3-tools holds the org's protocol-agnostic comparison tooling: the
`routing-bench` harness (an ns-3 contrib module in `ns3/routing-bench/`,
whose program `routing-compare` runs every protocol "arm" on the same
scenarios and seeds) and the Python package `ns3-tool` in `python/` (the
readers, the statistics and the campaign driver `ns3-campaign`). This page
shows how to set up, build, test and lint both, and what CI checks on a pull
request. The rules a change must keep are in [AGENTS.md](../AGENTS.md)
("Golden rules", notably the marker-line contract in
[markers.md](markers.md)); how issues and pull requests work is in the org's
[CONTRIBUTING.md](https://github.com/swarm-routing/.github/blob/main/CONTRIBUTING.md).

## Prerequisites

| Tool | Version | Needed for |
| --- | --- | --- |
| Linux or macOS, `git`, `make` | any recent | everything |
| Python | 3.11 or newer (CI: 3.11 and 3.13) | `python/`, its tests, the CLI |
| Docker | any recent; about 4 GB free for the debug image | the harness module |
| ruff | 0.17.1 | Python lint and format |
| PyYAML | 6.0.3 (optional) | only to read a `.yaml` plan, and one corpus test |
| GitHub CLI `gh`, logged in | any recent | only `ns3-campaign build`/`dispatch`/`status`/`collect` |

The Python package uses the standard library only. You do not install ns-3:
the harness builds inside the org's prebuilt image
`ghcr.io/swarm-routing/ns3-dev` (ns-3 3.49 today, GCC 13.3, CMake 3.28).

## Get the code

```bash
git clone https://github.com/swarm-routing/ns3-tools.git
cd ns3-tools
```

There are no submodules. Some tests compare against AntHocNet's committed
data; for those, also clone swarm-routing/AntHocNet next to this checkout
(see "Test").

## Dependencies

`routing-bench` needs only stock ns-3 modules, which the `ns3-dev` image
already has. A protocol arm comes from its own repository and registers with
the harness at build time (each arm module ships a
`helper/<module>-bench-arms.cc`). The Python package has no third-party
dependency; `python/tests/benchmarks/` vendors swarm-routing/benchmarks'
run validator at a pinned commit. How other repositories pin this one is
CONTRIBUTING "Dependencies" (AntHocNet's `ns3-tools.lock`); a vcpkg-based
setup is planned (swarm-routing/.github#489).

## Build

The harness, inside the `ns3-dev` image:

```bash
make build
# the same command written out:
docker run --rm -v "$PWD:/src:ro" ghcr.io/swarm-routing/ns3-dev:latest-debug \
    ns3-build-contrib routing-bench=/src/ns3/routing-bench
```

The Python package, into a virtual environment:

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install ./python
ns3-tool --help
```

`make dist` builds the release assets (the `routing-bench` tarball, the
wheel and the sdist) into `dist/` and checks each; `DIST_CHECK=0 make dist`
skips the Docker suite and the wheel check.

## Test

| What | Command | CI |
| --- | --- | --- |
| Python self-tests on fixtures | `make test` (each `python/tests/test_*.py`; each exits non-zero on its first failure) | `ns3-tool tests (Python 3.11)`, `(Python 3.13)` |
| the installed console scripts | `pip install ./python`, then e.g. `ns3-tool compare python/tests/fixtures/cells/fanet-main-6arm.txt` | same jobs |
| readers against AntHocNet's committed cells | `NS3_TOOL_CELLS=../AntHocNet/results/cells python3 python/tests/test_bench_parse_query.py` | `query equivalence on AntHocNet's committed cells` |
| `scenario_check` verdicts equal AntHocNet's | `NS3_TOOL_CELLS=../AntHocNet/results/cells NS3_TOOL_SCENARIO_CHECK=../AntHocNet/tools/bench/scenario_check.py python3 python/tests/test_scenario_check_corpus.py` | same job |
| change-plan verdicts equal AntHocNet's (needs PyYAML) | `NS3_TOOL_ANTHOCNET=../AntHocNet python3 python/tests/test_change_plan.py` | same job |
| the harness suite, strict warnings as errors | `docker run --rm -v "$PWD:/src:ro" ghcr.io/swarm-routing/ns3-dev:latest-debug ns3-build-contrib --strict-warnings --test routing-bench=/src/ns3/routing-bench` | `module / ns-latest debug`, `module / ns-latest sanitized` |
| every stock arm delivers | `docker run --rm -v "$PWD:/src:ro" ghcr.io/swarm-routing/ns3-dev:latest-debug bash -c 'ns3-build-contrib routing-bench=/src/ns3/routing-bench && bash /src/tools/checks/check-stock-arms.sh /opt/ns-3 "olsr dsdv hwmp"'` | `stock arms deliver` |

The stock AODV arm is checked on the `3.48-debug` image only, because
ns-3.49's AODV livelocks (swarm-routing/ns3-containers#35). CI also runs,
on the image only: the determinism gate (`routing-compare ... --protocols=olsr`
twice per seed, byte-identical), the `##PERF##` marker check
(`tools/checks/check-perf-markers.sh`), and `ns3-tool scaffold`, which
generates a new arm module and checks that it builds and delivers.

## Run

A small comparison of one stock arm inside the image (prints the
`##CONFIG##`, `##RUN##` and `##BENCH##` lines described in
[markers.md](markers.md)):

```bash
docker run --rm -v "$PWD:/src:ro" ghcr.io/swarm-routing/ns3-dev:latest-debug bash -c \
  'ns3-build-contrib routing-bench=/src/ns3/routing-bench && cd /opt/ns-3 &&
   ./ns3 run "routing-compare --scenario=paper --nNodes=20 --time=30 --runs=1 --protocols=olsr"'
```

Read results with the CLI (`ns3-tool compare <cell file>` for paired per-seed
deltas and the noise verdict). A campaign is a plan in `campaigns/`:

```bash
ns3-campaign plan campaigns/anthocnet-aodv-smoke.toml   # offline: the image and every cell's command
```

`ns3-campaign build`, `dispatch`, `status` and `collect` run the plan on
GitHub Actions through `campaign-image.yml` (one image per plan) and
`campaign-cell.yml` (one cell, pulled by digest); they need `gh` and write
access to the repository. The plan format is the docstring of
`python/ns3_tool/campaign.py`. Profiling one cell (perf, heaptrack,
massif) is [profiling.md](profiling.md).

## Docker and dev containers

`make shell` opens an interactive shell in the `ns3-dev` debug image with
this checkout at `/src`; ns-3 is at `/opt/ns-3`, and `ns3-build-contrib
--help` lists the build options. To build a protocol arm beside the harness,
mount its repository too and list the harness first, e.g.
`ns3-build-contrib routing-bench=/src/ns3/routing-bench ara=/src-ara/ns3`
with `-v ../ARA:/src-ara:ro`, plus that protocol's own dependencies
(swarm-core). Campaign images (`ns3-tools-dev`) are built by CI only. There
is no `.devcontainer/` yet.

## Lint and format

```bash
make lint      # ruff check + ruff format --check with the org's ruff.toml (fetches the org tools)
make format    # ruff format
make headers   # licence headers (org rule 1)
make org-sync-check
make check     # lint, test, headers and org-sync together
```

The C++ harness keeps AntHocNet's style and is not format-checked yet (the
`module` CI runs with `format: false` until a restyle PR adopts the org
`.clang-format`).

## CI map

| Workflow | Job (check name) | What it checks | Required |
| --- | --- | --- | --- |
| `ns3.yml` | `module / ns-latest debug`, `module / ns-latest sanitized` | the harness builds with strict warnings, `test.py -s routing-bench` passes, determinism, coverage | yes |
| `ns3.yml` | `module / headers / licence` | licence headers | yes |
| `ns3.yml` | `python-quality / python` | ruff, the Python tests with coverage | yes |
| `ns3.yml` | `stock arms deliver (...)` | OLSR, DSDV, HWMP (latest) and AODV (3.48) deliver; `##PERF##` lines | no |
| `ns3.yml` | `ns3-tool scaffold builds and registers an arm` | a scaffolded arm builds beside the harness and delivers | no |
| `ns3.yml` | `codecov`, `codecov-python` | coverage upload | no (report-only) |
| `python.yml` | `ns3-tool tests (Python 3.11)`, `(Python 3.13)` | the self-tests, the installed console scripts | yes |
| `python.yml` | `query equivalence on AntHocNet's committed cells` | readers and verdicts equal AntHocNet's on its data | yes |
| `campaign-image.yml`, `campaign-cell.yml` | | a campaign image, one cell (dispatched by `ns3-campaign`) | no (manual) |
| `org-sync.yml` | `check / org-sync` | org-managed files match the templates | yes |
| `pr-title.yml` | `title / title` | the PR title is a lowercase Conventional Commit | yes |
| `review-gate.yml` | `review / coderabbit` | CodeRabbit has reviewed the PR | yes |
| `dco.yml`, `scorecard.yml`, `sync-labels.yml` | | sign-off on outside contributions; OpenSSF Scorecard; labels | no |

The required checks are the ones the `main` ruleset lists: `gh api
repos/swarm-routing/ns3-tools/rules/branches/main`. `main` merges through a
merge queue, so a pull request is merged by enabling auto-merge.

## Release

Releases follow the org procedure: [CONTRIBUTING "Cutting a release"](https://github.com/swarm-routing/.github/blob/main/CONTRIBUTING.md#cutting-a-release).
Here `release.yml` is dispatched with `dry-run`, then `bump`; merging the
bot's `chore(release): bump version A to B` PR tags and publishes, with no
approval step (the `release` environment is ungated). A release ships the
`routing-bench` tarball and the `ns3-tool` wheel and sdist (`make dist`).

## Troubleshooting

- **A reader change alters output on the fixtures.** Reader output must stay
  byte-identical unless the change is the point; say so in the PR and update
  AntHocNet's callers.
- **A change to a marker line.** Fields are only appended; anything else
  bumps the schema version and updates the readers and swarm-routing/benchmarks
  in the same change ([markers.md](markers.md)).
- **The AODV arm hangs on the latest image.** Known ns-3.49 livelock; use
  `ghcr.io/swarm-routing/ns3-dev:3.48-debug` for it.
- **The corpus tests say a path is missing.** They need a clone of
  swarm-routing/AntHocNet and the environment variables in "Test"; without
  them they test the fixtures only.
- **Docker on Apple silicon is slow or fails with an exec format error.** The
  `ns3-dev` images are `linux/amd64` only; `make shell PLATFORM=linux/amd64`.
- **`ns3-campaign dispatch` is refused.** It dispatches workflows in a
  repository, so it needs `gh` logged in with write access there.
