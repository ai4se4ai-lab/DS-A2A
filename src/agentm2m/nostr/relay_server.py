"""A minimal NIP-01 relay for local development and offline tests.

    agentm2m nostr serve --port 7777      # then relays: [ws://127.0.0.1:7777]

Backed by `MemoryRelay` (verifies signatures, deduplicates, fans out), so it
behaves like a real relay for AgentM2M's purposes without any external
service. Not meant for production: no persistence, no rate limits.
"""
from __future__ import annotations

import json
import threading
from typing import Any

from .errors import NostrUnavailable
from .relay import MemoryRelay


class DevRelayServer:
    def __init__(self, host: str = "127.0.0.1", port: int = 7777, backend: MemoryRelay | None = None) -> None:
        self.host = host
        self.port = port
        self.backend = backend or MemoryRelay(f"ws://{host}:{port}")
        self._server: Any = None
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        return f"ws://{self.host}:{self.port}"

    def _handler(self, conn: Any) -> None:
        subs: dict[str, Any] = {}
        try:
            for raw in conn:
                try:
                    msg = json.loads(raw)
                except ValueError:
                    conn.send(json.dumps(["NOTICE", "invalid: not JSON"]))
                    continue
                if not isinstance(msg, list) or not msg:
                    continue
                if msg[0] == "EVENT" and len(msg) > 1:
                    res = self.backend.publish_raw(msg[1] if isinstance(msg[1], dict) else {})
                    conn.send(json.dumps(["OK", res.event_id, res.ok, res.message]))
                elif msg[0] == "REQ" and len(msg) > 1:
                    sub_id, filters = str(msg[1]), [f for f in msg[2:] if isinstance(f, dict)]
                    if sub_id in subs:
                        subs.pop(sub_id).close()

                    def push(d: dict, sid: str = sub_id) -> None:
                        try:
                            conn.send(json.dumps(["EVENT", sid, d]))
                        except Exception:  # noqa: BLE001,S110 - client went away
                            pass

                    subs[sub_id] = self.backend.subscribe(filters, push)
                    conn.send(json.dumps(["EOSE", sub_id]))
                elif msg[0] == "CLOSE" and len(msg) > 1:
                    sub = subs.pop(str(msg[1]), None)
                    if sub is not None:
                        sub.close()
        except Exception:  # noqa: BLE001,S110 - connection closed
            pass
        finally:
            for s in subs.values():
                s.close()

    def start(self) -> DevRelayServer:
        try:
            from websockets.sync.server import serve
        except ImportError as exc:  # pragma: no cover
            raise NostrUnavailable("the dev relay needs: pip install 'agentm2m[nostr]'") from exc
        self._server = serve(self._handler, self.host, self.port)
        self.port = self._server.socket.getsockname()[1]
        self._thread = threading.Thread(target=self._server.serve_forever, name="nostr-dev-relay", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def serve_forever(self) -> None:
        self.start()
        assert self._thread is not None
        try:
            self._thread.join()
        except KeyboardInterrupt:
            self.stop()
