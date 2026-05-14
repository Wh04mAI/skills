#!/usr/bin/env python3
"""
Path-based reviewer selection for bmad-team-review.

Reads `git diff --name-only <base>..HEAD` from the current repo and emits
JSON: {reviewer, dual, co_reviewer, fallback_used, vote_breakdown,
size, skip, reasoning}.

Self-model exclusion is applied via the fallback chain in the YAML.

Usage:
    select-reviewer.py <self_model> [--base <branch>]

self_model: one of codex | claude | deepseek (the model that wrote the code)
--base:     base branch for diff (default: dev). Falls back to main if dev not present.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import subprocess
import sys
import tomllib
from collections import Counter
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
MAPPING_PATH = SCRIPT_DIR / "reviewer-mapping.toml"


def load_mapping() -> dict:
    with MAPPING_PATH.open("rb") as f:
        return tomllib.load(f)


def git_diff_name_only(base: str) -> list[str]:
    out = subprocess.run(
        ["git", "diff", "--name-only", f"{base}...HEAD"],
        capture_output=True, text=True, check=True,
    )
    return [line.strip() for line in out.stdout.splitlines() if line.strip()]


def git_diff_loc(base: str) -> int:
    out = subprocess.run(
        ["git", "diff", "--shortstat", f"{base}...HEAD"],
        capture_output=True, text=True, check=True,
    )
    line = out.stdout.strip()
    # e.g. "5 files changed, 123 insertions(+), 45 deletions(-)"
    total = 0
    for part in line.split(","):
        part = part.strip()
        if "insertion" in part or "deletion" in part:
            total += int(part.split()[0])
    return total


def match_file(path: str, mapping: dict) -> dict | None:
    for entry in mapping["mappings"]:
        for pattern in entry["patterns"]:
            if fnmatch.fnmatch(path, pattern):
                return entry
    return None


def fallback_swap(reviewer: str, self_model: str, mapping: dict) -> tuple[str, bool]:
    """Return (effective_reviewer, fallback_used)."""
    if reviewer != self_model:
        return reviewer, False
    chain = mapping["fallback_chains"].get(self_model, [self_model, "codex", "claude", "deepseek"])
    for candidate in chain:
        if candidate != self_model:
            return candidate, True
    return reviewer, False  # all candidates were self — degenerate


def select(self_model: str, base: str) -> dict:
    mapping = load_mapping()
    files = git_diff_name_only(base)
    loc = git_diff_loc(base)

    skip_below = mapping["size_overrides"]["skip_below"]
    if loc < skip_below:
        return {
            "skip": True,
            "reason": f"size {loc} LOC < skip_below {skip_below}",
            "size": loc,
            "files": len(files),
            "stopping_rules": mapping.get("stopping_rules", {}),
        }

    votes: Counter[str] = Counter()
    co_votes: Counter[str] = Counter()
    categories: set[str] = set()
    per_file_choice: list[dict] = []

    for path in files:
        match = match_file(path, mapping)
        if match is None:
            continue
        votes[match["reviewer"]] += 1
        if "co_reviewer" in match:
            co_votes[match["co_reviewer"]] += 1
        categories.add(",".join(match["patterns"]))
        per_file_choice.append({"path": path, "reviewer": match["reviewer"]})

    if not votes:
        return {
            "skip": False,
            "reviewer": "codex",  # default
            "dual": False,
            "fallback_used": False,
            "size": loc,
            "files": len(files),
            "stopping_rules": mapping.get("stopping_rules", {}),
            "reasoning": "no path matched mapping; default reviewer",
        }

    primary = votes.most_common(1)[0][0]
    primary_eff, fb_used = fallback_swap(primary, self_model, mapping)

    # Dual review trigger: large change AND multiple categories
    dual_above = mapping["size_overrides"]["dual_above"]
    dual_min_cats = mapping["size_overrides"]["dual_min_categories"]
    dual = loc >= dual_above and len(categories) >= dual_min_cats

    co_reviewer = None
    co_candidates: list[str] = []
    if co_votes:
        co_candidates.extend([r for r, _ in co_votes.most_common()])
    co_candidates.extend([r for r, _ in votes.most_common() if r != primary])
    # Last-resort: walk all reviewers
    co_candidates.extend(["codex", "claude", "deepseek"])
    for cand in co_candidates:
        cand_eff, _ = fallback_swap(cand, self_model, mapping)
        if cand_eff != primary_eff and cand_eff != self_model:
            co_reviewer = cand_eff
            break

    if dual and not co_reviewer:
        # Could not find a diverse co-reviewer (only one model available
        # after self-exclusion). Degrade to single.
        dual = False

    return {
        "skip": False,
        "reviewer": primary_eff,
        "dual": dual,
        "co_reviewer": co_reviewer,
        "fallback_used": fb_used,
        "size": loc,
        "files": len(files),
        "categories": len(categories),
        "vote_breakdown": dict(votes),
        "co_vote_breakdown": dict(co_votes) if co_votes else None,
        "stopping_rules": mapping.get("stopping_rules", {}),
        "reasoning": (
            f"primary={primary} via majority vote ({dict(votes)}); "
            f"{'fallback to ' + primary_eff + ' (writer was ' + self_model + ')' if fb_used else 'no fallback needed'}; "
            f"{'dual review with ' + co_reviewer if co_reviewer else 'single reviewer'}"
        ),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("self_model", choices=["codex", "claude", "deepseek"])
    ap.add_argument("--base", default="dev",
                    help="base branch for diff (default: dev; falls back to main)")
    args = ap.parse_args()

    base = args.base
    # Verify base exists; fall back to main if dev is missing
    rc = subprocess.run(
        ["git", "rev-parse", "--verify", base],
        capture_output=True, text=True,
    ).returncode
    if rc != 0:
        base = "main"

    result = select(args.self_model, base)
    result["base"] = base
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
