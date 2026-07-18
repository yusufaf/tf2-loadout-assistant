from __future__ import annotations

import os

import pytest

from tf2_loadout.config import (
    DEFAULT_MODEL,
    DEFAULT_OLLAMA_BASE_URL,
    LLMSettings,
    apply_provider_env,
)

# Every provider key var the settings logic knows about, so a dev machine with a
# real key exported can't accidentally make these tests pass.
_KEY_VARS = [
    "LLM_MODEL",
    "LLM_API_KEY",
    "LLM_BASE_URL",
    "LLM_MAX_REQUESTS",
    "ANTHROPIC_API_KEY",
    "OPENROUTER_API_KEY",
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "OLLAMA_BASE_URL",
]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in _KEY_VARS:
        monkeypatch.delenv(var, raising=False)


def test_defaults_to_anthropic_opus() -> None:
    assert LLMSettings.from_env().model == DEFAULT_MODEL
    assert DEFAULT_MODEL == "anthropic:claude-opus-4-8"


def test_disabled_when_no_key_anywhere() -> None:
    assert LLMSettings.from_env().enabled is False


def test_enabled_by_generic_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_API_KEY", "sk-generic")
    assert LLMSettings.from_env().enabled is True


def test_enabled_by_provider_native_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-native")
    assert LLMSettings.from_env().enabled is True


def test_native_key_for_a_different_provider_does_not_enable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_MODEL", "openrouter:anthropic/claude-sonnet-4.6")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-native")
    assert LLMSettings.from_env().enabled is False


def test_keyless_provider_is_enabled_without_a_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_MODEL", "ollama:llama3.3")
    assert LLMSettings.from_env().enabled is True


def test_base_url_alone_enables_a_self_hosted_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_MODEL", "openai:local-model")
    monkeypatch.setenv("LLM_BASE_URL", "http://localhost:8080/v1")
    assert LLMSettings.from_env().enabled is True


def test_max_requests_defaults_and_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    # ~8 requests for a plain turn, 13 for a lore-checked style question; the default
    # has to clear that or honest turns die on the limit.
    assert LLMSettings.from_env().max_requests == 25
    monkeypatch.setenv("LLM_MAX_REQUESTS", "3")
    assert LLMSettings.from_env().max_requests == 3


def test_blank_values_are_treated_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_MODEL", "")
    monkeypatch.setenv("LLM_API_KEY", "   ")
    settings = LLMSettings.from_env()
    assert settings.model == DEFAULT_MODEL
    assert settings.api_key is None


def test_apply_provider_env_exports_generic_key_under_native_var(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_MODEL", "openrouter:anthropic/claude-sonnet-4.6")
    monkeypatch.setenv("LLM_API_KEY", "sk-or-generic")
    apply_provider_env(LLMSettings.from_env())
    assert os.environ["OPENROUTER_API_KEY"] == "sk-or-generic"


def test_apply_provider_env_exports_base_url_for_ollama(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_MODEL", "ollama:llama3.3")
    monkeypatch.setenv("LLM_BASE_URL", "http://box:11434/v1")
    apply_provider_env(LLMSettings.from_env())
    assert os.environ["OLLAMA_BASE_URL"] == "http://box:11434/v1"


def test_ollama_gets_a_default_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    # The Ollama provider errors out unless OLLAMA_BASE_URL is set, so "enabled with no
    # config" has to mean "pointed at the local daemon" or boot explodes.
    monkeypatch.setenv("LLM_MODEL", "ollama:qwen3.5")
    apply_provider_env(LLMSettings.from_env())
    assert os.environ["OLLAMA_BASE_URL"] == DEFAULT_OLLAMA_BASE_URL


def test_explicit_base_url_beats_the_ollama_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_MODEL", "ollama:qwen3.5")
    monkeypatch.setenv("LLM_BASE_URL", "http://box:11434/v1")
    apply_provider_env(LLMSettings.from_env())
    assert os.environ["OLLAMA_BASE_URL"] == "http://box:11434/v1"


def test_apply_provider_env_is_a_noop_without_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-native")
    apply_provider_env(LLMSettings.from_env())
    assert os.environ["ANTHROPIC_API_KEY"] == "sk-native"
