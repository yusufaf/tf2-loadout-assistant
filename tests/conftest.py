from __future__ import annotations

import pytest


@pytest.fixture(autouse=True, scope="session")
def _no_real_model_requests(request: pytest.FixtureRequest) -> None:
    """Hard guarantee no test reaches an LLM provider unless --live is passed.

    Agent tests use TestModel/FunctionModel; this catches any that slip through,
    regardless of what keys happen to be exported on the dev machine.
    """
    import pydantic_ai.models

    if not request.config.getoption("--live"):
        pydantic_ai.models.ALLOW_MODEL_REQUESTS = False


@pytest.fixture(autouse=True, scope="session")
def _live_env(request: pytest.FixtureRequest) -> None:
    """Load ``.env`` for --live runs only.

    Live tests need real keys, but loading the file unconditionally would break the
    hermeticity the default run depends on. Without this, every live test hits its API
    with an empty key and fails 403 even when the key is sitting in ``.env``.
    """
    if request.config.getoption("--live"):
        from tf2_loadout.config import load_env

        load_env()


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--live",
        action="store_true",
        default=False,
        help="run tests marked @pytest.mark.live (hit real external APIs)",
    )


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    if config.getoption("--live"):
        return
    skip_live = pytest.mark.skip(reason="need --live to run")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip_live)
