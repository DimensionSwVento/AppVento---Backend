#!/usr/bin/env python3
"""RQ2 timing in GitHub Actions.

The RQ2 protocol is compared against the project's CI by wall-clock duration and
compute minutes on the same runner image and the same product commit.  Each
iteration is one push to a trigger path that fires two workflows on that commit:

  B  the baseline workflow (a faithful copy of develop's CI, see
     .github/workflows/rq2-timing-baseline.yml for the backend)
  P  the protocol workflow already present on the research branch
     (backend-quality-pipeline.yml / quality-pipeline.yml)

Subcommands
-----------
  run      drive N iterations: commit + push the trigger file, wait for the two
           runs, record their ids in a JSON file (resumable: iterations with both
           runs already recorded are skipped).
  collect  read the recorded run ids from the GitHub API, compute queue /
           execution / wall seconds and compute minutes per run, aggregate by
           condition (median, min, max) and ratio P/B, and write JSON+Markdown.

Only the research branch is used; no branch is created and develop/main are
never pushed to.  Requires an authenticated `gh` CLI.
"""
from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_REPO = "DimensionSwVento/AppVento---Backend"
DEFAULT_BRANCH = "research/quality-pipeline-v2"
TRIGGER_PATH = ".rq2-timing/trigger.txt"
BASELINE_WORKFLOW = "RQ2 Timing Baseline (develop CI)"
PROTOCOL_WORKFLOW = "Backend Quality Attributes Pipeline"
POLL_SECONDS = 10


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def gh(*args: str) -> str:
    p = subprocess.run(["gh", *args], capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args)} failed:\n{p.stderr.strip()}")
    return p.stdout


def git(cwd: Path, *args: str) -> str:
    p = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed in {cwd}:\n{p.stderr.strip()}")
    return p.stdout.strip()


# ── run: one trigger pair per iteration ──────────────────────────────────────

def cmd_run(args: argparse.Namespace) -> int:
    repo_dir = args.repo_dir.resolve()
    branch = git(repo_dir, "branch", "--show-current")
    if branch != args.branch:
        raise SystemExit(f"refusing to push: worktree is on {branch!r}, expected {args.branch!r}")
    if git(repo_dir, "status", "--porcelain", "--untracked-files=no"):
        raise SystemExit("refusing to push: worktree has uncommitted changes")

    records: list[dict] = []
    if args.runs.exists():
        records = json.loads(args.runs.read_text(encoding="utf-8"))
    done = {r["iteration"] for r in records
            if r.get("runs", {}).get("B") and r.get("runs", {}).get("P")}
    start = args.start or (max(done) + 1 if done else 1)

    trigger = repo_dir / TRIGGER_PATH
    trigger.parent.mkdir(exist_ok=True)
    for i in range(start, start + args.iterations):
        if i in done:
            print(f"[rq2-timing] iteration {i}: already recorded, skip", flush=True)
            continue
        stamp = now()
        trigger.write_text(
            f"iteration={i}\ntimestamp={stamp}\n"
            "purpose=trigger the baseline (B) and protocol (P) workflows on the same commit\n",
            encoding="utf-8")
        git(repo_dir, "add", TRIGGER_PATH)
        git(repo_dir, "-c", "user.name=rq2-timing",
            "-c", "user.email=rq2-timing@localhost",
            "commit", "-q", "-m", f"chore(rq2-timing): iteration {i}")
        sha = git(repo_dir, "rev-parse", "HEAD")
        git(repo_dir, "push", "-q", "origin", f"HEAD:{args.branch}")
        print(f"[rq2-timing] iteration {i}: pushed {sha[:10]} at {stamp}", flush=True)

        deadline = time.monotonic() + args.timeout
        while True:
            runs = json.loads(gh("run", "list", "--repo", args.repo, "--commit", sha,
                                 "--limit", "20", "--json",
                                 "databaseId,name,status,conclusion,createdAt,url"))
            ours = {r["name"]: r for r in runs if r["name"] in (BASELINE_WORKFLOW, PROTOCOL_WORKFLOW)}
            if len(ours) == 2 and all(r["status"] == "completed" for r in ours.values()):
                break
            if time.monotonic() > deadline:
                raise SystemExit(f"iteration {i}: timed out waiting for the two runs of {sha}")
            time.sleep(POLL_SECONDS)

        b, p = ours[BASELINE_WORKFLOW], ours[PROTOCOL_WORKFLOW]
        records = [r for r in records if r.get("iteration") != i]
        records.append({
            "iteration": i,
            "sha": sha,
            "pushedAt": stamp,
            "runs": {"B": b["databaseId"], "P": p["databaseId"]},
            "conclusions": {"B": b["conclusion"], "P": p["conclusion"]},
            "urls": {"B": b["url"], "P": p["url"]},
        })
        records.sort(key=lambda r: r["iteration"])
        write_json(args.runs, records)
        print(f"[rq2-timing] iteration {i}: B={b['databaseId']} ({b['conclusion']}) "
              f"P={p['databaseId']} ({p['conclusion']})", flush=True)
    return 0


