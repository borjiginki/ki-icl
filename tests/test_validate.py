"""The validation gate. It must collect every failure, never stop at the first."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.validate_context import validate  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_a_well_formed_tree_passes(source_tree: Path):
    assert validate(source_tree) == []


def test_this_repos_own_content_passes(): 
    assert validate(REPO_ROOT) == []


def test_a_binary_file_is_rejected_by_name_and_by_rule(source_tree: Path):
    png = source_tree / "domains" / "company" / "expense-policy" / "diagram.png"
    png.write_bytes(b"\x89PNG\r\n")

    errors = validate(source_tree)

    assert any("diagram.png" in e and "text" in e.lower() for e in errors), errors


def test_a_directory_under_a_domain_without_an_artifact_yaml_is_rejected(source_tree: Path):
    """Catches a half-finished removal, which every other check is blind to."""
    (source_tree / "domains" / "company" / "leftover").mkdir()

    errors = validate(source_tree)

    assert any("leftover" in e and "artifact.yaml" in e for e in errors), errors


def test_a_missing_readme_is_rejected(source_tree: Path):
    (source_tree / "domains" / "company" / "expense-policy" / "README.md").unlink()

    assert any("README.md" in e for e in validate(source_tree))


def test_a_domain_id_that_disagrees_with_its_folder_is_rejected(source_tree: Path):
    (source_tree / "domains" / "company" / "domain.yaml").write_text(
        "id: kompany\ndescription: x\n", encoding="utf-8"
    )

    assert any("kompany" in e for e in validate(source_tree))


def test_an_artifact_folder_name_that_is_not_kebab_case_is_rejected(source_tree: Path):
    (source_tree / "domains" / "company" / "expense-policy").rename(
        source_tree / "domains" / "company" / "Expense_Policy"
    )

    assert any("Expense_Policy" in e for e in validate(source_tree))


def test_an_empty_required_field_is_rejected(source_tree: Path):
    (source_tree / "domains" / "company" / "expense-policy" / "artifact.yaml").write_text(
        "title: Expense policy\nkind: guideline\ndescription: ''\n", encoding="utf-8"
    )

    assert any("description" in e for e in validate(source_tree))


def test_an_oversized_file_is_rejected(source_tree: Path):
    big = source_tree / "domains" / "company" / "expense-policy" / "big.md"
    big.write_text("x" * (1_048_576 + 1), encoding="utf-8")

    assert any("big.md" in e for e in validate(source_tree))


def test_every_failure_is_collected_not_just_the_first(source_tree: Path):
    company = source_tree / "domains" / "company"
    (company / "expense-policy" / "a.png").write_bytes(b"x")
    (company / "discovery-workshop" / "b.jpg").write_bytes(b"x")
    (company / "leftover").mkdir()

    errors = validate(source_tree)

    assert len(errors) >= 3, errors
