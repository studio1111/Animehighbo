#!/usr/bin/env python3
"""Merge publication state without dropping records written by concurrent publishers."""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any


def merge_state(base: Any, local: Any) -> Any:
    """Union disjoint state while keeping the authoritative base on scalar conflicts.

    Dicts merge recursively. Lists are stable unions, which is important for
    complete_qualities. For conflicting scalar values, base wins because it
    represents the newest durable main-branch state at merge time.
    """
    if isinstance(base, dict) and isinstance(local, dict):
        merged = copy.deepcopy(base)
        for key, value in local.items():
            if key in merged:
                merged[key] = merge_state(merged[key], value)
            else:
                merged[key] = copy.deepcopy(value)
        return merged
    if isinstance(base, list) and isinstance(local, list):
        merged = copy.deepcopy(base)
        for value in local:
            if value not in merged:
                merged.append(copy.deepcopy(value))
        return merged
    if base == local:
        return copy.deepcopy(base)
    return copy.deepcopy(base)


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot read valid JSON state from {path}: {exc}") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True, type=Path, help="Latest durable state from origin/main")
    parser.add_argument("--local", required=True, type=Path, help="State produced by this publisher")
    parser.add_argument("--output", required=True, type=Path, help="Path for merged state JSON")
    args = parser.parse_args(argv)

    base = _read_json(args.base)
    local = _read_json(args.local)
    if not isinstance(base, dict) or not isinstance(local, dict):
        raise ValueError("Publication state must be a JSON object.")
    merged = merge_state(base, local)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(merged, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
