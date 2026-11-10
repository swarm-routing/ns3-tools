#!/usr/bin/env python3
# Copyright (c) 2026 Daniel Joppi (https://github.com/swarm-routing)
# SPDX-License-Identifier: MIT OR GPL-2.0-only
"""Validate benchmark runs: the manifest, the marker data, and immutability.

A run is a directory runs/<yyyy-mm-dd>-<campaign>/ holding manifest.json and
the files it lists (cells of raw marker lines, the pre-registration, others).

  validate_run.py check RUN_DIR [...]     one or more runs, fully
  validate_run.py all [RUNS_ROOT]         every entry of runs/ (default: this
                                          repo's runs/); a directory without a
                                          manifest fails (golden rule 11)
  validate_run.py manifest FILE           a manifest alone: schema and the checks
                                          that need no data (e.g. the example)
  validate_run.py immutable [--base REF]  refuse a change to a merged run: any
                                          edit, deletion or addition under a run
                                          directory that exists at the merge base
                                          with REF (default origin/main; rule 10)
  validate_run.py --self-test             every check, on fixtures built in a
                                          temporary directory

What a full check enforces:
  - manifest.json against schemas/manifest.schema.json (a stdlib interpreter of
    the keywords that schema uses; an unsupported keyword is an error, so the
    schema can never say more than is checked);
  - the directory is <date>-<campaign>; the base image tag's ns-3 version is the
    manifest's; the campaign image is not a rolling tag, and a campaign tag
    <commit7>-<basesha7>[-optimized] names a module commit; the harness module is a module;
  - every file in the directory is listed with its sha256, and every listed file
    is there; the pre-registration, when a path, is a listed file;
  - every cell parses under the marker contract schemas/markers/v<N>.json the
    manifest names: each ##NAME## known, ##RUN## rows well formed, ##CONFIG##,
    ##RUN## and ##PROV## present, a ##HARNESS## version equal to N;
  - per scenario, the data's arms are the manifest's, and each arm ran exactly
    the manifest's seeds (>= 20, golden rule 6), once each;
  - every ##PROV## line names the base digest, the campaign-image digest and
    every module commit, and no other digest or commit;
  - a supersedes entry names an existing run.

Stdlib only. Exit 0 when everything passes, 1 on a problem, 2 on a usage error.
"""

import argparse
import contextlib
import datetime
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCHEMA_PATH = os.path.join(REPO, "schemas", "manifest.schema.json")
MARKERS_DIR = os.path.join(REPO, "schemas", "markers")
EXAMPLE_PATH = os.path.join(REPO, "schemas", "examples", "manifest.example.json")
MANIFEST = "manifest.json"

MARKER = re.compile(r"^##([A-Z]+)##(?:\s+(.*))?$")
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
COMMIT = re.compile(r"(?<![0-9a-f])[0-9a-f]{40}(?![0-9a-f])")
BASE_TAG = re.compile(r":([0-9]+\.[0-9]+(?:\.[0-9]+)?)-")
# <commit7>-<basesha7>, or with the -optimized profile suffix that
# swarm-routing/.github reusable-ns3-image.yml appends to every tag it pushes.
CAMPAIGN_TAG = re.compile(r"^([0-9a-f]{7})-([0-9a-f]{7})(-optimized)?$")

# --- the JSON Schema subset ---------------------------------------------------

ANNOTATIONS = {"$schema", "$id", "title", "description", "$defs"}
KEYWORDS = {
    "$ref",
    "type",
    "const",
    "enum",
    "pattern",
    "minLength",
    "minimum",
    "items",
    "minItems",
    "uniqueItems",
    "properties",
    "required",
    "additionalProperties",
    "anyOf",
}
TYPES = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
}


def unsupported_keywords(schema, where="$"):
    """Keywords of `schema` this interpreter does not enforce (must be none)."""
    out = []
    if isinstance(schema, dict):
        for k, v in schema.items():
            if k not in KEYWORDS and k not in ANNOTATIONS:
                out.append(f"{where}.{k}")
            if k in ("properties", "$defs"):
                for name, sub in v.items():
                    out += unsupported_keywords(sub, f"{where}.{k}.{name}")
            elif k == "items" or (k == "additionalProperties" and isinstance(v, dict)):
                out += unsupported_keywords(v, f"{where}.{k}")
            elif k == "anyOf":
                for i, sub in enumerate(v):
                    out += unsupported_keywords(sub, f"{where}.anyOf[{i}]")
    return out


