"""Phase 4: relay abstraction -- in-memory relay, NIP-01 filters, a real
websocket client against a local dev relay (no internet needed)."""
from __future__ import annotations

import os
import threading
import time

import pytest

from agentm2m.nostr.errors import RelayError
from agentm2m.nostr.events import UnsignedEvent
from agentm2m.nostr.filters import matches
from agentm2m.nostr.relay import MemoryRelay, MultiRelay, WebSocketRelay
from agentm2m.nostr.relay_server import DevRelayServer
from agentm2m.nostr.signer import KeySigner

SIGNER = KeySigner.generate()
OTHER = KeySigner.generate()


def ev(content="x", kind=4242, tags=None, created_at=1000, signer=SIGNER):
    return signer.sign(UnsignedEvent(created_at=created_at, kind=kind, tags=tags or [["t", "agentm2m"]], content=content))


# -- filters ----------------------------------------------------------------

def test_filters():
    e = ev(tags=[["t", "agentm2m"], ["t", "agentm2m-run-r1"], ["p", "aa" * 32], ["run", "r1"]])
    assert matches({}, e)
    assert matches({"kinds": [4242]}, e) and not matches({"kinds": [1]}, e)
    assert matches({"authors": [SIGNER.pubkey]}, e) and not matches({"authors": [OTHER.pubkey]}, e)
    assert matches({"ids": [e.id]}, e)
    assert matches({"#t": ["agentm2m-run-r1"]}, e) and not matches({"#t": ["nope"]}, e)
    assert matches({"#p": ["aa" * 32]}, e)
    assert matches({"since": 1000, "until": 1000}, e) and not matches({"since": 1001}, e) and not matches({"until": 999}, e)
    assert matches({"kinds": [4242], "#t": ["agentm2m"], "authors": [SIGNER.pubkey]}, e)


# -- memory relay -------------------------------------------------------------

def test_publish_and_query():
    r = MemoryRelay()
    e = ev()
    res = r.publish(e)
    assert res.ok and res.event_id == e.id
    assert [d["id"] for d in r.query([{"kinds": [4242]}])] == [e.id]


def test_query_order_and_limit():
    r = MemoryRelay()
    for i in range(5):
        r.publish(ev(content=str(i), created_at=1000 + i))
    got = r.query([{"limit": 2}])
    assert [d["content"] for d in got] == ["4", "3"]  # newest first, like real relays


def test_duplicate_event():
    r = MemoryRelay()
    e = ev()
    assert r.publish(e).ok
    again = r.publish(e)
    assert again.ok and again.message.startswith("duplicate:")
    assert len(r.query([{}])) == 1


def test_invalid_signature_rejected_by_relay():
    r = MemoryRelay()
    d = ev().to_dict()
    d["content"] = "tampered"
    res = r.publish_raw(d)
    assert not res.ok and res.message.startswith("invalid:")
    assert r.query([{}]) == []


def test_invalid_event_rejected_by_relay():
    r = MemoryRelay()
    assert not r.publish_raw({"id": "nope"}).ok


def test_subscribe_receives_history_then_live():
    r = MemoryRelay()
    old = ev(content="old")
    r.publish(old)
    got: list[dict] = []
    sub = r.subscribe([{"kinds": [4242]}], got.append)
    new = ev(content="new", created_at=1001)
    r.publish(new)
    r.publish(ev(content="other-kind", kind=1))
    assert [d["content"] for d in got] == ["old", "new"]
    sub.close()
    r.publish(ev(content="after-close", created_at=1002))
    assert len(got) == 2


def test_disconnect_and_reconnect():
    r = MemoryRelay()
    r.down = True
    with pytest.raises(RelayError):
        r.publish(ev())
    with pytest.raises(RelayError):
        r.query([{}])
    r.down = False
    assert r.publish(ev()).ok


def test_timeout():
    r = MemoryRelay()
    r.timeout = True
    with pytest.raises(RelayError, match="timeout"):
        r.publish(ev())


def test_reject_mode():
    r = MemoryRelay()
    r.reject = "blocked: rate-limited"
    res = r.publish(ev())
    assert not res.ok and "rate-limited" in res.message


def test_multi_relay_any_ack_and_union():
    a, b = MemoryRelay("mem://a"), MemoryRelay("mem://b")
    m = MultiRelay([a, b])
    a.down = True
    e = ev()
    res = m.publish(e)
    assert res.ok and b.query([{}]) and not a.events
    a.down = False
    a.publish(e)
    a.publish(ev(content="only-a", created_at=1001))
    assert len(m.query([{}])) == 2  # deduplicated by id
    b.down = True
    a.down = True
    assert not m.publish(ev(content="z")).ok


# -- websocket client against the local dev relay ------------------------------

@pytest.fixture
def dev_relay():
    server = DevRelayServer(host="127.0.0.1", port=0)
    server.start()
    yield server
    server.stop()


def test_websocket_publish_query(dev_relay):
    client = WebSocketRelay(dev_relay.url, timeout=5)
    e = ev()
    assert client.publish(e).ok
    assert client.publish(e).message.startswith("duplicate:")
    assert [d["id"] for d in client.query([{"authors": [SIGNER.pubkey]}])] == [e.id]
    bad = e.to_dict() | {"content": "tampered"}
    assert not client.publish_raw(bad).ok
    client.close()


def test_websocket_subscribe(dev_relay):
    client = WebSocketRelay(dev_relay.url, timeout=5)
    got: list[dict] = []
    seen = threading.Event()

    def cb(d):
        got.append(d)
        if len(got) == 2:
            seen.set()

    client.publish(ev(content="one"))
    sub = client.subscribe([{"kinds": [4242]}], cb)
    client.publish(ev(content="two", created_at=1001))
    assert seen.wait(5)
    assert sorted(d["content"] for d in got) == ["one", "two"]
    sub.close()
    client.close()


def test_websocket_relay_down_and_reconnect(dev_relay):
    client = WebSocketRelay(dev_relay.url, timeout=2)
    assert client.publish(ev(content="a")).ok
    port = dev_relay.port
    dev_relay.stop()
    with pytest.raises(RelayError):
        client.publish(ev(content="b"))
    restarted = DevRelayServer(host="127.0.0.1", port=port)
    restarted.start()
    try:
        deadline = time.time() + 5
        while True:
            try:
                assert client.publish(ev(content="c")).ok
                break
            except RelayError:
                if time.time() > deadline:
                    raise
                time.sleep(0.1)
    finally:
        client.close()
        restarted.stop()


def test_unreachable_relay_times_out_fast():
    client = WebSocketRelay("ws://127.0.0.1:9", timeout=1)
    t0 = time.monotonic()
    with pytest.raises(RelayError):
        client.publish(ev())
    assert time.monotonic() - t0 < 5


@pytest.mark.skipif(not os.getenv("AGENTM2M_TEST_RELAY_URL"), reason="set AGENTM2M_TEST_RELAY_URL for a live relay")
def test_live_relay_roundtrip():
    client = WebSocketRelay(os.environ["AGENTM2M_TEST_RELAY_URL"], timeout=10)
    e = ev(content=f"agentm2m live test {time.time()}", created_at=int(time.time()))
    assert client.publish(e).ok
    assert any(d["id"] == e.id for d in client.query([{"ids": [e.id]}]))
