"""The two-stage manifest: a facet index, then rows for a group.

The index is built from ALREADY-REDACTED rows, per request. That is the whole reason
these tests exist as a separate module: a precomputed index would disclose the
existence of a value whose every row is denied, which is what the compartments in
access-policy.yaml are for.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from tests.conftest import write_artifact, write_policy

POLICY = """\
version: 1
roles:
  ctx.colleague:
    description: Any authenticated colleague.
    grants:
      company: internal
      projects: internal
  ctx.delivery:
    description: Delivery leads.
    grants:
      projects: restricted
"""


def _row(artifact_id: str, sensitivity: str, sector: str, client: str, caps: list[str]) -> dict:
    return {
        "id": artifact_id,
        "title": artifact_id.replace("-", " ").title(),
        "kind": "engagement",
        "review": "draft",
        "sensitivity": sensitivity,
        "description": f"An engagement called {artifact_id}.",
        "class": None,
        "owner": None,
        "facets": {
            "lifecycle": "past",
            "sector": sector,
            "client": client,
            "capability": caps,
        },
        "version_id": f"v-{artifact_id}",
    }


ROWS = [
    _row("mercedes-benz-churn", "internal", "automotive", "mercedes-benz", ["predictive-analytics-ml"]),
    _row("mercedes-benz-verso", "internal", "automotive", "mercedes-benz", ["data-governance", "bi-reporting"]),
    _row("eurowings-crew", "internal", "aviation", "eurowings", ["agentic-ai"]),
    _row("uniper-grid", "restricted", "energy-utilities", "uniper", ["data-governance"]),
]

FACET_SPECS = {
    "lifecycle": {"description": "Running now, or delivered."},
    "sector": {"description": "The client's industry."},
    "client": {"description": "The client group."},
    "capability": {"description": "What KI group actually did.", "multi": True},
}


@pytest.fixture
def faceted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A catalog whose `projects` domain declares facets, plus a flat `company`."""
    projects = tmp_path / "domains" / "projects"
    projects.mkdir(parents=True)
    for row in ROWS:
        write_artifact(
            projects, row["id"], README__md=f"# {row['title']}\n", artifact__yaml="title: x\n"
        )
    (projects / "_manifest.json").write_text(
        json.dumps(
            {
                "domain": "projects",
                "description": "One artifact per engagement.",
                "owner": None,
                "facets": FACET_SPECS,
                "artifacts": ROWS,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    flat = tmp_path / "domains" / "company"
    flat.mkdir(parents=True)
    write_artifact(flat, "expense-policy", README__md="# E\n", artifact__yaml="title: x\n")
    (flat / "_manifest.json").write_text(
        json.dumps(
            {
                "domain": "company",
                "description": "How we work.",
                "owner": None,
                "artifacts": [
                    {
                        "id": "expense-policy",
                        "title": "Expense policy",
                        "kind": "guideline",
                        "review": "draft",
                        "sensitivity": "internal",
                        "description": "What we reimburse.",
                        "class": None,
                        "owner": None,
                        "version_id": "aaaa1111",
                    }
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    write_policy(tmp_path, POLICY)

    from server import artifacts as module

    monkeypatch.setattr(module, "ARTIFACTS_ROOT", tmp_path)
    return module


def _as(role: str) -> dict:
    from server import access

    return {
        "principal": access.Principal(
            authenticated=True,
            roles=frozenset({role}),
            actor=None,
            actor_key=None,
            tenant=None,
            client=None,
            source="demo",
        ),
        "policy": access.parse_policy(yaml.safe_load(POLICY), mode=access.Mode.ENFORCE),
    }


# --- stage one: the index ----------------------------------------------------


def test_a_faceted_domain_answers_a_group_less_call_with_an_index_and_no_rows(faceted):
    payload = faceted.domain_manifest_payload("projects", **_as("ctx.colleague"))

    assert "artifacts" not in payload
    assert payload["artifact_count"] == 3
    assert payload["facets"]["sector"]["values"] == {"automotive": 2, "aviation": 1}
    assert payload["facets"]["sector"]["description"] == "The client's industry."
    assert "group=" in payload["fetch_hint"]


def test_a_multi_valued_facet_counts_every_value_a_row_carries(faceted):
    payload = faceted.domain_manifest_payload("projects", **_as("ctx.colleague"))

    assert payload["facets"]["capability"]["values"] == {
        "agentic-ai": 1,
        "bi-reporting": 1,
        "data-governance": 1,
        "predictive-analytics-ml": 1,
    }
    assert payload["facets"]["capability"]["multi"] is True


def test_the_index_never_names_a_value_whose_every_row_is_denied(faceted):
    """The disclosure case, and the reason the index is derived per request.

    `uniper-grid` is the only `energy-utilities` row and it is restricted, so a
    colleague must not learn that KI group has a utilities engagement at all.
    """
    colleague = faceted.domain_manifest_payload("projects", **_as("ctx.colleague"))
    delivery = faceted.domain_manifest_payload("projects", **_as("ctx.delivery"))

    assert "energy-utilities" not in colleague["facets"]["sector"]["values"]
    assert "uniper" not in colleague["facets"]["client"]["values"]
    assert delivery["facets"]["sector"]["values"]["energy-utilities"] == 1


def test_a_domain_that_declares_no_facets_is_unchanged(faceted):
    """company/ has no facets block, so it answers exactly as it did before."""
    payload = faceted.domain_manifest_payload("company", **_as("ctx.colleague"))

    assert [a["id"] for a in payload["artifacts"]] == ["expense-policy"]
    assert "facets" not in payload
    assert "get_artifact" in payload["fetch_hint"]


# --- stage two: the rows ----------------------------------------------------


def test_one_filter_returns_only_that_group(faceted):
    payload = faceted.domain_manifest_payload(
        "projects", group="sector:automotive", **_as("ctx.colleague")
    )

    assert [a["id"] for a in payload["artifacts"]] == [
        "mercedes-benz-churn",
        "mercedes-benz-verso",
    ]
    assert payload["group"] == ["sector:automotive"]


def test_several_filters_are_anded(faceted):
    payload = faceted.domain_manifest_payload(
        "projects",
        group=["sector:automotive", "capability:data-governance"],
        **_as("ctx.colleague"),
    )

    assert [a["id"] for a in payload["artifacts"]] == ["mercedes-benz-verso"]


def test_a_filter_matches_any_value_of_a_multi_valued_facet(faceted):
    payload = faceted.domain_manifest_payload(
        "projects", group="capability:bi-reporting", **_as("ctx.colleague")
    )

    assert [a["id"] for a in payload["artifacts"]] == ["mercedes-benz-verso"]


def test_a_filter_cannot_reach_a_denied_row(faceted):
    payload = faceted.domain_manifest_payload(
        "projects", group="capability:data-governance", **_as("ctx.colleague")
    )

    assert [a["id"] for a in payload["artifacts"]] == ["mercedes-benz-verso"]


def test_a_group_that_matches_nothing_is_an_honest_empty_answer(faceted):
    payload = faceted.domain_manifest_payload(
        "projects",
        group=["sector:aviation", "capability:data-governance"],
        **_as("ctx.colleague"),
    )

    assert payload["artifacts"] == []
    assert "report_gap" not in payload["fetch_hint"]


# --- recovery ---------------------------------------------------------------


def test_an_unknown_facet_returns_not_found_naming_the_real_ones(faceted):
    payload = faceted.domain_manifest_payload(
        "projects", group="industry:automotive", **_as("ctx.colleague")
    )

    assert payload["status"] == "not_found"
    assert sorted(payload["known_facets"]) == ["capability", "client", "lifecycle", "sector"]


def test_an_unknown_value_returns_not_found_naming_the_readable_values(faceted):
    payload = faceted.domain_manifest_payload(
        "projects", group="sector:banking", **_as("ctx.colleague")
    )

    assert payload["status"] == "not_found"
    assert payload["known_values"] == {"automotive": 2, "aviation": 1}


def test_a_malformed_filter_returns_not_found_rather_than_every_row(faceted):
    payload = faceted.domain_manifest_payload(
        "projects", group="automotive", **_as("ctx.colleague")
    )

    assert payload["status"] == "not_found"


def test_a_group_on_a_domain_with_no_facets_returns_not_found(faceted):
    payload = faceted.domain_manifest_payload(
        "company", group="sector:automotive", **_as("ctx.colleague")
    )

    assert payload["status"] == "not_found"
    assert payload["known_facets"] == []
