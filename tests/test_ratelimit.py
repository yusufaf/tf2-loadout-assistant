"""The in-process limiters, client keying, and the two plain-ASGI middlewares.

Limiters take an injectable clock so refill is tested without sleeping; the
middlewares are driven with ``asyncio.run`` and hand-built scopes, the same way
``test_api.py`` drives ``CanonicalHostRedirect`` and ``RequestBodyLimit``.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from tf2_loadout.ratelimit import (
    ChatConcurrencyLimit,
    ConcurrencyLimiter,
    OriginCheck,
    RateLimiter,
    RateLimits,
    client_key,
)


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def _limiter(burst: int, refill: float, **kwargs) -> tuple[RateLimiter, _Clock]:
    clock = _Clock()
    return RateLimiter(burst, refill, clock=clock, **kwargs), clock


def test_allows_burst_then_refuses_with_retry_after():
    limiter, _ = _limiter(3, 10.0)
    assert [limiter.hit("a") for _ in range(3)] == [None, None, None]
    assert limiter.hit("a") == pytest.approx(10.0)


def test_tokens_refill_over_time():
    limiter, clock = _limiter(3, 10.0)
    for _ in range(3):
        limiter.hit("a")
    clock.now += 10.0
    assert limiter.hit("a") is None
    assert limiter.hit("a") is not None


def test_refill_never_exceeds_burst():
    limiter, clock = _limiter(3, 10.0)
    limiter.hit("a")
    clock.now += 1000.0
    assert [limiter.hit("a") for _ in range(3)] == [None, None, None]
    assert limiter.hit("a") is not None


def test_keys_are_independent():
    limiter, _ = _limiter(1, 10.0)
    assert limiter.hit("a") is None
    assert limiter.hit("a") is not None
    assert limiter.hit("b") is None


def test_max_keys_evicts_least_recently_used():
    limiter, _ = _limiter(1, 10.0, max_keys=2)
    limiter.hit("a")
    limiter.hit("b")
    limiter.hit("a")  # refused, but touches a
    limiter.hit("c")  # evicts b, the least recently used
    assert limiter.hit("a") is not None  # still drained
    assert limiter.hit("b") is None  # a fresh, full bucket


def test_concurrency_per_key_cap():
    limiter = ConcurrencyLimiter(per_key=2, total=10)
    assert limiter.try_acquire("a") and limiter.try_acquire("a")
    assert not limiter.try_acquire("a")
    assert limiter.try_acquire("b")


def test_concurrency_total_cap_across_keys():
    limiter = ConcurrencyLimiter(per_key=2, total=3)
    assert limiter.try_acquire("a") and limiter.try_acquire("b") and limiter.try_acquire("c")
    assert not limiter.try_acquire("d")


def test_release_frees_a_slot():
    limiter = ConcurrencyLimiter(per_key=1, total=1)
    assert limiter.try_acquire("a")
    assert not limiter.try_acquire("b")
    limiter.release("a")
    assert limiter.try_acquire("b")


def _scope(
    *headers: tuple[bytes, bytes],
    client: tuple[str, int] | None = ("10.0.0.1", 1234),
    method: str = "POST",
    path: str = "/chat",
    scheme: str = "https",
) -> dict:
    return {
        "type": "http",
        "method": method,
        "path": path,
        "scheme": scheme,
        "headers": list(headers),
        "client": client,
    }


def test_client_key_prefers_trusted_header():
    scope = _scope((b"fly-client-ip", b"1.2.3.4"))
    assert client_key(scope, "fly-client-ip") == "1.2.3.4"


def test_client_key_ignores_header_when_not_trusted():
    scope = _scope((b"fly-client-ip", b"1.2.3.4"))
    assert client_key(scope, None) == "10.0.0.1"


@pytest.mark.parametrize("headers", [(), ((b"fly-client-ip", b""),)])
def test_client_key_falls_back_when_header_absent_or_empty(headers):
    assert client_key(_scope(*headers), "fly-client-ip") == "10.0.0.1"


def test_client_key_groups_ipv6_by_64():
    def key(ip: str) -> str:
        return client_key(_scope((b"fly-client-ip", ip.encode())), "fly-client-ip")

    assert key("2001:db8::1") == key("2001:db8::ffff")
    assert key("2001:db8:0:1::1") != key("2001:db8::1")


def test_client_key_unwraps_ipv4_mapped_ipv6():
    assert client_key(_scope(client=("::ffff:1.2.3.4", 1)), None) == "1.2.3.4"
    assert client_key(_scope(client=("::ffff:5.6.7.8", 1)), None) == "5.6.7.8"


def test_client_key_without_scope_client_is_unknown():
    assert client_key(_scope(client=None), None) == "unknown"
    scope = _scope()
    del scope["client"]
    assert client_key(scope, None) == "unknown"


class _Recorder:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def __call__(self, message: dict) -> None:
        self.sent.append(message)

    @property
    def status(self) -> int:
        return self.sent[0]["status"]

    @property
    def json(self) -> dict:
        return json.loads(self.sent[1]["body"])


async def _ok(scope, receive, send):
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b""})


async def _unreachable(scope, receive, send):
    raise AssertionError("a refused request reached the app")


def _blocking_app(entered: list, release: asyncio.Event):
    async def app(scope, receive, send):
        entered.append(scope)
        await release.wait()
        await _ok(scope, receive, send)

    return app


def _ip(value: str) -> tuple[bytes, bytes]:
    return (b"fly-client-ip", value.encode())


def test_concurrency_cap_refuses_a_second_stream_from_one_ip():
    async def run():
        entered, release = [], asyncio.Event()
        app = ChatConcurrencyLimit(
            _blocking_app(entered, release),
            ConcurrencyLimiter(per_key=1, total=10),
            client_ip_header="fly-client-ip",
        )
        first, second = _Recorder(), _Recorder()
        task = asyncio.create_task(app(_scope(_ip("1.1.1.1"), path="/chat/stream"), None, first))
        await asyncio.sleep(0)
        await app(_scope(_ip("1.1.1.1")), None, second)
        assert second.status == 429
        assert second.json == {"detail": "too many concurrent chat turns"}
        assert len(entered) == 1
        release.set()
        await task
        assert first.status == 200

    asyncio.run(run())


def test_concurrency_cap_is_per_ip():
    async def run():
        entered, release = [], asyncio.Event()
        app = ChatConcurrencyLimit(
            _blocking_app(entered, release),
            ConcurrencyLimiter(per_key=1, total=10),
            client_ip_header="fly-client-ip",
        )
        sends = [_Recorder(), _Recorder()]
        tasks = [
            asyncio.create_task(app(_scope(_ip(ip)), None, send))
            for ip, send in zip(("1.1.1.1", "2.2.2.2"), sends)
        ]
        await asyncio.sleep(0)
        assert len(entered) == 2
        release.set()
        await asyncio.gather(*tasks)
        assert [s.status for s in sends] == [200, 200]

    asyncio.run(run())


def test_concurrency_defaults_allow_two_per_ip():
    async def run():
        entered, release = [], asyncio.Event()
        app = ChatConcurrencyLimit(_blocking_app(entered, release), RateLimits().chat_concurrency)
        sends = [_Recorder() for _ in range(3)]
        tasks = [asyncio.create_task(app(_scope(), None, s)) for s in sends[:2]]
        await asyncio.sleep(0)
        await app(_scope(), None, sends[2])
        assert sends[2].status == 429
        assert len(entered) == 2
        release.set()
        await asyncio.gather(*tasks)

    asyncio.run(run())


def test_concurrency_slot_released_when_app_raises():
    limiter = ConcurrencyLimiter(per_key=1, total=1)

    async def boom(scope, receive, send):
        raise RuntimeError("handler failed")

    with pytest.raises(RuntimeError):
        asyncio.run(ChatConcurrencyLimit(boom, limiter)(_scope(), None, _Recorder()))
    send = _Recorder()
    asyncio.run(ChatConcurrencyLimit(_ok, limiter)(_scope(), None, send))
    assert send.status == 200


def test_concurrency_slot_released_when_cancelled():
    limiter = ConcurrencyLimiter(per_key=1, total=1)

    async def run():
        entered, release = [], asyncio.Event()
        task = asyncio.create_task(
            ChatConcurrencyLimit(_blocking_app(entered, release), limiter)(
                _scope(), None, _Recorder()
            )
        )
        await asyncio.sleep(0)
        assert entered
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        send = _Recorder()
        await ChatConcurrencyLimit(_ok, limiter)(_scope(), None, send)
        assert send.status == 200

    asyncio.run(run())


@pytest.mark.parametrize(
    "method,path", [("GET", "/chat"), ("POST", "/loadout/conflicts"), ("GET", "/healthz")]
)
def test_concurrency_cap_ignores_other_paths_and_get(method, path):
    limiter = ConcurrencyLimiter(per_key=1, total=1)
    assert limiter.try_acquire("10.0.0.1")  # the only slot is taken
    send = _Recorder()
    asyncio.run(ChatConcurrencyLimit(_ok, limiter)(_scope(method=method, path=path), None, send))
    assert send.status == 200


def _origin_scope(origin: bytes | None, *, method: str = "POST", scheme: str = "https") -> dict:
    headers = [(b"host", b"tf2.yusufaf.dev")]
    if origin is not None:
        headers.append((b"origin", origin))
    return _scope(*headers, method=method, path="/auth/logout", scheme=scheme)


def test_origin_check_refuses_cross_site_post():
    send = _Recorder()
    scope = _origin_scope(b"https://analytics.yusufaf.dev")
    asyncio.run(OriginCheck(_unreachable)(scope, None, send))
    assert send.status == 403
    assert send.json == {"detail": "cross-origin request refused"}


@pytest.mark.parametrize(
    "origin,allowed",
    [
        (b"https://tf2.yusufaf.dev", ()),
        (b"HTTPS://TF2.yusufaf.dev", ()),
        (b"http://localhost:5173", ("http://localhost:5173",)),
        (None, ()),
    ],
)
def test_origin_check_allows_same_origin_allowlisted_and_absent(origin, allowed):
    send = _Recorder()
    asyncio.run(OriginCheck(_ok, allowed)(_origin_scope(origin), None, send))
    assert send.status == 200


def test_origin_check_allows_get_with_foreign_origin():
    send = _Recorder()
    scope = _origin_scope(b"https://evil.example", method="GET")
    asyncio.run(OriginCheck(_ok)(scope, None, send))
    assert send.status == 200


@pytest.mark.parametrize(
    "origin,scheme", [(b"null", "https"), (b"http://tf2.yusufaf.dev", "https")]
)
def test_origin_check_refuses_null_origin_and_scheme_mismatch(origin, scheme):
    send = _Recorder()
    asyncio.run(OriginCheck(_unreachable)(_origin_scope(origin, scheme=scheme), None, send))
    assert send.status == 403
