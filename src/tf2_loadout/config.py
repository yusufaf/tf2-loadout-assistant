"""Environment-driven config for the LLM layer.

The provider is chosen entirely by ``LLM_MODEL`` -- a Pydantic AI ``provider:model``
string -- so switching between Anthropic, OpenRouter, a local Ollama box, or anything
else needs no code change. Keys are resolved the way Pydantic AI expects: each provider
reads its own native env var, and ``LLM_API_KEY`` is a generic override that we export
under that native name at startup (see ``apply_provider_env``).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

DEFAULT_MODEL = "anthropic:claude-opus-4-8"
# Measured: a full "build me a themed loadout" turn takes ~8 model requests (search,
# a get_cosmetic per candidate, check_conflicts, final output). Leave headroom.
DEFAULT_MAX_REQUESTS = 15

# Native API-key env var per provider prefix. Pydantic AI's providers read these
# themselves, so we never have to construct a provider object by hand.
_PROVIDER_KEY_VARS = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "github": "GITHUB_API_KEY",
}

# Providers that need no key at all (local inference).
_KEYLESS_PROVIDERS = frozenset({"ollama"})

_PROVIDER_BASE_URL_VARS = {
    "ollama": "OLLAMA_BASE_URL",
    "openai": "OPENAI_BASE_URL",
}

# Pydantic AI's Ollama provider refuses to construct without an explicit base URL, so
# "ollama with no further config" has to resolve to the local daemon or startup fails.
DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434/v1"
_PROVIDER_DEFAULT_BASE_URLS = {"ollama": DEFAULT_OLLAMA_BASE_URL}


def _env(name: str) -> str | None:
    """Read an env var, treating blank/whitespace as unset."""
    value = os.environ.get(name, "").strip()
    return value or None


def load_env() -> None:
    """Load the project ``.env`` into the process environment.

    Real process env wins over the file. Called from ``main`` only -- never at import
    time, so tests stay hermetic.
    """
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(PROJECT_ROOT / ".env", override=False)


@dataclass(frozen=True)
class LLMSettings:
    model: str
    api_key: str | None
    base_url: str | None
    max_requests: int

    @classmethod
    def from_env(cls) -> LLMSettings:
        raw_limit = _env("LLM_MAX_REQUESTS")
        return cls(
            model=_env("LLM_MODEL") or DEFAULT_MODEL,
            api_key=_env("LLM_API_KEY"),
            base_url=_env("LLM_BASE_URL"),
            max_requests=int(raw_limit) if raw_limit else DEFAULT_MAX_REQUESTS,
        )

    @property
    def provider(self) -> str:
        return self.model.split(":", 1)[0]

    @property
    def enabled(self) -> bool:
        """Whether we have enough to talk to the configured provider."""
        if self.api_key or self.base_url:
            return True
        if self.provider in _KEYLESS_PROVIDERS:
            return True
        native_var = _PROVIDER_KEY_VARS.get(self.provider)
        return bool(native_var and _env(native_var))


def apply_provider_env(settings: LLMSettings) -> None:
    """Export generic overrides under the provider's native env var names.

    This is what lets ``build_model`` hand Pydantic AI a bare ``provider:model`` string
    and have credentials resolve correctly, with no per-provider dispatch table here.
    """
    if settings.api_key:
        native_var = _PROVIDER_KEY_VARS.get(settings.provider)
        if native_var:
            os.environ[native_var] = settings.api_key
    base_url = settings.base_url or _PROVIDER_DEFAULT_BASE_URLS.get(settings.provider)
    if base_url:
        base_url_var = _PROVIDER_BASE_URL_VARS.get(settings.provider, "OPENAI_BASE_URL")
        os.environ[base_url_var] = base_url


def build_model(settings: LLMSettings) -> str:
    """Resolve settings to the model spec handed to ``Agent(...)``."""
    apply_provider_env(settings)
    return settings.model
