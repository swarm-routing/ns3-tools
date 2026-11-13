#!/usr/bin/env python3
# Copyright (c) 2026 Daniel Joppi (https://github.com/swarm-routing)
# SPDX-License-Identifier: MIT OR GPL-2.0-only
"""ns3-tool scaffold NAME [--out DIR] [--holder "Name"] [--year YYYY]

Write the skeleton of an ns-3 contrib module that is an arm of the comparison
harness (ns3-tools#39): it registers through `helper/<NAME>-bench-arms.cc`,
the contract of `ns3/routing-bench/README.md` "Registering an arm" (AntHocNet
ADR-0027), so `routing-compare --protocols=<NAME>` runs it once the module is
installed next to `routing-bench`:

    DIR/NAME/CMakeLists.txt              build_lib; the arm file only where routing-bench is enabled
    DIR/NAME/model/NAME.{h,cc}           the module's own code (a placeholder to replace)
    DIR/NAME/helper/NAME-bench-arms.cc   the arm: its name, its routing helper, its streams
    DIR/NAME/README.md                   what to replace, how to build and run it

Until it is replaced, the arm installs stock ns-3 OLSR, so a fresh skeleton
builds, registers and delivers: CI builds it beside routing-bench and checks
that `routing-compare --PrintHelp` lists it and that it delivers. The files
are GPL-2.0-only (an ns-3 module), with the copyright holder given by
--holder. Nothing is overwritten: an existing DIR/NAME is refused.
"""

import argparse
import datetime
import os
import re
import sys

NAME = re.compile(r"^[a-z][a-z0-9-]*[a-z0-9]$")
RESERVED = {"core", "routing-bench"}  # ns-3's src/core; the harness itself


