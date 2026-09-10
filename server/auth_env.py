"""Canonical Azure env names, with the KI_ICL_ENTRA_* aliases still accepted."""

from __future__ import annotations

import os


def canonical_or_legacy(canonical: str, legacy: str) -> str:
    return os.environ.get(canonical, "").strip() or os.environ.get(legacy, "").strip()


def expected_tenant_from_env() -> str | None:
    return canonical_or_legacy("AZURE_TENANT_ID", "KI_ICL_ENTRA_TENANT_ID") or None
