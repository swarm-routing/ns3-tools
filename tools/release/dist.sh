#!/usr/bin/env bash
# Copyright (c) 2026 Daniel Joppi (https://github.com/swarm-routing)
# SPDX-License-Identifier: MIT OR GPL-2.0-only
#
# dist.sh VERSION OUT-DIR: the release assets (#26), as `make dist` runs it for
# swarm-routing/.github's reusable-release.yml:
#
#   routing-bench-VERSION.tar.gz   the harness module, ready for ns-3's contrib/:
#                                  routing-bench/ with its README, the GPL-2.0
#                                  text and INSTALL.txt
#   ns3_tool-VERSION-*.whl         the readers (python/), wheel and sdist, each
#   ns3_tool-VERSION.tar.gz        with both licence texts (#14)
#
# Deterministic: the tarball's entries are sorted with a fixed owner and the
# release commit's time, gzip -n; the Python build gets SOURCE_DATE_EPOCH.
# Only files tracked by git go into the tarball.
#
# Then it checks what it built, so a release never ships a package that does
# not work (the workflow stops before signing or tagging on a non-zero exit):
#   - the tarball, unpacked into a clean ns3-dev tree's contrib/, builds and
#     passes `test.py -s routing-bench` (docker; NS3_IMAGE);
#   - the wheel installs into a fresh venv and its console scripts start.
# DIST_CHECK=0 skips both (a quick local build).
set -euo pipefail
ver=${1:?usage: dist.sh VERSION OUT-DIR}
out=${2:?usage: dist.sh VERSION OUT-DIR}
NS3_IMAGE=${NS3_IMAGE:-ghcr.io/swarm-routing/ns3-dev:latest-debug}
BUILD_PKG=${BUILD_PKG:-build==1.6.1}   # PyPA build, the wheel and sdist front end
repo=$(cd "$(dirname "$0")/../.." && pwd)
mkdir -p "$out"
out=$(cd "$out" && pwd)
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
epoch=$(git -C "$repo" log -1 --format=%ct)

# --- routing-bench-VERSION.tar.gz -------------------------------------------
pkg="$tmp/pkg/routing-bench"
mkdir -p "$pkg"
git -C "$repo" ls-files -z ns3/routing-bench |
    (cd "$repo/ns3" && sed -z 's|^ns3/||' | xargs -0 cp --parents -t "$tmp/pkg")
cp "$repo/LICENSE" "$pkg/LICENSE"
cat > "$pkg/INSTALL.txt" <<TXT
routing-bench $ver (swarm-routing/ns3-tools): the protocol-agnostic ns-3
comparison harness (routing-compare) with the stock ns-3 arms (AODV, OLSR,
DSDV, HWMP). GPL-2.0-only (LICENSE). It links stock ns-3 only.

  tar -xzf routing-bench-$ver.tar.gz -C /path/to/ns-3/contrib
  cd /path/to/ns-3
  ./ns3 configure --enable-examples --enable-tests && ./ns3 build
  ./test.py -s routing-bench && ./ns3 run "routing-compare --PrintHelp"

Arm modules from other repositories register with it through their
helper/<module>-bench-arms.cc when installed beside it (README.md).
Source: https://github.com/swarm-routing/ns3-tools/tree/v$ver/ns3/routing-bench
TXT
tar --sort=name --owner=0 --group=0 --numeric-owner --mtime="@${epoch}" \
    -C "$tmp/pkg" -cf - routing-bench | gzip -n > "$out/routing-bench-$ver.tar.gz"

# --- the ns3-tool wheel and sdist --------------------------------------------
grep -qx "version = \"$ver\"" "$repo/python/pyproject.toml" ||
    { echo "dist.sh: python/pyproject.toml is not at version $ver" >&2; exit 1; }
python3 -m venv "$tmp/build-venv"
"$tmp/build-venv/bin/python" -m pip install --quiet "$BUILD_PKG"
# A copy, so no build/ or egg-info lands in the checkout; -L: python/LICENSE*
# are symlinks to the root texts (#14), which a copy must resolve.
cp -rL "$repo/python" "$tmp/python"
SOURCE_DATE_EPOCH=$epoch "$tmp/build-venv/bin/python" -m build --quiet \
    --sdist --wheel --outdir "$out" "$tmp/python" > /dev/null
for f in "$out/ns3_tool-$ver.tar.gz" "$out/ns3_tool-$ver-py3-none-any.whl"; do
    [ -f "$f" ] || { echo "dist.sh: the Python build wrote no $(basename "$f")" >&2; exit 1; }
done
ls -l "$out"

[ "${DIST_CHECK:-1}" = 0 ] && { echo "dist.sh: DIST_CHECK=0, the packages are not checked"; exit 0; }

# --- check: the wheel installs, carries both licence texts, its scripts start -
python3 -m venv "$tmp/check-venv"
"$tmp/check-venv/bin/python" -m pip install --quiet --no-index "$out/ns3_tool-$ver-py3-none-any.whl"
for s in ns3-bench-parse ns3-native-view ns3-comparator-view ns3-pool-runs \
         ns3-sweep-summary ns3-scenario-check ns3-campaign; do
    "$tmp/check-venv/bin/$s" --help > /dev/null
done
lic=$(python3 -m zipfile -l "$out/ns3_tool-$ver-py3-none-any.whl" | grep -c '\.dist-info/licenses/LICENSE' || true)
[ "$lic" -eq 2 ] || { echo "dist.sh: the wheel carries $lic licence texts, not 2" >&2; exit 1; }
echo "dist.sh: the wheel installs and its scripts start"

# --- check: the tarball builds in a clean ns3-dev tree and passes its suite ---
mkdir -p "$tmp/contrib"
tar -xzf "$out/routing-bench-$ver.tar.gz" -C "$tmp/contrib"
docker run --rm -v "$tmp/contrib:/contrib:ro" "$NS3_IMAGE" \
    ns3-build-contrib --test routing-bench=/contrib/routing-bench
echo "dist.sh: routing-bench-$ver.tar.gz builds in $NS3_IMAGE and passes test.py -s routing-bench"
