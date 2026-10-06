"""Architecture/design: components, endpoints and ADRs for the requested capabilities."""

from __future__ import annotations

from .. import knowledge
from ..models import AgentResult, Decision
from .base import Agent, AgentContext


class DesignAgent(Agent):
    name = "design"

    async def run(self, ctx: AgentContext) -> AgentResult:
        req = ctx.store.get("requirements")
        impact = ctx.store.get("impact_analysis")
        components: dict[str, list[str]] = {}
        endpoints: set[str] = set()
        adrs = []
        decisions = []
        for cap in req["capabilities"]:
            spec = knowledge.DESIGN.get(cap["id"], {"components": [], "endpoints": [], "adr": None})
            components[cap["id"]] = spec["components"]
            endpoints.update(spec["endpoints"])
            if spec["adr"]:
                title, why, alternatives = spec["adr"]
                adrs.append(
                    {
                        "id": f"ADR-{len(adrs) + 1:02d}",
                        "capability": cap["id"],
                        "title": title,
                        "rationale": why,
                        "alternatives": alternatives,
                    }
                )
                decisions.append(Decision(title=title, rationale=why, alternatives=alternatives))
        design = {
            "style": "modular monolith: api -> service -> repository -> db, pure helpers below",
            "components": components,
            "endpoints": sorted(endpoints),
            "adrs": adrs,
            "open_questions": req["open_questions"],
        }
        if impact:
            design["change_surface"] = impact["impacted"]
            design["compatibility"] = (
                "Existing routes and response fields are preserved; new request fields are "
                "optional; the schema change is additive and nullable."
            )
            design["risks_from_analysis"] = impact["findings"]
        return AgentResult(artifacts={"design": design}, decisions=decisions)
