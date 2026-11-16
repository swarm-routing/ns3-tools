# Upstream ns-3 bugs the org found

The register of defects in **upstream ns-3** (the simulator and its stock
modules, not our code) that swarm-routing found while benchmarking, running CI
or testing the `ns3-dev` images. It lives here because every comparison runs
through this repository's harness (`routing-bench`), where the stock arms
(AODV, DSDV, OLSR, HWMP) meet these defects (maintainer decision 2026-11-16,
[#233](https://github.com/swarm-routing/ns3-tools/issues/233)).

Each entry says what goes wrong, which ns-3 releases and modules it touches,
how to reproduce it, what our repositories do about it, every issue and pull
request that worked on it, and whether it has been reported upstream. The
evidence stays in the linked threads; this page is the index into them. A
workaround anywhere in the org (a pinned image, a `test/known-failures.txt`
entry, a refused arm) names its `NS3-NNN` row here.

Upstream reports are filed on the [nsnam GitLab](https://gitlab.com/nsnam/ns-3-dev/-/issues)
by the maintainer, under their own account: a report speaks for the project. A
session drafts the text in an issue and records the link once it is filed; it
never posts upstream.

## Index

Status values: **not yet reported** (no upstream issue), **reported** (an
upstream issue exists, no fix merged), **fix proposed** (an upstream merge
request is open), **fixed in ns-3.X** (the first release with the fix),
**by design** (upstream behaviour we work around, not a defect to report),
**unclassified** (not yet known whether the defect is ns-3's).

| ID | Title | ns-3 versions | Module | Status | Upstream |
|----|-------|---------------|--------|--------|----------|
| [NS3-001](#ns3-001-aodv-livelocks-in-a-zero-time-icmp-net-unreachable-storm) | AODV livelocks in a zero-time ICMP "net unreachable" storm | 3.49 (and ns-3-dev since 2026-10); not 3.48 | internet (`Ipv4L3Protocol::SendIcmpNoRoute`), aodv | open upstream, not yet reported (draft ready, filing decided 2026-11-09); worked around | not yet reported |
| [NS3-002](#ns3-002-aodv-and-dsdv-order-per-interface-sockets-by-heap-address) | AODV and DSDV order per-interface sockets by heap address | 3.42, 3.48, 3.49 checked | aodv, dsdv | open upstream, not yet reported (text ready); worked around | not yet reported |
| [NS3-003](#ns3-003-dsdv-sends-on-a-route-with-a-null-output-device) | DSDV sends on a route with a null output device (assert, or a silent SIGSEGV in optimized builds) | 3.36–3.48 reproduced; ns-3-dev of 2026-08-15 | dsdv (fault seen in internet's `Ipv4L3Protocol`) | reported, fix proposed (merge request open); worked around | [#1364](https://gitlab.com/nsnam/ns-3-dev/-/issues/1364), [!2985](https://gitlab.com/nsnam/ns-3-dev/-/merge_requests/2985) |
| [NS3-004](#ns3-004-address-type-numbers-are-assigned-per-process) | `ns3::Address` type numbers are assigned per process | 3.49 shown | network (`Address::Register`) | by design; worked around | not a report candidate |
| [NS3-005](#ns3-005-four-wifi-unit-suites-fail-on-348-with-a-reduced-module-set) | Four wifi unit suites fail on 3.48 with a reduced module set | 3.48; 3.49 passes | wifi (tests) | unclassified | not yet reported |

---

## NS3-001: AODV livelocks in a zero-time ICMP "net unreachable" storm

**Problem.** On ns-3.49 a stock AODV run in a network that stays partitioned
never finishes. Simulated time stops at one instant and the process spins until
it is killed. ns-3.49 added `Ipv4L3Protocol::SendIcmpNoRoute` (RFC 1812
§4.3.2.7, ns-3 issue 825), which sends an ICMP Destination Unreachable on every
routing failure of a forwarding node, including AODV's request-queue drops
reported through the error callback. The ICMP error has no route either, so
AODV defers it through loopback into the same full request queue; the queue
drops another packet, which raises another ICMP error, all at the same instant.
RFC 1122 §3.2.2 forbids an ICMP error about an ICMP error, and
`SendIcmpNoRoute` checks neither that nor whether the node itself originated
the packet.

What we saw: CI steps that ran AODV or an AODV-derived arm (AntHocNet's
vendored AOMDV) ran until cancelled (about 2 h) or until the job timeout. One
10 s run logged 1,284,336 `SendDestUnreach*` calls, all at simulated time
+8.146253127 s. The gdb backtrace cycles through `LoopbackNetDevice::Receive`
→ `Ipv4L3Protocol::Receive` → `aodv::RoutingProtocol::RouteInput` →
`DeferredRouteOutput` → `aodv::RequestQueue::Enqueue` → `RequestQueue::Drop`
→ `Ipv4L3Protocol::RouteInputError` → `SendIcmpNoRoute`.

**Affected versions and modules.** ns-3.49 (the release the `latest` images
carry). ns-3-dev showed it on 2026-10-08 (commit `230ea2a3`), and `master`
still had the same `SendIcmpNoRoute` on 2026-11-01 (source read, not run).
ns-3.48 is not affected. Modules: `internet` (`Ipv4L3Protocol`,
`Icmpv4L4Protocol`) and `aodv`; any AODV-derived protocol that defers packets
the same way (AntHocNet's vendored `aomdv`). OLSR, DSDV and HWMP finish.

**Findings.**

- First seen on ns-3-dev by the nightly build: [swarm-routing/AntHocNet#656](https://github.com/swarm-routing/AntHocNet/issues/656)
  (backtrace through `SendIcmpNoRoute`), from [swarm-routing/AntHocNet#658](https://github.com/swarm-routing/AntHocNet/pull/658).
- Full investigation on 3.49 (CI steps that hung, the 3.48 vs 3.49 table, the
  ICMP count, the source diff that rules out AODV itself):
  [swarm-routing/AntHocNet#843](https://github.com/swarm-routing/AntHocNet/issues/843).
- Org-wide tracking, the minimal reproducer with stock models only and the
  strict expected failure in the image pipeline:
  [swarm-routing/ns3-containers#35](https://github.com/swarm-routing/ns3-containers/issues/35),
  [its reproducer comment](https://github.com/swarm-routing/ns3-containers/issues/35#issuecomment-6567122574).
- The upstream report as it would be filed:
  [swarm-routing/ns3-containers#55](https://github.com/swarm-routing/ns3-containers/issues/55).

**How to reproduce.** Stock models only, with the `manet-canary` program
([`test/fixtures/manet-canary/manet-canary.cc`](https://github.com/swarm-routing/ns3-containers/blob/main/test/fixtures/manet-canary/manet-canary.cc)
in swarm-routing/ns3-containers: 16 nodes in 500 m × 500 m, 802.11b ad hoc,
`RangePropagationLossModel`, random waypoint ≤ 20 m/s with 120 s pause, three
UDP CBR flows, 60 s simulated):

```bash
# from a swarm-routing/ns3-containers checkout: runs manet-canary aodv/olsr/dsdv
# under the wall-clock budget, with test/known-failures.txt applied
test/run.sh --stages integration ghcr.io/swarm-routing/ns3-dev:3.49-debug   # sparse-aodv: XFAIL (timeout)
test/run.sh --stages integration ghcr.io/swarm-routing/ns3-dev:3.48-debug   # sparse-aodv: finishes
```

or by hand in an ns-3 tree (the program copied to `scratch/manet-canary/`):

```bash
timeout 120 ./ns3 run "manet-canary --protocol=aodv --range=100"   # 3.49: never finishes; 3.48: 2 s
timeout 120 ./ns3 run "manet-canary --protocol=aodv --range=100 --seed=2"   # 3.49: never finishes; 3.48: 4 s
timeout 120 ./ns3 run "manet-canary --protocol=olsr --range=100"   # finishes on both
```

Through this repository's harness (expected: finishes in about 6 s on 3.48;
actual on 3.49: no output after 150 s):

```bash
docker run --rm -v "$PWD:/src:ro" ghcr.io/swarm-routing/ns3-dev:3.49-debug \
    timeout 300 ns3-build-contrib routing-bench=/src/ns3/routing-bench \
    --run "routing-compare --nNodes=16 --area=500 --range=175 --pause=120 --flows=3 --time=60 --runs=1 --protocols=aodv"
```

The trigger needs a network partitioned long enough for AODV's request queue
to drop packets: stock `examples/routing/manet-routing-compare` with its
defaults does not reproduce it, and `--range=175` (connected) finishes.

**Workaround in our repositories.** Maintainer decision of 2026-11-02, option
A ([comment](https://github.com/swarm-routing/ns3-containers/issues/35#issuecomment-6568436492)):
the CI steps that run AODV-family arms use `ns3-dev:3.48-<profile>`, a
documented, temporary exception to the rule that CI follows `latest`; no gate is
skipped or loosened. Revert each when the first ns-3 release with the fix
reaches `latest`.

- ns3-tools: the `aodv` arm's delivery check runs on `ns3-dev:3.48`
  (`.github/workflows/ns3.yml`, `stock-arms` matrix; `AGENTS.md` "Build &
  verify").
- swarm-routing/ns3-containers: `test/known-failures.txt` holds
  `3.49 sparse-aodv ns3-containers#35 timeout`, a strict expected failure (the
  stage fails if the run ever finishes); README "Known issues and findings".
- swarm-routing/AntHocNet: the gates, package checks and kits run on
  `ns3-dev:3.48` (`ci.yml`, `ci-ns3.yml`, `ci-web.yml`, `release.yml`,
  `campaign-image.yml`, `benchmarks.yml`; `AGENTS.md`), from
  [swarm-routing/AntHocNet#842](https://github.com/swarm-routing/AntHocNet/pull/842).

**Investigation and fix trail.** swarm-routing/AntHocNet#656 (found on
ns-3-dev, closed as a duplicate), swarm-routing/AntHocNet#658 (the nightly
ns-3-dev build that found it), swarm-routing/AntHocNet#843 (investigation,
open until the fixed release), swarm-routing/AntHocNet#842 (CI on the 3.48
pin), swarm-routing/ns3-containers#35 (org tracking, open),
swarm-routing/ns3-containers#42 and swarm-routing/ns3-containers#48 (the
integration stage and `manet-canary`), swarm-routing/ns3-containers#55 (the
upstream draft, open).

**Upstream.** Not yet reported. The maintainer decided on 2026-11-09 to file
it ([comment](https://github.com/swarm-routing/ns3-containers/issues/55#issuecomment-6748640772)):
first re-run the reproducer on current ns-3-dev `master` and record the commit,
then file the draft from swarm-routing/ns3-containers#55. A search of the nsnam
GitLab issues for `SendIcmpNoRoute` on 2026-11-16 found no report. Suggested
fix (in the draft): send no ICMP error about an ICMP error (protocol 1, types
3, 4, 5, 11, 12) or about a packet the node originated.

---

## NS3-002: AODV and DSDV order per-interface sockets by heap address

**Problem.** Stock AODV keeps its per-interface sockets in
`std::map<Ptr<Socket>, Ipv4InterfaceAddress> m_socketAddresses` and
`m_socketSubnetBroadcastAddresses`; DSDV has the same `m_socketAddresses`.
`Ptr<T>::operator<` compares raw pointers, so the maps iterate in heap-address
order. AODV draws one broadcast jitter (`m_uniformRandomVariable->GetInteger(0, 10)`)
per socket inside the loop when it sends or forwards a RREQ, sends a hello or a
RERR; DSDV iterates its map for every periodic and triggered update. On a node
with more than one interface, which interface gets which draw, and the order of
the sends, depends on where the allocator placed each socket. A fixed seed then
does not give a fixed result once anything earlier in the process allocated
differently (org golden rule 5). With one interface per node the maps hold one
entry and nothing varies.

What we saw:

- AntHocNet's `isl-grid` (a torus, four interfaces per node) gave different
  AODV rows for `--protocols=anthocnet,aodv` and `--protocols=aodv,anthocnet`,
  same seeds (seed 2: `delay99` 11 → 24 ms).
- Stock AODV's satellite rows moved with an unrelated commit in the v2.2.0
  re-baseline, while every other row reproduced byte for byte.
- AntHocNet's `leo-walker` gate: a function-local static added in the
  `anthocnet` arm moved the `aodv` arm's row run after it in the same process
  (`##RUN## 1 aodv 60.26 105.14 849.00 …` → `56.73 103.35 870.00 …`).
- On a 3×3 point-to-point torus, 3000 dummy allocations before the run moved
  AODV's `delaySum` 3.7024096 → 4.3779808 and `l3tx` 2432 → 2431. With the two
  maps ordered by interface index (patched in a scratch container) every churn
  level gave the identical row.

**Affected versions and modules.** `aodv` and `dsdv`. The declarations were
read in ns-3.42 (swarm-routing/AntHocNet#362), ns-3.48 and ns-3.49 (#206:
`aodv-routing-protocol.h:256`, `:259`; `dsdv-routing-protocol.h:151`). The
code predates those releases; earlier ones were not checked.

**Findings.**

- First found, mechanism and the per-protocol signature:
  [swarm-routing/AntHocNet#362](https://github.com/swarm-routing/AntHocNet/issues/362).
- Stock AODV's satellite rows moving with unrelated code:
  [swarm-routing/AntHocNet#675](https://github.com/swarm-routing/AntHocNet/issues/675).
- The `leo-walker` gate's AODV row following another arm's allocation:
  [swarm-routing/AntHocNet#1039](https://github.com/swarm-routing/AntHocNet/issues/1039).
- The cause shown on stock AODV with a heap churn, and the patched control:
  [#198](https://github.com/swarm-routing/ns3-tools/issues/198),
  [its closing comment](https://github.com/swarm-routing/ns3-tools/issues/198#issuecomment-6896069314).
- The same dependence on the state the harness is entered with (the test
  forked its runs from different heaps):
  [#211](https://github.com/swarm-routing/ns3-tools/issues/211),
  [#212](https://github.com/swarm-routing/ns3-tools/pull/212) (churn 0/50/500/5000
  gave three different `aodv-spare-if` rows from one command line).
- The upstream text, line references and proposed fix:
  [#206](https://github.com/swarm-routing/ns3-tools/issues/206).

**How to reproduce.** This repository's test suite carries the case: the
`aodv-spare-if` test arm is stock AODV with a second, idle interface on every
node, and `heap-churn` is stock AODV that reshapes the heap.

```bash
docker run --rm -v "$PWD:/src:ro" ghcr.io/swarm-routing/ns3-dev:3.49-debug \
    ns3-build-contrib --test routing-bench=/src/ns3/routing-bench
```

`ArmIsolationTest` ("isolateArms: an arm's rows do not depend on the arms run
before it (#198)") asserts the isolated half and **reports** the stock
behaviour: with `--isolateArms=0` it prints
`isolateArms=0: aodv-spare-if after heap-churn DIFFERS from its rows alone (#198)`
on glibc's allocator (expected once ns-3 fixes it: `matches`). ASan does not
reuse freed blocks soon, so the sanitized image may print `matches`. Seeds are
the suite's own. The upstream report's own reproducer (a 3×3
point-to-point torus, stock AODV, UDP traffic, a few thousand allocations kept
alive before the second `Simulator::Run()`) is described in #206; no
standalone program for it is committed yet.

**Workaround in our repositories.** The code is stock ns-3, so nobody patches
it (golden rule 2; stock images in swarm-routing/ns3-containers). Instead:

- ns3-tools: `routing-compare` and `anthocnet-compare` run each arm in its own
  worker process, forked from one zygote, by default (`--isolateArms`, since
  v1.1.0, [#202](https://github.com/swarm-routing/ns3-tools/pull/202);
  `ns3/routing-bench/README.md` "Each arm in its own process"). This removes
  the dependence on *earlier arms*; a multi-interface scenario still depends
  on the allocations within one arm's own run.
- swarm-routing/AntHocNet: its own adapter orders the maps by the bound
  device's interface index
  ([swarm-routing/AntHocNet#471](https://github.com/swarm-routing/AntHocNet/pull/471));
  `tools/checks/check-seed-independence.py` compares the `anthocnet` rows only
  in the `isl-grid` order case, because AODV "is upstream ns-3 and still
  pointer-keyed"; the `leo-walker` gate runs the stock `aodv` arm in its own
  process ([swarm-routing/AntHocNet#1059](https://github.com/swarm-routing/AntHocNet/pull/1059));
  `docs/benchmarks/methodology.md` records the threat to validity for
  multi-interface baseline comparisons.

**Investigation and fix trail.** swarm-routing/AntHocNet#362 (found),
swarm-routing/AntHocNet#471 (our adapter's maps ordered),
swarm-routing/AntHocNet#675 (stock AODV satellite rows), swarm-routing/AntHocNet#1039
(the `leo-walker` gate), swarm-routing/AntHocNet#1040 (the static removed,
which only hid it), swarm-routing/AntHocNet#1059 (the gate's AODV arm in its
own process), [#198](https://github.com/swarm-routing/ns3-tools/issues/198)
(the cause shown), [#202](https://github.com/swarm-routing/ns3-tools/pull/202)
(`--isolateArms`, v1.1.0), [#211](https://github.com/swarm-routing/ns3-tools/issues/211)
and [#212](https://github.com/swarm-routing/ns3-tools/pull/212) (the test's own
heap, v1.1.1), [#214](https://github.com/swarm-routing/ns3-tools/pull/214)
(the suite through the bare test runner in CI),
[#206](https://github.com/swarm-routing/ns3-tools/issues/206) (the upstream
text, open).

**Upstream.** Not yet reported. The report is ready to file in #206 (title,
description, reproducer, proposed fix: key the maps by interface index, e.g.
`std::map<uint32_t, std::pair<Ptr<Socket>, Ipv4InterfaceAddress>>`); filing it
is the maintainer's. When a fixed release reaches `ns3-dev:latest`, #206 adds
a multi-interface, single-process (`--isolateArms=0`) heap-churn case that
must pass for `aodv` and `dsdv`.

---

## NS3-003: DSDV sends on a route with a null output device

**Problem.** Two defects in stock DSDV, the first enough on its own:

- **(a)** `LookForQueuedPackets()` ignores the return value of
  `LookupRoute(rt.GetNextHop(), newrt)`. When the lookup fails, `newrt` stays
  default-constructed: its `Ipv4Route` is allocated, so the `NS_ASSERT(route)`
  below passes, but its output device is null. `Ipv4L3Protocol::SendRealOut`
  then computes `GetInterfaceForDevice(nullptr) == -1`.
- **(b)** DSDV advertises its own address as `m_ipv4->GetAddress(1, 0)`, a
  hard-coded interface index, so a node with several interfaces announces only
  its first address, and neighbours learn next hops (the update's source
  address) that no advertisement names. That makes (a) fire reliably on
  multi-interface nodes.

What we saw: with asserts on, `NS_ASSERT failed, cond="interface >= 0"` in
`src/internet/model/ipv4-l3-protocol.cc` at DSDV's first periodic update
(+15.017 s on a 6×6 ISL torus); in an optimized build a bare SIGSEGV with empty
stderr (exit 139). Which seed crashed depended on what ran before in the
process. Upstream issue [#503](https://gitlab.com/nsnam/ns-3-dev/-/issues/503)
(2021, single-interface Wi-Fi, about 0.27 % of 7800 runs) carries the same
frames, so (a) is also reachable with one interface.

**Affected versions and modules.** `dsdv`; the fault surfaces in `internet`'s
`Ipv4L3Protocol`, which is why the assert names the wrong module. Reproduced on
ns-3.36 and ns-3.48 (same simulated time), on point-to-point and CSMA, and
present in ns-3-dev `master` on 2026-08-15 at the same lines as 3.48. ns-3.49
was not run by us; the upstream fix is not merged (2026-11-16), so it is
expected to carry the defect.

**Findings.**

- The crash on the ISL torus:
  [swarm-routing/AntHocNet#281](https://github.com/swarm-routing/AntHocNet/issues/281)
  (four CI runs, the assert, the optimized SIGSEGV) and
  [swarm-routing/AntHocNet#420](https://github.com/swarm-routing/AntHocNet/issues/420)
  (root cause: DSDV assumes one non-loopback interface).
- The upstream handoff: both defects, the dead assert that upstream MR !1652
  moved in 2023, a survey of the sibling protocols, the reproducer and the
  draft: [swarm-routing/AntHocNet#434](https://github.com/swarm-routing/AntHocNet/issues/434);
  the archived handoff page, with the repaired reproducer and both patches, is
  in [its comment](https://github.com/swarm-routing/AntHocNet/issues/434#issuecomment-6267904034).
- The filing record and its verification against GitLab:
  [swarm-routing/AntHocNet#445](https://github.com/swarm-routing/AntHocNet/pull/445),
  [swarm-routing/AntHocNet#452](https://github.com/swarm-routing/AntHocNet/pull/452),
  [the reconciliation comment](https://github.com/swarm-routing/AntHocNet/issues/434#issuecomment-5310630314).

**How to reproduce.** The standalone program `dsdv-multi-interface-crash.cc`
(stock modules only: a three-node point-to-point chain, n2 → n0 traffic; only
n1 has two interfaces) is inlined in
[swarm-routing/AntHocNet#434](https://github.com/swarm-routing/AntHocNet/issues/434)
§6; use the repaired copy from the
[archived handoff](https://github.com/swarm-routing/AntHocNet/issues/434#issuecomment-6267904034),
because the copy in the issue body lost its template arguments to HTML
escaping. In an ns-3 tree with asserts on (for example
`ghcr.io/swarm-routing/ns3-dev:3.48-debug`), the program copied to `scratch/`:

```bash
./ns3 run "dsdv-multi-interface-crash"                   # NS_ASSERT failed, cond="interface >= 0" at +1.512 s
./ns3 run "dsdv-multi-interface-crash --nNodes=2"        # runs clean
./ns3 run "dsdv-multi-interface-crash --protocol=aodv"   # completes and delivers (also olsr, rip)
./ns3 run "dsdv-multi-interface-crash --csma=1"          # the same assert over CSMA links
```

Expected: the run completes and delivers, as AODV, OLSR and RIP do on the same
topology. Actual: the assert (debug or default profile), or exit 139 with empty
stderr (optimized/release profile). No randomness is involved; the fault is
deterministic on the chain.

**Workaround in our repositories.**

- swarm-routing/AntHocNet: `isl-grid` refuses `dsdv` on any topology where a
  node holds more than one ISL ("ns-3's DSDV is single-interface-only (#420)"),
  and `ci-ns3.yml`'s satellite protocol-arm smoke asserts every arm runs on a
  3×3 torus and that `dsdv` is refused rather than crashing
  ([swarm-routing/AntHocNet#422](https://github.com/swarm-routing/AntHocNet/pull/422)).
- ns3-tools: `routing-compare`'s own scenarios give each node one Wi-Fi
  interface, so the deterministic multi-interface trigger is not reached; the
  single-interface path of (a) stays possible, as upstream #503 shows. No guard
  here.

**Investigation and fix trail.** swarm-routing/AntHocNet#281 (found, closed as
a duplicate of #420), swarm-routing/AntHocNet#420 (root cause),
swarm-routing/AntHocNet#422 (the `isl-grid` guard and the arm smoke),
swarm-routing/AntHocNet#434 (upstream handoff, closed by the filing),
swarm-routing/AntHocNet#445 and swarm-routing/AntHocNet#452 (the filing record),
swarm-routing/AntHocNet#437 and swarm-routing/AntHocNet#416 (the same audit
turned on AntHocNet's vendored GPSR and AOMDV).

**Upstream.** Reported by the maintainer on 2026-08-16:
[nsnam/ns-3-dev#1364](https://gitlab.com/nsnam/ns-3-dev/-/issues/1364), with
fix merge request [nsnam/ns-3-dev!2985](https://gitlab.com/nsnam/ns-3-dev/-/merge_requests/2985)
(the guard for (a), the per-interface advertisement for (b), the error callback
on the drop, a multi-interface regression test) and a pointer comment on
[#503](https://gitlab.com/nsnam/ns-3-dev/-/issues/503). On 2026-11-16 both
#1364 and !2985 are open and the fix is in no release (ns-3.49 is the latest).
When it ships, AntHocNet's `isl-grid` guard can be revisited.

---

## NS3-004: Address type numbers are assigned per process

**Problem.** An `ns3::Address` carries a type number that `Address::Register`
hands out per process, in first-use order. The same kind of address
(`InetSocketAddress`) can therefore have a different type number in two
processes, and its serialised bytes (`CopyAllTo`) do not compare equal after
they cross a process boundary. This is how ns-3 is designed (addresses are not
meant to be compared across processes), so it is a pitfall for multi-process
tools, not a defect to report.

What we saw: with `--isolateArms` (each arm in its own worker), `--protocols=hwmp,olsr`
printed no `##COMMON##` rows at all, without a warning. The hwmp worker builds
802.11s mesh devices, which register an address type first, so its keys
carried type 2 while the olsr and aodv workers' carried type 1; the cross-arm
intersection of `(flow, seq)` keys was empty. AntHocNet's pin control lost its
static-mesh `##COMMON##` rows the same way (8 rows with v0.1.0, 0 with v1.1.1).

**Affected versions and modules.** `network` (`Address`); shown on ns-3.49
(`ns3-dev:latest-debug`). Any tool that ships `ns3::Address` bytes between
processes is affected, on any release with this registration scheme.

**Findings.** [#218](https://github.com/swarm-routing/ns3-tools/issues/218)
(the missing rows, the reproducer table), [#219](https://github.com/swarm-routing/ns3-tools/pull/219)
(root cause confirmed by printing the type byte in each worker).

**How to reproduce.** With routing-bench v1.1.1 (before the fix), in
`ghcr.io/swarm-routing/ns3-dev:latest-debug`, count the `##COMMON##` rows:

```bash
routing-compare --scenario=paper --nNodes=20 --time=60 --runs=1 --perf=0 --standard=80211a \
    --rateManager=constant6 --protocols=hwmp,olsr --isolateArms=1   # 0 rows (actual, before the fix)
# the same with --isolateArms=0, or --protocols=aodv,olsr: 2 rows (expected)
```

From v1.1.2 the same command prints the rows, and the test suite's
`ArmIsolationTest` asserts that `hwmp,olsr` isolated prints the same
non-empty `##MATCH##` and `##COMMON##` rows as `--isolateArms=0`.

**Workaround in our repositories.** ns3-tools: the arm workers send an
`InetSocketAddress` (and an `Inet6SocketAddress`) by value, IP and port, and
the parent rebuilds it with its own type number; any other address keeps its
bytes, tagged as raw (`ns3/routing-bench/model/arm-isolation.h`). The harness
warns on stderr when every arm delivered but no `(flow, seq)` is common to all,
instead of printing nothing (v1.1.2, [#219](https://github.com/swarm-routing/ns3-tools/pull/219)).

**Investigation and fix trail.** [#218](https://github.com/swarm-routing/ns3-tools/issues/218)
(found by swarm-routing/AntHocNet#1063's pin control),
[#219](https://github.com/swarm-routing/ns3-tools/pull/219) (fix and test,
released in v1.1.2).

**Upstream.** Not a report candidate: per-process registration is ns-3's
design. Recorded here so the next multi-process tool does not rediscover it.

---

## NS3-005: Four wifi unit suites fail on 3.48 with a reduced module set

**Problem.** On ns-3.48 built with the `ns3-dev` images' 16-module set, four
wifi unit suites fail: `wifi-txop`, `wifi-mlo`, `wifi-gcr` and
`wifi-emlsr-basic-exchanges`. With the reduced set the simulation transmits
fewer frames than the test expects; `WifiTxopTest::CheckResults` records that
with an `NS_TEST_EXPECT` and then indexes past its frame list, so the run ends
in a SIGSEGV (UBSan: member call on an invalid `WifiMacHeader` at
`wifi-txop-test.cc:1427`). The same suites pass on 3.48 built with every
module, and on 3.49 with the images' set. It is not yet known whether this is
an ns-3 defect (an undeclared module dependency, plus a test that should
`ASSERT` before indexing) or a consequence of the images' module list.

**Affected versions and modules.** ns-3.48, `wifi` (its tests); debug and
default profiles. ns-3.49 passes. The suites test 802.11ax/be machinery (EMLSR,
MLO, groupcast, TXOP limits) that the org's 802.11a/b ad hoc scenarios do not
use.

**Findings.** [swarm-routing/ns3-containers#49](https://github.com/swarm-routing/ns3-containers/issues/49)
(the failing table, the backtrace, the full-module check, the ASan/UBSan run,
the exposure analysis); swarm-routing/ns3-containers README "Known issues and
findings".

**How to reproduce.** In `ghcr.io/swarm-routing/ns3-dev:3.48-debug`,
reconfigure with the image's recorded flags and
`--filter-module-examples-and-tests=wifi`, build, then:

```bash
./test.py --no-build -c unit -f QUICK        # expected: all pass; actual: the four suites FAIL
./build/utils/ns3.48-test-runner-debug --suite=wifi-txop   # SIGSEGV (exit 139)
```

Seeds are the suites' own. On a 3.48 tree configured with all modules (no
`--enable-modules`, fresh CMake cache), the four pass.

**Workaround in our repositories.** None needed for results: 3.48 is history,
built on demand, and the paths are not ones our scenarios use. The images'
`unit` stage would fail an on-demand 3.48 build until the cause is classified.

**Investigation and fix trail.** swarm-routing/ns3-containers#49 (open,
parked), found while sizing the unit stage (swarm-routing/ns3-containers#40).
Next step recorded there: bisect the modules missing from the image, one at a
time, rerunning `wifi-txop`.

**Upstream.** Not yet reported. 3.49's release notes list wifi fixes in these
areas (!2938, !2944, !2945 and others), none matched to these suites yet. If
the cause turns out to be ns-3's, the report is the maintainer's to file.

---

## Not registered: open questions

Findings that may or may not be an ns-3 defect, with no cause yet, stay in
their issue until one is shown:

- [swarm-routing/AntHocNet#923](https://github.com/swarm-routing/AntHocNet/issues/923):
  ns-3 3.42 → 3.48 moves AntHocNet's delay tail and overhead (same build
  recipe, different release); an intended upstream change or a regression is
  not known.

## Adding a bug

Anyone who finds a defect in upstream ns-3 (a session or a contributor) adds it
here, in the pull request that records the finding or the one right after it.

1. Open the issue that holds the evidence, in the repository where it was
   found, and link it from the entry.
2. Take the next free number, `NS3-NNN`; numbers are never reused, and an entry
   is never deleted: a fixed bug keeps its row with status **fixed in
   ns-3.X**.
3. Add a row to the index and a section with these fields:
   - **Problem**: what goes wrong in ns-3, and the symptom we saw;
   - **Affected versions and modules**: the releases checked (say which were
     run and which only read), the modules;
   - **Findings**: links to the issues, comments and runs with the evidence;
   - **How to reproduce**: the exact command(s), the image tag (for example
     `ghcr.io/swarm-routing/ns3-dev:3.49-debug`), the seed, expected vs actual;
     a minimal program if one exists in a repository;
   - **Workaround in our repositories**: what each repository does, with links;
   - **Investigation and fix trail**: every issue and pull request that
     investigated, simulated or fixed it, in the cross-repository form
     `swarm-routing/<repo>#N`;
   - **Upstream**: the report's status and link. The maintainer files upstream
     reports; a session drafts the text in an issue and never posts upstream.
4. Every workaround points back at its row: a `test/known-failures.txt` entry in
   swarm-routing/ns3-containers, a pinned image in a workflow, a refused arm, a
   gate that skips an arm. Write the ID next to the existing issue reference
   (a comment line such as `# NS3-001: …` above a `known-failures.txt` entry, a
   workflow comment, a code comment).
5. When a fix reaches a release, update the status, re-run the reproducer on
   that release, and remove the workarounds in the same round, each linking
   back here.

Only what a source supports goes in: a claim without a link to its evidence
stays in its issue until it has one.
