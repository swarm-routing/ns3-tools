// SPDX-License-Identifier: GPL-2.0-only
// Copyright (C) 2026 Daniel Henrique Joppi

/**
 * Unit tests for the routing-bench arm registry (ADR-0027, #766):
 * registration, lookup, the rejection of a duplicate or empty name, the
 * optional hooks' defaults, and the stock arms' names and stream-free hwmp
 * arm.
 */

#include "ns3/routing-bench-arm.h"
#include "ns3/stock-arms.h"
#include "ns3/test.h"

#include <sstream>

// TestSuite/TestCase enums became scoped (enum class) in ns-3.42 and the
// unscoped aliases were removed in ns-3.47 (the oracle module's pattern).
#ifdef ROUTING_BENCH_NS3_SCOPED_TEST_ENUMS
#define BENCH_TEST_TYPE_UNIT TestSuite::Type::UNIT
#define BENCH_TEST_QUICK TestCase::Duration::QUICK
#else
#define BENCH_TEST_TYPE_UNIT UNIT
#define BENCH_TEST_QUICK TestCase::QUICK
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

class RoutingBenchTestSuite : public TestSuite {
public:
    RoutingBenchTestSuite() : TestSuite("routing-bench", BENCH_TEST_TYPE_UNIT) {
        AddTestCase(new RegistryRulesTest, BENCH_TEST_QUICK);
        AddTestCase(new DefaultHooksTest, BENCH_TEST_QUICK);
        AddTestCase(new StockArmsTest, BENCH_TEST_QUICK);
    }
};

static RoutingBenchTestSuite g_routingBenchTestSuite;

}  // namespace bench
}  // namespace ns3
