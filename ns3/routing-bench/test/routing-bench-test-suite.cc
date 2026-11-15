// SPDX-License-Identifier: GPL-2.0-only
// Copyright (C) 2026 Daniel Henrique Joppi

/**
 * Unit tests for the routing-bench arm registry (ADR-0027, #766):
 * registration, lookup, the rejection of a duplicate or empty name, the
 * optional hooks' defaults, and the stock arms' names and stream-free hwmp
 * arm; and that an arm's rows do not depend on the arms run before it
 * (--isolateArms, ns3-tools#198).
 */

#include "ns3/aodv-helper.h"
#include "ns3/ipv4.h"
#include "ns3/routing-bench-arm.h"
#include "ns3/simple-channel.h"
#include "ns3/simple-net-device.h"
#include "ns3/stock-arms.h"
#include "ns3/test.h"

#include <fcntl.h>
#include <sys/wait.h>
#include <unistd.h>

#include <array>
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <iostream>
#include <sstream>
#include <string>
#include <vector>

// TestSuite/TestCase enums became scoped (enum class) in ns-3.42 and the
// unscoped aliases were removed in ns-3.47 (the oracle module's pattern).
#ifdef ROUTING_BENCH_NS3_SCOPED_TEST_ENUMS
#define BENCH_TEST_TYPE_UNIT TestSuite::Type::UNIT
#define BENCH_TEST_QUICK TestCase::Duration::QUICK
#else
#define BENCH_TEST_TYPE_UNIT UNIT
#define BENCH_TEST_QUICK TestCase::QUICK
#endif

// A --coverage build's counters, written before the child's _exit (strong, and
// only in a coverage build: see model/arm-isolation.cc).
#ifdef ROUTING_BENCH_GCOV_DUMP
extern "C" void __gcov_dump();
#endif

