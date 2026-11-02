#!/usr/bin/env python3
"""
Summarise the backend RQ2 results (written by run_corpus.py) into the shared
summary.md: only the section between the backend markers is (re)written, so the
frontend section of the same file is preserved.

Usage: python quality/experiment/summarize.py --results <results/backend> --summary <results/summary.md>
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import mean

START, END = "<!-- rq2:backend:start -->", "<!-- rq2:backend:end -->"


def yn(v) -> str:
    return "—" if v is None else ("yes" if v else "no")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", required=True, type=Path)
    ap.add_argument("--summary", required=True, type=Path)
    args = ap.parse_args(argv)

    cb = json.loads((args.results / "clean-B.json").read_text())
    cp = json.loads((args.results / "clean-P.json").read_text())
    rows = []
    for f in sorted(args.results.glob("*.json")):
        if f.name.startswith("clean-"):
            continue
        rows.append(json.loads(f.read_text()))
    order = {"S": 0, "R": 1}
    rows.sort(key=lambda r: (order.get(r["defect"]["family"], 9), r["defect"]["id"]))

    lines = [START, "", "## Backend (RQ2)", "",
             f"Research commit `{cb['researchCommit'][:12]}` (`research/quality-pipeline-v2`) for the clean runs"
             + (f"; defects ran at {', '.join(sorted({'`' + r['researchCommit'][:12] + '`' for r in rows}))}"
                " (differences limited to quality/experiment/)" if rows else "") + ", "
             f"baseline tests from `origin/develop` `{cb['developCommit'][:12]}`; "
             f"clean runs {len(cb['runs'])}× per condition; generated {rows[-1]['date'] if rows else ''}.", "",
             "Clean state: B "
             + ", ".join(f"{k}={v['status']}" for k, v in cb["runs"][0]["checks"].items())
             + f" ({cb['runs'][0]['checks']['unit'].get('pytestSummary')}, coverage "
             f"{cb['runs'][0]['checks']['unit'].get('coverageTotal')}%); P decision "
             f"{cp['runs'][0].get('decision')} "
             f"({sum(1 for g in cp['runs'][0]['checks'].values() if g['status'] == 'passed')}/"
             f"{len(cp['runs'][0]['checks'])} gates passed).", "",
             "| id | family | target attribute | B detected | B blocks deploy | P detected | P blocks | "
             "P failing gates (attribute) | attribute match | B s | P s |",
             "|---|---|---|---|---|---|---|---|---|---:|---:|"]
    stats: dict[str, dict] = {}
    for r in rows:
        d, b, p = r["defect"], r.get("B", {}), r.get("P", {})
        bd, pd = b.get("detection", {}), p.get("detection", {})
        gates = ", ".join(f"{g} ({p['checks'].get(g, {}).get('attributeId', '?')})"
                          for g in pd.get("failingGates", [])) or "—"
        lines.append(f"| {d['id']} | {d['family']} | {d['targetAttribute']} | {yn(bd.get('detected'))} | "
                     f"{yn(b.get('blocksDeploy'))} | {yn(pd.get('detected'))} | {yn(p.get('blocksDeploy'))} | "
                     f"{gates} | {yn(pd.get('attributeMatch'))} | {b.get('durationSeconds', '—')} | "
                     f"{p.get('durationSeconds', '—')} |")
        for fam in (d["family"], "total"):
            s = stats.setdefault(fam, {"n": 0, "b": 0, "p": 0, "m": 0, "bs": [], "ps": []})
            s["n"] += 1
            s["b"] += bool(bd.get("detected"))
            s["p"] += bool(pd.get("detected"))
            s["m"] += bool(pd.get("attributeMatch"))
            if "durationSeconds" in b:
                s["bs"].append(b["durationSeconds"])
            if "durationSeconds" in p:
                s["ps"].append(p["durationSeconds"])

    pct = lambda a, n: f"{a}/{n} ({100 * a / n:.0f}%)" if n else "—"  # noqa: E731
    lines += ["", "| family | n | B detected | P detected | P attribute match (of detected) | mean B s | mean P s |",
              "|---|---:|---|---|---|---:|---:|"]
    for fam in ("S", "R", "total"):
        s = stats.get(fam)
        if not s:
            continue
        lines.append(f"| {fam} | {s['n']} | {pct(s['b'], s['n'])} | {pct(s['p'], s['n'])} | "
                     f"{pct(s['m'], s['p'])} | {mean(s['bs']):.1f} | {mean(s['ps']):.1f} |")
    neither = [r["defect"]["id"] for r in rows
               if not r.get("B", {}).get("detection", {}).get("detected")
               and not r.get("P", {}).get("detection", {}).get("detected")]
    lines += ["", f"Detected by neither: {', '.join(neither) or 'none'}.", "",
              "Notes: B never blocks the deploy (the deploy workflow does not depend on CI) and B's `unit` job "
              "is red in the clean state (stale tests + coverage below fail_under 80), so B detection rests on "
              "per-test JUnit and the lint/build jobs. B s = sequential sum of the three jobs (in CI they run "
              "in parallel); P s = wall time of collect_evidence.py (all gates sequential + collection count). "
              "D4 (#29) and D9 (#34) are open and covered by strict xfail tests, so they are not in corpus R; "
              "D2 (#28) is open as well. Attribute match is counted among P detections (m/detected); over all "
              "defects it is m/n.", "", END]
    block = "\n".join(lines) + "\n"

    text = args.summary.read_text() if args.summary.exists() else "# RQ2 — Defect detection results\n\n"
    if START in text and END in text:
        pre, rest = text.split(START, 1)
        post = rest.split(END, 1)[1].lstrip("\n")
        text = pre + block + ("\n" + post if post else "")
    else:
        text = text.rstrip("\n") + "\n\n" + block
    args.summary.parent.mkdir(parents=True, exist_ok=True)
    args.summary.write_text(text)
    print(block)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
