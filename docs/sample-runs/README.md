# Sample runs

Real outputs from one deterministic run of each scenario (`scripts/refresh_samples.sh`
regenerates them). The human steps were played by `scripts/demo.sh` as "reviewer".

| Scenario | Start here | What to look for |
|---|---|---|
| [greenfield](greenfield/report.md) | `report.md` §5–§8 | 9-task plan, parallel branches, every gate result, release approval |
| [brownfield](brownfield/report.md) | `report.md` §3, §7 | static analysis finding the real bugs; `changes/impl.expiry.a1.diff` (rolled back) vs `a2.diff` (approved) |
| [ambiguous](ambiguous/report.md) | `report.md` §2, §7 | three ambiguities, the privacy question, `plan.revised` reusing finished work |

Each folder contains:

- `report.md`: the evidence pack (requirements, impact, ADRs, plan, DAG, timeline, gates, policy, approvals, decisions, metrics)
- `audit.jsonl`: the hash-chained audit trail (`python -m agentflow verify` checks it in a live run)
- `approvals.json`: every approval request and decision, bound to change digests
- `metrics.json`: reliability metrics derived from the audit trail
- `changes/`: every change set applied, including rolled-back attempts
- `workspace/`: the generated product (source, tests, `openapi.json`, docs, release notes)
