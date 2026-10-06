"""Requirement understanding: normalise prose into capabilities with acceptance criteria,
and surface ambiguity as explicit questions instead of silently guessing."""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field

from .. import knowledge
from ..models import AgentError, AgentResult, Decision
from .base import Agent, AgentContext

_SENTENCE = re.compile(r"(?<=[.!?\n])\s+")


def _sentences(text: str) -> list[str]:
    return [s.strip(" -*\t") for s in _SENTENCE.split(text) if s.strip(" -*\t")]


def _match_capabilities(text: str) -> dict[str, str]:
    """capability -> the sentence that evidences it (traceability back to the request)."""
    found: dict[str, str] = {}
    for sentence in _sentences(text):
        lower = sentence.lower()
        here = {
            cap for cap, spec in knowledge.CAPABILITIES.items() if any(k in lower for k in spec["keywords"])
        }
        for specific, generic in knowledge.REFINES.items():
            if specific in here:
                here -= set(generic)
        for cap in sorted(here):
            found.setdefault(cap, sentence)
    return found


def _questions(text: str, caps: dict[str, str]) -> list[dict[str, Any]]:
    out = []
    lower_all = text.lower()
    for q in knowledge.QUESTIONS:
        needed = q.get("requires_capability")
        if needed and needed not in caps:
            continue
        if any(u in lower_all for u in q.get("unless", [])):
            continue
        evidence = [
            s
            for s in _sentences(text)
            if any(re.search(rf"\b{re.escape(t)}\b", s.lower()) for t in q["triggers"])
            and not re.search(r"\d", s)  # a number in the sentence = a measurable target
        ]
        if evidence:
            out.append(
                {
                    "id": q["id"],
                    "text": q["text"],
                    "blocking": q["blocking"],
                    "evidence": evidence[0],
                    "default": q["default"],
                    "options": {k: v["label"] for k, v in q["options"].items()},
                }
            )
    return out


def build_requirements(text: str, answers: dict[str, Any], title: str) -> tuple[dict, list[Decision]]:
    caps = _match_capabilities(text)
    if not caps:
        raise AgentError("no recognisable capability in the requirement")
    questions = _questions(text, caps)
    decisions: list[Decision] = []
    assumptions: list[str] = []
    out_of_scope: list[str] = []
    resolved: dict[str, dict] = {}
    for q in questions:
        spec = next(x for x in knowledge.QUESTIONS if x["id"] == q["id"])
        answer = answers.get(q["id"])
        if answer:
            option, source = answer["option"], f"human:{answer['by']}"
        elif not q["blocking"]:
            option, source = q["default"], "assumption"
            assumptions.append(
                f"{q['id']}: assumed '{spec['options'][option]['label']}' (non-blocking; confirm or override)"
            )
        else:
            continue  # stays open; the planner gates dependent work on it
        if option not in spec["options"]:
            raise AgentError(f"answer '{option}' is not an option for {q['id']}")
        chosen = spec["options"][option]
        resolved[q["id"]] = {"option": option, "source": source}
        for cap in chosen["adds"]:
            caps.setdefault(cap, f"{q['id']} -> {option}")
        if chosen.get("out_of_scope"):
            out_of_scope.append(chosen["out_of_scope"])
        decisions.append(
            Decision(
                title=f"{q['id']} resolved as '{option}'",
                rationale=chosen["label"],
                alternatives=[k for k in spec["options"] if k != option],
                decided_by=source,
            )
        )
    if "performance" in caps and "redirect_cache" not in caps:
        caps.pop("performance")  # vague without a target; only kept once a target is agreed
    open_questions = [q for q in questions if q["id"] not in resolved]
    requirements = {
        "title": title,
        "capabilities": [
            {"id": cap, "source": src, "acceptance_criteria": knowledge.CAPABILITIES[cap]["criteria"]}
            for cap, src in sorted(caps.items())
        ],
        "questions": questions,
        "resolved": resolved,
        "open_questions": [q["id"] for q in open_questions],
        "blocking_open": [q["id"] for q in open_questions if q["blocking"]],
        "assumptions": assumptions,
        "out_of_scope": out_of_scope,
    }
    return requirements, decisions


class RequirementsAgent(Agent):
    name = "requirements"

    async def run(self, ctx: AgentContext) -> AgentResult:
        text = ctx.store.get("requirement_text")
        answers = ctx.store.get("human_answers", {}) or {}
        req, decisions = build_requirements(text, answers, ctx.scenario["title"])
        notes = [
            f"{len(req['capabilities'])} capabilities, {len(req['questions'])} ambiguities, "
            f"{len(req['blocking_open'])} blocking"
        ]
        return AgentResult(artifacts={"requirements": req}, decisions=decisions, notes=notes)


class _LLMRequirements(BaseModel):
    capabilities: list[str] = Field(description="Capability ids from the allowed vocabulary")
    evidence: dict[str, str] = Field(description="capability id -> sentence from the request")
    ambiguities: list[str] = Field(description="Vague or untestable phrases worth a question")


class LLMRequirementsAgent(Agent):
    """Model-based capability extraction, validated against the known vocabulary. Questions
    and their options still come from the governed catalogue, so a human always chooses
    from options the downstream playbooks and policies understand."""

    name = "requirements.llm"

    async def run(self, ctx: AgentContext) -> AgentResult:
        if ctx.llm is None:
            raise AgentError("no LLM provider configured")
        text = ctx.store.get("requirement_text")
        vocab = {k: v["criteria"] for k, v in knowledge.CAPABILITIES.items()}
        parsed, tokens = await ctx.llm.structured(
            system="You are a requirements analyst. Map the request onto the capability "
            "vocabulary. Only use ids that appear in the vocabulary.",
            prompt=f"Vocabulary (id -> acceptance criteria):\n{vocab}\n\nRequest:\n{text}",
            schema=_LLMRequirements,
        )
        unknown = sorted(set(parsed.capabilities) - set(vocab))
        if unknown:
            raise AgentError(f"model proposed unknown capabilities {unknown}")
        answers = ctx.store.get("human_answers", {}) or {}
        req, decisions = build_requirements(text, answers, ctx.scenario["title"])
        have = {c["id"] for c in req["capabilities"]}
        everything = have | set(parsed.capabilities)
        # The same refinement rules as the deterministic path: a specific capability in the
        # request overrides the generic one it mentions in passing.
        refined = {g for s_, gs in knowledge.REFINES.items() if s_ in everything for g in gs}
        dropped = sorted((set(parsed.capabilities) - have) & refined)
        for cap in parsed.capabilities:
            if cap not in have and cap not in refined:
                req["capabilities"].append(
                    {
                        "id": cap,
                        "source": parsed.evidence.get(cap, "model"),
                        "acceptance_criteria": knowledge.CAPABILITIES[cap]["criteria"],
                    }
                )
        req["model_ambiguities"] = parsed.ambiguities
        notes = [f"model suggested {sorted(parsed.capabilities)}"]
        if dropped:
            notes.append(f"dropped {dropped}: refined by a more specific capability in the request")
        return AgentResult(artifacts={"requirements": req}, decisions=decisions, tokens=tokens, notes=notes)
