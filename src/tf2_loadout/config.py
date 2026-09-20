"""Config for the LLM layer.

The server holds no LLM credential at all. Every chat turn arrives with the player's
own provider choice and API key (``X-LLM-Provider`` / ``X-LLM-API-Key`` headers), and
``build_user_model`` turns that pair into a Pydantic AI model object for that one run.
The provider objects are constructed explicitly with the key rather than through the
providers' native env vars: the key is per-request, and anything process-global would
leak one player's key into another player's turn.

What the environment still decides is only the runaway-loop guard
(``LLM_MAX_REQUESTS``) and, for ``--live`` tests, which key to test with.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pydantic_ai.models import Model

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Measured: a plain loadout turn takes ~8 model requests; a hard style question that
# checks item lore before committing took 13. Leave real headroom above that -- the
# limit exists to stop a runaway loop, not to cut off honest work.
DEFAULT_MAX_REQUESTS = 25


@dataclass(frozen=True)
class ProviderSpec:
    id: str
    label: str
    # One fixed model per provider. The system prompt is tuned against frontier
    # models; letting players type any model name in is how weak models end up
    # inventing cosmetics.
    model: str


# The allowlist the UI dropdown is built from and the header is validated against.
# Matches the Pydantic AI extras installed in pyproject -- add an extra before adding
# a row here.
PROVIDERS: tuple[ProviderSpec, ...] = (
    ProviderSpec("anthropic", "Anthropic", "claude-opus-4-8"),
    ProviderSpec("openai", "OpenAI", "gpt-5"),
    ProviderSpec("openrouter", "OpenRouter", "anthropic/claude-opus-4-8"),
)


def provider_ids() -> tuple[str, ...]:
    return tuple(spec.id for spec in PROVIDERS)


def provider_spec(provider: str) -> ProviderSpec | None:
    return next((spec for spec in PROVIDERS if spec.id == provider), None)


class UnknownProviderError(ValueError):
    """The provider id is not in ``PROVIDERS``."""


@dataclass(frozen=True)
class UserLLM:
    """What one request brings: which provider to talk to, and with whose key."""

    provider: str
    api_key: str


def build_user_model(llm: UserLLM) -> Model:
    """Construct a model for one run from the player's provider + key.

    Provider imports live inside the function so an uninstalled extra fails the one
    request that needs it, not app startup.
    """
    spec = provider_spec(llm.provider)
    if spec is None:
        raise UnknownProviderError(llm.provider)
    api_key = llm.api_key.strip()
    if not api_key:
        raise ValueError("empty api key")

    if spec.id == "anthropic":
        from pydantic_ai.models.anthropic import AnthropicModel
        from pydantic_ai.providers.anthropic import AnthropicProvider

        return AnthropicModel(spec.model, provider=AnthropicProvider(api_key=api_key))
    if spec.id == "openai":
        from pydantic_ai.models.openai import OpenAIChatModel
        from pydantic_ai.providers.openai import OpenAIProvider

        return OpenAIChatModel(spec.model, provider=OpenAIProvider(api_key=api_key))
    if spec.id == "openrouter":
        from pydantic_ai.models.openrouter import OpenRouterModel
        from pydantic_ai.providers.openrouter import OpenRouterProvider

        return OpenRouterModel(spec.model, provider=OpenRouterProvider(api_key=api_key))
    raise UnknownProviderError(llm.provider)  # pragma: no cover -- PROVIDERS drifted


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
    max_requests: int

    @classmethod
    def from_env(cls) -> LLMSettings:
        raw_limit = _env("LLM_MAX_REQUESTS")
        return cls(max_requests=int(raw_limit) if raw_limit else DEFAULT_MAX_REQUESTS)


@dataclass(frozen=True)
class AuthSettings:
    public_base_url: str | None
    session_secret: str | None
    steam_api_key: str | None

    @classmethod
    def from_env(cls) -> AuthSettings:
        return cls(
            public_base_url=_env("PUBLIC_BASE_URL"),
            session_secret=_env("SESSION_SECRET"),
            steam_api_key=_env("STEAM_API_KEY"),
        )

    @property
    def enabled(self) -> bool:
        """Whether we have enough to run Steam sign-in at all.

        ``steam_api_key`` is checked separately at call sites that need the
        profile lookup -- sign-in itself only needs the session secret and a
        base URL to build realm/return_to from.
        """
        return bool(self.session_secret and self.public_base_url)


# Matches the plan's table-name convention (`<app>-<stage>-main`) and the region
# every other AWS project here already uses -- see cdk/.
DEFAULT_DYNAMODB_REGION = "us-west-2"


@dataclass(frozen=True)
class LoadoutsSettings:
    table_name: str | None
    region: str

    @classmethod
    def from_env(cls) -> LoadoutsSettings:
        return cls(
            table_name=_env("DYNAMODB_TABLE_NAME"),
            region=_env("AWS_REGION") or DEFAULT_DYNAMODB_REGION,
        )

    @property
    def enabled(self) -> bool:
        """AWS credentials aren't checked here -- boto3 reads AWS_ACCESS_KEY_ID /
        AWS_SECRET_ACCESS_KEY from the environment itself, its own native var names
        so the only thing this app needs to decide is which table to point at.
        """
        return bool(self.table_name)
