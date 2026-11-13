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
  validate_run.py legacy all [ROOT]       every legacy cell under legacy/ (default:
                                          this repo's) and each source's generated
                                          INDEX.md (ADR-0003)
  validate_run.py legacy check CELL [...] one or more legacy cell directories
  validate_run.py immutable [--base REF]  refuse a change to a merged run or legacy
                                          cell: any edit, deletion or addition under
                                          a run or cell directory that exists at the
                                          merge base with REF (default origin/main;
                                          rule 10); legacy/<source>/INDEX.md is
                                          generated and may change
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
  - per cell, and per seed slice of one (a block opens at each ##CONFIG##
    scenario line): every key of the scenario's parameters that the ##CONFIG##
    line carries has the manifest's value there, and its protocols= are the
    scenario's arms; each ##ARGS## line starts with harness.program, is one of
    the manifest's commands, carries every parameter ##CONFIG## does not
    (--key=value, or --key for a true flag such as diag), has the manifest's
    value for each parameter it carries, and its --protocols, when it has one,
    are the arms. ##ARGS## stays
    optional in marker contract v1: a slice without one has its commands and
    those parameters unchecked;
  - every ##PROV## line names the base digest, the campaign-image digest and
    every module commit, and no other digest or commit;
  - a supersedes entry names an existing run.

What a legacy check enforces (legacy/<source>/<cell>/, ADR-0003): the manifest
against schemas/legacy-manifest.schema.json; the directory holds exactly the
manifest and the cell, byte-identical to source.sha256 (the file the release
tag published); measured, images and arms are exactly what the cell's ##PROV##
and ##RUN## lines say; an unverified (rolling-tag) digest names where it was
read; the declared defects equal the ones found (garbled markers, missing
##PROV##, several commits, duplicate seeds, fewer than 20 seeds); the invalid
entry is exactly what the recorded #459 ancestry gives (swarm-routing/AntHocNet
#460: ##COMMON##/##MATCH## of a cell not known to contain the fix); a
superseded legacy cell exists; INDEX.md is the generated one.

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
# A campaign image's package: <repo>-dev, or <repo>-ns3-campaign before
# swarm-routing/.github#370. Any other package is a release image (<repo>, or
# <repo>-ns3 before #370); the base packages are neither.
CAMPAIGN_REPO = re.compile(r"-(ns3-campaign|dev)$")
BASE_REPOS = {"ghcr.io/swarm-routing/ns3", "ghcr.io/swarm-routing/ns3-dev"}

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
    elif repo in BASE_REPOS:
        errs.append(f"campaign_image: {repo} is the base image, not a campaign or release image")
    elif CAMPAIGN_REPO.search(repo):
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


def _agrees(want, got):
    """Whether the text `got` of a marker line is the manifest's value `want`."""
    if isinstance(want, bool):
        return got.lower() in (("true", "1") if want else ("false", "0"))
    if TYPES["number"](want):
        return _number(got) and float(got) == want
    return got == str(want)


def _shown(want):
    return json.dumps(want) if isinstance(want, bool) else str(want)


def config_errors(where, config, scen):
    """One ##CONFIG## scenario line (its key=value fields) against the manifest's scenario."""
    errs = []
    for key, want in scen["parameters"].items():
        if key in config and not _agrees(want, config[key]):
            errs.append(
                f"{where}: ##CONFIG## {key}={config[key]}, the manifest's scenario "
                f"{scen['name']} says {key}={_shown(want)}"
            )
    if "protocols" in config and sorted(config["protocols"].split(",")) != sorted(scen["arms"]):
        errs.append(
            f"{where}: ##CONFIG## protocols={config['protocols']}, the manifest's scenario "
            f"{scen['name']} has arms {','.join(scen['arms'])}"
        )
    return errs


def args_errors(where, body, config, scen, m):
    """One ##ARGS## line against the manifest's harness, commands and scenario: every
    parameter it carries, each one its block's ##CONFIG## line (`config`) does not
    carry, and its --protocols, when it has one, against the arms."""
    errs = []
    toks = body.split()
    program = toks[0] if toks else ""
    if program != m["harness"]["program"]:
        errs.append(
            f"{where}: ##ARGS## runs {program}, the manifest's harness.program is "
            f"{m['harness']['program']}"
        )
    if body not in m["commands"]:
        errs.append(
            f"{where}: ##ARGS## {body!r} is none of the manifest's {len(m['commands'])} commands"
        )
    opts = {}
    for t in toks[1:]:
        if t.startswith("--"):
            key, eq, value = t[2:].partition("=")
            opts[key] = value if eq else "true"
    for key, want in scen["parameters"].items():
        says = f"the manifest's scenario {scen['name']} says {key}={_shown(want)}"
        if key not in opts:
            if key not in config and want is not False:  # ##CONFIG## may carry it instead
                errs.append(f"{where}: ##ARGS## has no --{key}, {says}")
        elif not _agrees(want, opts[key]):
            errs.append(f"{where}: ##ARGS## --{key}={opts[key]}, {says}")
    if "protocols" in opts and sorted(opts["protocols"].split(",")) != sorted(scen["arms"]):
        errs.append(
            f"{where}: ##ARGS## --protocols={opts['protocols']}, the manifest's scenario "
            f"{scen['name']} has arms {','.join(scen['arms'])}"
        )
    return errs


def cell_errors(path, rel, contract, m, rows, scen):
    """Marker-line errors of one cell of scenario `scen`; adds its ##RUN## rows to `rows`."""
    errs = []
    config = {}  # the ##CONFIG## scenario line of the block (seed slice) being read
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
        if name == "CONFIG" and toks and toks[0] != "attr":
            config = _kv(body)
            errs += config_errors(where, config, scen)
        if name == "ARGS":
            errs += args_errors(where, body, config, scen, m)
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
                path = os.path.join(run_dir, f["path"])
                errs += cell_errors(path, f["path"], contract, m, rows, scen)
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


# --- the legacy (pre-org) archive (ADR-0003) ----------------------------------

# A legacy cell is not a run: legacy/<source>/<cell>/ holds manifest.json and
# the cell file, byte-identical to the file a protocol repository's release tag
# published. verdicts/ and the overview read runs/ only, never legacy/.
LEGACY_SCHEMA_PATH = os.path.join(REPO, "schemas", "legacy-manifest.schema.json")
LEGACY_DIR = os.path.join(REPO, "legacy")
LEGACY_INDEX = "INDEX.md"
# swarm-routing/AntHocNet#459: before it, ##COMMON##/##MATCH## keys named a
# different transmission in each arm (swarm-routing/AntHocNet#460).
FIX_459 = "1a7c32b9538d9a1d9449a00c114286ad49f64c93"
ISSUE_460 = "https://github.com/swarm-routing/AntHocNet/issues/460"
MATCHED_MARKERS = ("COMMON", "MATCH")
REASON_460 = (
    "measured before swarm-routing/AntHocNet#459 (or at a commit whose ancestry is unresolved): "
    "the cross-arm (flow, seq) keys named a different transmission in each arm; "
    "per-arm markers stand"
)


def _kv(body):
    return dict(t.split("=", 1) for t in body.split() if "=" in t[1:])


def legacy_scan(text, contract):
    """What a legacy cell holds, read under `contract` (a marker version)."""
    garbled, present, prov, rows = [], set(), [], {}
    for n, line in enumerate(text.splitlines(), 1):
        if not line.startswith("##"):
            continue
        mk = MARKER.match(line)
        name = mk.group(1) if mk else None
        spec = contract["markers"].get(name) if name else None
        if spec is None:
            garbled.append(n)
            continue
        body = (mk.group(2) or "").strip()
        toks = body.split()
        if name == "RUN":
            if len(toks) < spec["min_fields"] or not toks[0].isdigit():
                garbled.append(n)
                continue
            if not all(_number(t) for t in toks[2:]):
                garbled.append(n)
                continue
            rows.setdefault(toks[1], []).append(int(toks[0]))
        if name == "PROV":
            if not all("=" in t[1:] for t in toks):
                garbled.append(n)  # contract v1: every ##PROV## field is key=value
                continue
            prov.append(_kv(body))
        present.add(name)
    return {"garbled": garbled, "present": present, "prov": prov, "rows": rows}


def _split_image(value):
    ref, _, digest = value.partition("@")
    return ref, (digest if DIGEST.fullmatch(digest or "") else None)


def legacy_derived(scan):
    """The manifest fields a cell's content fixes: measured (without ancestry),
    images (role, ref, PROV digest or None), arms and defects."""
    measured, by_commit = [], {}
    for p in scan["prov"]:
        c = p.get("commit", "")
        if c not in by_commit:
            by_commit[c] = {"commit": c, "ref": p.get("ref", ""), "run_ids": []}
            measured.append(by_commit[c])
        rid = p.get("run_id", "")
        if rid.isdigit() and int(rid) not in by_commit[c]["run_ids"]:
            by_commit[c]["run_ids"].append(int(rid))
    for e in measured:
        e["run_ids"].sort()
    images = []
    for p in scan["prov"]:
        for role, key in (("run", "image"), ("base", "base")):
            if key in p:
                img = (role, *_split_image(p[key]))
                if img not in images:
                    images.append(img)
    arms = [{"arm": a, "seeds": sorted(set(s))} for a, s in sorted(scan["rows"].items())]
    defects = []
    if scan["garbled"]:
        defects.append({"kind": "garbled-marker", "detail": "lines " + _ranges(scan["garbled"])})
    if not scan["prov"]:
        defects.append({"kind": "no-prov", "detail": "no ##PROV## line"})
    if len(measured) > 1:
        defects.append({"kind": "multi-commit", "detail": f"{len(measured)} commits"})
    for a, s in sorted(scan["rows"].items()):
        dup = sorted({x for x in s if s.count(x) > 1})
        if dup:
            defects.append({"kind": "duplicate-seed", "detail": f"{a} seeds {_ranges(dup)}"})
    short = [f"{x['arm']} {len(x['seeds'])}" for x in arms if len(x["seeds"]) < 20]
    if short:
        defects.append({"kind": "seeds-below-20", "detail": ", ".join(short)})
    return measured, images, arms, defects


def legacy_missing(scan, contract):
    """Required markers a cell lacks, other than ##PROV## (a declared no-prov defect)."""
    return [x for x in contract["required"] if x != "PROV" and x not in scan["present"]]


def legacy_invalid(scan, measured):
    """The #460 entry a cell needs: its matched-set markers, when any commit is not
    known to contain the fix (or there is no ##PROV## to tell)."""
    matched = sorted(m for m in MATCHED_MARKERS if m in scan["present"])
    unsure = not measured or any(e.get("contains_fix") is not True for e in measured)
    if matched and unsure:
        return [{"markers": matched, "issue": ISSUE_460, "reason": REASON_460}]
    return []


def _norm(items):
    return sorted(json.dumps(i, sort_keys=True) for i in items)


def legacy_cell_errors(cell_dir):
    """Every problem of one legacy cell directory."""
    cell_dir = os.path.normpath(cell_dir)
    name = os.path.basename(cell_dir)
    mpath = os.path.join(cell_dir, MANIFEST)
    if not os.path.isfile(mpath):
        return [f"no {MANIFEST} (golden rule 11: no manifest, no merge)"]
    try:
        m = load_json(mpath)
    except (OSError, ValueError) as e:
        return [f"{MANIFEST}: not JSON ({e})"]
    schema = load_json(LEGACY_SCHEMA_PATH)
    errs = schema_errors(m, schema, schema)
    if errs:
        return errs
    if m["cell"] != name:
        errs.append(f"cell: {m['cell']} is not the directory's name {name}")
    if not (m["cell"] == m["family"] or m["cell"].startswith(m["family"] + "-")):
        errs.append(f"family: {m['family']} is not the cell's name prefix")
    fname = os.path.basename(m["source"]["path"])
    if fname != f"{m['cell']}.txt":
        errs.append(f"source.path: {fname} is not {m['cell']}.txt")
    on_disk = sorted(os.listdir(cell_dir))
    if on_disk != sorted([MANIFEST, fname]):
        errs.append(f"directory holds {on_disk}, expected exactly {MANIFEST} and {fname}")
    path = os.path.join(cell_dir, fname)
    if not os.path.isfile(path):
        return errs + [f"{fname}: missing"]
    if sha256(path) != m["source"]["sha256"]:
        errs.append(f"{fname}: sha256 differs from source.sha256 (not the released file)")
    contract = known_marker_versions().get(m["markers"]["schema_version"])
    if contract is None:
        return errs + [f"markers: schema_version {m['markers']['schema_version']} unknown"]
    with open(path, encoding="utf-8", errors="replace") as f:
        scan = legacy_scan(f.read(), contract)
    for x in legacy_missing(scan, contract):
        errs.append(f"{fname}: no ##{x}## line (marker contract v{m['markers']['schema_version']})")
    measured, images, arms, defects = legacy_derived(scan)
    got = [{k: e[k] for k in ("commit", "ref", "run_ids")} for e in m["measured"]]
    if got != measured:
        errs.append(f"measured: {got} is not what ##PROV## names: {measured}")
    for e in m["measured"]:
        if (e["contains_fix"] == "unresolved") != (e["ancestry"] == "none"):
            errs.append(f"measured {e['commit'][:7]}: contains_fix and ancestry disagree")
    want, have = {}, {}
    for r, ref, d in images:
        if (r, ref) in want:
            errs.append(f"images: ##PROV## names {r} image {ref} with conflicting digests")
        want[(r, ref)] = d
    for i in m["images"]:
        if (i["role"], i["ref"]) in have:
            errs.append(f"images: {i['role']} image {i['ref']} is listed twice")
        have[(i["role"], i["ref"])] = i
    for key in sorted(set(want) - set(have)):
        errs.append(f"images: ##PROV## names {key[0]} image {key[1]}, not in the manifest")
    for key in sorted(set(have) - set(want)):
        errs.append(f"images: {key[0]} image {key[1]} is in no ##PROV## line")
    for key in sorted(set(want) & set(have)):
        i, d = have[key], want[key]
        if d is not None and (i["digest"] != d or not i["verified"]):
            errs.append(f"images: {key[1]}: ##PROV## names {d}, verified")
        if d is None and i["verified"]:
            errs.append(f"images: {key[1]}: a rolling tag's digest cannot be verified")
        if not i["verified"] and not ("digest_source" in i and "digest_read" in i):
            errs.append(
                f"images: {key[1]}: an unverified digest needs digest_source and digest_read"
            )
    if m["arms"] != arms:
        errs.append("arms: not the cell's ##RUN## arms and seeds")
    if _norm(m["defects"]) != _norm(defects):
        errs.append(f"defects: declared {_norm(m['defects'])}, found {_norm(defects)}")
    inv, want_inv = m["invalid"], legacy_invalid(scan, m["measured"])
    if _norm(inv) != _norm(want_inv):
        errs.append(f"invalid: declared {_norm(inv)}, the #459 ancestry gives {_norm(want_inv)}")
    source_dir = os.path.dirname(cell_dir)
    root = os.path.dirname(source_dir)
    for s in m.get("supersedes", []):
        t = s["target"]
        if t.startswith("legacy/") and not os.path.isfile(
            os.path.join(root, *t.split("/")[1:], MANIFEST)
        ):
            errs.append(f"supersedes: {t} does not exist")
    return errs


def _cell_manifests(source_dir):
    out = []
    for entry in sorted(os.listdir(source_dir)):
        p = os.path.join(source_dir, entry, MANIFEST)
        if os.path.isfile(p):
            try:
                out.append(load_json(p))
            except ValueError:
                continue
    return out


def legacy_index(source_dir):
    """legacy/<source>/INDEX.md, generated from the manifests (never hand-edited)."""
    src = os.path.basename(os.path.normpath(source_dir))
    ms = _cell_manifests(source_dir)
    by = {}
    for m in ms:
        for s in m.get("supersedes", []):
            by.setdefault(s["target"], []).append(f"legacy/{src}/{m['cell']}")
    lines = [
        f"# Legacy (pre-org) cells: {src}",
        "",
        "Generated by `tools/import_anthocnet.py` from the manifests; checked by",
        "`tools/validate_run.py legacy`. Never edit by hand. Pre-org numbers are not",
        "current results: verdicts and the overview never read them (ADR-0003).",
        "",
        f"{len(ms)} cell(s).",
        "",
        "| cell | family | tag | measured | image | defects | invalid | superseded by |",
        "|---|---|---|---|---|---|---|---|",
    ]

    def cellref(m):
        return f"[`{m['cell']}`]({m['cell']}/manifest.json)"

    for m in sorted(ms, key=lambda m: (m["family"], m["cell"])):
        meas = (
            ", ".join(
                f"`{e['commit'][:7]}`"
                + (
                    ""
                    if e["contains_fix"] is True
                    else " (pre-#459)"
                    if e["contains_fix"] is False
                    else " (ancestry unresolved)"
                )
                for e in m["measured"]
            )
            or "-"
        )
        imgs = (
            ", ".join(
                f"`{i['ref']}`" + ("" if i["verified"] else " (unverified)")
                for i in m["images"]
                if i["role"] == "run"
            )
            or "-"
        )
        defs = ", ".join(f"{d['kind']}: {d['detail']}" for d in m["defects"]) or "-"
        inv = (
            ", ".join(
                f"{'/'.join(i['markers'])} ({i['issue'].split('/')[-3]}#{i['issue'].split('/')[-1]})"
                for i in m["invalid"]
            )
            or "-"
        )
        sup = ", ".join(f"`{t}`" for t in sorted(by.get(f"legacy/{src}/{m['cell']}", []))) or "-"
        lines.append(
            f"| {cellref(m)} | {m['family']} | {m['source']['tag']} | {meas} | {imgs} "
            f"| {defs} | {inv} | {sup} |"
        )
    return "\n".join(lines) + "\n"


def legacy_build(cell, family, source, text, ancestry, records):
    """A legacy manifest for one cell. `source`: repo, tag, commit, path, sha256;
    `ancestry(commit)` -> (contains_fix, how); `records`: rolling tag -> its
    digest of record ({digest, source, read}). Raises ValueError when a rolling
    tag has none (an image is never recorded without a digest)."""
    contract = known_marker_versions()[1]
    scan = legacy_scan(text, contract)
    missing = legacy_missing(scan, contract)
    if missing:
        raise ValueError(f"{cell}: no " + ", ".join(f"##{x}##" for x in missing) + " line")
    measured, images, arms, defects = legacy_derived(scan)
    for e in measured:
        e["contains_fix"], e["ancestry"] = ancestry(e["commit"])
    imgs = []
    for role, ref, digest in images:
        if digest:
            imgs.append({"role": role, "ref": ref, "digest": digest, "verified": True})
            continue
        rec = records.get(ref)
        if rec is None:
            raise ValueError(f"{cell}: no digest of record for {ref}")
        imgs.append(
            {
                "role": role,
                "ref": ref,
                "digest": rec["digest"],
                "verified": False,
                "digest_source": rec["source"],
                "digest_read": rec["read"],
            }
        )
    return {
        "schema_version": 1,
        "provenance": "pre-org",
        "cell": cell,
        "family": family,
        "source": source,
        "measured": measured,
        "images": imgs,
        "arms": arms,
        "markers": {"schema_version": 1},
        "defects": defects,
        "invalid": legacy_invalid(scan, measured),
    }


def legacy_write(source_dir, m, data):
    """Write one cell directory: the file's bytes and its manifest."""
    d = os.path.join(source_dir, m["cell"])
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, os.path.basename(m["source"]["path"])), "wb") as f:
        f.write(data)
    with open(os.path.join(d, MANIFEST), "w", encoding="utf-8") as f:
        json.dump(m, f, indent=2)
        f.write("\n")