def _same(a, b):
    return json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def schema_errors(inst, schema, root, path="manifest"):
    """Every way `inst` breaks `schema`, as 'path: reason' strings."""
    if "$ref" in schema:
        ref = schema["$ref"]
        if not ref.startswith("#/$defs/"):
            return [f"{path}: unsupported $ref {ref}"]
        return schema_errors(inst, root["$defs"][ref[len("#/$defs/") :]], root, path)
    errs = []
    if "type" in schema and not TYPES[schema["type"]](inst):
        return [f"{path}: expected {schema['type']}, got {type(inst).__name__}"]
    if "const" in schema and not _same(inst, schema["const"]):
        errs.append(f"{path}: must be {json.dumps(schema['const'])}")
    if "enum" in schema and not any(_same(inst, e) for e in schema["enum"]):
        errs.append(f"{path}: must be one of {', '.join(map(json.dumps, schema['enum']))}")
    if isinstance(inst, str):
        if "pattern" in schema and not re.search(schema["pattern"], inst):
            errs.append(f"{path}: {inst!r} does not match {schema['pattern']}")
        if len(inst) < schema.get("minLength", 0):
            errs.append(f"{path}: shorter than {schema['minLength']}")
    if TYPES["number"](inst) and "minimum" in schema and inst < schema["minimum"]:
        errs.append(f"{path}: below the minimum {schema['minimum']}")
    if isinstance(inst, list):
        if len(inst) < schema.get("minItems", 0):
            errs.append(f"{path}: {len(inst)} items, at least {schema['minItems']} required")
        if schema.get("uniqueItems"):
            seen = [json.dumps(i, sort_keys=True) for i in inst]
            if len(set(seen)) != len(seen):
                errs.append(f"{path}: items are not unique")
        if "items" in schema:
            for i, item in enumerate(inst):
                errs += schema_errors(item, schema["items"], root, f"{path}[{i}]")
    if isinstance(inst, dict):
        for key in schema.get("required", []):
            if key not in inst:
                errs.append(f"{path}: missing required '{key}'")
        props = schema.get("properties", {})
        for key, val in inst.items():
            if key in props:
                errs += schema_errors(val, props[key], root, f"{path}.{key}")
            elif schema.get("additionalProperties") is False:
                errs.append(f"{path}: unknown field '{key}'")
    if "anyOf" in schema:
        branches = [schema_errors(inst, s, root, path) for s in schema["anyOf"]]
        if all(branches):
            errs.append(f"{path}: matches none of its forms ({'; '.join(b[0] for b in branches)})")
    return errs


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def known_marker_versions():
    out = {}
    for name in sorted(os.listdir(MARKERS_DIR)):
        m = re.fullmatch(r"v([0-9]+)\.json", name)
        if m:
            out[int(m.group(1))] = load_json(os.path.join(MARKERS_DIR, name))
    return out


# --- the manifest's own consistency ------------------------------------------


