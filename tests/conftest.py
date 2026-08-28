"""Shared fixtures: a throwaway artifacts catalog on tmp_path."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


def write_artifact(domain_dir: Path, artifact_id: str, **files: str) -> None:
    """Create <domain_dir>/<artifact_id>/ holding `files` (name -> body)."""
    d = domain_dir / artifact_id
    d.mkdir(parents=True)
    for name, body in files.items():
        path = d / name.replace("__", ".")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")


@pytest.fixture
def catalog(tmp_path: Path) -> Path:
    """A packaged catalog root: <root>/domains/company/ with two artifacts."""
    company = tmp_path / "domains" / "company"
    company.mkdir(parents=True)

    write_artifact(
        company,
        "expense-policy",
        README__md="# Expense policy\n\nReceipts within 30 days.\n",
        artifact__yaml="title: Expense policy\n",
    )
    write_artifact(
        company,
        "discovery-workshop",
        README__md="# Discovery workshop\n",
        artifact__yaml="title: Discovery workshop\n",
    )

    (company / "_manifest.json").write_text(
        json.dumps(
            {
                "domain": "company",
                "description": "How we work and what we offer.",
                "owner": None,
                "artifacts": [
                    {
                        "id": "discovery-workshop",
                        "title": "Discovery workshop",
                        "kind": "methodology",
                        "description": "How we run discovery.",
                        "class": "functional",
                        "owner": None,
                        "version_id": "bbbb2222",
                    },
                    {
                        "id": "expense-policy",
                        "title": "Expense policy",
                        "kind": "guideline",
                        "description": "What we reimburse.",
                        "class": None,
                        "owner": None,
                        "version_id": "aaaa1111",
                    },
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return tmp_path


@pytest.fixture
def artifacts(catalog: Path, monkeypatch: pytest.MonkeyPatch):
    """The read-path module, pointed at the `catalog` fixture."""
    from server import artifacts as module

    monkeypatch.setattr(module, "ARTIFACTS_ROOT", catalog)
    return module


@pytest.fixture
def source_tree(tmp_path: Path) -> Path:
    """An unpackaged content repo: domain.yaml + artifact folders, no _manifest.json."""
    company = tmp_path / "domains" / "company"
    company.mkdir(parents=True)
    (company / "domain.yaml").write_text(
        "id: company\ndescription: How we work and what we offer.\n", encoding="utf-8"
    )
    write_artifact(
        company,
        "expense-policy",
        artifact__yaml="title: Expense policy\nkind: guideline\ndescription: What we reimburse.\n",
        README__md="# Expense policy\n",
    )
    write_artifact(
        company,
        "discovery-workshop",
        artifact__yaml=(
            "title: Discovery workshop\nkind: methodology\n"
            "description: How we run discovery.\nclass: functional\n"
        ),
        README__md="# Discovery workshop\n",
        agenda__md="# Agenda\n",
    )
    return tmp_path
