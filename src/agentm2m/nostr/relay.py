"""Relay clients.

Everything a relay returns is *untrusted raw JSON*: `query` and
subscription callbacks hand back plain dicts, and callers must run
`verifier.verify_event` before acting on them.

    MemoryRelay     in-process relay with fault injection (tests, demos)
    WebSocketRelay  NIP-01 over websockets (optional `websockets` dependency)
    MultiRelay      fan-out: a publish succeeds if any relay accepts it
"""
from __future__ import annotations

import itertools
import json
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from .errors import InvalidEvent, NostrUnavailable, RelayError
from .events import NostrEvent
from .filters import matches_any
from .verifier import verify_event

Callback = Callable[[dict], None]


@dataclass
class PublishResult:
    ok: bool
    event_id: str
    message: str = ""
    relay: str = ""


class Subscription(Protocol):
    def close(self) -> None: ...


class Relay(Protocol):
    url: str

    def publish(self, ev: NostrEvent) -> PublishResult: ...

    def query(self, filters: list[dict]) -> list[dict]: ...

    def subscribe(self, filters: list[dict], callback: Callback) -> Subscription: ...

    def close(self) -> None: ...


def _apply_limit(events: list[dict], filters: list[dict]) -> list[dict]:
    events = sorted(events, key=lambda d: (-d["created_at"], d["id"]))
    limits = [f["limit"] for f in filters if isinstance(f.get("limit"), int)]
    return events[: max(limits)] if limits else events


# ----------------------------------------------------------------------------
# In-memory relay
# ----------------------------------------------------------------------------


class _MemorySub:
    def __init__(self, relay: MemoryRelay, filters: list[dict], cb: Callback) -> None:
        self.relay, self.filters, self.cb = relay, filters, cb

    def close(self) -> None:
        with self.relay._lock:
            if self in self.relay._subs:
                self.relay._subs.remove(self)


class MemoryRelay:
    """Verifies, stores and fans out events like a real relay. Flip `down`,
    `timeout` or `reject` to inject faults."""

    def __init__(self, url: str = "memory://relay") -> None:
        self.url = url
        self.events: dict[str, dict] = {}
        self.down = False
        self.timeout = False
        self.reject: str | None = None
        self.publish_attempts = 0
        self._subs: list[_MemorySub] = []
        self._lock = threading.RLock()

    def _check_up(self) -> None:
        if self.down:
            raise RelayError(f"{self.url}: connection refused (relay down)")
        if self.timeout:
            raise RelayError(f"{self.url}: timeout waiting for relay")

    def publish(self, ev: NostrEvent) -> PublishResult:
        return self.publish_raw(ev.to_dict())

    def publish_raw(self, d: dict) -> PublishResult:
        self.publish_attempts += 1
        self._check_up()
        eid = d.get("id", "") if isinstance(d, dict) else ""
        if self.reject:
            return PublishResult(False, eid, self.reject, self.url)
        try:
            ev = verify_event(d)
        except InvalidEvent as exc:
            return PublishResult(False, eid if isinstance(eid, str) else "", f"invalid: {exc}", self.url)
        with self._lock:
            if ev.id in self.events:
                return PublishResult(True, ev.id, "duplicate: already have this event", self.url)
            self.events[ev.id] = ev.to_dict()
            targets = [s for s in self._subs if matches_any(s.filters, ev)]
        for s in targets:
            s.cb(ev.to_dict())
        return PublishResult(True, ev.id, "", self.url)

    def inject(self, d: dict) -> None:
        """Store an event *without* verification -- simulates a malicious or
        buggy relay serving forged data to subscribers and queries."""
        with self._lock:
            self.events[str(d.get("id"))] = d
            targets = list(self._subs)
        for s in targets:
            s.cb(d)

    def query(self, filters: list[dict]) -> list[dict]:
        self._check_up()
        with self._lock:
            found = []
            for d in self.events.values():
                try:
                    ev = NostrEvent.from_dict(d)
                except (KeyError, TypeError):
                    continue
                if matches_any(filters, ev):
                    found.append(d)
        return _apply_limit(found, filters)

    def subscribe(self, filters: list[dict], callback: Callback) -> _MemorySub:
        history = self.query(filters)
        sub = _MemorySub(self, filters, callback)
        for d in reversed(history):
            callback(d)
        with self._lock:
            self._subs.append(sub)
        return sub

    def close(self) -> None:
        with self._lock:
            self._subs.clear()