def manifest_errors(m, run_name=None):
    """Schema, then the checks that need no data. `run_name` is the directory's name."""
    schema = load_json(SCHEMA_PATH)
    errs = schema_errors(m, schema, schema)
    if errs:
        return errs  # the semantic checks below assume the shape
    try:
        datetime.date.fromisoformat(m["date"])
    except ValueError:
        errs.append(f"date: {m['date']} is not a calendar date")
    if run_name is not None and run_name != f"{m['date']}-{m['campaign']}":
        errs.append(f"directory {run_name} is not <date>-<campaign> = {m['date']}-{m['campaign']}")
    base = m["base_image"]
    tag_ver = BASE_TAG.search(base["ref"]).group(1)
    if tag_ver != base["ns3_version"]:
        errs.append(f"base_image: tag names ns-3 {tag_ver}, ns3_version says {base['ns3_version']}")
    if base["digest"] == m["campaign_image"]["digest"]:
        errs.append("campaign_image: same digest as the base image (the modules are not in it)")
    commits = [mod["commit"] for mod in m["modules"]]
    names = [mod["name"] for mod in m["modules"]]
    if len(set(names)) != len(names):
        errs.append("modules: a module is listed twice")
    ref = m["campaign_image"]["ref"]
    repo, tag = ref.rsplit(":", 1)
    if "latest" in tag:
        errs.append(f"campaign_image: {ref} is a rolling tag (golden rule 11)")
    elif repo.endswith("-ns3-campaign"):
        t = CAMPAIGN_TAG.match(tag)
        if not t:
            errs.append(f"campaign_image: tag {tag} is not <commit7>-<basesha7>[-optimized]")
        elif not any(c.startswith(t.group(1)) for c in commits):
            errs.append(f"campaign_image: tag commit {t.group(1)} is none of the modules' commits")
    elif not tag.endswith("-optimized"):
        errs.append(
            f"campaign_image: release tag {tag} is not <version>-optimized (golden rule 13)"
        )
    if m["harness"]["module"] not in names:
        errs.append(f"harness: module {m['harness']['module']} is not in modules")
    scen = [s["name"] for s in m["scenarios"]]
    if len(set(scen)) != len(scen):
        errs.append("scenarios: a scenario is listed twice")
    paths = [f["path"] for f in m["files"]]
    if len(set(paths)) != len(paths):
        errs.append("files: a path is listed twice")
    if MANIFEST in paths:
        errs.append("files: manifest.json lists itself")
    for f in m["files"]:
        if f["kind"] == "cell" and f.get("scenario") not in scen:
            errs.append(f"files: cell {f['path']} names no scenario of the manifest")
        if f["kind"] != "cell" and "scenario" in f:
            errs.append(f"files: {f['path']} is not a cell but names a scenario")
    for s in scen:
        if not any(f["kind"] == "cell" and f.get("scenario") == s for f in m["files"]):
            errs.append(f"scenarios: {s} has no cell file")
    prereg = m["preregistration"].get("path")
    listed_prereg = [f["path"] for f in m["files"] if f["kind"] == "preregistration"]
    if prereg is not None and prereg not in listed_prereg:
        errs.append(f"preregistration: {prereg} is not a files entry of kind preregistration")
    if prereg is None and listed_prereg:
        errs.append("files: a preregistration file is listed but the manifest points to a URL")
    if m["markers"]["schema_version"] not in known_marker_versions():
        errs.append(
            f"markers: schema_version {m['markers']['schema_version']} has no "
            "schemas/markers/v<N>.json"
        )
    sup = m.get("supersedes")
    if sup and run_name is not None and sup["run"] == run_name:
        errs.append("supersedes: a run cannot supersede itself")
    return errs


# --- the data ----------------------------------------------------------------


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _number(tok):
    try:
        float(tok)
        return True
    except ValueError:
        return False


def cell_errors(path, rel, contract, m, rows):
    """Marker-line errors of one cell; adds its ##RUN## rows to `rows`."""
    errs = []
    seen = set()
    version = m["markers"]["schema_version"]
    base = m["base_image"]["digest"]
    image = m["campaign_image"]["digest"]
    commits = {mod["commit"] for mod in m["modules"]}
    with open(path, encoding="utf-8", errors="replace") as f:
        lines = f.read().splitlines()
    for n, line in enumerate(lines, 1):
        if not line.startswith("##"):
            continue  # the human table and '# ...' lines are not markers
        where = f"{rel}:{n}"
        mk = MARKER.match(line)
        if not mk:
            errs.append(f"{where}: malformed marker line")
            continue
        name, body = mk.group(1), (mk.group(2) or "").strip()
        spec = contract["markers"].get(name)
        if spec is None:
            errs.append(f"{where}: ##{name}## is not in marker contract v{version}")
            continue
        seen.add(name)
        toks = body.split()
        if spec["form"] == "keyvalue" and not all("=" in t[1:] for t in toks):
            errs.append(f"{where}: ##{name}## fields must all be key=value")
        if spec["form"] == "positional":
            if len(toks) < spec["min_fields"]:
                errs.append(
                    f"{where}: ##{name}## has {len(toks)} fields, at least "
                    f"{spec['min_fields']} required"
                )
            elif name == "RUN":
                if not toks[0].isdigit() or not all(_number(t) for t in toks[2:]):
                    errs.append(f"{where}: ##RUN## is not <run> <arm> <numbers...>")
                else:
                    rows.append((int(toks[0]), toks[1], where))
        if name == "HARNESS":
            v = dict(t.split("=", 1) for t in toks if "=" in t).get("version")
            if v != str(version):
                errs.append(f"{where}: ##HARNESS## version={v}, the manifest says {version}")
        if name == "PROV":
            digests = set(DIGEST.findall(body))
            hexes = set(COMMIT.findall(DIGEST.sub("", body)))
            for what, d in (("base", base), ("campaign-image", image)):
                if d not in digests:
                    errs.append(f"{where}: ##PROV## does not name the {what} digest {d}")
            for d in sorted(digests - {base, image}):
                errs.append(f"{where}: ##PROV## names digest {d}, not in the manifest")
            for c in sorted(commits - hexes):
                errs.append(f"{where}: ##PROV## does not name module commit {c}")
            for c in sorted(hexes - commits):
                errs.append(f"{where}: ##PROV## names commit {c}, not a module of the manifest")
    for name in contract["required"]:
        if name not in seen:
            errs.append(f"{rel}: no ##{name}## line")
    if contract.get("harness_line_required") and "HARNESS" not in seen:
        errs.append(f"{rel}: no ##HARNESS## version line (contract v{version} requires it)")
    return errs


