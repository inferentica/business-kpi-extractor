"""The regression net a change passes before it is pushed, in one command:

    python -m kpi_extractor.verify [--trial eval-results/trial.json] [--remote]

1. Every test, by its real exit code.
2. The rule replay over a trial's accepted figures (no AI): every figure whose value or status the change would alter.
3. With --remote, the two scoring sets on GitHub: the 88-value golden set (stored lists) and the hand-audited set
   (evals/audited.json, fresh lists), waited for and summarised.

A change goes out only when the tests pass, the golden set stays whole, and every replay change is one the change meant.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time

REPO = os.environ.get("KPI_EXTRACTOR_REPO", "inferentica/business-kpi-extractor")


def run_tests() -> bool:
    print("== Tests", flush=True)
    result = subprocess.run([sys.executable, "-m", "pytest", "-q", "tests"], capture_output=True, text=True)
    tail = [line for line in result.stdout.splitlines() if line.strip()][-3:]
    print("\n".join(tail))
    print("tests: passed" if result.returncode == 0 else f"tests: FAILED (exit {result.returncode})", flush=True)
    return result.returncode == 0


def run_replay(trial: str, cache: str | None) -> int:
    from .rule_replay import CachedFetcher, replay, report
    print(f"== Rule replay over {trial}", flush=True)
    results = replay(json.load(open(trial))["values"], CachedFetcher(cache))
    print(report(results), flush=True)
    return sum(1 for result in results if result.change not in ("same", "unreplayable"))


def _dispatch(fields: list[str]) -> str:
    before = _latest_run()
    subprocess.run(["gh", "workflow", "run", "evaluate.yml", "-R", REPO, *sum((["-f", f] for f in fields), [])], check=True)
    for _ in range(30):
        time.sleep(3)
        latest = _latest_run()
        if latest and latest != before:
            return latest
    raise RuntimeError("the dispatched run did not appear")


def _latest_run() -> str:
    out = subprocess.run(["gh", "run", "list", "-R", REPO, "-w", "evaluate.yml", "-L", "1", "--json", "databaseId",
                          "-q", ".[0].databaseId"], capture_output=True, text=True)
    return out.stdout.strip()


def _summary(run: str) -> str:
    subprocess.run(["gh", "run", "watch", run, "-R", REPO], capture_output=True)
    log = subprocess.run(["gh", "run", "view", run, "-R", REPO, "--log"], capture_output=True, text=True).stdout
    lines = [line.split("Z ", 1)[-1] for line in log.splitlines()]
    wanted = [line for line in lines if line.startswith("Golden set:") or line.rstrip().endswith("| wrong |")]
    return "\n".join(wanted[:30]) or f"run {run}: no summary line"


def _push_run() -> str:
    """The golden-set run a push of this commit already started, if any: a second one would read the same 88 figures again."""
    sha = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    out = subprocess.run(["gh", "run", "list", "-R", REPO, "-w", "evaluate.yml", "-e", "push", "-c", sha, "-L", "1",
                          "--json", "databaseId", "-q", ".[0].databaseId"], capture_output=True, text=True)
    return out.stdout.strip()


def run_remote() -> bool:
    print("== Scoring sets on GitHub (golden, audited)", flush=True)
    golden = _push_run() or _dispatch(["suite=golden", "offpeak=false"])
    audited = _dispatch(["suite=golden", "golden=evals/audited.json", "lists=fresh", "quarters=4", "offpeak=false"])
    golden_summary, audited_summary = _summary(golden), _summary(audited)
    print(f"golden (run {golden}):\n{golden_summary}\naudited (run {audited}):\n{audited_summary}", flush=True)
    return " 0 wrong" in golden_summary and "0 missing" in golden_summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--trial", default="", help="A trial.json to replay (the last trial's artifact)")
    parser.add_argument("--cache", default=os.path.expanduser("~/.cache/kpi-extractor/filings"))
    parser.add_argument("--remote", action="store_true", help="Also score the golden and audited sets on GitHub")
    args = parser.parse_args(argv)
    ok = run_tests()
    if not ok:
        print("Stopped: the tests fail. Nothing else is worth running.")
        return 1
    if args.trial:
        changes = run_replay(args.trial, args.cache)
        print(f"replay: {changes} figure(s) would change — each must be one the change meant", flush=True)
    if args.remote and not run_remote():
        print("Stopped: the golden set is no longer whole.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
