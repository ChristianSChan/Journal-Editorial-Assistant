"""OpenAlex request configuration."""

from __future__ import annotations

import os


def openalex_params(extra: dict[str, object] | None = None) -> dict[str, object]:
    params: dict[str, object] = dict(extra or {})
    email = os.getenv("OPENALEX_CONTACT_EMAIL", "").strip()
    if email:
        params["mailto"] = email
    return params


def openalex_headers() -> dict[str, str]:
    api_key = os.getenv("OPENALEX_API_KEY", "").strip()
    if not api_key:
        return {}
    return {"Authorization": f"Bearer {api_key}"}