def run_errors(run_dir):
    """Every problem of one run directory."""
    run_dir = os.path.normpath(run_dir)
    name = os.path.basename(run_dir)
    mpath = os.path.join(run_dir, MANIFEST)
    if not os.path.isfile(mpath):
        return [f"no {MANIFEST} (golden rule 11: no manifest, no merge)"]
    try:
        m = load_json(mpath)
    except (OSError, ValueError) as e:
        return [f"{MANIFEST}: not JSON ({e})"]
    schema = load_json(SCHEMA_PATH)
    if schema_errors(m, schema, schema):
        return manifest_errors(m, name)  # the schema errors; the data checks assume the shape
    errs = manifest_errors(m, name)
    on_disk = set()
    for dirpath, _dirs, files in os.walk(run_dir):
        for f in files:
            rel = os.path.relpath(os.path.join(dirpath, f), run_dir).replace(os.sep, "/")
            if rel != MANIFEST:
                on_disk.add(rel)
    listed = {f["path"]: f for f in m["files"]}
    for rel in sorted(on_disk - set(listed)):
        errs.append(f"{rel}: in the directory but not in files")
    for rel in sorted(set(listed) - on_disk):
        errs.append(f"{rel}: listed in files but missing")
    for rel in sorted(on_disk & set(listed)):
        if sha256(os.path.join(run_dir, rel)) != listed[rel]["sha256"]:
            errs.append(f"{rel}: sha256 differs from the manifest")
    sup = m.get("supersedes")
    if sup and not os.path.isfile(os.path.join(os.path.dirname(run_dir), sup["run"], MANIFEST)):
        errs.append(f"supersedes: run {sup['run']} does not exist")
    contract = known_marker_versions().get(m["markers"]["schema_version"])
    if contract is None:
        return errs
    seeds = set(m["seeds"])
    for scen in m["scenarios"]:
        rows = []
        for f in m["files"]:
            if f["kind"] == "cell" and f.get("scenario") == scen["name"] and f["path"] in on_disk:
                errs += cell_errors(os.path.join(run_dir, f["path"]), f["path"], contract, m, rows)
        per_arm = {}
        for seed, arm, where in rows:
            got = per_arm.setdefault(arm, {})
            if seed in got:
                errs.append(
                    f"{where}: {scen['name']} {arm} seed {seed} again (first at {got[seed]})"
                )
            got.setdefault(seed, where)
        for arm in sorted(set(per_arm) - set(scen["arms"])):
            errs.append(f"{scen['name']}: data has arm {arm}, not in the manifest's arms")
        for arm in scen["arms"]:
            got = set(per_arm.get(arm, {}))
            if not got:
                errs.append(f"{scen['name']}: no ##RUN## rows for arm {arm}")
                continue
            if seeds - got:
                errs.append(f"{scen['name']} {arm}: seeds missing {_ranges(seeds - got)}")
            if got - seeds:
                errs.append(
                    f"{scen['name']} {arm}: seeds not in the manifest {_ranges(got - seeds)}"
                )
    return errs


def _ranges(nums):
    return ",".join(str(n) for n in sorted(nums))


