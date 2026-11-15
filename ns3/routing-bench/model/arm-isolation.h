// Copyright (c) 2026 Daniel Joppi (https://github.com/swarm-routing)
// SPDX-License-Identifier: GPL-2.0-only

/*
 * Each arm in its own process (ns3-tools#198).
 *
 * Stock ns-3 AODV (and DSDV) key their per-interface sockets as
 * std::map<Ptr<Socket>, Ipv4InterfaceAddress>, ordered by heap address, and
 * draw one broadcast jitter per socket in that order. On a node with more than
 * one interface the order decides which interface gets which draw, so the
 * same seed gives a different run when an earlier arm in the same process
 * left the heap in a different state (swarm-routing/AntHocNet#1039). That
 * cannot be fixed from here, so the harness runs every arm in a fresh worker
 * process by default (--isolateArms, default on).
 *
 * How: RunHarness forks a "zygote" before it allocates anything. For each arm
 * the parent sends the arm's name to the zygote, which forks a worker. The
 * worker re-enters RunHarness with the original command line and
 * `--protocols=<arm>` in place of the list, prints its arm's per-seed rows
 * (everything before them goes to /dev/null: the parent printed it), sends
 * back the arm's aggregates and _exits. Every worker starts from the same
 * heap and runs the same command line, so an arm's rows are the same whatever
 * arms are listed before it, and whatever they allocated.
 *
 * What it does not remove: the state RunHarness is entered with. Workers
 * start from that heap, so a caller that allocates differently before each
 * call (a test calling RunHarness in-process with heap-allocated argv, say)
 * still sees an arm's rows move with it. routing-compare and
 * anthocnet-compare call it first thing from main(), with argv on the stack,
 * so every invocation enters it in the same state (#211).
 *
 * The zygote's loop allocates nothing (read, fork, waitpid, write on file
 * descriptors and stack buffers), so its heap never changes after the fork.
 */

#ifndef ROUTING_BENCH_ARM_ISOLATION_H
#define ROUTING_BENCH_ARM_ISOLATION_H

#include "ns3/address.h"

#include <cstdint>
#include <cstring>
#include <map>
#include <string>
#include <type_traits>
#include <utility>

namespace routing_bench_detail {

class ArmIsolation {
public:
    /// What a worker runs: RunHarness with the worker's command line.
    using Entry = int (*)(int argc, char* argv[], void* ctx);

    /// Whether the command line leaves --isolateArms on (the default), read
    /// from argv alone, the way CommandLine will (last occurrence wins). False
    /// in a worker: a worker never isolates again.
    static bool Requested(int argc, char* argv[]);
    /// True in a worker process.
    static bool InWorker();

    ArmIsolation() = default;
    ArmIsolation(const ArmIsolation&) = delete;
    ArmIsolation& operator=(const ArmIsolation&) = delete;
    /// Stops the zygote and reaps it.
    ~ArmIsolation();

    /// Fork the zygote. Call first thing, before the process allocates
    /// anything that depends on the command line.
    void Start(int argc, char* argv[], Entry entry, void* ctx);
    bool Active() const { return m_zygote > 0; }
    /// Parent: run one arm in a fresh worker and return what it sent back.
    /// Aborts if the worker fails.
    std::string RunArm(const std::string& arm);

    /// Worker: from here on stdout is the real one (before the first row).
    static void WorkerBeginOutput();
    /// Worker: send the arm's aggregates, flush stdout and _exit(0).
    [[noreturn]] static void WorkerFinish(const std::string& payload);

private:
    int m_req = -1;      ///< parent -> zygote: arm names
    int m_res = -1;      ///< zygote and workers -> parent: frames
    long m_zygote = -1;  ///< the zygote's pid
};

// The payload a worker sends back: plain bytes, in the order written.
template <typename T> void Put(std::string& out, const T& v) {
    static_assert(std::is_trivially_copyable_v<T>, "Put copies bytes");
    out.append(reinterpret_cast<const char*>(&v), sizeof v);
}

inline void Put(std::string& out, const ns3::Address& a) {
    uint8_t buf[ns3::Address::MAX_SIZE + 2];
    const uint8_t n = static_cast<uint8_t>(a.CopyAllTo(buf, sizeof buf));
    Put(out, n);
    out.append(reinterpret_cast<const char*>(buf), n);
}

template <typename K, typename V> void Put(std::string& out, const std::map<K, V>& m) {
    Put(out, static_cast<uint64_t>(m.size()));
    for (const auto& [k, v] : m) {
        Put(out, k);
        Put(out, v);
    }
}

class PayloadReader {
public:
    explicit PayloadReader(const std::string& s) : m_s(s) {}

    template <typename T> void Get(T& v) {
        static_assert(std::is_trivially_copyable_v<T>, "Get copies bytes");
        Take(&v, sizeof v);
    }
    void Get(ns3::Address& a) {
        uint8_t n = 0;
        Get(n);
        uint8_t buf[ns3::Address::MAX_SIZE + 2];
        Take(buf, n);
        a.CopyAllFrom(buf, n);
    }
    template <typename K, typename V> void Get(std::map<K, V>& m) {
        uint64_t n = 0;
        Get(n);
        m.clear();
        for (uint64_t i = 0; i < n; ++i) {
            K k{};
            V v{};
            Get(k);
            Get(v);
            m.emplace(std::move(k), std::move(v));
        }
    }
    bool Done() const { return m_pos == m_s.size(); }

private:
    void Take(void* p, std::size_t n);
    const std::string& m_s;
    std::size_t m_pos = 0;
};

}  // namespace routing_bench_detail

#endif  // ROUTING_BENCH_ARM_ISOLATION_H