def legacy_write_index(source_dir):
    with open(os.path.join(source_dir, LEGACY_INDEX), "w", encoding="utf-8") as f:
        f.write(legacy_index(source_dir))


def legacy_check_all(root):
    """Every source directory of legacy/: each cell, and its generated INDEX.md."""
    if not os.path.isdir(root):
        print(f"OK   {root}: no legacy data")
        return True
    ok = True
    for src in sorted(os.listdir(root)):
        sdir = os.path.join(root, src)
        if not os.path.isdir(sdir):
            ok = report(sdir, [f"{src}: legacy/ holds only source directories"]) and ok
            continue
        for entry in sorted(os.listdir(sdir)):
            p = os.path.join(sdir, entry)
            if entry == LEGACY_INDEX:
                continue
            if not os.path.isdir(p):
                ok = (
                    report(p, [f"{entry}: a source directory holds cells and {LEGACY_INDEX}"])
                    and ok
                )
            else:
                ok = report(p, legacy_cell_errors(p)) and ok
        ipath = os.path.join(sdir, LEGACY_INDEX)
        errs = []
        if not os.path.isfile(ipath):
            errs.append(f"no {LEGACY_INDEX}")
        else:
            with open(ipath, encoding="utf-8") as f:
                if f.read() != legacy_index(sdir):
                    errs.append(
                        f"{LEGACY_INDEX} is not the generated one (rerun the importer's index)"
                    )
        ok = report(ipath, errs) and ok
    return ok


