from __future__ import annotations

import pytest
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.models.openrouter import OpenRouterModel

from tf2_loadout.config import (
    PROVIDERS,
    AuthSettings,
    LLMSettings,
    UnknownProviderError,
    UserLLM,
    build_user_model,
    provider_ids,
)

# A dev machine with a real key exported must not be able to influence these tests:
# the server never reads a provider key from the environment any more, and the
# tests should fail loudly if that ever regresses.
_KEY_VARS = [
    "LLM_MAX_REQUESTS",
    "LLM_API_KEY",
    "LLM_PROVIDER",
    "ANTHROPIC_API_KEY",
    "OPENROUTER_API_KEY",
    "OPENAI_API_KEY",
]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in _KEY_VARS:
        monkeypatch.delenv(var, raising=False)


def test_max_requests_defaults_and_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    # ~8 requests for a plain turn, 13 for a lore-checked style question; the default
    # has to clear that or honest turns die on the limit.
    assert LLMSettings.from_env().max_requests == 25
    monkeypatch.setenv("LLM_MAX_REQUESTS", "3")
    assert LLMSettings.from_env().max_requests == 3


def test_blank_max_requests_is_treated_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_MAX_REQUESTS", "   ")
    assert LLMSettings.from_env().max_requests == 25


def test_provider_allowlist_is_exactly_the_three_installed_extras() -> None:
    assert provider_ids() == ("anthropic", "openai", "openrouter")
    assert all(spec.label and spec.model for spec in PROVIDERS)


@pytest.mark.parametrize(
    ("provider", "model_cls"),
    [
        ("anthropic", AnthropicModel),
        ("openai", OpenAIChatModel),
        ("openrouter", OpenRouterModel),
    ],
)
def test_build_user_model_constructs_the_provider_with_the_users_key(
    provider: str, model_cls: type
) -> None:
    model = build_user_model(UserLLM(provider=provider, api_key="sk-user"))
    spec = next(s for s in PROVIDERS if s.id == provider)
    assert isinstance(model, model_cls)
    assert model.model_name == spec.model
    # The key must be wired into the client explicitly, never read from env.
    assert model.client.api_key == "sk-user"


def test_build_user_model_never_touches_process_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import os

    build_user_model(UserLLM(provider="anthropic", api_key="sk-user"))
    assert "ANTHROPIC_API_KEY" not in os.environ


def test_build_user_model_rejects_unknown_provider() -> None:
    with pytest.raises(UnknownProviderError):
        build_user_model(UserLLM(provider="ollama", api_key="x"))


def test_build_user_model_rejects_blank_key() -> None:
    with pytest.raises(ValueError):
        build_user_model(UserLLM(provider="anthropic", api_key="   "))


_AUTH_VARS = ["PUBLIC_BASE_URL", "SESSION_SECRET", "STEAM_API_KEY"]


@pytest.fixture(autouse=True)
def clean_auth_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in _AUTH_VARS:
        monkeypatch.delenv(var, raising=False)


def test_auth_disabled_with_nothing_set() -> None:
    assert AuthSettings.from_env().enabled is False


def test_auth_disabled_with_only_one_of_the_two_required_vars(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SESSION_SECRET", "shh")
    assert AuthSettings.from_env().enabled is False


def test_auth_enabled_once_both_required_vars_are_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SESSION_SECRET", "shh")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://tf2.example.dev")
    assert AuthSettings.from_env().enabled is True
