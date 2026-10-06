import pytest

from agentflow.models import ChangeSet, FileOp, Node
from agentflow.policy import PolicyEngine


def evaluate(path, after, before=None, writes=None, baseline=(), config=None):
    node = Node("t", "task", "implement", "t", writes=writes or [path])
    cs = ChangeSet("t", "s", [FileOp("write", path, after)])
    result = PolicyEngine(config, set(baseline)).evaluate(node, cs, {path: (before, after)})
    return {(f.rule, f.severity) for f in result.findings}


@pytest.mark.parametrize(
    "code,rule",
    [
        ('token = "AKIAABCDEFGHIJKLMNOP"\n', "SEC-001"),
        ('password = "hunter2hunter2"\n', "SEC-001"),
        ("eval(user_input)\n", "SEC-002"),
        ("import os\nos.system(cmd)\n", "SEC-002"),
        ("import subprocess\nsubprocess.run(cmd, shell=True)\n", "SEC-002"),
        ('cur.execute(f"SELECT * FROM t WHERE id = {x}")\n', "SEC-002"),
        ("def broken(:\n", "SEC-000"),
    ],
)
def test_security_findings_block(code, rule):
    assert (rule, "block") in evaluate("m.py", code)


def test_placeholder_credentials_in_fixtures_are_allowed():
    assert evaluate("tests/conftest.py", 'API_KEY = "test-key-1"\n') == set()


def test_writes_outside_declared_scope_are_blocked():
    assert ("CHG-003", "block") in evaluate("other.py", "x = 1\n", writes=["mine.py"])


def test_forbidden_paths_are_blocked():
    assert ("CHG-001", "block") in evaluate(".env", "A=1\n")


def test_schema_change_needs_approval_only_against_existing_code():
    before = 'SQL = "CREATE TABLE t (a TEXT)"\n'
    after = 'SQL = "CREATE TABLE t (a TEXT, b TEXT)"\n'
    assert ("DATA-001", "approve") in evaluate("db.py", after, before, baseline={"db.py"})
    assert ("DATA-001", "approve") not in evaluate("db.py", after, before, baseline=set())


def test_schema_change_inside_multiline_literal_is_detected():
    before = 'SQL = ("CREATE TABLE t ("\n       " a TEXT)")\n'
    after = 'SQL = ("CREATE TABLE t ("\n       " a TEXT,"\n       " b TEXT)")\n'
    assert ("DATA-001", "approve") in evaluate("db.py", after, before, baseline={"db.py"})


def test_raw_personal_data_columns_are_blocked():
    assert ("PII-001", "block") in evaluate("db.py", 'SQL = "ALTER TABLE c ADD COLUMN client_ip TEXT"\n')


def test_dependencies_must_be_allowlisted():
    before = '[project]\nname="x"\nversion="1"\ndependencies=["fastapi"]\n'
    bad = before.replace('["fastapi"]', '["fastapi", "leftpad"]')
    ok = before.replace('["fastapi"]', '["fastapi", "httpx"]')
    assert ("DEP-001", "block") in evaluate("pyproject.toml", bad, before, baseline={"pyproject.toml"})
    findings = evaluate("pyproject.toml", ok, before, baseline={"pyproject.toml"})
    assert ("DEP-002", "approve") in findings and ("CHG-002", "approve") in findings


def test_large_diffs_need_approval_but_generated_files_do_not_count():
    big = "\n".join(f"x{i} = {i}" for i in range(700)) + "\n"
    assert ("CHG-004", "approve") in evaluate("m.py", big)
    assert ("CHG-004", "approve") not in evaluate("openapi.json", big)