def report(label, errs):
    if errs:
        print(f"FAIL {label}")
        for e in errs:
            print(f"  - {e}")
    else:
        print(f"OK   {label}")
    return not errs


def check_all(root):
    if not os.path.isdir(root):
        print(f"OK   {root}: no runs yet")
        return True
    ok = True
    for entry in sorted(os.listdir(root)):
        path = os.path.join(root, entry)
        if not os.path.isdir(path):
            ok = report(path, [f"{entry}: runs/ holds only run directories"]) and ok
        else:
            ok = report(path, run_errors(path)) and ok
    return ok


# --- immutability (golden rule 10) -------------------------------------------


def _git(repo, *args):
    return subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, check=False)


def immutable_errors(repo, base):
    """Changes under runs/ since the merge base with `base` that touch a merged run."""
    mb = _git(repo, "merge-base", base, "HEAD")
    if mb.returncode:
        return [f"git merge-base {base} HEAD failed: {mb.stderr.strip()}"]
    mb = mb.stdout.strip()
    diff = _git(repo, "diff", "--name-status", "--no-renames", mb, "HEAD", "--", "runs/")
    if diff.returncode:
        return [f"git diff failed: {diff.stderr.strip()}"]
    errs = []
    verbs = {"M": "edits", "D": "deletes", "T": "changes the type of"}
    for line in diff.stdout.splitlines():
        status, path = line.split("\t", 1)
        parts = path.split("/")
        if len(parts) < 3:
            errs.append(f"{path}: runs/ holds only run directories")
            continue
        merged = _git(repo, "cat-file", "-e", f"{mb}:runs/{parts[1]}").returncode == 0
        if status != "A":
            errs.append(f"{path}: {verbs.get(status[0], 'changes')} a merged file (golden rule 10)")
        elif merged:
            errs.append(f"{path}: adds a file to merged run {parts[1]} (golden rule 10)")
    return errs


# --- self-test ---------------------------------------------------------------


def _cell(m, arms, seeds, prov=None, extra=()):
    base, image = m["base_image"]["digest"], m["campaign_image"]["digest"]
    mods = ",".join(f"{x['name']}@{x['commit']}" for x in m["modules"])
    out = ["# example cell", "##CONFIG## scenario=paper nNodes=50 time=900 runs=20 firstRun=1"]
    for arm in arms:
        for s in seeds:
            out.append(f"##RUN## {s} {arm} 95.1 18.4 258.5 12.51 6.06 27.79 2.5 120.5 7.7219")
    out.append("protocol  pdr  delay   (the human table is not a marker)")
    out += list(extra)
    out.append(
        prov
        if prov is not None
        else f"##PROV## base={m['base_image']['ref']}@{base} "
        f"image={m['campaign_image']['ref']}@{image} modules={mods} run_id=1 attempt=1"
    )
    return "\n".join(out) + "\n"


def _make_run(root, m, cell_text, extra_files=None):
    run = os.path.join(root, f"{m['date']}-{m['campaign']}")
    os.makedirs(os.path.join(run, "cells"), exist_ok=True)
    files = {"cells/paper-50n.txt": cell_text, **(extra_files or {})}
    for rel, text in files.items():
        with open(os.path.join(run, rel), "w", encoding="utf-8") as f:
            f.write(text)
    for entry in m["files"]:
        p = os.path.join(run, entry["path"])
        if os.path.isfile(p):
            entry["sha256"] = sha256(p)
    with open(os.path.join(run, MANIFEST), "w", encoding="utf-8") as f:
        json.dump(m, f, indent=2)
    return run


