import pytest

from agentflow.models import ChangeSet, FileOp
from agentflow.workspace import Workspace, WorkspaceError


@pytest.fixture
def ws(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\ny = 2\n")
    return Workspace(tmp_path)


def test_apply_and_rollback_restore_exact_state(ws):
    cs = ChangeSet(
        "t",
        "s",
        [
            FileOp("edit", "a.py", old="x = 1\n", new="x = 10\n"),
            FileOp("write", "pkg/new.py", "z = 3\n"),
            FileOp("delete", "a.py"),
        ],
    )
    undo, diff = ws.apply(cs)
    assert not ws.exists("a.py") and ws.read("pkg/new.py") == "z = 3\n"
    assert "+z = 3" in diff
    ws.rollback(undo)
    assert ws.read("a.py") == "x = 1\ny = 2\n"
    assert not ws.exists("pkg/new.py")


def test_edit_anchor_must_match_exactly_once_and_nothing_is_written(ws):
    cs = ChangeSet(
        "t", "s", [FileOp("write", "b.py", "b\n"), FileOp("edit", "a.py", old="not there", new="?")]
    )
    with pytest.raises(WorkspaceError, match="matched 0 times"):
        ws.apply(cs)
    assert not ws.exists("b.py")  # validated before any write: all-or-nothing


@pytest.mark.parametrize("path", ["../escape.py", "/etc/passwd", "a/../../x"])
def test_paths_cannot_escape_the_workspace(ws, path):
    with pytest.raises(WorkspaceError, match="escapes"):
        ws.apply(ChangeSet("t", "s", [FileOp("write", path, "x")]))
