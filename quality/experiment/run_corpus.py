#!/usr/bin/env python3
"""
RQ2 defect-detection harness for the backend (see the paper workspace,
evidence/corpus/DESIGN.md). Runs every defect of the corpus under two verification
layers over the SAME product code (this branch's HEAD + the defect):

  B (baseline)  the checks of .github/workflows/backend-ci.yml on origin/develop,
                with the tests as they are on origin/develop: product code from HEAD,
                tests/ and pyproject.toml restored from origin/develop, test files
                absent in develop removed. Jobs: lint (ruff), unit (pytest --cov,
                fail_under from develop's pyproject), build (compileall + import
                smoke). gitleaks is omitted (not applicable to functional defects).
  P (protocol)  the gates of quality/gates.json, run by quality/scripts/collect_evidence.py
                (per-gate status, per-test JUnit, deterministic deployment decision).

Detection rule: a defect is DETECTED under a condition when at least one check
(job or gate) or test case that passed in EVERY clean run fails with the defect.
B never blocks the deploy (the deploy workflow does not depend on CI); P blocks when
a blocking gate fails.

Each condition runs in its own disposable detached worktree
(<worktrees>/tmp-back-B, tmp-back-P), reset hard between defects; the test DB
schema is dropped and recreated before every run. Defects are applied with
`git apply` (seeded patches and hand-crafted reverse patches of real fixes alike).

Usage:
  python quality/experiment/run_corpus.py --corpus <dir with manifest.json> \
      --results <dir> [--ids D-B03,R-D1] [--clean-runs 2] [--skip-clean]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PY = str(REPO / ".venv" / "bin" / "python")
WT_ROOT = REPO.parent
WT_B = WT_ROOT / "tmp-back-B"
WT_P = WT_ROOT / "tmp-back-P"
DEVELOP = "origin/develop"
DB_URL = os.getenv("TEST_DATABASE_URL", "postgresql+psycopg://vento:vento@localhost:55432/vento")
TIMEOUT_S = int(os.getenv("CORPUS_TIMEOUT_S", "1500"))
IMPORT_SMOKE = ("import main; import api.routers.tiles; import api.routers.historial; "
                "import application.services.tile_service; print('imports ok')")


def utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def git(cwd: Path, *args: str, check: bool = True) -> str:
    p = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if check and p.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed in {cwd}:\n{p.stderr}")
    return p.stdout.strip()


def env() -> dict:
    e = {k: v for k, v in os.environ.items() if k not in ("INTERNAL_API_SECRET", "COVERAGE_FILE")}
    e.update(DATABASE_URL=DB_URL, TEST_DATABASE_URL=DB_URL, PYTHONDONTWRITEBYTECODE="1")
    return e


def run(cmd: list[str], cwd: Path, timeout: int = TIMEOUT_S) -> dict:
    t0 = time.monotonic()
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, env=env(), timeout=timeout)
        rc, out = p.returncode, p.stdout + p.stderr
    except subprocess.TimeoutExpired as exc:
        rc = "timeout"
        out = ((exc.stdout or b"").decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")) + \
              f"\n[harness] timeout after {timeout}s"
    return {"rc": rc, "seconds": round(time.monotonic() - t0, 2), "output": out}


def reset_db() -> None:
    code = (
        "from sqlalchemy import create_engine, text\n"
        f"e = create_engine({DB_URL!r}, isolation_level='AUTOCOMMIT')\n"
        "with e.connect() as c:\n"
        "    c.execute(text(\"SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
        "WHERE datname = current_database() AND pid <> pg_backend_pid()\"))\n"
        "    c.execute(text('DROP SCHEMA public CASCADE'))\n"
        "    c.execute(text('CREATE SCHEMA public'))\n"
    )
    subprocess.run([PY, "-c", code], check=True, capture_output=True, text=True, env=env())


def parse_junit(path: Path, gate: str | None = None) -> dict[str, dict]:
    cases: dict[str, dict] = {}
    if not path.exists():
        return cases
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError:
        return cases
    for tc in root.iter("testcase"):
        outcome, msg = "passed", ""
        for tag in ("failure", "error", "skipped"):
            el = tc.find(tag)
            if el is not None:
                outcome = {"failure": "failed", "error": "error", "skipped": "skipped"}[tag]
                msg = (el.get("message") or el.text or "").strip().splitlines()[:1]
                msg = msg[0][:300] if msg else ""
                break
        tid = f"{tc.get('classname', '')}::{tc.get('name', '')}"
        cases[tid] = {"outcome": outcome, "message": msg, **({"gate": gate} if gate else {})}
    return cases


# ── worktrees ────────────────────────────────────────────────────────────────

def make_worktree(path: Path) -> None:
    if path.exists():
        git(REPO, "worktree", "remove", "--force", str(path), check=False)
        shutil.rmtree(path, ignore_errors=True)
    git(REPO, "worktree", "prune")
    git(REPO, "worktree", "add", "--detach", str(path), "HEAD")


def prepare_b() -> str:
    """Worktree at HEAD with develop's tests; commits the result on the detached HEAD
    (no branch) so `reset --hard` restores the baseline between defects."""
    make_worktree(WT_B)
    head_tests = set(git(WT_B, "ls-tree", "-r", "--name-only", "HEAD", "tests").splitlines())
    dev_tests = set(git(WT_B, "ls-tree", "-r", "--name-only", DEVELOP, "tests").splitlines())
    git(WT_B, "checkout", DEVELOP, "--", "tests", "pyproject.toml")
    removed = sorted(head_tests - dev_tests)
    if removed:
        git(WT_B, "rm", "-q", "--", *removed)
    git(WT_B, "-c", "user.name=rq2-harness", "-c", "user.email=rq2-harness@localhost",
        "commit", "-q", "-m", f"rq2 baseline: tests and pyproject from {DEVELOP}")
    return git(WT_B, "rev-parse", "HEAD")


def prepare_p() -> str:
    make_worktree(WT_P)
    return git(WT_P, "rev-parse", "HEAD")


def reset_wt(path: Path, sha: str) -> None:
    git(path, "reset", "-q", "--hard", sha)
    git(path, "clean", "-q", "-fd")
    shutil.rmtree(path / "quality" / "evidence" / "raw", ignore_errors=True)


def fresh(path: Path, sha: str) -> None:
    reset_wt(path, sha)
    reset_db()


def apply_patch(path: Path, patch: Path) -> None:
    git(path, "apply", "--whitespace=nowarn", str(patch))


# ── conditions ───────────────────────────────────────────────────────────────

def run_b(raw_dir: Path) -> dict:
    raw_dir.mkdir(parents=True, exist_ok=True)
    junit = raw_dir / "unit.xml"
    t0 = time.monotonic()
    steps = {
        "ruff": run([PY, "-m", "ruff", "check"], WT_B),
        "pytest": run([PY, "-m", "pytest", "--cov", "-p", "no:cacheprovider",
                       f"--junitxml={junit}"], WT_B),
        "compileall": run([PY, "-m", "compileall", "-q", "domain", "application", "infrastructure",
                           "api", "shared", "main.py", "tools"], WT_B),
        "import-smoke": run([PY, "-c", IMPORT_SMOKE], WT_B),
    }
    total = round(time.monotonic() - t0, 2)
    for name, s in steps.items():
        (raw_dir / f"{name}.log").write_text(s["output"], encoding="utf-8")
    out = steps["pytest"]["output"]
    cov = re.search(r"^TOTAL\s+.*?(\d+(?:\.\d+)?)%\s*$", out, flags=re.M)
    summary = re.findall(r"=+ (.*(?:passed|failed|error).*) in [\d.]+s", out)
    ok = lambda s: s["rc"] == 0  # noqa: E731
    jobs = {
        "lint": {"status": "passed" if ok(steps["ruff"]) else "failed",
                 "seconds": steps["ruff"]["seconds"]},
        "unit": {"status": "passed" if ok(steps["pytest"]) else "failed",
                 "seconds": steps["pytest"]["seconds"],
                 "coverageTotal": float(cov.group(1)) if cov else None,
                 "coverageFailUnder": "Required test coverage" in out and "not reached" in out,
                 "pytestSummary": summary[-1] if summary else None},
        "build": {"status": "passed" if ok(steps["compileall"]) and ok(steps["import-smoke"]) else "failed",
                  "seconds": round(steps["compileall"]["seconds"] + steps["import-smoke"]["seconds"], 2)},
    }
    return {
        "condition": "B",
        "steps": {k: {"rc": v["rc"], "seconds": v["seconds"],
                      "tail": v["output"][-1500:] if v["rc"] != 0 else ""} for k, v in steps.items()},
        "checks": jobs,
        "tests": parse_junit(junit),
        "durationSeconds": total,
        "criticalPathSeconds": max(j["seconds"] for j in jobs.values()),
        "blocksDeploy": False,
        "ciRed": any(j["status"] != "passed" for j in jobs.values()),
    }


def run_p(raw_dir: Path, run_id: str) -> dict:
    raw_dir.mkdir(parents=True, exist_ok=True)
    r = run([PY, "quality/scripts/collect_evidence.py", "--run-id", run_id], WT_P)
    (raw_dir / "collector.log").write_text(r["output"], encoding="utf-8")
    bundle_path = WT_P / "quality" / "evidence" / f"run-{run_id}.json"
    gate_raw = WT_P / "quality" / "evidence" / "raw" / run_id
    if not bundle_path.exists():
        return {"condition": "P", "error": f"collector rc={r['rc']}, no bundle",
                "checks": {}, "tests": {}, "durationSeconds": r["seconds"],
                "blocksDeploy": None, "tail": r["output"][-3000:]}
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    shutil.copy(bundle_path, raw_dir / "bundle.json")
    tests: dict[str, dict] = {}
    checks = {}
    for g in bundle["gates"]:
        checks[g["gateId"]] = {"status": g["status"], "attributeId": g["attributeId"],
                               "blocking": g["blocking"], "testsRun": g["testsRun"],
                               "testsPassed": g["testsPassed"], "seconds": g["durationMs"] / 1000}
        x = gate_raw / f"{g['gateId']}.xml"
        if x.exists():
            shutil.copy(x, raw_dir / x.name)
            tests.update(parse_junit(x, g["gateId"]))
    d = bundle["deployment"]
    return {
        "condition": "P",
        "checks": checks,
        "tests": tests,
        "decision": d["decision"],
        "blockingFailures": [f["gateId"] for f in d["blockingFailures"]],
        "coverage": bundle.get("coverage", {}),
        "durationSeconds": r["seconds"],
        "blocksDeploy": d["decision"] == "block",
    }


# ── detection ────────────────────────────────────────────────────────────────

def stable_passing(cleans: list[dict]) -> tuple[set[str], set[str]]:
    checks = set.intersection(*[{k for k, v in c["checks"].items() if v["status"] == "passed"}
                                for c in cleans])
    tests = set.intersection(*[{k for k, v in c["tests"].items() if v["outcome"] == "passed"}
                               for c in cleans])
    return checks, tests


def detect(res: dict, cleans: list[dict], gate_attr: dict[str, str] | None = None) -> dict:
    ok_checks, ok_tests = stable_passing(cleans)
    bad = ("failed", "error", "skipped")  # a gate that produced no evidence also counts for P
    new_checks = sorted(k for k in ok_checks
                        if res["checks"].get(k, {}).get("status", "failed") in bad)
    new_tests = sorted(k for k in ok_tests
                       if res["tests"].get(k, {}).get("outcome", "missing") in ("failed", "error", "missing"))
    out = {"detected": bool(new_checks or new_tests), "newlyFailingChecks": new_checks,
           "newlyFailingTests": [{"id": t, **{k: v for k, v in res["tests"].get(t, {"outcome": "missing"}).items()}}
                                 for t in new_tests]}
    if gate_attr is not None:
        gates = set(new_checks) | {res["tests"][t]["gate"] for t in new_tests
                                   if t in res["tests"] and "gate" in res["tests"][t]}
        out["failingGates"] = sorted(gates)
        out["failingAttributes"] = sorted({gate_attr[g] for g in gates if g in gate_attr})
    return out


# ── main ─────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="RQ2 backend defect-detection harness")
    ap.add_argument("--corpus", required=True, type=Path)
    ap.add_argument("--results", required=True, type=Path)
    ap.add_argument("--ids", default="")
    ap.add_argument("--clean-runs", type=int, default=2)
    ap.add_argument("--skip-clean", action="store_true", help="reuse clean-B.json / clean-P.json")
    args = ap.parse_args(argv)

    manifest = json.loads((args.corpus / "manifest.json").read_text(encoding="utf-8"))
    defects = [d for d in (manifest if isinstance(manifest, list) else manifest["defects"])
               if d.get("unit") == "backend"]
    if args.ids:
        want = {i.strip() for i in args.ids.split(",") if i.strip()}
        defects = [d for d in defects if d["id"] in want]
    args.results.mkdir(parents=True, exist_ok=True)
    raw = args.results / "raw"
    research_commit = git(REPO, "rev-parse", "HEAD")
    develop_commit = git(REPO, "rev-parse", DEVELOP)
    gate_attr = {g["id"]: g["attributeId"]
                 for g in json.loads((REPO / "quality" / "gates.json").read_text())["gates"]}
    meta = {"researchCommit": research_commit, "developCommit": develop_commit, "testDatabase": DB_URL}

    base_b = prepare_b()
    base_p = prepare_p()
    print(f"research {research_commit[:8]} · develop {develop_commit[:8]} · B base {base_b[:8]}", flush=True)

    clean_b_path, clean_p_path = args.results / "clean-B.json", args.results / "clean-P.json"
    if args.skip_clean:
        clean_b = json.loads(clean_b_path.read_text())["runs"]
        clean_p = json.loads(clean_p_path.read_text())["runs"]
    else:
        clean_b, clean_p = [], []
        for i in range(args.clean_runs):
            fresh(WT_B, base_b)
            clean_b.append(run_b(raw / f"clean-B-{i + 1}"))
            fresh(WT_P, base_p)
            clean_p.append(run_p(raw / f"clean-P-{i + 1}", f"rq2-clean-{i + 1}"))
            print(f"clean run {i + 1}: B {clean_b[-1]['durationSeconds']}s "
                  f"({clean_b[-1]['checks']['unit'].get('pytestSummary')}) · "
                  f"P {clean_p[-1]['durationSeconds']}s ({clean_p[-1].get('decision')})", flush=True)
        for path, cond, runs in ((clean_b_path, "B", clean_b), (clean_p_path, "P", clean_p)):
            ok_c, ok_t = stable_passing(runs)
            path.write_text(json.dumps({**meta, "date": utc(), "condition": cond, "runs": runs,
                                        "stablePassingChecks": sorted(ok_c),
                                        "stablePassingTestCount": len(ok_t)},
                                       indent=1, ensure_ascii=False) + "\n", encoding="utf-8")

    for d in defects:
        patch = args.corpus / d["patch"]
        row = {**meta, "date": utc(), "defect": d}
        for cond, wt, base in (("B", WT_B, base_b), ("P", WT_P, base_p)):
            fresh(wt, base)
            try:
                apply_patch(wt, patch)
            except RuntimeError as exc:
                row[cond] = {"error": f"patch does not apply: {exc}"}
                continue
            res = run_b(raw / f"{d['id']}-B") if cond == "B" else run_p(raw / f"{d['id']}-P", f"rq2-{d['id']}")
            cleans = clean_b if cond == "B" else clean_p
            res["detection"] = detect(res, cleans, gate_attr if cond == "P" else None)
            if cond == "P":
                res["detection"]["attributeMatch"] = d["targetAttribute"] in res["detection"]["failingAttributes"]
            row[cond] = res
        reset_wt(WT_B, base_b)
        reset_wt(WT_P, base_p)
        (args.results / f"{d['id']}.json").write_text(json.dumps(row, indent=1, ensure_ascii=False) + "\n",
                                                      encoding="utf-8")
        b, p = row.get("B", {}), row.get("P", {})
        print(f"{d['id']:<7} B det={b.get('detection', {}).get('detected')} ({b.get('durationSeconds')}s) · "
              f"P det={p.get('detection', {}).get('detected')} block={p.get('blocksDeploy')} "
              f"gates={p.get('detection', {}).get('failingGates')} ({p.get('durationSeconds')}s)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
