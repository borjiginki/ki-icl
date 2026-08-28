#!/usr/bin/env python3
"""Walk the acceptance demo (spec section 1.2, steps 4 to 8) through a real MCP client.

Steps 1 to 3 are the git and CI path, which the POC does not cover: run
`make package` in their place. Everything from step 4 on is exercised here over
FastMCP's in-memory transport, so this is the same call path a remote client takes.

    make demo
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastmcp import Client  # noqa: E402

from server import artifacts  # noqa: E402
from server.mcp_server import mcp  # noqa: E402

GREEN, RED, DIM, OFF = "\033[32m", "\033[31m", "\033[2m", "\033[0m"
failures: list[str] = []


def check(step: str, ok: bool, detail: str) -> None:
    mark = f"{GREEN}PASS{OFF}" if ok else f"{RED}FAIL{OFF}"
    print(f"  {mark}  {step}\n        {DIM}{detail}{OFF}")
    if not ok:
        failures.append(step)


async def main() -> int:
    print(f"\nServing {artifacts.ARTIFACTS_ROOT}\n")
    async with Client(mcp) as client:

        async def call(name: str, args: dict) -> dict:
            return json.loads((await client.call_tool(name, args)).content[0].text)

        domains = await call("list_domains", {})
        ids = [d["id"] for d in domains["domains"]]
        check("4. list_domains() shows the company domain", "company" in ids, f"domains={ids}")

        manifest = await call("get_domain_manifest", {"domain": "company"})
        row = next((a for a in manifest.get("artifacts", []) if a["id"] == "expense-policy"), None)
        check(
            "5. get_domain_manifest lists expense-policy with a version_id",
            bool(row and row.get("version_id")),
            f"version_id={row and row.get('version_id')}",
        )

        found = await call("get_artifact", {"domain": "company", "ids": ["expense-policy"]})
        entry = found["artifacts"][0]
        readme = next((f for f in entry.get("files", []) if f["path"] == "README.md"), None)
        check(
            "6. get_artifact returns the full text of README.md",
            bool(readme and readme["content"].startswith("# Expense policy")),
            f"{entry['status']}, {entry.get('file_count')} file(s), "
            f"{readme and len(readme['content'])} chars of README.md",
        )

        typo = await call("get_artifact", {"domain": "company", "ids": ["expense-polcy"]})
        miss = typo["artifacts"][0]
        check(
            "7. one character wrong is an honest miss, and nothing else",
            miss == {"status": "not_found", "id": "expense-polcy"},
            json.dumps(miss),
        )

        batch = await call(
            "get_artifact", {"domain": "company", "ids": ["expense-policy", "does-not-exist"]}
        )
        statuses = [(a["id"], a["status"]) for a in batch["artifacts"]]
        check(
            "8. a batch degrades per item and the call still succeeds",
            statuses == [("expense-policy", "found"), ("does-not-exist", "not_found")],
            f"{statuses}",
        )

    print()
    if failures:
        print(f"{RED}{len(failures)} step(s) failed.{OFF}\n")
        return 1
    print(f"{GREEN}All 5 demo steps passed.{OFF}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