def files(name, holder, year):
    """{relative path: text} of the skeleton."""
    fn = name.replace("-", "_")  # RegisterArms_<module>: hyphens become underscores
    ns = fn
    cls = "".join(p.capitalize() for p in name.split("-"))
    head = f"// Copyright (c) {year} {holder}\n// SPDX-License-Identifier: GPL-2.0-only\n"
    cmake_head = f"# Copyright (c) {year} {holder}\n# SPDX-License-Identifier: GPL-2.0-only\n"
    return {
        "CMakeLists.txt": f"""{cmake_head}#
# The `{name}` ns-3 contrib module, an arm of swarm-routing/ns3-tools'
# comparison harness (routing-bench). The arm file is compiled only where
# routing-bench is installed and enabled, and its library is named through a
# variable: ns-3 scans module CMakeLists for library tokens, and `{name}` must
# also build alone (routing-bench README, "Registering an arm").
set({fn}_bench_sources)
set({fn}_bench_libs)
set({fn}_bench_module routing-bench)
if(EXISTS ${{PROJECT_SOURCE_DIR}}/contrib/${{{fn}_bench_module}}/CMakeLists.txt
   AND {fn}_bench_module IN_LIST ns3-all-enabled-modules)
  set({fn}_bench_sources helper/{name}-bench-arms.cc)
  set({fn}_bench_lib lib${{{fn}_bench_module}})
  set({fn}_bench_libs ${{${{{fn}_bench_lib}}}})
endif()

build_lib(
  LIBNAME {name}
  SOURCE_FILES
    model/{name}.cc
    ${{{fn}_bench_sources}}
  HEADER_FILES
    model/{name}.h
  # olsr: the placeholder routing the arm installs until you replace it.
  LIBRARIES_TO_LINK ${{libcore}} ${{libinternet}} ${{libolsr}} ${{{fn}_bench_libs}}
)
""",
        f"model/{name}.h": f"""{head}//
// The `{name}` module's own code. Replace this placeholder with the
// protocol: its RoutingProtocol (an Ipv4RoutingProtocol) and the helper that
// installs it, which helper/{name}-bench-arms.cc then names.
#ifndef {fn.upper()}_H
#define {fn.upper()}_H

namespace ns3 {{
namespace {ns} {{

/// The module's name, so the library has a source file before the protocol exists.
const char* ModuleName();

}}  // namespace {ns}
}}  // namespace ns3

#endif  // {fn.upper()}_H
""",
        f"model/{name}.cc": f"""{head}
#include "{name}.h"

namespace ns3 {{
namespace {ns} {{

const char* ModuleName() {{
    return "{name}";
}}

}}  // namespace {ns}
}}  // namespace ns3
""",
        f"helper/{name}-bench-arms.cc": f"""{head}//
// `{name}` as an arm of swarm-routing/ns3-tools' comparison harness
// (routing-bench): compiled only when routing-bench is installed beside this
// module (CMakeLists.txt). routing-compare finds this file and calls
// RegisterArms_{fn}() at start-up; `--protocols={name}` runs the arm.
//
// TODO: replace OlsrHelper, the placeholder, with this module's own helper,
// and say in RegisterArm's description which paper the arm follows. An arm
// counts in a comparison only after its three gates (org golden rule 6): a
// fidelity sheet from its paper, the per-PR delivery smoke, and an anchor
// reproducing the paper's own trend.
#include "ns3/olsr-helper.h"
#include "ns3/routing-bench-arm.h"

namespace ns3 {{
namespace {ns} {{
namespace {{

class {cls}Arm : public bench::Arm {{
public:
    std::string Name() const override {{ return "{name}"; }}
    void InstallRouting(InternetStackHelper& internet, bench::RunContext&) override {{
        internet.SetRoutingHelper(m_helper);
    }}
    int64_t AssignStreams(bench::RunContext& run, int64_t stream) override {{
        return m_helper.AssignStreams(run.nodes, stream);
    }}

private:
    OlsrHelper m_helper;  // TODO: this module's helper
}};

Ptr<bench::Arm> Make{cls}Arm() {{
    return Create<{cls}Arm>();
}}

}}  // namespace
}}  // namespace {ns}

namespace bench {{

void RegisterArms_{fn}();

void RegisterArms_{fn}() {{
    RegisterArm("{name}", "{name} (placeholder: stock OLSR until the module's own routing replaces it)",
                &{ns}::Make{cls}Arm);
}}

}}  // namespace bench
}}  // namespace ns3
""",
        "README.md": f"""# {name}

An ns-3 contrib module and an arm of
[swarm-routing/ns3-tools](https://github.com/swarm-routing/ns3-tools)' comparison
harness, written by `ns3-tool scaffold {name}`.

- `model/{name}.{{h,cc}}`: the module's code; replace the placeholder with the
  protocol and its helper.
- `helper/{name}-bench-arms.cc`: the arm. It installs stock OLSR until you
  name your helper there (the `TODO` lines).
- `CMakeLists.txt`: add your sources and the ns-3 libraries they link.

Build it beside `routing-bench` in an `ns3-dev` image and run the arm:

```bash
docker run --rm -v "$PWD:/src/{name}:ro" -v /path/to/ns3-tools:/src/ns3-tools:ro \\
    ghcr.io/swarm-routing/ns3-dev:latest-debug \\
    ns3-build-contrib routing-bench=/src/ns3-tools/ns3/routing-bench {name}=/src/{name} \\
    --run "routing-compare --scenario=paper --nNodes=20 --time=30 --runs=1 --protocols={name}"
```

Its numbers count in a comparison only after its three gates: a fidelity sheet
from its paper, the per-PR delivery smoke, and an anchor reproducing the
paper's own trend.
""",
    }


def main(argv=None):
    ap = argparse.ArgumentParser(prog="ns3-tool scaffold", description=__doc__.split("\n")[0])
    ap.add_argument("name", help="the module and arm name, [a-z][a-z0-9-]*")
    ap.add_argument("--out", default=".", help="the directory the module directory goes in")
    ap.add_argument("--holder", default="<copyright holder>", help="the copyright line's holder")
    ap.add_argument("--year", type=int, default=datetime.date.today().year)
    a = ap.parse_args(argv)
    if not NAME.match(a.name) or a.name in RESERVED:
        print(f"scaffold: '{a.name}' is not a module name ([a-z][a-z0-9-]*, not core "
              "or routing-bench)", file=sys.stderr)  # fmt: skip
        return 2
    root = os.path.join(a.out, a.name)
    if os.path.exists(root):
        print(f"scaffold: {root} exists; nothing written", file=sys.stderr)
        return 2
    for rel, text in files(a.name, a.holder, a.year).items():
        path = os.path.join(root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(text)
        print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
