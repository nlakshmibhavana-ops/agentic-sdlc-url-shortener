"""Human approval checkpoints.

An approval is bound to the exact subject it was given for (a change set digest, a
release candidate digest). If an agent later produces something different, the old
approval does not carry over and a new one is requested.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

from .audit import AuditLog

APPROVED, REJECTED, PENDING = "approved", "rejected", "pending"
RESERVED_ACTORS = {"agent", "orchestrator", "system", ""}


class ApprovalBroker:
    def __init__(self, path: Path, audit: AuditLog, mode: str = "pause") -> None:
        self.path = path
        self.audit = audit
        self.mode = mode  # "interactive" prompts on a TTY; "pause" stops and waits for the CLI
        self.data: dict[str, Any] = (
            json.loads(path.read_text()) if path.exists() else {"requests": {}, "decisions": {}}
        )

    def _save(self) -> None:
        self.path.write_text(json.dumps(self.data, indent=2))

    def check(self, node: str, subject: str, summary: str, reasons: list[str]) -> str:
        key = f"{node}@{subject}"
        decision = self.data["decisions"].get(key)
        if decision:
            return decision["status"]
        if key not in self.data["requests"]:
            self.data["requests"][key] = {
                "node": node,
                "subject": subject,
                "summary": summary,
                "reasons": reasons,
                "requested_at": time.time(),
            }
            self._save()
            self.audit.record("approval.requested", node=node, subject=subject, reasons=reasons)
        if self.mode == "interactive" and sys.stdin.isatty():
            return self._prompt(node, subject, summary, reasons)
        return PENDING

    def _prompt(self, node: str, subject: str, summary: str, reasons: list[str]) -> str:
        print(f"\n=== APPROVAL REQUIRED: {node} ===\n{summary}")
        for reason in reasons:
            print(f"  - {reason}")
        answer = input("Approve? [y/N] ").strip().lower()
        who = input("Your name: ").strip() or "unknown"
        comment = input("Comment (optional): ").strip()
        self.decide(node, answer == "y", who, comment, subject=subject)
        return APPROVED if answer == "y" else REJECTED

    def pending(self) -> list[dict[str, Any]]:
        return [
            {"key": key, **req}
            for key, req in self.data["requests"].items()
            if key not in self.data["decisions"]
        ]

    def decide(
        self, node: str, approve: bool, by: str, comment: str = "", subject: str | None = None
    ) -> dict[str, Any]:
        if by.strip().lower() in RESERVED_ACTORS:
            raise ValueError("approvals must name a human approver")
        keys = [
            k
            for k, req in self.data["requests"].items()
            if req["node"] == node
            and (subject is None or req["subject"] == subject)
            and k not in self.data["decisions"]
        ]
        if not keys:
            raise KeyError(f"no pending approval request for node '{node}'")
        key = keys[-1]
        decision = {
            "status": APPROVED if approve else REJECTED,
            "by": by,
            "comment": comment,
            "decided_at": time.time(),
            "waited_s": round(time.time() - self.data["requests"][key]["requested_at"], 1),
        }
        self.data["decisions"][key] = decision
        self._save()
        self.audit.record(
            "approval.decided",
            actor=f"human:{by}",
            node=node,
            subject=self.data["requests"][key]["subject"],
            **decision,
        )
        return decision
