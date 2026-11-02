#!/usr/bin/env python3
"""
Backend quality-gate runner: executes every enabled gate of quality/gates.json and
emits ONE normalized evidence bundle per run, quality/evidence/run-<id>.json,
shaped after the frontend contract (quality/evidence-bundle.schema.json).

Stages (all deterministic, no LLM involved):
  1. validate   catalog + gate inventory (attribute ids exist, catalogTests are
                drawn from the attribute's catalog entry, markers registered).
  2. inventory  attribute coverage with the shared 50% rule:
                covered   >= strongCoverageMinRatio of the catalog tests implemented
                weak      >= 1 gate, below the ratio
                uncovered no gate
  3. collect    run each gate (`pytest -m <marker> --junitxml`, or a command),
                reduce it to testsRun / testsPassed / assertions / durationMs /
                logExcerpt; line and branch coverage accumulated across the pytest
                gates (pytest-cov, --cov-append) and read from coverage JSON.
  4. decide     block if any blocking gate failed or produced no evidence
                (skipped); a failed non-blocking gate is a warning; else allow.

The collector never fails because a gate failed: a failed gate is evidence. It
exits non-zero only on an invalid inventory, or with --enforce on a block.

Usage:
  python quality/scripts/collect_evidence.py                      # run id from GITHUB_RUN_ID or local-<utc>
  python quality/scripts/collect_evidence.py --run-id local-001
  python quality/scripts/collect_evidence.py --validate-only
  python quality/scripts/collect_evidence.py --skip ruff-lint --enforce

Metric notes:
  - testsRun / testsPassed count pytest test cases (a parametrized case counts
    once per parameter set), excluding skipped ones.
  - assertions is a STATIC count of `assert` statements and `pytest.raises`
    blocks in the body of each executed test function (pytest reports no
    assertion counts); see `assertionCountNote` in the bundle.
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import platform
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
QUALITY = ROOT / "quality"
LOG_EXCERPT_LIMIT = 4000
SUPPORTED_KINDS = {"pytest", "command"}


# ── model ────────────────────────────────────────────────────────────────────

def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def registered_markers() -> set[str]:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    return set(re.findall(r'^\s*"(qa_[a-z_]+):', text, flags=re.M))


def validate(catalog: dict, gates: dict) -> list[str]:
    errors: list[str] = []
    attrs = {a["id"]: a for a in catalog["attributes"]}
    markers = registered_markers()
    seen: set[str] = set()
    for a in catalog["attributes"]:
        if f"qa_{a['id'].replace('-', '_')}" not in markers:
            errors.append(f"pyproject.toml does not register marker for attribute {a['id']}")
    for g in gates["gates"]:
        gid = g["id"]
        if gid in seen:
            errors.append(f"duplicate gate id {gid}")
        seen.add(gid)
        attr = attrs.get(g["attributeId"])
        if attr is None:
            errors.append(f"{gid}: unknown attributeId {g['attributeId']!r}")
            continue
        for t in g["catalogTests"]:
            if t not in attr["tests"]:
                errors.append(f"{gid}: {t!r} is not a catalog test of {attr['id']}")
        kind = g["source"]["kind"]
        if kind not in SUPPORTED_KINDS:
            errors.append(f"{gid}: source kind {kind!r} is not supported by the backend runner")
        if kind == "pytest":
            expr = g["source"].get("markers", "")
            used = set(re.findall(r"\bqa_[a-z_]+\b", expr))
            if not used:
                errors.append(f"{gid}: pytest gate without a qa_* marker")
            for m in used - markers:
                errors.append(f"{gid}: marker {m} is not registered in pyproject.toml")
            expected = f"qa_{g['attributeId'].replace('-', '_')}"
            if used and used != {expected}:
                errors.append(f"{gid}: markers {sorted(used)} do not match attribute marker {expected}")
    errors += _schema_errors(gates, QUALITY / "gates.schema.json")
    return errors


def _schema_errors(doc: dict, schema_path: Path) -> list[str]:
    try:
        import jsonschema  # noqa: PLC0415
    except ImportError:
        return []  # optional dependency; CI installs it (requirements-dev.txt)
    validator = jsonschema.Draft7Validator(load_json(schema_path))
    return [f"{schema_path.name}: {'/'.join(map(str, e.path)) or '<root>'}: {e.message}"
            for e in validator.iter_errors(doc)]


def attribute_coverage(catalog: dict, gates: dict) -> dict:
    threshold = gates["coveragePolicy"]["strongCoverageMinRatio"]
    active = [g for g in gates["gates"] if g["enabled"]]
    per = []
    for a in catalog["attributes"]:
        covering = [g for g in active if g["attributeId"] == a["id"]]
        implemented = sorted({t for g in covering for t in g["catalogTests"]})
        missing = [t for t in a["tests"] if t not in implemented]
        ratio = len(implemented) / len(a["tests"]) if a["tests"] else 0.0
        status = "uncovered" if not covering else ("covered" if ratio >= threshold else "weak")
        per.append({
            "attributeId": a["id"], "name": a["name"], "status": status,
            "gateIds": [g["id"] for g in covering], "implementedTests": implemented,
            "missingTests": missing, "catalogTestCount": len(a["tests"]),
            "implementedTestRatio": round(ratio, 4),
        })
    by = lambda s: [p["attributeId"] for p in per if p["status"] == s]  # noqa: E731
    return {
        "totalAttributes": len(per),
        "strongCoverageMinRatio": threshold,
        # Same semantics as the frontend bundle: `covered` = at least one enabled gate.
        "covered": [p["attributeId"] for p in per if p["status"] != "uncovered"],
        "uncovered": by("uncovered"),
        "stronglyCovered": by("covered"),
        "weaklyCovered": by("weak"),
        "perAttribute": per,
    }


# ── gate runners ─────────────────────────────────────────────────────────────

def _excerpt(text: str) -> str:
    text = text.strip()
    return text if len(text) <= LOG_EXCERPT_LIMIT else "…" + text[-LOG_EXCERPT_LIMIT:]


def _postgres_available() -> bool:
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        return False
    try:
        from sqlalchemy import create_engine, text  # noqa: PLC0415
        from sqlalchemy.pool import NullPool  # noqa: PLC0415
        engine = create_engine(url, poolclass=NullPool, connect_args={"connect_timeout": 5})
        with engine.connect() as c:
            c.execute(text("SELECT 1"))
        engine.dispose()
        return True
    except Exception:  # noqa: BLE001
        return False


_ASSERT_CACHE: dict[Path, dict[str, int]] = {}


def _assertions_in(file: Path) -> dict[str, int]:
    if file not in _ASSERT_CACHE:
        counts: dict[str, int] = {}
        try:
            tree = ast.parse(file.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            tree = ast.Module(body=[], type_ignores=[])
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test"):
                n = 0
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Assert):
                        n += 1
                    elif (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute)
                          and sub.func.attr == "raises"):
                        n += 1
                counts[node.name] = n
        _ASSERT_CACHE[file] = counts
    return _ASSERT_CACHE[file]


def _parse_junit(path: Path) -> dict:
    root = ET.parse(path).getroot()
    cases = []
    for tc in root.iter("testcase"):
        outcome, message = "passed", ""
        for tag in ("failure", "error", "skipped"):
            el = tc.find(tag)
            if el is not None:
                outcome = {"failure": "failed", "error": "error", "skipped": "skipped"}[tag]
                message = (el.get("message") or el.text or "").strip()
                break
        cases.append({"classname": tc.get("classname", ""), "name": tc.get("name", ""),
                      "time": float(tc.get("time") or 0), "outcome": outcome, "message": message})
    return {"cases": cases}


def _case_assertions(case: dict) -> int:
    file = ROOT / (case["classname"].replace(".", "/") + ".py")
    func = case["name"].split("[", 1)[0]
    return _assertions_in(file).get(func, 0)


def run_pytest_gate(gate: dict, raw: Path, cov_env: dict) -> dict:
    junit = raw / f"{gate['id']}.xml"
    log = raw / f"{gate['id']}.log"
    cmd = [sys.executable, "-m", "pytest", "-m", gate["source"]["markers"], "-q",
           "-p", "no:cacheprovider", f"--junitxml={junit}",
           "--cov", "--cov-append", "--cov-report=", "--cov-fail-under=0",
           *gate["source"].get("include", [])]
    t0 = time.monotonic()
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, env=cov_env)
    duration = int((time.monotonic() - t0) * 1000)
    log.write_text(proc.stdout + proc.stderr, encoding="utf-8")

    if not junit.exists():
        return {"status": "failed", "testsRun": 0, "testsPassed": 0, "assertions": 0,
                "durationMs": duration, "artifacts": [_rel(log)],
                "logExcerpt": _excerpt(f"pytest exited {proc.returncode} without a report.\n"
                                       + proc.stdout + proc.stderr)}
    cases = _parse_junit(junit)["cases"]
    executed = [c for c in cases if c["outcome"] != "skipped"]
    passed = [c for c in executed if c["outcome"] == "passed"]
    bad = [c for c in executed if c["outcome"] in ("failed", "error")]
    skipped = len(cases) - len(executed)
    if not executed:
        status = "skipped"
    elif bad or proc.returncode not in (0,):
        status = "failed"
    else:
        status = "passed"

    if status == "failed" and bad:
        lines = [f"{len(passed)}/{len(executed)} test cases passed; {len(bad)} failed:"]
        for c in bad:
            first = c["message"].splitlines()[0] if c["message"] else ""
            lines.append(f"- {c['classname']}::{c['name']} [{c['outcome']}] {first[:300]}")
        excerpt = "\n".join(lines)
    elif status == "failed":
        excerpt = f"pytest exited {proc.returncode}.\n" + (proc.stdout + proc.stderr)[-2000:]
    elif status == "skipped":
        excerpt = (f"No test case executed ({skipped} skipped). "
                   + ((proc.stdout.strip().splitlines() or [""])[-1]))
    else:
        files = {c["classname"] for c in executed}
        excerpt = (f"{len(passed)}/{len(executed)} test cases passed across {len(files)} file(s)"
                   + (f"; {skipped} skipped." if skipped else "."))
    return {"status": status, "testsRun": len(executed), "testsPassed": len(passed),
            "assertions": sum(_case_assertions(c) for c in executed),
            "durationMs": duration, "logExcerpt": _excerpt(excerpt),
            "artifacts": [_rel(junit), _rel(log)]}


def run_command_gate(gate: dict, raw: Path) -> dict:
    command = gate["source"]["command"]
    argv = command.split()
    if argv[0] == "python":
        argv[0] = sys.executable
    log = raw / f"{gate['id']}.log"
    t0 = time.monotonic()
    proc = subprocess.run(argv, cwd=ROOT / gate["source"].get("workingDirectory", "."),
                          capture_output=True, text=True)
    duration = int((time.monotonic() - t0) * 1000)
    log.write_text(proc.stdout + proc.stderr, encoding="utf-8")
    ok = proc.returncode == 0
    excerpt = f"Command '{command}' exited {proc.returncode}."
    if not ok:
        excerpt += "\n" + (proc.stdout + proc.stderr)
    return {"status": "passed" if ok else "failed", "testsRun": 1, "testsPassed": int(ok),
            "assertions": 0, "durationMs": duration, "logExcerpt": _excerpt(excerpt),
            "artifacts": [_rel(log)]}


def _rel(p: Path) -> str:
    return str(p.relative_to(ROOT))


def collected_test_count() -> int | None:
    proc = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q",
                           "-p", "no:cacheprovider"], cwd=ROOT, capture_output=True, text=True)
    m = re.search(r"(\d+) tests? collected", proc.stdout)
    return int(m.group(1)) if m else None


def read_coverage(raw: Path, cov_env: dict) -> dict:
    out = raw / "coverage.json"
    subprocess.run([sys.executable, "-m", "coverage", "json", "-q", "-o", str(out)],
                   cwd=ROOT, capture_output=True, text=True, env=cov_env)
    if not out.exists():
        return {"lines": None, "branches": None}
    totals = load_json(out)["totals"]
    pct = lambda a, b: round(100.0 * a / b, 2) if b else None  # noqa: E731
    return {"lines": pct(totals["covered_lines"], totals["num_statements"]),
            "branches": pct(totals.get("covered_branches", 0), totals.get("num_branches", 0)),
            "source": "pytest-cov (coverage.py), union of all pytest gates",
            "artifact": _rel(out)}


# ── decision ─────────────────────────────────────────────────────────────────

def decide(bundle: dict) -> dict:
    blocking, warnings = [], []
    for g in bundle["gates"]:
        if g["blocking"] and g["status"] == "failed":
            blocking.append({"gateId": g["gateId"], "attributeId": g["attributeId"],
                             "reason": f"blocking gate failed ({g['testsPassed']}/{g['testsRun']} "
                                       "test cases passed)"})
        elif g["blocking"] and g["status"] == "skipped":
            blocking.append({"gateId": g["gateId"], "attributeId": g["attributeId"],
                             "reason": "blocking gate produced no evidence (skipped)"})
        elif not g["blocking"] and g["status"] == "failed":
            warnings.append({"gateId": g["gateId"], "attributeId": g["attributeId"],
                             "reason": "non-blocking gate failed"})
    part = bundle.get("partition") or {}
    if part.get("unassigned"):
        warnings.append({"gateId": None, "attributeId": None,
                         "reason": f"{part['unassigned']} collected test(s) belong to no enabled gate"})
    allowed = not blocking
    return {
        "decision": "allow" if allowed else "block",
        "reason": ("Every blocking quality gate produced evidence and passed." if allowed
                   else f"{len(blocking)} blocking quality gate(s) did not pass."),
        "blockingFailures": blocking,
        "warnings": warnings,
        "evaluatedAt": _utc(),
        "rule": "deterministic; the generative analysis explains a block but never causes or lifts one",
    }


# ── main ─────────────────────────────────────────────────────────────────────

def _utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _git_commit() -> str:
    if os.getenv("GITHUB_SHA"):
        return os.environ["GITHUB_SHA"]
    proc = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True)
    return proc.stdout.strip() or "unknown"


def _render(bundle: dict) -> str:
    lines = ["## Backend quality evidence", "",
             f"Run `{bundle['runId']}` · commit `{bundle['commit'][:12]}` · "
             f"coverage {bundle['coverage']['lines']}% lines / {bundle['coverage']['branches']}% branches", "",
             "| Gate | Attribute | Status | Passed / run | Assertions | ms |",
             "| --- | --- | --- | --- | --- | --- |"]
    for g in bundle["gates"]:
        lines.append(f"| `{g['gateId']}` | {g['attributeId']} | {g['status']} | "
                     f"{g['testsPassed']}/{g['testsRun']} | {g['assertions']} | {g['durationMs']} |")
    ac = bundle["attributeCoverage"]
    lines += ["", f"**Attributes**: {len(ac['stronglyCovered'])} covered, {len(ac['weaklyCovered'])} weak, "
              f"{len(ac['uncovered'])} uncovered of {ac['totalAttributes']}.", "",
              "| Attribute | Status | Implemented catalog tests |", "| --- | --- | --- |"]
    for p in ac["perAttribute"]:
        lines.append(f"| {p['attributeId']} | {p['status']} | "
                     f"{len(p['implementedTests'])}/{p['catalogTestCount']} |")
    d = bundle["deployment"]
    lines += ["", f"### Deployment decision: {d['decision'].upper()}", "", d["reason"]]
    for f in d["blockingFailures"]:
        lines.append(f"- `{f['gateId']}` ({f['attributeId']}): {f['reason']}")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--run-id")
    ap.add_argument("--out-dir", default=str(QUALITY / "evidence"))
    ap.add_argument("--skip", default="", help="comma-separated gate ids to skip")
    ap.add_argument("--validate-only", action="store_true")
    ap.add_argument("--enforce", action="store_true", help="exit 1 when the decision is block")
    args = ap.parse_args(argv)

    started = time.monotonic()
    catalog = load_json(QUALITY / "quality-attributes.json")
    gates = load_json(QUALITY / "gates.json")
    errors = validate(catalog, gates)
    if errors:
        print("Invalid quality gate inventory:\n  " + "\n  ".join(errors), file=sys.stderr)
        return 2
    coverage_inventory = attribute_coverage(catalog, gates)
    print(f"Inventory valid: {len(gates['gates'])} gates, catalog {catalog['catalogVersion']}.")
    for p in coverage_inventory["perAttribute"]:
        print(f"  {p['status'].upper():<10} {p['attributeId']:<28} "
              f"{len(p['implementedTests'])}/{p['catalogTestCount']}  {', '.join(p['gateIds']) or '-'}")
    if args.validate_only:
        return 0

    run_id = args.run_id or os.getenv("GITHUB_RUN_ID") or \
        "local-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = Path(args.out_dir)
    raw = out_dir / "raw" / run_id
    raw.mkdir(parents=True, exist_ok=True)
    cov_env = {**os.environ, "COVERAGE_FILE": str(raw / ".coverage")}
    (raw / ".coverage").unlink(missing_ok=True)

    skip = {s.strip() for s in args.skip.split(",") if s.strip()}
    pg = _postgres_available()
    print(f"PostgreSQL ({os.getenv('TEST_DATABASE_URL', 'TEST_DATABASE_URL unset')}): "
          f"{'available' if pg else 'NOT available'}")

    results = []
    for gate in gates["gates"]:
        if not gate["enabled"]:
            continue
        base = {"gateId": gate["id"], "attributeId": gate["attributeId"],
                "blocking": gate["blocking"]}
        missing = [r for r in gate["source"].get("requires", []) if r == "postgres" and not pg]
        if gate["id"] in skip or missing:
            why = "skipped by --skip" if gate["id"] in skip else f"prerequisite unavailable: {missing}"
            results.append({**base, "status": "skipped", "testsRun": 0, "testsPassed": 0,
                            "assertions": 0, "durationMs": 0, "logExcerpt": why, "artifacts": []})
        elif gate["source"]["kind"] == "pytest":
            results.append({**base, **run_pytest_gate(gate, raw, cov_env)})
        else:
            results.append({**base, **run_command_gate(gate, raw)})
        r = results[-1]
        print(f"[{r['status']:>7}] {r['gateId']:<32} {r['testsPassed']}/{r['testsRun']} "
              f"({r['durationMs']} ms)")

    total = collected_test_count()
    in_gates = sum(r["testsRun"] for r in results
                   if next(g for g in gates["gates"] if g["id"] == r["gateId"])["source"]["kind"] == "pytest")
    bundle = {
        "runId": str(run_id),
        "commit": _git_commit(),
        "timestamp": _utc(),
        "workflowDurationMs": 0,
        "catalogVersion": catalog["catalogVersion"],
        "gatesVersion": gates["gatesVersion"],
        "repository": "AppVento---Backend",
        "assertionCountNote": ("static count of `assert` statements and `pytest.raises` blocks in "
                               "the body of each executed test function (pytest reports no "
                               "assertion counts)"),
        "environment": {
            "ci": os.getenv("CI") == "true",
            "workflow": os.getenv("GITHUB_WORKFLOW"),
            "ref": os.getenv("GITHUB_REF"),
            "python": platform.python_version(),
        },
        "gates": results,
        "partition": {"collectedTests": total, "executedInGates": in_gates,
                      "unassigned": (total - in_gates) if total is not None and
                      all(r["status"] != "skipped" for r in results) else None},
        "coverage": read_coverage(raw, cov_env),
        "audit": {"critical": 0, "high": 0, "moderate": 0, "low": 0},
        "auditNote": "the backend inventory declares no dependency-audit gate; these counts are not measured",
        "attributeCoverage": coverage_inventory,
        "analysis": None,
    }
    bundle["deployment"] = decide(bundle)
    bundle["workflowDurationMs"] = int((time.monotonic() - started) * 1000)

    bundle_errors = _schema_errors(bundle, QUALITY / "evidence-bundle.schema.json")
    if bundle_errors:
        print("Evidence bundle does not match the schema:\n  " + "\n  ".join(bundle_errors),
              file=sys.stderr)
        return 2

    out = out_dir / f"run-{run_id}.json"
    out.write_text(json.dumps(bundle, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    summary = _render(bundle)
    print("\n" + summary)
    print(f"Evidence bundle written to {out.relative_to(ROOT) if out.is_relative_to(ROOT) else out}")
    if os.getenv("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write(summary)
    if os.getenv("GITHUB_OUTPUT"):
        d = bundle["deployment"]
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as f:
            f.write(f"decision={d['decision']}\n")
            f.write(f"deploy_allowed={'true' if d['decision'] == 'allow' else 'false'}\n")
            f.write(f"reason={d['reason']}\n")
            f.write(f"blocking_gates={','.join(x['gateId'] for x in d['blockingFailures'])}\n")
            f.write(f"bundle={out.relative_to(ROOT)}\n")
    return 1 if args.enforce and bundle["deployment"]["decision"] == "block" else 0


if __name__ == "__main__":
    sys.exit(main())