# --- immutability (golden rule 10) -------------------------------------------


def _git(repo, *args):
    return subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, check=False)


def immutable_errors(repo, base):
    """Changes under runs/ and legacy/ since the merge base with `base` that touch
    a merged run or a merged legacy cell. legacy/<source>/INDEX.md is generated
    (validate_run.py legacy checks it), so it may change."""
    mb = _git(repo, "merge-base", base, "HEAD")
    if mb.returncode:
        return [f"git merge-base {base} HEAD failed: {mb.stderr.strip()}"]
    mb = mb.stdout.strip()
    diff = _git(repo, "diff", "--name-status", "--no-renames", mb, "HEAD", "--", "runs/", "legacy/")
    if diff.returncode:
        return [f"git diff failed: {diff.stderr.strip()}"]
    errs = []
    verbs = {"M": "edits", "D": "deletes", "T": "changes the type of"}
    for line in diff.stdout.splitlines():
        status, path = line.split("\t", 1)
        parts = path.split("/")
        if parts[0] == "legacy":
            if len(parts) == 3 and parts[2] == LEGACY_INDEX:
                continue
            if len(parts) < 4:
                errs.append(
                    f"{path}: legacy/<source>/ holds only cell directories and {LEGACY_INDEX}"
                )
                continue
            unit, what = "/".join(parts[:3]), "legacy cell"
        else:
            if len(parts) < 3:
                errs.append(f"{path}: runs/ holds only run directories")
                continue
            unit, what = "/".join(parts[:2]), "run"
        merged = _git(repo, "cat-file", "-e", f"{mb}:{unit}").returncode == 0
        if status != "A":
            errs.append(f"{path}: {verbs.get(status[0], 'changes')} a merged file (golden rule 10)")
        elif merged:
            errs.append(
                f"{path}: adds a file to merged {what} {unit.rsplit('/', 1)[-1]} (golden rule 10)"
            )
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


