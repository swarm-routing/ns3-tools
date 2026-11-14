# ns3-tools

<!-- org-sync:begin badges -->
[![CI](https://github.com/swarm-routing/ns3-tools/actions/workflows/ns3.yml/badge.svg)](https://github.com/swarm-routing/ns3-tools/actions/workflows/ns3.yml)
[![Release](https://img.shields.io/github/v/release/swarm-routing/ns3-tools?sort=semver&cacheSeconds=1800)](https://github.com/swarm-routing/ns3-tools/releases)
[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.24136068.svg)](https://doi.org/10.5281/zenodo.24136068)
[![Cite](https://img.shields.io/badge/cite-CITATION.cff-blue)](CITATION.cff)
[![Licence: GPL-2.0-only, python/ tools/ MIT OR GPL-2.0-only](https://img.shields.io/badge/licence-GPL--2.0--only%2C%20python%2F%20tools%2F%20MIT%20OR%20GPL--2.0--only-blue)](LICENSING.md)
[![Conventional Commits](https://img.shields.io/badge/Conventional%20Commits-1.0.0-yellow)](https://www.conventionalcommits.org)
[![Colony Arena: ns3-tools](https://img.shields.io/badge/Colony%20Arena-project%20page-orange)](https://swarm-routing.github.io/projects/ns3-tools/)
[![C++23](https://img.shields.io/badge/C%2B%2B-23-blue?logo=cplusplus)](AGENTS.md)
[![Runs on ns-3](https://img.shields.io/badge/runs%20on-ns--3-informational)](https://www.nsnam.org)
[![Built on: ns3-dev](https://img.shields.io/badge/built%20on-ns3--dev%20image-blue?logo=docker)](https://github.com/orgs/swarm-routing/packages/container/package/ns3-dev)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue?logo=python)](AGENTS.md)
[![Quality Gate Status](https://sonarcloud.io/api/project_badges/measure?project=swarm-routing_ns3-tools&metric=alert_status)](https://sonarcloud.io/summary/new_code?id=swarm-routing_ns3-tools)
[![Bugs](https://sonarcloud.io/api/project_badges/measure?project=swarm-routing_ns3-tools&metric=bugs)](https://sonarcloud.io/summary/new_code?id=swarm-routing_ns3-tools)
[![Security Rating](https://sonarcloud.io/api/project_badges/measure?project=swarm-routing_ns3-tools&metric=security_rating)](https://sonarcloud.io/summary/new_code?id=swarm-routing_ns3-tools)
[![Maintainability Rating](https://sonarcloud.io/api/project_badges/measure?project=swarm-routing_ns3-tools&metric=sqale_rating)](https://sonarcloud.io/summary/new_code?id=swarm-routing_ns3-tools)
[![codecov](https://codecov.io/gh/swarm-routing/ns3-tools/graph/badge.svg)](https://codecov.io/gh/swarm-routing/ns3-tools)
[![CodeRabbit Pull Request Reviews](https://img.shields.io/coderabbit/prs/github/swarm-routing/ns3-tools?utm_source=oss&utm_medium=github&utm_campaign=swarm-routing%2Fns3-tools&labelColor=171717&color=FF570A&link=https%3A%2F%2Fcoderabbit.ai&label=CodeRabbit+Reviews)](https://coderabbit.ai)
<!-- org-sync:end badges -->

Protocol-agnostic diagnostics and comparison tooling for ns-3: the
`routing-bench` comparison harness (`ns3/routing-bench/`), the result readers
and statistics (`python/`) behind one CLI, `ns3-tool` (`compare`, `sweep`,
`diagnose`, `scaffold`; `pip install ./python`, then `ns3-tool --help`), the
campaign driver `ns3-campaign`, and, planned, non-intrusive probes as their
own module (#40). **Status:** v1.0.0 is released; it declares today's CLI and marker
contract v1 stable ([docs/markers.md](docs/markers.md) "Stability").

What each release must show, and what blocks what: [docs/roadmap.md](docs/roadmap.md).

How to work here, for people and AI agents alike: [AGENTS.md](AGENTS.md) has
the rules and how to build and verify; the org conventions are in
[CONTRIBUTING.md](https://github.com/swarm-routing/.github/blob/main/CONTRIBUTING.md).

<!-- org-sync:begin citing -->
## Releases & citing

Versions follow [SemVer](https://semver.org) ([CHANGELOG.md](CHANGELOG.md)). Each release is listed under [Releases](https://github.com/swarm-routing/ns3-tools/releases), built by the org's release workflow and archived on [Zenodo](https://zenodo.org) with its own DOI.

To cite this repository, use the “Cite this repository” button (from [CITATION.cff](CITATION.cff)):

| DOI | resolves to |
|---|---|
| [`10.5281/zenodo.24136068`](https://doi.org/10.5281/zenodo.24136068) | **concept DOI**: always the latest release. This is the badge above, and the right citation when you mean this project. |

Each release also has its own **version DOI**, listed on [its Zenodo record](https://doi.org/10.5281/zenodo.24136068): cite that one when you reproduce a number measured at that release.

Please also cite the work it builds on:

- D. H. Joppi, *AntHocNet for NS-3*, 2026, [doi:10.5281/zenodo.20981979](https://doi.org/10.5281/zenodo.20981979). The source of the routing-bench harness and the ns3-tool readers, moved from it.
- G. F. Riley, T. R. Henderson, *The ns-3 Network Simulator*, Modeling and Tools for Network Simulation, pp. 15–34, 2010, [doi:10.1007/978-3-642-12331-3_2](https://doi.org/10.1007/978-3-642-12331-3_2). ns-3, the simulator the harness, probes and arenas run on.
- J. Zhao, J. Pan, *LENS: A LEO Satellite Network Measurement Dataset*, Proceedings of the 15th ACM Multimedia Systems Conference, pp. 278–284, 2024, [doi:10.1145/3625468.3652170](https://doi.org/10.1145/3625468.3652170). The Starlink measurements python/tests/fixtures/cells/lens-bruhl-20261006.txt derives its statistics from (dataset CC BY-SA 4.0; the traces are not redistributed).

Using it in your work? Contributions are welcome, and citing the DOI helps just as much.
<!-- org-sync:end citing -->

## Epics

<!-- epics:begin -->
<!-- Generated by swarm-routing/.github tools/epics/epics.py from the issues labelled `epic`; edit the issues, then re-run it. -->

What is planned and in progress here: the issues labelled [`epic`](https://github.com/swarm-routing/ns3-tools/issues?q=label%3Aepic). An epic's open sub-issues carry a `priority:*` and a `model:*` label, so a contributor or an AI agent can pick one up ([how issues work](https://github.com/swarm-routing/.github/blob/main/CONTRIBUTING.md#issues)). The whole org's plan: the [roadmap](https://github.com/swarm-routing/.github/blob/main/ROADMAP.md#epics).

| Epic | Status | Sub-issues closed |
|------|--------|-------------------|
| [#2](https://github.com/swarm-routing/ns3-tools/issues/2) A protocol-agnostic comparison harness, packaged on its own | In progress | 3 / 8 |
| [#7](https://github.com/swarm-routing/ns3-tools/issues/7) metrics 2026 — age of information, delay percentile/CDF export, energy-per-bit, route lifetime/convergence as CSV metrics | Planned | 0 / 1 |
| [#15](https://github.com/swarm-routing/ns3-tools/issues/15) Comparators: swarm, learned and secure protocols under identical conditions | Planned | 0 / 10 |
| [#34](https://github.com/swarm-routing/ns3-tools/issues/34) promote AntHocNet's vendored comparator modules into ns3/arms/: aomdv, gpsr, qrouting, oracle | Planned | 0 / 2 |
| [#43](https://github.com/swarm-routing/ns3-tools/issues/43) Network substrates: the ns-3 pieces new network types need, for every arm | Planned | 0 / 10 |

<!-- epics:end -->

## Licence

See [LICENSING.md](LICENSING.md).
