#!/usr/bin/env python3
"""CLI adapter for the read-only project inspector."""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from project_context.inspector import inspect_project

def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect a software repository")
    parser.add_argument("root", type=Path)
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()
    try:
        result = inspect_project(args.root)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result.to_dict(), indent=2 if args.pretty else None, sort_keys=False))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