namespace ns3 {
namespace bench {
namespace {

class FakeArm : public Arm {
public:
    std::string Name() const override { return "fake"; }
    void InstallRouting(InternetStackHelper&, RunContext&) override {}
    int64_t AssignStreams(RunContext&, int64_t) override { return 3; }
};

Ptr<Arm> MakeFakeArm() { return Create<FakeArm>(); }

// ns3-tools#198. Stock AODV, with a second interface on every node: a
// SimpleNetDevice alone on its own channel, so it carries nothing, but AODV
// opens sockets on it, and its RREQ loop draws one jitter per socket in the
// order of std::map<Ptr<Socket>, ...>, which is heap-address order. Which
// draw the Wi-Fi interface gets then depends on the heap: the leo-walker and
// isl-grid shape (swarm-routing/AntHocNet#1039, #362), on the harness's
// one-radio scenario.
class SpareIfAodvArm : public Arm {
public:
    std::string Name() const override { return "aodv-spare-if"; }
    void InstallRouting(InternetStackHelper& internet, RunContext&) override {
        internet.SetRoutingHelper(m_aodv);
    }
    int64_t AssignStreams(RunContext& run, int64_t stream) override {
        return m_aodv.AssignStreams(run.nodes, stream);
    }
    void AfterAddresses(RunContext& run, const Ipv4InterfaceContainer&) override {
        for (uint32_t i = 0; i < run.nodes.GetN(); ++i) {
            Ptr<Node> node = run.nodes.Get(i);
            Ptr<SimpleNetDevice> dev = CreateObject<SimpleNetDevice>();
            dev->SetChannel(CreateObject<SimpleChannel>());
            dev->SetAddress(Mac48Address::ConvertFrom(Mac48Address::Allocate()));
            node->AddDevice(dev);
            Ptr<Ipv4> ipv4 = node->GetObject<Ipv4>();
            const uint32_t ifIndex = ipv4->AddInterface(dev);
            ipv4->AddAddress(ifIndex,
                             Ipv4InterfaceAddress(Ipv4Address(0xac100001u + (i << 8)),
                                                  Ipv4Mask("255.255.255.0")));
            ipv4->SetUp(ifIndex);
        }
    }

private:
    AodvHelper m_aodv;
};

// ns3-tools#198: stock AODV that also leaves the heap in another state, the
// way an earlier arm's own allocations do (AntHocNet#1035's function-local
// static): it allocates blocks of many sizes, frees two in three and keeps
// the rest for the life of the process.
class HeapChurnArm : public Arm {
public:
    std::string Name() const override { return "heap-churn"; }
    void InstallRouting(InternetStackHelper& internet, RunContext&) override {
        static std::vector<char*> kept;
        std::vector<char*> blocks;
        for (uint32_t k = 0; k < 6000; ++k) blocks.push_back(new char[16 + (k % 256) * 8]);
        for (std::size_t k = 0; k < blocks.size(); ++k) {
            if (k % 3 == 0)
                kept.push_back(blocks[k]);
            else
                delete[] blocks[k];
        }
        internet.SetRoutingHelper(m_aodv);
    }
    int64_t AssignStreams(RunContext& run, int64_t stream) override {
        return m_aodv.AssignStreams(run.nodes, stream);
    }

private:
    AodvHelper m_aodv;
};

Ptr<Arm> MakeSpareIfAodvArm() { return Create<SpareIfAodvArm>(); }
Ptr<Arm> MakeHeapChurnArm() { return Create<HeapChurnArm>(); }

// The harness run once per command line, each in a child process, every
// child forked from this process in the same state (#211). Every argument
// vector, temporary file and status slot is made before the first fork, and
// nothing is allocated between two forks, so each child enters RunHarness
// with the same heap whatever its arguments, as routing-compare does from
// main(), whose argv lives on the stack. --isolateArms forks its workers from
// the state RunHarness is entered with: when the test built one child's argv,
// read its output and only then forked the next, each later child started
// from another heap, and an arm's rows could differ between "alone" and
// "after other arms" for that reason alone (seen under the bare test runner
// and beside AntHocNet's arm modules, not under test.py).
struct ChildRun {
    std::string output;
    int status = -1;
};

std::vector<ChildRun> RunHarnessInChildren(const std::vector<std::vector<std::string>>& runs) {
    const std::size_t n = runs.size();
    std::vector<std::vector<std::string>> words(n);
    std::vector<std::vector<char*>> argvs(n);
    std::vector<std::array<char, 40>> paths(n);
    std::vector<int> fds(n, -1);
    std::vector<int> statuses(n, -1);
    for (std::size_t i = 0; i < n; ++i) {
        words[i].push_back("routing-compare");
        words[i].insert(words[i].end(), runs[i].begin(), runs[i].end());
        for (std::string& w : words[i]) argvs[i].push_back(w.data());
        argvs[i].push_back(nullptr);
        std::snprintf(paths[i].data(), paths[i].size(), "/tmp/routing-bench-isolation-XXXXXX");
        fds[i] = mkstemp(paths[i].data());
        NS_ABORT_MSG_IF(fds[i] < 0, "mkstemp failed");
    }
    std::cout.flush();
    std::fflush(nullptr);
    // From here to the last waitpid: no allocation in this process.
    for (std::size_t i = 0; i < n; ++i) {
        const pid_t pid = fork();
        if (pid < 0) break;  // statuses[i] stays -1
        if (pid == 0) {
            dup2(fds[i], 1);
            const int rc =
                RunHarness(static_cast<int>(words[i].size()), argvs[i].data(), "routing-compare");
            std::cout.flush();
            std::fflush(nullptr);
#ifdef ROUTING_BENCH_GCOV_DUMP
            __gcov_dump();
#endif
            _exit(rc);
        }
        waitpid(pid, &statuses[i], 0);
    }
    std::vector<ChildRun> out(n);
    for (std::size_t i = 0; i < n; ++i) {
        close(fds[i]);
        std::ifstream in(paths[i].data());
        std::stringstream text;
        text << in.rdbuf();
        std::remove(paths[i].data());
        out[i].output = text.str();
        out[i].status = statuses[i];
    }
    return out;
}

// One arm's per-seed result rows (everything printed per arm and seed except
// ##PERF##, which is cost, not result).
std::string ArmRows(const std::string& output, const std::string& arm) {
    std::istringstream in(output);
    std::string line;
    std::string rows;
    for (const char* marker : {"##RUN## ", "##GOODPUT## ", "##ROUTE## ", "##HOLD## "}) {
        in.clear();
        in.seekg(0);
        while (std::getline(in, line)) {
            if (line.rfind(marker, 0) != 0) continue;
            std::istringstream f(line);
            std::string m, seed, name;
            f >> m >> seed >> name;
            if (name == arm) rows += line + "\n";
        }
    }
    return rows;
}

/// A measurement no hook may touch: every count is zero.
class NullMeasurement : public Measurement {
public:
    uint64_t ControlPackets() const override { return 0; }
    uint64_t ControlBytes() const override { return 0; }
    double DeliveredDataBytes() const override { return 0.0; }
    void TrackReinjectionIdentity() override {}
    void MacReinjectedData(const Ipv4Header&, Ptr<const Packet>) override {}
    void ArpReinjectedData(const Ipv4Header&, Ptr<const Packet>) override {}
    uint64_t ReinjectedData() const override { return 0; }
    void TrackPacketFate() override {}
    void TrackPendingQueue() override {}
    void MeshManagementEnqueue(Ptr<const BENCH_WIFI_MPDU>) override {}
    void MeshDataEnqueue(uint32_t, Ptr<const BENCH_WIFI_MPDU>) override {}
    uint64_t MeshPeeringFrames() const override { return 0; }
    uint64_t MeshBeacons() const override { return 0; }
    void TypedControlTx(uint8_t, uint8_t, bool) override {}
    void TypedControlRx(uint8_t, uint8_t) override {}
    uint64_t TypedControlTxCount(uint8_t) const override { return 0; }
    uint64_t TypedControlRxCount(uint8_t) const override { return 0; }
};

}  // namespace

class RegistryRulesTest : public TestCase {
public:
    RegistryRulesTest() : TestCase("arm registry: add, lookup, duplicate and empty names") {}

private:
    void DoRun() override {
        ArmRegistry r;
        NS_TEST_ASSERT_MSG_EQ(r.Names().empty(), true, "starts empty");
        NS_TEST_ASSERT_MSG_EQ(r.Add("fake", "a test arm", &MakeFakeArm), true, "first add");
        NS_TEST_ASSERT_MSG_EQ(r.Add("fake", "again", &MakeFakeArm), false, "duplicate refused");
        NS_TEST_ASSERT_MSG_EQ(r.Summary("fake"), "a test arm", "duplicate changed nothing");
        NS_TEST_ASSERT_MSG_EQ(r.Add("", "no name", &MakeFakeArm), false, "empty name refused");
        NS_TEST_ASSERT_MSG_EQ(r.Add("null", "no factory", nullptr), false, "null factory refused");
        NS_TEST_ASSERT_MSG_EQ(r.Names().size(), 1u, "one arm");

        NS_TEST_ASSERT_MSG_EQ(r.Has("fake"), true, "found");
        NS_TEST_ASSERT_MSG_EQ(r.Has("aodv"), false, "absent");
        NS_TEST_ASSERT_MSG_EQ(r.Make("aodv"), Ptr<Arm>(), "unknown name gives no arm");
        Ptr<Arm> a = r.Make("fake");
        Ptr<Arm> b = r.Make("fake");
        NS_TEST_ASSERT_MSG_NE(a, Ptr<Arm>(), "known name gives an arm");
        NS_TEST_ASSERT_MSG_NE(a, b, "a fresh arm for every run");
        RunContext run;
        NS_TEST_ASSERT_MSG_EQ(a->AssignStreams(run, 0), 3, "the arm's own hook");
    }
};

class DefaultHooksTest : public TestCase {
public:
    DefaultHooksTest()
        : TestCase("arm hooks: an arm that overrides none of the optional hooks changes nothing") {}

private:
    void DoRun() override {
        Ptr<Arm> arm = MakeFakeArm();
        RunContext run;
        NullMeasurement m;
        std::ostringstream out;
        arm->Configure(run);
        arm->Validate(run);
        arm->EmitConfig(out);
        NS_TEST_ASSERT_MSG_EQ(arm->ProvidesDevices(), false, "ad-hoc Wi-Fi by default");
        NetDeviceContainer radios;
        DeviceRequest req{};
        NS_TEST_ASSERT_MSG_EQ(arm->InstallDevices(run, req, radios).GetN(), 0u, "no devices");
        NS_TEST_ASSERT_MSG_EQ(radios.GetN(), 0u, "no radios");
        NS_TEST_ASSERT_MSG_EQ(arm->AssignDeviceStreams(run, 7), 0, "no device streams");
        arm->AfterStack(run);
        arm->ConnectTraces(run, m);
        arm->AfterAddresses(run, Ipv4InterfaceContainer());
        NS_TEST_ASSERT_MSG_EQ(arm->StartReplay(run, ReplayRequest(), nullptr, nullptr), false,
                              "no replay");
        arm->FinishReplay(1, 1, 100.0);
        RunCounts c;
        c.txPackets = 10;
        c.dropRoute = 2;
        arm->AdjustCounts(run, c);
        NS_TEST_ASSERT_MSG_EQ(c.txPackets, 10u, "counts untouched");
        NS_TEST_ASSERT_MSG_EQ(c.dropRoute, 2u, "counts untouched");
        arm->EmitRunNote(run, m, out);
        DropDetail d;
        arm->FillDropDetail(run, d);
        NS_TEST_ASSERT_MSG_EQ(d.holdCauses, false, "no drop detail");
        std::vector<HoldTime> hold;
        NS_TEST_ASSERT_MSG_EQ(arm->CollectHoldTimes(run, hold), false, "no pending queue");
        NS_TEST_ASSERT_MSG_EQ(hold.empty(), true, "no hold times");
        arm->AppendDiag(run, m, out);
        arm->EmitDiagnostics(run, m, 0.0, out);
        arm->Check(run, m);
        NS_TEST_ASSERT_MSG_EQ(out.str(), "", "the default hooks print nothing");
    }
};

class StockArmsTest : public TestCase {
public:
    StockArmsTest() : TestCase("stock arms: aodv, dsdv, hwmp and olsr register once") {}

private:
    void DoRun() override {
        // The process-wide registry: other suites in this binary may have
        // registered already, so register only if absent.
        if (!Registry().Has("aodv")) RegisterStockArms();
        const std::vector<std::string> want{"aodv", "dsdv", "hwmp", "olsr"};
        for (const auto& name : want) {
            NS_TEST_ASSERT_MSG_EQ(Registry().Has(name), true, name << " registered");
            Ptr<Arm> arm = Registry().Make(name);
            NS_TEST_ASSERT_MSG_EQ(arm->Name(), name, "the arm answers to its token");
        }
        // hwmp's streams are pinned with the mesh devices; static routing
        // takes none (#708).
        RunContext run;
        NS_TEST_ASSERT_MSG_EQ(Registry().Make("hwmp")->AssignStreams(run, 100), 0,
                              "hwmp takes no routing streams");
        // An olsr arm with no refusals leaves the counts alone.
        RunCounts c;
        c.txPackets = 10;
        c.dropRoute = 2;
        Registry().Make("olsr")->AdjustCounts(run, c);
        NS_TEST_ASSERT_MSG_EQ(c.txPackets, 10u, "no refusals, no change");
        NS_TEST_ASSERT_MSG_EQ(c.dropRoute, 2u, "no refusals, no change");
    }
};

class ArmIsolationTest : public TestCase {
public:
    ArmIsolationTest()
        : TestCase("isolateArms: an arm's rows do not depend on the arms run before it (#198)") {}

private:
    void DoRun() override {
        if (!Registry().Has("aodv")) RegisterStockArms();
        if (!Registry().Has("aodv-spare-if")) {
            RegisterArm("aodv-spare-if", "test: AODV with a second, idle interface",
                        &MakeSpareIfAodvArm);
        }
        if (!Registry().Has("heap-churn")) {
            RegisterArm("heap-churn", "test: AODV that reshapes the heap", &MakeHeapChurnArm);
        }
        const std::vector<std::string> base{"--scenario=paper", "--nNodes=8", "--time=8",
                                            "--runs=2", "--perf=0"};
        const auto with = [&base](std::vector<std::string> extra) {
            std::vector<std::string> a = base;
            a.insert(a.end(), extra.begin(), extra.end());
            return a;
        };
        const std::vector<std::string> arms{"aodv-spare-if", "aodv", "olsr", "dsdv"};
        // hwmp, the fourth stock arm, brings its own 802.11s devices and needs
        // 802.11a (#708). Its multi-arm run spells -protocols with one dash, a
        // spelling CommandLine reads too: the worker replaces it.
        const std::vector<std::string> mesh{"--standard=80211a", "--rateManager=constant6"};
        auto meshArgs = [&with, &mesh](const std::string& protocols) {
            std::vector<std::string> a = with(mesh);
            a.push_back(protocols);
            return a;
        };
        // Every run is made up front and forked from one state (#211).
        std::vector<std::vector<std::string>> runs;
        // 0: every arm after another arm and after a reshaped heap, in one run.
        runs.push_back(with({"--protocols=heap-churn,olsr,aodv-spare-if,aodv,dsdv"}));
        for (const std::string& arm : arms) runs.push_back(with({"--protocols=" + arm}));  // 1-4
        runs.push_back(meshArgs("--protocols=hwmp"));                                      // 5
        runs.push_back(meshArgs("-protocols=heap-churn,aodv,hwmp"));                       // 6
        // 7, 8: the same comparison with --isolateArms=0, reported below.
        runs.push_back(with({"--isolateArms=0", "--protocols=aodv-spare-if"}));
        runs.push_back(with({"--isolateArms=0", "--protocols=heap-churn,aodv-spare-if"}));
        const std::vector<ChildRun> res = RunHarnessInChildren(runs);
        for (std::size_t i = 0; i < res.size(); ++i) {
            NS_TEST_ASSERT_MSG_EQ(res[i].status, 0, "run " << i << " exits cleanly");
        }
        const std::string& after = res[0].output;
        for (std::size_t k = 0; k < arms.size(); ++k) {
            const std::string& arm = arms[k];
            const std::string rows = ArmRows(res[1 + k].output, arm);
            NS_TEST_ASSERT_MSG_NE(rows.find("##RUN## 2 " + arm), std::string::npos,
                                  arm << " printed its rows");
            NS_TEST_ASSERT_MSG_EQ(ArmRows(after, arm), rows,
                                  arm << ": same rows alone and after other arms");
        }
        const std::string hwmpRows = ArmRows(res[5].output, "hwmp");
        NS_TEST_ASSERT_MSG_NE(hwmpRows.find("##RUN## 2 hwmp"), std::string::npos,
                              "hwmp printed its rows");
        NS_TEST_ASSERT_MSG_EQ(ArmRows(res[6].output, "hwmp"), hwmpRows,
                              "hwmp: same rows alone and after other arms");
        // The same with the reshaping in the arm's own process is not this
        // test's business: --isolateArms isolates arms, not code. What it
        // fixes shows with it off: the aodv-spare-if rows after heap-churn
        // differ from the rows alone on glibc's allocator (not under ASan,
        // which does not reuse freed blocks soon), so that half is reported,
        // not asserted.
        const std::string& inAlone = res[7].output;
        const std::string& inAfter = res[8].output;
        std::clog << "isolateArms=0: aodv-spare-if after heap-churn "
                  << (ArmRows(inAlone, "aodv-spare-if") == ArmRows(inAfter, "aodv-spare-if")
                          ? "matches"
                          : "DIFFERS from")
                  << " its rows alone (#198)\n";
    }
};

class RoutingBenchTestSuite : public TestSuite {
public:
    RoutingBenchTestSuite() : TestSuite("routing-bench", BENCH_TEST_TYPE_UNIT) {
        AddTestCase(new RegistryRulesTest, BENCH_TEST_QUICK);
        AddTestCase(new DefaultHooksTest, BENCH_TEST_QUICK);
        AddTestCase(new StockArmsTest, BENCH_TEST_QUICK);
        AddTestCase(new ArmIsolationTest, BENCH_TEST_QUICK);
    }
};

static RoutingBenchTestSuite g_routingBenchTestSuite;

}  // namespace bench
}  // namespace ns3
