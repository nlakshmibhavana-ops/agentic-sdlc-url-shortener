import json

from agentflow.audit import AuditLog, verify
from agentflow.context import ContextStore


def test_audit_chain_detects_modification_and_deletion(tmp_path):
    path = tmp_path / "audit.jsonl"
    log = AuditLog(path, "run")
    for i in range(5):
        log.record("event", n=i)
    assert verify(path)[0]

    lines = path.read_text().splitlines()
    record = json.loads(lines[2])
    record["data"]["n"] = 99
    path.write_text("\n".join([*lines[:2], json.dumps(record), *lines[3:]]) + "\n")
    ok, _, msg = verify(path)
    assert not ok and "modified" in msg

    path.write_text("\n".join([*lines[:2], *lines[3:]]) + "\n")
    ok, _, msg = verify(path)
    assert not ok and "chain broken" in msg


def test_audit_log_continues_chain_across_sessions(tmp_path):
    path = tmp_path / "audit.jsonl"
    AuditLog(path, "run").record("a")
    AuditLog(path, "run").record("b")
    assert verify(path) == (True, 2, "2 records, chain intact")


def test_context_versions_only_on_change_and_tracks_lineage():
    store = ContextStore()
    store.put("text", "v1", "scenario")
    req, changed = store.put("req", {"a": 1}, "requirements", {"text": 1})
    assert changed and req.version == 1
    _, changed = store.put("req", {"a": 1}, "requirements", {"text": 1})
    assert not changed
    store.put("plan", {"t": []}, "plan", {"req": 1})
    assert [(x["artifact"], x["producer"]) for x in store.lineage("plan")] == [
        ("plan", "plan"),
        ("req", "requirements"),
        ("text", "scenario"),
    ]
    restored = ContextStore.from_dict(store.to_dict())
    assert restored.get("plan") == {"t": []}
