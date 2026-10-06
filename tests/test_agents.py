import pytest

from agentflow import scenario as scenario_mod
from agentflow.agents.planner import make_plan
from agentflow.agents.requirements import build_requirements
from agentflow.models import AgentError


def reqs(name, answers=None):
    return build_requirements(scenario_mod.load(name)["requirement"], answers or {}, "t")[0]


def caps(req):
    return {c["id"] for c in req["capabilities"]}


def test_well_defined_requirement_has_no_open_questions():
    req = reqs("greenfield")
    assert req["questions"] == []
    assert {"link_creation", "redirect", "analytics", "url_safety", "rate_limiting"} <= caps(req)
    assert all(c["acceptance_criteria"] for c in req["capabilities"])


def test_brownfield_ticket_mentions_are_not_new_capabilities():
    assert caps(reqs("brownfield")) == {"url_safety", "click_accuracy", "expiry"}


def test_ambiguous_requirement_raises_questions_and_assumes_only_non_blocking():
    req = reqs("ambiguous")
    assert {q["id"] for q in req["questions"]} == {"Q-PRIVACY", "Q-PERF", "Q-SAFETY"}
    assert req["blocking_open"] == ["Q-PRIVACY"]
    assert len(req["assumptions"]) == 2
    assert {"redirect_cache", "domain_denylist"} <= caps(req)
    assert "unique_visitors" not in caps(req)


def test_answers_change_capabilities():
    hashed = reqs("ambiguous", {"Q-PRIVACY": {"option": "hashed", "by": "x"}})
    none = reqs("ambiguous", {"Q-PRIVACY": {"option": "none", "by": "x"}})
    assert "unique_visitors" in caps(hashed) and hashed["blocking_open"] == []
    assert "unique_visitors" not in caps(none)
    with pytest.raises(AgentError):
        reqs("ambiguous", {"Q-PRIVACY": {"option": "maybe", "by": "x"}})


def test_planner_serialises_overlapping_writes_and_gates_open_questions():
    existing = {c: {"modules": ["shortener.service"]} for c in ("analytics", "redirect", "url_safety")}
    impact = {"impacted": existing, "import_graph": {}, "test_map": {}}
    plan, decisions = make_plan("shortener_v2", reqs("ambiguous"), impact)
    assert set(plan["satisfied_by_existing"]) == {"analytics", "redirect", "url_safety"}
    by_id = {t["id"]: t for t in plan["tasks"]}
    assert "perf.redirect_cache" in by_id["safety.domain_denylist"]["deps"]
    assert by_id["clarify.Q-PRIVACY"]["gate"] == "clarified:Q-PRIVACY"
    assert any("Serialise" in d.title for d in decisions)


def test_planner_refuses_uncovered_capabilities():
    req = reqs("greenfield")
    req["capabilities"].append({"id": "click_accuracy", "source": "x", "acceptance_criteria": ["x"]})
    with pytest.raises(AgentError, match="no task covers"):
        make_plan("shortener", req, None)


class ScriptedLLM:
    """A model that over-extracts: it adds 'analytics' for a click-count bug report."""

    async def structured(self, system, prompt, schema):
        return schema(
            capabilities=["url_safety", "click_accuracy", "expiry", "analytics"],
            evidence={},
            ambiguities=[],
        ), 10


def test_model_capabilities_follow_the_same_refinement_rules(tmp_path):
    import asyncio

    from agentflow.agents.base import AgentContext
    from agentflow.agents.requirements import LLMRequirementsAgent
    from agentflow.context import ContextStore
    from agentflow.models import Node
    from agentflow.workspace import Workspace

    store = ContextStore()
    store.put("requirement_text", scenario_mod.load("brownfield")["requirement"], "scenario")
    ctx = AgentContext(
        Node("requirements", "stage", "requirements.llm", "r"),
        store,
        Workspace(tmp_path),
        {"title": "t", "playbook": "linkly"},
        1,
        tmp_path,
        asyncio.Lock(),
        llm=ScriptedLLM(),
    )
    result = asyncio.run(LLMRequirementsAgent().run(ctx))
    assert caps(result.artifacts["requirements"]) == {"url_safety", "click_accuracy", "expiry"}
    assert any("dropped ['analytics']" in n for n in result.notes)