# ── collect: durations from the API ─────────────────────────────────────────

def metrics(run: dict) -> dict:
    created = parse_ts(run["createdAt"])
    jobs = []
    for j in run.get("jobs", []):
        if not j.get("startedAt") or not j.get("completedAt"):
            continue
        started, completed = parse_ts(j["startedAt"]), parse_ts(j["completedAt"])
        jobs.append({"name": j["name"], "conclusion": j.get("conclusion"),
                     "startedAt": j["startedAt"], "completedAt": j["completedAt"],
                     "seconds": round((completed - started).total_seconds(), 1)})
    if not jobs:
        raise ValueError(f"run {run.get('databaseId')} has no completed jobs")
    first = min(parse_ts(j["startedAt"]) for j in jobs)
    last = max(parse_ts(j["completedAt"]) for j in jobs)
    return {
        "queueSeconds": round((first - created).total_seconds(), 1),
        "executionSeconds": round((last - first).total_seconds(), 1),
        "wallSeconds": round((last - created).total_seconds(), 1),
        "computeMinutes": round(sum(j["seconds"] for j in jobs) / 60, 2),
        "jobs": jobs,
    }


def aggregate(rows: list[dict], key: str) -> dict:
    vals = [r[key] for r in rows]
    if not vals:
        return {}
    return {"n": len(vals), "median": round(statistics.median(vals), 2),
            "mean": round(statistics.mean(vals), 2),
            "min": round(min(vals), 2), "max": round(max(vals), 2)}