# ----------------------------------------------------------------------------
# WebSocket relay (NIP-01 wire protocol)
# ----------------------------------------------------------------------------


def _ws_connect(url: str, timeout: float):
    try:
        from websockets.sync.client import connect
    except ImportError as exc:  # pragma: no cover
        raise NostrUnavailable("relay I/O needs: pip install 'agentm2m[nostr]'") from exc
    try:
        ws = connect(url, open_timeout=timeout, close_timeout=1, max_size=2**22)
        # Entering the connection (we close it ourselves) is the portable way
        # to hold one open across calls: websockets >= 17.1 warns otherwise.
        return ws.__enter__()
    except Exception as exc:
        raise RelayError(f"{url}: cannot connect ({type(exc).__name__}: {exc})") from exc


_sub_ids = itertools.count(1)


class _WsSubscription:
    def __init__(self, url: str, filters: list[dict], cb: Callback, timeout: float) -> None:
        self.url, self.filters, self.cb, self.timeout = url, filters, cb, timeout
        self.sub_id = f"amt-{next(_sub_ids)}"
        self._stop = threading.Event()
        self._ws: Any = None
        self._seen: set[str] = set()
        self._thread = threading.Thread(target=self._loop, name=f"nostr-sub-{self.sub_id}", daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        backoff = 0.2
        while not self._stop.is_set():
            try:
                self._ws = _ws_connect(self.url, self.timeout)
                self._ws.send(json.dumps(["REQ", self.sub_id, *self.filters]))
                backoff = 0.2
                while not self._stop.is_set():
                    try:
                        raw = self._ws.recv(timeout=0.2)
                    except TimeoutError:
                        continue
                    msg = json.loads(raw)
                    if msg[:2] == ["EVENT", self.sub_id] and isinstance(msg[2], dict):
                        eid = msg[2].get("id")
                        if eid not in self._seen:  # history is re-sent after a reconnect
                            self._seen.add(eid)
                            self.cb(msg[2])
            except Exception:  # noqa: BLE001 - drop, back off, reconnect
                if self._stop.wait(backoff):
                    break
                backoff = min(backoff * 2, 5.0)
            finally:
                ws, self._ws = self._ws, None
                if ws is not None:
                    try:
                        ws.close()
                    except Exception:  # noqa: BLE001,S110
                        pass

    def close(self) -> None:
        self._stop.set()
        ws = self._ws
        if ws is not None:
            try:
                ws.send(json.dumps(["CLOSE", self.sub_id]))
            except Exception:  # noqa: BLE001,S110
                pass
        self._thread.join(timeout=2)


class WebSocketRelay:
    def __init__(self, url: str, *, timeout: float = 5.0) -> None:
        self.url = url
        self.timeout = timeout
        self._ws: Any = None
        self._lock = threading.Lock()
        self._subs: list[_WsSubscription] = []

    def _drop(self) -> None:
        ws, self._ws = self._ws, None
        if ws is not None:
            try:
                ws.close()
            except Exception:  # noqa: BLE001,S110
                pass

    def _roundtrip(self, payload: list, until: Callable[[list], Any]) -> Any:
        """Send `payload` and read messages until `until(msg)` returns non-None.
        One reconnect attempt on a dropped connection."""
        with self._lock:
            for attempt in (0, 1):
                if self._ws is None:
                    self._ws = _ws_connect(self.url, self.timeout)
                try:
                    self._ws.send(json.dumps(payload))
                    deadline = time.monotonic() + self.timeout
                    while True:
                        left = deadline - time.monotonic()
                        if left <= 0:
                            raise TimeoutError
                        msg = json.loads(self._ws.recv(timeout=left))
                        out = until(msg)
                        if out is not None:
                            return out
                except TimeoutError:
                    self._drop()
                    raise RelayError(f"{self.url}: timeout waiting for relay") from None
                except RelayError:
                    raise
                except Exception as exc:
                    self._drop()
                    if attempt == 1:
                        raise RelayError(f"{self.url}: connection lost ({type(exc).__name__})") from exc
        raise RelayError(f"{self.url}: unreachable")  # pragma: no cover

    def publish(self, ev: NostrEvent) -> PublishResult:
        return self.publish_raw(ev.to_dict())

    def publish_raw(self, d: dict) -> PublishResult:
        eid = d.get("id", "")

        def ok(msg: list):
            if len(msg) >= 3 and msg[0] == "OK" and msg[1] == eid:
                return PublishResult(bool(msg[2]), eid, str(msg[3]) if len(msg) > 3 else "", self.url)
            return None

        return self._roundtrip(["EVENT", d], ok)

    def query(self, filters: list[dict]) -> list[dict]:
        sub_id = f"amt-q{next(_sub_ids)}"
        got: list[dict] = []

        def collect(msg: list):
            if msg[:2] == ["EVENT", sub_id] and len(msg) > 2 and isinstance(msg[2], dict):
                got.append(msg[2])
            elif msg[:2] in (["EOSE", sub_id], ["CLOSED", sub_id]):
                return True
            return None

        self._roundtrip(["REQ", sub_id, *filters], collect)
        try:
            with self._lock:
                if self._ws is not None:
                    self._ws.send(json.dumps(["CLOSE", sub_id]))
        except Exception:  # noqa: BLE001
            self._drop()
        return got

    def subscribe(self, filters: list[dict], callback: Callback) -> _WsSubscription:
        sub = _WsSubscription(self.url, filters, callback, self.timeout)
        self._subs.append(sub)
        return sub

    def close(self) -> None:
        for s in self._subs:
            s.close()
        self._subs.clear()
        with self._lock:
            self._drop()


# ----------------------------------------------------------------------------
# Fan-out
# ----------------------------------------------------------------------------


class _MultiSub:
    def __init__(self, subs: list[Any]) -> None:
        self.subs = subs

    def close(self) -> None:
        for s in self.subs:
            s.close()


class MultiRelay:
    def __init__(self, relays: list[Any]) -> None:
        self.relays = list(relays)
        self.url = ",".join(r.url for r in self.relays)

    def publish(self, ev: NostrEvent) -> PublishResult:
        results: list[PublishResult] = []
        errors: list[str] = []
        for r in self.relays:
            try:
                results.append(r.publish(ev))
            except RelayError as exc:
                errors.append(str(exc))
        accepted = [r for r in results if r.ok]
        if accepted:
            return PublishResult(True, ev.id, "; ".join(r.relay for r in accepted), self.url)
        msg = "; ".join([r.message for r in results] + errors) or "no relays configured"
        return PublishResult(False, ev.id, msg, self.url)

    def query(self, filters: list[dict]) -> list[dict]:
        found: dict[str, dict] = {}
        errors: list[str] = []
        for r in self.relays:
            try:
                for d in r.query(filters):
                    found.setdefault(str(d.get("id")), d)
            except RelayError as exc:
                errors.append(str(exc))
        if errors and len(errors) == len(self.relays):
            raise RelayError("; ".join(errors))
        return _apply_limit(list(found.values()), filters)

    def subscribe(self, filters: list[dict], callback: Callback) -> _MultiSub:
        seen: set[str] = set()
        lock = threading.Lock()

        def dedup(d: dict) -> None:
            with lock:
                if d.get("id") in seen:
                    return
                seen.add(d.get("id"))
            callback(d)

        return _MultiSub([r.subscribe(filters, dedup) for r in self.relays])

    def close(self) -> None:
        for r in self.relays:
            r.close()
