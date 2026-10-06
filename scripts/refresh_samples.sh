#!/usr/bin/env bash
# Regenerates docs/sample-runs/ from fresh deterministic runs of all three scenarios.
# The committed samples let reviewers read real outputs (report, audit trail, approvals,
# diffs, generated code) without running anything.
set -euo pipefail
cd "$(dirname "$0")/.."
rm -rf runs/demo-*-sample docs/sample-runs
STAMP=sample ./scripts/demo.sh > /dev/null
for s in greenfield brownfield ambiguous; do
  src="runs/demo-$s-sample"
  dst="docs/sample-runs/$s"
  mkdir -p "$dst"
  cp "$src"/{report.md,audit.jsonl,metrics.json,approvals.json} "$dst"/
  cp -r "$src/changes" "$dst/changes"
  # The generated product, without caches or local databases.
  cp -r "$src/workspace" "$dst/workspace"
  find "$dst/workspace" \( -name __pycache__ -o -name .pytest_cache -o -name .ruff_cache \) \
       -prune -exec rm -rf {} +
  find "$dst/workspace" \( -name '*.db' -o -name .coverage \) -delete
  # Paths in the report are machine-specific; make them repo-relative.
  sed -i "s#$(pwd)/##g" "$dst/report.md"
done
echo "samples written to docs/sample-runs/"