def _legacy_cell_text(commit, seeds=range(1, 21), arms=("anthocnet", "aodv"), extra=(), prov=True):
    out = ["# legacy cell", "##CONFIG## scenario=paper nNodes=50 time=900"]
    for a in arms:
        for s in seeds:
            out.append(f"##RUN## {s} {a} 95.1 18.4 258.5 12.51 6.06")
            out.append(f"##COMMON## {s} {a} nCommon=10 hopsC=1.5")
    out += list(extra)
    if prov:
        out.append(
            f"##PROV## commit={commit} ref=main run_id=7 attempt=1 "
            "image=ghcr.io/example/ns3:3.42-opt profile=release harness=compare"
        )
    return "\n".join(out) + "\n"


def _legacy_self_test(expect):
    """The legacy checks on cells built in a temporary directory."""
    schema = load_json(LEGACY_SCHEMA_PATH)
    expect("legacy schema uses only enforced keywords", unsupported_keywords(schema), None)
    pre, post = "a" * 40, "b" * 40
    records = {
        "ghcr.io/example/ns3:3.42-opt": {
            "digest": "sha256:" + "c" * 64,
            "source": "https://github.com/example/p/blob/" + "d" * 40 + "/docker/README.md",
            "read": "2026-11-11",
        }
    }

    def ancestry(c):
        return {pre: (False, "git"), post: (True, "git")}.get(c, ("unresolved", "none"))

    def build(root, name, text, mutate=None):
        data = text.encode()
        src = {
            "repo": "example/p",
            "tag": "v1.0.0",
            "commit": "e" * 40,
            "path": f"results/cells/{name}.txt",
            "sha256": hashlib.sha256(data).hexdigest(),
        }
        m = legacy_build(name, "fam", src, text, ancestry, records)
        if mutate:
            mutate(m)
        sdir = os.path.join(root, "legacy", "src")
        legacy_write(sdir, m, data)
        return os.path.join(sdir, name), m

    def case(name, needle, text, mutate=None, after=None):
        with tempfile.TemporaryDirectory() as root:
            d, _ = build(root, "fam-x", text, mutate)
            if after:
                after(d)
            expect(f"legacy: {name}", legacy_cell_errors(d), needle)

    case("post-fix cell", None, _legacy_cell_text(post))
    with tempfile.TemporaryDirectory() as root:
        _, m = build(root, "fam-x", _legacy_cell_text(pre))
        expect("legacy: pre-fix cell is flagged", [] if m["invalid"] else ["no invalid"], None)
        _, m = build(root, "fam-y", _legacy_cell_text("f" * 40))
        expect(
            "legacy: unresolved ancestry is flagged",
            [] if m["invalid"] and m["measured"][0]["ancestry"] == "none" else ["x"],
            None,
        )
        _, m = build(
            root, "fam-z", _legacy_cell_text(pre, seeds=(1, 2), extra=("##AI##RSS## t=1",))
        )
        kinds = sorted(d["kind"] for d in m["defects"])
        expect(
            "legacy: defects found",
            [] if kinds == ["garbled-marker", "seeds-below-20"] else [str(kinds)],
            None,
        )
        legacy_write_index(os.path.join(root, "legacy", "src"))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            ok = legacy_check_all(os.path.join(root, "legacy"))
        expect("legacy: all passes", [] if ok else [buf.getvalue()], None)
        with open(os.path.join(root, "legacy", "src", LEGACY_INDEX), "a", encoding="utf-8") as f:
            f.write("hand edit\n")
        with contextlib.redirect_stdout(buf):
            ok = legacy_check_all(os.path.join(root, "legacy"))
        expect("legacy: a hand-edited index fails", [] if not ok else ["passed"], None)
        _, m = build(root, "fam-w", _legacy_cell_text(post, prov=False))
        expect(
            "legacy: no PROV is a defect and flags COMMON",
            [] if m["invalid"] and m["defects"][0]["kind"] == "no-prov" else ["x"],
            None,
        )
        try:
            build(root, "fam-v", _legacy_cell_text(post).replace("example/ns3", "example/other"))
            expect("legacy: a tag without a digest of record is refused", ["built"], None)
        except ValueError:
            pass
    case(
        "undeclared garbled line",
        "defects: declared",
        _legacy_cell_text(post, extra=("##AI##RSS## t=1",)),
        lambda m: m.update(defects=[]),
    )
    case(
        "stale declared defect",
        "defects: declared",
        _legacy_cell_text(post),
        lambda m: m["defects"].append({"kind": "multi-commit", "detail": "2 commits"}),
    )
    case(
        "pre-fix without its invalid entry",
        "invalid: declared",
        _legacy_cell_text(pre),
        lambda m: m.update(invalid=[]),
    )
    case(
        "invalid entry on a post-fix cell",
        "invalid: declared",
        _legacy_cell_text(post),
        lambda m: m.update(invalid=[{"markers": ["COMMON"], "issue": ISSUE_460, "reason": "x"}]),
    )
    case(
        "ancestry claimed without an answer",
        "disagree",
        _legacy_cell_text(post),
        lambda m: m["measured"][0].update(ancestry="none"),
    )
    case(
        "wrong commit",
        "measured:",
        _legacy_cell_text(post),
        lambda m: m["measured"][0].update(commit="1" * 40),
    )
    case(
        "unverified digest without its source",
        "needs digest_source",
        _legacy_cell_text(post),
        lambda m: m["images"][0].pop("digest_source"),
    )
    case(
        "rolling tag claimed verified",
        "cannot be verified",
        _legacy_cell_text(post),
        lambda m: m["images"][0].update(verified=True),
    )
    case(
        "invalid reason replaced",
        "invalid: declared",
        _legacy_cell_text(pre),
        lambda m: m["invalid"][0].update(reason="something else"),
    )
    case(
        "image listed twice",
        "listed twice",
        _legacy_cell_text(post),
        lambda m: m["images"].append(dict(m["images"][0], digest="sha256:" + "0" * 64)),
    )
    case(
        "stray PROV token undeclared",
        "defects: declared",
        _legacy_cell_text(post).replace("harness=compare", "harness=compare stray"),
        lambda m: m.update(defects=[]),
    )

    def strip_runs(d):
        p = os.path.join(d, "fam-x.txt")
        with open(p, encoding="utf-8") as f:
            text = "".join(x for x in f.read().splitlines(True) if not x.startswith("##RUN##"))
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)
        mp = os.path.join(d, MANIFEST)
        m = load_json(mp)
        m["source"]["sha256"] = sha256(p)
        m["arms"] = []
        with open(mp, "w", encoding="utf-8") as f:
            json.dump(m, f)

    case("no ##RUN## rows", "no ##RUN## line", _legacy_cell_text(post), after=strip_runs)
    case(
        "conflicting PROV digests",
        "conflicting digests",
        _legacy_cell_text(post, prov=False)
        + f"##PROV## commit={post} ref=main run_id=7 image=ghcr.io/x/y@sha256:{'1' * 64}\n"
        + f"##PROV## commit={post} ref=main run_id=7 image=ghcr.io/x/y@sha256:{'2' * 64}\n",
    )
    case("arms differ", "arms:", _legacy_cell_text(post), lambda m: m["arms"].pop())
    case("not pre-org", "must be", _legacy_cell_text(post), lambda m: m.update(provenance="org"))
    case(
        "branch as tag",
        "does not match",
        _legacy_cell_text(post),
        lambda m: m["source"].update(tag="main"),
    )
    case(
        "superseded cell missing",
        "does not exist",
        _legacy_cell_text(post),
        lambda m: m.update(
            supersedes=[{"target": "legacy/src/gone", "scope": "COMMON tables", "issue": ISSUE_460}]
        ),
    )

    def tamper(d):
        with open(os.path.join(d, "fam-x.txt"), "a", encoding="utf-8") as f:
            f.write("# edited\n")

    case("edited cell", "sha256 differs", _legacy_cell_text(post), after=tamper)

    def stray(d):
        with open(os.path.join(d, "extra.txt"), "w", encoding="utf-8") as f:
            f.write("x\n")

    case("stray file", "expected exactly", _legacy_cell_text(post), after=stray)


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
    case(
        "base named ns3 (swarm-routing/.github#370)",
        None,
        setp(["base_image", "ref"], "ghcr.io/swarm-routing/ns3:3.48-optimized-1a2b3c4"),
    )
    case(
        "rolling base tag on ns3",
        "does not match",
        setp(["base_image", "ref"], "ghcr.io/swarm-routing/ns3:latest-optimized"),
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
        "campaign image named <repo>-dev (swarm-routing/.github#370)",
        None,
        setp(["campaign_image", "ref"], "ghcr.io/swarm-routing/ara-dev:0123456-bbbbbbb-optimized"),
    )
    case(
        "<repo>-dev with another profile",
        "is not <commit7>-<basesha7>[-optimized]",
        setp(["campaign_image", "ref"], "ghcr.io/swarm-routing/ara-dev:0123456-bbbbbbb-debug"),
    )
    case(
        "<repo>-dev rolling tag",
        "rolling tag",
        setp(["campaign_image", "ref"], "ghcr.io/swarm-routing/ara-dev:latest"),
    )
    case(
        "release image named <repo> (swarm-routing/.github#370)",
        None,
        setp(["campaign_image", "ref"], "ghcr.io/swarm-routing/ara:v1.0.0-optimized"),
    )
    case(
        "release image named <repo>, rolling",
        "rolling tag",
        setp(["campaign_image", "ref"], "ghcr.io/swarm-routing/ara:latest-optimized"),
    )
    case(
        "the base as the campaign image",
        "is the base image",
        setp(["campaign_image", "ref"], "ghcr.io/swarm-routing/ns3:3.48-optimized-1a2b3c4"),
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
    case(
        "release image named <repo>, not optimized",
        "is not <version>-optimized",
        setp(["campaign_image", "ref"], "ghcr.io/swarm-routing/ara:v1.0.0-debug"),
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
    # rule 11: the scenario, the harness and the commands are the data's (#87)
    command = example["commands"][0]
    args = f"##ARGS## {command}"
    case("cell with its ##ARGS## line", None, cell=lambda m: _cell(m, arms, seeds, extra=[args]))
    case(
        "parameter differs from ##CONFIG##",
        "cells/paper-50n.txt:2: ##CONFIG## nNodes=50, the manifest's scenario paper-50n says "
        "nNodes=100",
        setp(["scenarios", 0, "parameters", "nNodes"], 100),
    )
    case(
        "parameter differs in the second seed slice only",
        "cells/paper-50n.txt:26: ##CONFIG## time=600, the manifest's scenario paper-50n says "
        "time=900",
        cell=lambda m: (
            _cell(m, ["ara"], seeds) + _cell(m, ["aodv"], seeds).replace("time=900", "time=600")
        ),
    )
    case(
        "##CONFIG## protocols are not the arms",
        "##CONFIG## protocols=ara,olsr, the manifest's scenario paper-50n has arms ara,aodv",
        cell=lambda m: _cell(
            m, arms, seeds, extra=["##CONFIG## scenario=paper protocols=ara,olsr"]
        ),
    )
    case(
        "##CONFIG## protocols in another order",
        None,
        cell=lambda m: _cell(
            m, arms, seeds, extra=["##CONFIG## scenario=paper protocols=aodv,ara"]
        ),
    )
    case(
        "command not in commands",
        "##ARGS## 'routing-compare --scenario=paper --nNodes=7' is none of the manifest's "
        "1 commands",
        cell=lambda m: _cell(
            m, arms, seeds, extra=["##ARGS## routing-compare --scenario=paper --nNodes=7"]
        ),
    )
    case(
        "harness program differs",
        "##ARGS## runs routing-compare, the manifest's harness.program is something-else",
        setp(["harness", "program"], "something-else"),
        cell=lambda m: _cell(m, arms, seeds, extra=[args]),
    )
    case(
        "parameter ##CONFIG## lacks differs from ##ARGS##",
        "##ARGS## --speed=10, the manifest's scenario paper-50n says speed=1",
        setp(["scenarios", 0, "parameters", "speed"], 1),
        cell=lambda m: _cell(m, arms, seeds, extra=[args]),
    )
    case(
        "flag parameter absent from ##ARGS##",
        "##ARGS## has no --diag, the manifest's scenario paper-50n says diag=true",
        setp(["scenarios", 0, "parameters", "diag"], True),
        cell=lambda m: _cell(m, arms, seeds, extra=[args]),
    )

    def with_diag(m):
        m["scenarios"][0]["parameters"]["diag"] = True
        m["commands"] = [command + " --diag"]

    case(
        "flag parameter on ##ARGS##",
        None,
        with_diag,
        cell=lambda m: _cell(m, arms, seeds, extra=[f"{args} --diag"]),
    )

    def with_command(old, new):
        """The manifest's command and the cell's ##ARGS## line, both with `old` -> `new`."""
        changed = command.replace(old, new)
        assert changed != command, old
        return (
            lambda m: m.__setitem__("commands", [changed]),
            lambda m: _cell(m, arms, seeds, extra=[f"##ARGS## {changed}"]),
        )

    # a listed command is still compared with the scenario: what ##CONFIG## carries too
    edit, cell = with_command("--time=900", "--time=600")
    case(
        "##ARGS## contradicts a parameter ##CONFIG## carries",
        "##ARGS## --time=600, the manifest's scenario paper-50n says time=900",
        edit,
        cell=cell,
    )
    edit, cell = with_command("--protocols=ara,aodv", "--protocols=ara,olsr")
    case(
        "##ARGS## protocols are not the arms",
        "##ARGS## --protocols=ara,olsr, the manifest's scenario paper-50n has arms ara,aodv",
        edit,
        cell=cell,
    )
    edit, cell = with_command("--protocols=ara,aodv", "--protocols=aodv,ara")
    case("##ARGS## protocols in another order", None, edit, cell=cell)
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
    _legacy_self_test(expect)
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
            os.makedirs(os.path.join(repo, "legacy", "src", "c1"))
            for rel, text in (
                ("runs/2026-11-20-a/c.txt", "1\n"),
                ("runs/2026-11-20-a/d.txt", "2\n"),
                ("legacy/src/c1/c1.txt", "1\n"),
                ("legacy/src/INDEX.md", "i\n"),
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
            scenario("new legacy cell", None, lambda: write("legacy/src/c2/c2.txt", "x\n"))
            scenario(
                "regenerated legacy index",
                None,
                lambda: write("legacy/src/INDEX.md", "j\n"),
            )
            scenario(
                "edit a merged legacy cell",
                "edits a merged file",
                lambda: write("legacy/src/c1/c1.txt", "changed\n"),
            )
            scenario(
                "add to a merged legacy cell",
                "adds a file to merged legacy cell c1",
                lambda: write("legacy/src/c1/more.txt", "x\n"),
            )
            scenario(
                "file directly in a legacy source",
                "holds only cell directories",
                lambda: write("legacy/src/notes.md", "x\n"),
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
    lg = sub.add_parser("legacy", help="the pre-org archive under legacy/ (ADR-0003)")
    lg.add_argument("action", choices=["all", "check"])
    lg.add_argument("paths", nargs="*", help="all: the legacy root; check: cell directories")
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
    if args.cmd == "legacy":
        if args.action == "all":
            return 0 if legacy_check_all(args.paths[0] if args.paths else LEGACY_DIR) else 1
        if not args.paths:
            p.error("legacy check needs at least one cell directory")
        ok = True
        for d in args.paths:
            ok = report(d, legacy_cell_errors(d)) and ok
        return 0 if ok else 1
    if args.cmd == "immutable":
        label = f"runs/ and legacy/ against {args.base}"
        return 0 if report(label, immutable_errors(args.repo, args.base)) else 1
    p.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
