"""Durable observability outbox: `.agentm2m/state/observability/outbox.jsonl`.

Every signed event is appended here *before* the first publish attempt, so
a relay outage, a timeout or a crash between publish and acknowledgement
loses nothing: the next `flush` (or a later process) retries it. Relays
deduplicate by event id, so a retry after an unacknowledged success is
harmless -- delivery is at-least-once, never exactly-once.

Records are an append-only log of `add` / `ack` / `fail` operations, replayed
incrementally and compacted to the pending set on `compact()`; a file lock
makes concurrent processes safe.
"""
from __future__ import annotations

import contextlib
import json
import os
import time
from collections.abc import Callable, Iterator
from pathlib import Path


class Outbox:
    def __init__(
        self,
        path: str | Path,
        *,
        clock: Callable[[], float] = time.time,
        base_backoff: float = 1.0,
        max_backoff: float = 300.0,
    ) -> None:
        self.path = Path(path)
        self.clock = clock
        self.base_backoff = base_backoff
        self.max_backoff = max_backoff
        self._records: dict[str, dict] = {}
        self._offset = 0
        self._inode: int | None = None

    # -- file plumbing ---------------------------------------------------

    @contextlib.contextmanager
    def _locked(self) -> Iterator[None]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path.with_suffix(".lock"), "a+") as fh:
            try:
                import fcntl

                fcntl.flock(fh, fcntl.LOCK_EX)
            except (ImportError, OSError):  # pragma: no cover - non-POSIX
                pass
            try:
                self._sync()
                yield
            finally:
                try:
                    import fcntl

                    fcntl.flock(fh, fcntl.LOCK_UN)
                except (ImportError, OSError):  # pragma: no cover
                    pass

    def _sync(self) -> None:
        """Replay whatever other writers appended since we last looked."""
        if not self.path.exists():
            self._records, self._offset, self._inode = {}, 0, None
            return
        st = self.path.stat()
        if st.st_ino != self._inode or st.st_size < self._offset:  # compacted/replaced: full replay
            self._records, self._offset, self._inode = {}, 0, st.st_ino
        with open(self.path, "rb") as fh:
            fh.seek(self._offset)
            data = fh.read()
        end = data.rfind(b"\n")
        if end < 0:
            return  # nothing new, or a line still being written
        self._offset += end + 1
        for line in data[: end + 1].decode("utf-8").splitlines():
            if line.strip():
                try:
                    self._apply(json.loads(line))
                except ValueError:
                    continue

    def _apply(self, op: dict) -> None:
        eid = op.get("id")
        kind = op.get("op")
        if kind == "add":
            self._records.setdefault(eid, {"id": eid, "event": op["event"], "attempts": 0, "next_try": 0.0,
                                           "last_error": None})
        elif kind == "ack":
            self._records.pop(eid, None)
        elif kind == "fail" and eid in self._records:
            r = self._records[eid]
            r["attempts"], r["next_try"], r["last_error"] = op["attempts"], op["next_try"], op.get("error")

    def _append(self, op: dict) -> None:
        line = json.dumps(op, sort_keys=True, separators=(",", ":")) + "\n"
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(line)
        self._apply(op)
        self._offset = self.path.stat().st_size
        self._inode = self.path.stat().st_ino

    # -- operations ------------------------------------------------------

    def add(self, event: dict) -> None:
        with self._locked():
            if event["id"] not in self._records:
                self._append({"op": "add", "id": event["id"], "event": event})

    def ack(self, event_id: str) -> None:
        with self._locked():
            if event_id in self._records:
                self._append({"op": "ack", "id": event_id})

    def fail(self, event_id: str, error: str) -> None:
        with self._locked():
            r = self._records.get(event_id)
            if r is None:
                return
            attempts = r["attempts"] + 1
            delay = min(self.base_backoff * 2 ** (attempts - 1), self.max_backoff)
            self._append({"op": "fail", "id": event_id, "attempts": attempts, "next_try": self.clock() + delay,
                          "error": error[:300]})

    def pending(self) -> list[dict]:
        with self._locked():
            return sorted(self._records.values(), key=lambda r: (r["event"].get("created_at", 0), r["id"]))

    def due(self, *, force: bool = False) -> list[dict]:
        now = self.clock()
        return [r for r in self.pending() if force or r["next_try"] <= now]

    def depth(self) -> int:
        with self._locked():
            return len(self._records)

    def compact(self) -> None:
        """Rewrite the log as just the pending records (atomic replace)."""
        with self._locked():
            tmp = self.path.with_suffix(".jsonl.tmp")
            with open(tmp, "w", encoding="utf-8") as fh:
                for r in self._records.values():
                    fh.write(json.dumps({"op": "add", "id": r["id"], "event": r["event"]}, sort_keys=True,
                                        separators=(",", ":")) + "\n")
                    if r["attempts"]:
                        fh.write(json.dumps({"op": "fail", "id": r["id"], "attempts": r["attempts"],
                                             "next_try": r["next_try"], "error": r["last_error"]},
                                            sort_keys=True, separators=(",", ":")) + "\n")
            os.replace(tmp, self.path)
            self._inode = None
            self._sync()
