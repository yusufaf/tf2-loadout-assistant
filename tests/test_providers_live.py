"""The hard-coded OpenRouter model id must be one OpenRouter's catalog lists.

OpenRouter currently aliases near-miss spellings (the hyphenated Anthropic-API id
resolves to the dotted catalog one), but that alias is undocumented, so a provider row
can look fine while depending on it. The catalog endpoint is public, so this needs no key.
Skipped unless --live.
"""

from __future__ import annotations

import httpx
import pytest

from tf2_loadout.config import provider_spec

pytestmark = pytest.mark.live


async def test_openrouter_model_is_in_openrouter_catalog() -> None:
    spec = provider_spec("openrouter")
    assert spec is not None
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get("https://openrouter.ai/api/v1/models")
    resp.raise_for_status()
    ids = {m["id"] for m in resp.json()["data"]}
    assert spec.model in ids, "OpenRouter no longer lists the configured model id"
