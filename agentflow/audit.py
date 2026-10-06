"""Append-only, hash-chained audit trail with trace/span ids.

Each record embeds the previous record's hash, so editing or deleting any line breaks
verification from that point on. One trace per run; one span per node attempt.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any

GENESIS = "0" * 64


def _digest(record: dict[str, Any]) -> str:
    body = json.dumps(record, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(body.encode()).hexdigest()


class AuditLog:
    def __init__(self, path: Path, run_id: str) -> None:
        self.path = path
        self.trace_id = run_id
        self._lock = threading.Lock()
        self._prev = GENESIS
        self._seq = 0
        self.listener = None  # optional callable(record) for live progress output
        if path.exists():
            for line in path.read_text().splitlines():
                if line.strip():
                    record = json.loads(line)
                    self._prev, self._seq = record["hash"], record["seq"]

    def record(
        self,
        event: str,
        *,
        actor: str = "orchestrator",
        node: str | None = None,
        span_id: str | None = None,
        **data: Any,
    ) -> dict[str, Any]:
        with self._lock:
            self._seq += 1
            record = {
                "seq": self._seq,
                "ts": time.time(),
                "trace_id": self.trace_id,
                "span_id": span_id,
                "event": event,
                "actor": actor,
                "node": node,
                "data": data,
                "prev_hash": self._prev,
            }
            record["hash"] = _digest(record)
            self._prev = record["hash"]
            with self.path.open("a") as fh:
                fh.write(json.dumps(record, default=str) + "\n")
        if self.listener is not None:
            self.listener(record)
        return record

    @contextmanager
    def span(self, name: str, node: str, **data: Any):
        span_id = uuid.uuid4().hex[:16]
        started = time.perf_counter()
        self.record(f"{name}.start", node=node, span_id=span_id, **data)
        outcome: dict[str, Any] = {"status": "ok"}
        try:
            yield span_id, outcome
        except BaseException as exc:
            outcome.update(status="error", error=repr(exc))
            raise
        finally:
            self.record(
                f"{name}.end",
                node=node,
                span_id=span_id,
                duration_ms=round((time.perf_counter() - started) * 1000, 1),
                **outcome,
            )


def verify(path: Path) -> tuple[bool, int, str]:
    """Returns (ok, records_checked, message)."""
    prev = GENESIS
    count = 0
    for count, line in enumerate(path.read_text().splitlines(), start=1):
        record = json.loads(line)
        claimed = record.pop("hash")
        if record["prev_hash"] != prev:
            return False, count, f"record {count}: chain broken (prev_hash mismatch)"
        if _digest(record) != claimed:
            return False, count, f"record {count}: content was modified"
        prev = claimed
    return True, count, f"{count} records, chain intact"


def read(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