def cmd_collect(args: argparse.Namespace) -> int:
    record = json.loads(args.runs.read_text(encoding="utf-8"))
    if not record:
        raise SystemExit(f"no runs recorded in {args.runs}")
    rows = []
    for rec in record:
        for condition in ("B", "P"):
            run = json.loads(gh("run", "view", str(rec["runs"][condition]), "--repo", args.repo,
                                "--json", "name,workflowName,headSha,createdAt,updatedAt,"
                                          "conclusion,url,jobs"))
            row = {"iteration": rec["iteration"], "condition": condition,
                   "runId": rec["runs"][condition], "workflow": run.get("workflowName"),
                   "conclusion": run.get("conclusion"), "url": run.get("url"),
                   **metrics(run)}
            rows.append(row)
            print(f"[rq2-timing] {row['iteration']:>2} {condition}: wall {row['wallSeconds']:6.1f}s "
                  f"exec {row['executionSeconds']:6.1f}s queue {row['queueSeconds']:5.1f}s "
                  f"compute {row['computeMinutes']:5.2f} min", flush=True)

    aggregates = {}
    for condition in ("B", "P"):
        subset = [r for r in rows if r["condition"] == condition]
        aggregates[condition] = {k: aggregate(subset, k)
                                 for k in ("wallSeconds", "executionSeconds",
                                           "queueSeconds", "computeMinutes")}
    ratio = {}
    for key in ("wallSeconds", "executionSeconds", "computeMinutes"):
        b = aggregates["B"][key]["median"]
        p = aggregates["P"][key]["median"]
        ratio[key] = round(p / b, 2) if b else None

    data = {
        "generatedAt": now(),
        "repo": args.repo,
        "branch": args.branch,
        "productCommit": record[-1]["sha"] if record else None,
        "baselineWorkflow": BASELINE_WORKFLOW,
        "protocolWorkflow": PROTOCOL_WORKFLOW,
        "runs": rows,
        "aggregates": aggregates,
        "ratioPoverB": ratio,
    }
    write_json(args.out, data)
    print(f"[rq2-timing] wrote {args.out}")

    if args.markdown:
        lines = [
            "| iteration | B wall (s) | P wall (s) | P/B | B compute (min) | P compute (min) |",
            "|---:|---:|---:|---:|---:|---:|",
        ]
        by_iter: dict[int, dict] = {}
        for r in rows:
            by_iter.setdefault(r["iteration"], {})[r["condition"]] = r
        for it in sorted(by_iter):
            b, p = by_iter[it].get("B"), by_iter[it].get("P")
            if not b or not p:
                continue
            lines.append(
                f"| {it} | {b['wallSeconds']:.1f} | {p['wallSeconds']:.1f} | "
                f"{p['wallSeconds'] / b['wallSeconds']:.2f} | "
                f"{b['computeMinutes']:.2f} | {p['computeMinutes']:.2f} |")
        a, ra = aggregates, ratio
        lines += [
            "",
            "| metric (median) | B | P | P/B |",
            "|---|---:|---:|---:|",
            f"| wall (s) | {a['B']['wallSeconds']['median']:.1f} "
            f"[{a['B']['wallSeconds']['min']:.1f}–{a['B']['wallSeconds']['max']:.1f}] | "
            f"{a['P']['wallSeconds']['median']:.1f} "
            f"[{a['P']['wallSeconds']['min']:.1f}–{a['P']['wallSeconds']['max']:.1f}] | "
            f"{ra['wallSeconds']:.2f}× |",
            f"| execution (s) | {a['B']['executionSeconds']['median']:.1f} | "
            f"{a['P']['executionSeconds']['median']:.1f} | {ra['executionSeconds']:.2f}× |",
            f"| queue (s) | {a['B']['queueSeconds']['median']:.1f} | "
            f"{a['P']['queueSeconds']['median']:.1f} | — |",
            f"| compute (min) | {a['B']['computeMinutes']['median']:.2f} | "
            f"{a['P']['computeMinutes']['median']:.2f} | {ra['computeMinutes']:.2f}× |",
        ]
        args.markdown.parent.mkdir(parents=True, exist_ok=True)
        args.markdown.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"[rq2-timing] wrote {args.markdown}")
        print("\n".join(lines))
    return 0


# ── CLI ─────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="RQ2 timing in GitHub Actions")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="push N trigger commits and record the run ids")
    r.add_argument("--repo-dir", type=Path, required=True)
    r.add_argument("--repo", default=DEFAULT_REPO)
    r.add_argument("--branch", default=DEFAULT_BRANCH)
    r.add_argument("--iterations", type=int, default=10)
    r.add_argument("--start", type=int, default=None)
    r.add_argument("--timeout", type=int, default=1800, help="seconds per iteration")
    r.add_argument("--runs", type=Path, required=True)
    r.set_defaults(func=cmd_run)

    c = sub.add_parser("collect", help="turn recorded run ids into timings")
    c.add_argument("--repo", default=DEFAULT_REPO)
    c.add_argument("--branch", default=DEFAULT_BRANCH)
    c.add_argument("--runs", type=Path, required=True)
    c.add_argument("--out", type=Path, required=True)
    c.add_argument("--markdown", type=Path, default=None)
    c.set_defaults(func=cmd_collect)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
