// Copyright (c) 2026 Daniel Joppi (https://github.com/swarm-routing)
// SPDX-License-Identifier: GPL-2.0-only

// Each arm in its own process (ns3-tools#198); see arm-isolation.h.

#include "arm-isolation.h"

#include "ns3/abort.h"

#include <fcntl.h>
#include <sys/wait.h>
#include <unistd.h>

#include <cerrno>
#include <cstdio>
#include <iostream>

// A --coverage build's counters, written before _exit skips the exit handlers
// that would write them (null without --coverage).
extern "C" void __gcov_dump() __attribute__((weak));

namespace routing_bench_detail {

namespace {

void DumpCoverage() {
    if (__gcov_dump) __gcov_dump();
}

constexpr int kMaxArgs = 1024;      // a worker's command line lives on the stack
constexpr uint32_t kMaxName = 255;  // an arm name, in a stack buffer
constexpr char kResult = 'R';       // worker -> parent: the payload
constexpr char kStatus = 'S';       // zygote -> parent: the worker's wait status

bool g_worker = false;
int g_resultFd = -1;
int g_realStdout = -1;

bool ReadAll(int fd, void* p, std::size_t n) {
    char* c = static_cast<char*>(p);
    while (n > 0) {
        const ssize_t r = read(fd, c, n);
        if (r < 0 && errno == EINTR) continue;
        if (r <= 0) return false;
        c += r;
        n -= static_cast<std::size_t>(r);
    }
    return true;
}

bool WriteAll(int fd, const void* p, std::size_t n) {
    const char* c = static_cast<const char*>(p);
    while (n > 0) {
        const ssize_t w = write(fd, c, n);
        if (w < 0 && errno == EINTR) continue;
        if (w <= 0) return false;
        c += w;
        n -= static_cast<std::size_t>(w);
    }
    return true;
}

// True unless the value spells false the way BooleanValue reads it.
bool BoolValue(const char* v) {
    return !(std::strcmp(v, "false") == 0 || std::strcmp(v, "0") == 0 || std::strcmp(v, "f") == 0);
}

// The worker: the parent's command line with --protocols=<name>, re-entering
// the harness. Everything here is on the stack or in fixed buffers, so two
// workers for the same arm and command line start from the same heap.
[[noreturn]] void RunWorker(int resFd, const char* name, int argc, char* argv[],
                            ArmIsolation::Entry entry, void* ctx) {
    g_worker = true;
    g_resultFd = resFd;
    // Until the first row, stdout is /dev/null: the parent printed the header.
    g_realStdout = dup(1);
    const int nul = open("/dev/null", O_WRONLY);
    if (g_realStdout < 0 || nul < 0 || dup2(nul, 1) < 0) _exit(120);
    close(nul);

    static char protocols[sizeof("--protocols=") + kMaxName];
    std::memcpy(protocols, "--protocols=", sizeof("--protocols=") - 1);
    std::strcpy(protocols + sizeof("--protocols=") - 1, name);
    char* wargv[kMaxArgs + 2];
    int n = 0;
    bool replaced = false;
    for (int i = 0; i < argc; ++i) {
        if (std::strncmp(argv[i], "--protocols=", sizeof("--protocols=") - 1) == 0) {
            if (!replaced) wargv[n++] = protocols;
            replaced = true;
            continue;
        }
        wargv[n++] = argv[i];
    }
    if (!replaced) wargv[n++] = protocols;
    wargv[n] = nullptr;
    const int rc = entry(n, wargv, ctx);
    // RunHarness ends a worker in WorkerFinish; returning means it did not run.
    std::fflush(nullptr);
    _exit(rc == 0 ? 121 : rc);
}

[[noreturn]] void ZygoteLoop(int reqFd, int resFd, int argc, char* argv[],
                             ArmIsolation::Entry entry, void* ctx) {
    char name[kMaxName + 1];
    for (;;) {
        uint32_t len = 0;
        if (!ReadAll(reqFd, &len, sizeof len) || len == 0 || len > kMaxName ||
            !ReadAll(reqFd, name, len)) {
            DumpCoverage();
            _exit(0);  // the parent is done (or gone)
        }
        name[len] = '\0';
        const pid_t w = fork();
        if (w == 0) {
            close(reqFd);
            RunWorker(resFd, name, argc, argv, entry, ctx);
        }
        int32_t status = -1;
        if (w > 0) {
            int st = 0;
            while (waitpid(w, &st, 0) < 0) {
                if (errno != EINTR) {
                    st = -1;
                    break;
                }
            }
            status = st;
        }
        if (!WriteAll(resFd, &kStatus, 1) || !WriteAll(resFd, &status, sizeof status)) _exit(0);
    }
}

}  // namespace

bool ArmIsolation::Requested(int argc, char* argv[]) {
    if (g_worker) return false;
    bool on = true;
    for (int i = 1; i < argc; ++i) {
        const char* a = argv[i];
        if (std::strcmp(a, "--isolateArms") == 0) {
            on = true;
        } else if (std::strncmp(a, "--isolateArms=", sizeof("--isolateArms=") - 1) == 0) {
            on = BoolValue(a + sizeof("--isolateArms=") - 1);
        }
    }
    return on;
}

bool ArmIsolation::InWorker() {
    return g_worker;
}

void ArmIsolation::Start(int argc, char* argv[], Entry entry, void* ctx) {
    NS_ABORT_MSG_IF(argc > kMaxArgs, "--isolateArms: more than " << kMaxArgs << " arguments");
    // Nothing buffered may be inherited: a worker flushes its stdout.
    std::cout.flush();
    std::fflush(nullptr);
    int req[2];
    int res[2];
    NS_ABORT_MSG_IF(pipe(req) != 0 || pipe(res) != 0, "--isolateArms: pipe() failed");
    const pid_t pid = fork();
    NS_ABORT_MSG_IF(pid < 0, "--isolateArms: fork() failed");
    if (pid == 0) {
        close(req[1]);
        close(res[0]);
        ZygoteLoop(req[0], res[1], argc, argv, entry, ctx);
    }
    close(req[0]);
    close(res[1]);
    m_req = req[1];
    m_res = res[0];
    m_zygote = pid;
}

ArmIsolation::~ArmIsolation() {
    if (m_zygote <= 0) return;
    close(m_req);  // the zygote reads end of file and exits
    close(m_res);
    int st = 0;
    while (waitpid(static_cast<pid_t>(m_zygote), &st, 0) < 0 && errno == EINTR) {
    }
}

std::string ArmIsolation::RunArm(const std::string& arm) {
    NS_ABORT_MSG_IF(arm.empty() || arm.size() > kMaxName,
                    "--isolateArms: arm name '" << arm << "' is empty or too long");
    // The worker writes to the same stdout: ours goes out first.
    std::cout.flush();
    std::fflush(stdout);
    const uint32_t len = static_cast<uint32_t>(arm.size());
    NS_ABORT_MSG_UNLESS(WriteAll(m_req, &len, sizeof len) && WriteAll(m_req, arm.data(), len),
                        "--isolateArms: the zygote is gone");
    std::string payload;
    bool got = false;
    for (;;) {
        char kind = 0;
        NS_ABORT_MSG_UNLESS(ReadAll(m_res, &kind, 1), "--isolateArms: the zygote is gone");
        if (kind == kResult) {
            uint64_t n = 0;
            NS_ABORT_MSG_UNLESS(ReadAll(m_res, &n, sizeof n), "--isolateArms: short result");
            payload.resize(n);
            NS_ABORT_MSG_UNLESS(ReadAll(m_res, payload.data(), n), "--isolateArms: short result");
            got = true;
            continue;
        }
        NS_ABORT_MSG_UNLESS(kind == kStatus, "--isolateArms: bad frame");
        int32_t st = 0;
        NS_ABORT_MSG_UNLESS(ReadAll(m_res, &st, sizeof st), "--isolateArms: short status");
        NS_ABORT_MSG_UNLESS(got && st == 0,
                            "arm '"
                                << arm << "' failed in its own process ("
                                << (st < 0            ? std::string("not started")
                                    : WIFSIGNALED(st) ? "signal " + std::to_string(WTERMSIG(st))
                                                      : "exit " + std::to_string(WEXITSTATUS(st)))
                                << "; its error is above)");
        return payload;
    }
}

void ArmIsolation::WorkerBeginOutput() {
    std::cout.flush();
    std::fflush(stdout);
    if (dup2(g_realStdout, 1) < 0) _exit(122);
    close(g_realStdout);
}

void ArmIsolation::WorkerFinish(const std::string& payload) {
    std::cout.flush();
    std::fflush(nullptr);
    const uint64_t n = payload.size();
    if (!WriteAll(g_resultFd, &kResult, 1) || !WriteAll(g_resultFd, &n, sizeof n) ||
        !WriteAll(g_resultFd, payload.data(), n)) {
        _exit(123);
    }
    DumpCoverage();
    _exit(0);
}

void PayloadReader::Take(void* p, std::size_t n) {
    NS_ABORT_MSG_IF(m_pos + n > m_s.size(), "--isolateArms: truncated payload");
    std::memcpy(p, m_s.data() + m_pos, n);
    m_pos += n;
}

}  // namespace routing_bench_detail