def self_test():
    failures = []

    def expect(name, errs, needle):
        hit = any(needle in e for e in errs) if needle else not errs
        if not hit:
            failures.append(f"{name}: expected {needle or 'no error'!r}, got {errs}")

    schema = load_json(SCHEMA_PATH)
    expect("schema uses only enforced keywords", unsupported_keywords(schema), None)
    example = load_json(EXAMPLE_PATH)
    expect("example manifest validates", manifest_errors(example), None)
    for v, c in known_marker_versions().items():
        expect(f"markers v{v} names its version", [] if c["version"] == v else ["x"], None)

    def fresh():
        return json.loads(json.dumps(example))

    arms, seeds = example["scenarios"][0]["arms"], example["seeds"]

    def case(name, needle, mutate=None, cell=None, extra_files=None, before=None):
        with tempfile.TemporaryDirectory() as root:
            m = fresh()
            if mutate:
                mutate(m)
            if before:
                before(root)
            text = cell(m) if cell else _cell(m, arms, seeds)
            run = _make_run(root, m, text, extra_files)
            expect(name, run_errors(run), needle)

    case("valid run", None)

    def drop(key):
        return lambda m: m.pop(key)

    def setp(path, value):
        def f(m):
            obj = m
            for k in path[:-1]:
                obj = obj[k]
            obj[path[-1]] = value

        return f

    # rule 11: the manifest and what it pins
    case("missing seeds", "missing required 'seeds'", drop("seeds"))
    case("missing commands", "missing required 'commands'", drop("commands"))
    case("unknown field", "unknown field 'imagee'", setp(["imagee"], "x"))
    case(
        "rolling base tag",
        "does not match",
        setp(["base_image", "ref"], "ghcr.io/swarm-routing/ns3-dev:latest-optimized"),
    )
    case("short digest", "does not match", setp(["base_image", "digest"], "sha256:abc"))
    case(
        "base tag vs ns3_version",
        "tag names ns-3 3.48",
        setp(["base_image", "ns3_version"], "3.49"),
    )
    case(
        "rolling campaign tag",
        "rolling tag",
        setp(["campaign_image", "ref"], "ghcr.io/swarm-routing/ara-ns3-campaign:latest"),
    )
    case(
        "campaign tag with the profile suffix",
        None,
        setp(
            ["campaign_image", "ref"],
            "ghcr.io/swarm-routing/ara-ns3-campaign:0123456-bbbbbbb-optimized",
        ),
    )
    case(
        "campaign tag with another profile",
        "is not <commit7>-<basesha7>[-optimized]",
        setp(
            ["campaign_image", "ref"],
            "ghcr.io/swarm-routing/ara-ns3-campaign:0123456-bbbbbbb-debug",
        ),
    )
    case(
        "campaign tag names no module",
        "none of the modules' commits",
        setp(["campaign_image", "ref"], "ghcr.io/swarm-routing/ara-ns3-campaign:fffffff-bbbbbbb"),
    )
    case(
        "campaign digest is the base's",
        "same digest as the base",
        setp(["campaign_image", "digest"], example["base_image"]["digest"]),
    )
    case("short module commit", "does not match", setp(["modules", 0, "commit"], "0123456"))
    case("harness module absent", "is not in modules", setp(["harness", "module"], "aodv-bench"))
    case("unknown marker version", "has no schemas/markers", setp(["markers", "schema_version"], 9))
    case("preregistration absent", "missing required 'preregistration'", drop("preregistration"))
    case(
        "preregistration path unlisted",
        "is not a files entry",
        setp(["preregistration"], {"path": "plan.md"}),
    )
    # rule 13: optimized only
    case("debug profile", 'must be "optimized"', setp(["base_image", "profile"], "debug"))
    case(
        "debug base tag",
        "does not match",
        setp(["base_image", "ref"], "ghcr.io/swarm-routing/ns3-dev:3.48-debug-1a2b3c4"),
    )
    case(
        "release image not optimized",
        "is not <version>-optimized",
        setp(["campaign_image", "ref"], "ghcr.io/swarm-routing/ara-ns3:1.0.0-debug"),
    )
    # rule 6: >= 20 paired seeds, every arm, every seed
    case("19 seeds", "at least 20 required", setp(["seeds"], list(range(1, 20))))
    case(
        "seed missing in the data",
        "aodv: seeds missing 20",
        cell=lambda m: _cell(m, ["ara"], seeds) + _cell(m, ["aodv"], seeds[:-1]),
    )
    case(
        "seed run twice",
        "seed 3 again",
        cell=lambda m: _cell(m, arms, seeds, extra=["##RUN## 3 ara 1 2 3 4 5"]),
    )
    case(
        "seed not in the manifest",
        "seeds not in the manifest 21",
        cell=lambda m: _cell(m, arms, seeds, extra=["##RUN## 21 ara 1 2 3 4 5"]),
    )
    case(
        "cell without a scenario",
        "names no scenario of the manifest",
        lambda m: m["files"][0].pop("scenario"),
    )
    case("arm missing", "no ##RUN## rows for arm aodv", cell=lambda m: _cell(m, ["ara"], seeds))
    case(
        "extra arm",
        "data has arm olsr",
        cell=lambda m: _cell(m, arms + ["olsr"], seeds),
    )
    # the directory and the files
    with tempfile.TemporaryDirectory() as root:
        run = _make_run(root, fresh(), _cell(fresh(), arms, seeds))
        moved = os.path.join(root, "2026-11-21-ara-anchor")
        os.rename(run, moved)
        expect("directory name", run_errors(moved), "is not <date>-<campaign>")
    with tempfile.TemporaryDirectory() as root:
        run = _make_run(root, fresh(), _cell(fresh(), arms, seeds))
        with open(os.path.join(run, "cells", "paper-50n.txt"), "a", encoding="utf-8") as f:
            f.write("##RUN## 1 ara 0 0 0 0 0\n")
        expect("edited cell", run_errors(run), "sha256 differs")
    case("unlisted file", "in the directory but not in files", extra_files={"notes.txt": "x\n"})
    case(
        "listed file missing",
        "listed in files but missing",
        lambda m: m["files"].append(
            {"path": "cells/other.txt", "sha256": "0" * 64, "kind": "other"}
        ),
    )
    case(
        "supersedes a missing run",
        "run 2026-10-01-ara-anchor does not exist",
        setp(
            ["supersedes"],
            {
                "run": "2026-10-01-ara-anchor",
                "issue": "https://github.com/swarm-routing/benchmarks/issues/99",
            },
        ),
    )

    def older(root):
        old = fresh()
        old["date"] = "2026-10-01"
        _make_run(root, old, _cell(old, arms, seeds))

    case(
        "supersedes an existing run",
        None,
        setp(
            ["supersedes"],
            {
                "run": "2026-10-01-ara-anchor",
                "issue": "https://github.com/swarm-routing/benchmarks/issues/99",
            },
        ),
        before=older,
    )
    case(
        "supersedes without an issue",
        "missing required 'issue'",
        setp(["supersedes"], {"run": "2026-10-01-ara-anchor"}),
    )
    # the marker contract
    case(
        "unknown marker",
        "##ENERG## is not in marker contract v1",
        cell=lambda m: _cell(m, arms, seeds, extra=["##ENERG## 1 ara 3.2"]),
    )
    case(
        "malformed RUN",
        "at least 7 required",
        cell=lambda m: _cell(m, arms, seeds, extra=["##RUN## 4 ara 95.1"]),
    )
    case(
        "non-numeric RUN",
        "is not <run> <arm> <numbers...>",
        cell=lambda m: _cell(m, arms, seeds, extra=["##RUN## x ara 1 2 3 4 5"]),
    )
    case(
        "HARNESS version differs",
        "##HARNESS## version=2",
        cell=lambda m: _cell(m, arms, seeds, extra=["##HARNESS## version=2"]),
    )
    case(
        "HARNESS version agrees",
        None,
        cell=lambda m: _cell(m, arms, seeds, extra=["##HARNESS## version=1"]),
    )
    case("no PROV line", "no ##PROV## line", cell=lambda m: _cell(m, arms, seeds, prov=""))
    case(
        "PROV with a tag, no digest",
        "does not name the campaign-image digest",
        cell=lambda m: _cell(
            m,
            arms,
            seeds,
            prov=f"##PROV## base={m['base_image']['digest']} image=ghcr.io/x:tag "
            + " ".join(f"{x['name']}={x['commit']}" for x in m["modules"]),
        ),
    )
    case(
        "PROV misses a module",
        "does not name module commit 2222",
        cell=lambda m: _cell(
            m,
            arms,
            seeds,
            prov=f"##PROV## base={m['base_image']['digest']} image={m['campaign_image']['digest']} "
            + " ".join(f"{x['name']}={x['commit']}" for x in m["modules"][:-1]),
        ),
    )
    case(
        "PROV names a foreign commit",
        "names commit " + "9" * 40,
        cell=lambda m: (
            _cell(m, arms, seeds)
            + f"##PROV## base={m['base_image']['digest']} image={m['campaign_image']['digest']} "
            + " ".join(f"{x['name']}={x['commit']}" for x in m["modules"])
            + f" commit={'9' * 40}\n"
        ),
    )
    case(
        "PROV not key=value",
        "must all be key=value",
        cell=lambda m: _cell(m, arms, seeds, extra=["##PROV## loose words"]),
    )
    # `all`: a run without a manifest, a stray file
    with tempfile.TemporaryDirectory() as root:
        os.makedirs(os.path.join(root, "2026-11-20-no-manifest"))
        with open(os.path.join(root, "stray.txt"), "w", encoding="utf-8") as f:
            f.write("x\n")
        out = run_errors(os.path.join(root, "2026-11-20-no-manifest"))
        expect("run without a manifest", out, "no manifest.json")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            ok = check_all(root)
        expect("all fails on both", [] if not ok and "stray.txt" in buf.getvalue() else ["x"], None)
    # rule 10: immutability against a merge base
    if shutil.which("git"):
        with tempfile.TemporaryDirectory() as repo:
            env_git = ["-c", "user.name=t", "-c", "user.email=t@example.invalid"]

            def g(*a):
                r = subprocess.run(["git", "-C", repo, *env_git, *a], capture_output=True)
                if r.returncode:
                    failures.append(f"git {a}: {r.stderr.decode()}")

            g("init", "-q", "-b", "main")
            os.makedirs(os.path.join(repo, "runs", "2026-11-20-a"))
            for rel, text in (
                ("runs/2026-11-20-a/c.txt", "1\n"),
                ("runs/2026-11-20-a/d.txt", "2\n"),
            ):
                with open(os.path.join(repo, rel), "w", encoding="utf-8") as f:
                    f.write(text)
            g("add", "-A")
            g("commit", "-qm", "merged run")
            g("branch", "base")

            def scenario(name, needle, change):
                g("checkout", "-q", "-B", "pr", "base")
                change()
                g("add", "-A")
                g("commit", "-qm", name, "--allow-empty")
                expect(name, immutable_errors(repo, "base"), needle)

            def write(rel, text):
                os.makedirs(os.path.dirname(os.path.join(repo, rel)), exist_ok=True)
                with open(os.path.join(repo, rel), "w", encoding="utf-8") as f:
                    f.write(text)

            scenario("new run", None, lambda: write("runs/2026-11-21-b/c.txt", "x\n"))
            scenario(
                "edit a merged file",
                "edits a merged file",
                lambda: write("runs/2026-11-20-a/c.txt", "changed\n"),
            )
            scenario(
                "delete a merged file",
                "deletes a merged file",
                lambda: os.remove(os.path.join(repo, "runs/2026-11-20-a/d.txt")),
            )
            scenario(
                "add to a merged run",
                "adds a file to merged run",
                lambda: write("runs/2026-11-20-a/e.txt", "x\n"),
            )
            scenario(
                "file directly in runs/",
                "holds only run directories",
                lambda: write("runs/README.md", "x\n"),
            )
    else:
        failures.append("git not found: the immutability cases did not run")

    for f in failures:
        print(f"FAIL {f}")
    print(f"validate_run self-test: {'FAILED' if failures else 'ok'} ({len(failures)} failures)")
    return 1 if failures else 0


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--self-test", action="store_true")
    sub = p.add_subparsers(dest="cmd")
    c = sub.add_parser("check")
    c.add_argument("runs", nargs="+")
    a = sub.add_parser("all")
    a.add_argument("root", nargs="?", default=os.path.join(REPO, "runs"))
    mf = sub.add_parser("manifest")
    mf.add_argument("file")
    im = sub.add_parser("immutable")
    im.add_argument("--base", default="origin/main")
    im.add_argument("--repo", default=REPO)
    args = p.parse_args(argv)
    if args.self_test:
        return self_test()
    if args.cmd == "check":
        ok = True
        for r in args.runs:
            ok = report(r, run_errors(r)) and ok
        return 0 if ok else 1
    if args.cmd == "all":
        return 0 if check_all(args.root) else 1
    if args.cmd == "manifest":
        try:
            m = load_json(args.file)
        except (OSError, ValueError) as e:
            return 0 if report(args.file, [f"not JSON ({e})"]) else 1
        return 0 if report(args.file, manifest_errors(m)) else 1
    if args.cmd == "immutable":
        return (
            0 if report(f"runs/ against {args.base}", immutable_errors(args.repo, args.base)) else 1
        )
    p.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
