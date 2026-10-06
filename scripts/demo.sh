#!/usr/bin/env bash
# Runs all three scenarios end to end, playing the human operator at each checkpoint.
# Every human action is an explicit CLI call, so it is visible here and in the audit trail.
set -euo pipefail
cd "$(dirname "$0")/.."
PY=${PY:-.venv/bin/python}
[ -x "$PY" ] || PY=python
AF="$PY -m agentflow"
# --no-input: this script plays the human with explicit approve/answer commands below.
# Run a scenario yourself (python -m agentflow run <name>) to be prompted interactively.
WHO=${APPROVER:-reviewer}
STAMP=${STAMP:-$(date +%H%M%S)}

banner() { printf '\n\033[1;36m==== %s ====\033[0m\n' "$*"; }

banner "1/3 GREENFIELD: URL shortener from a well-defined spec"
$AF run greenfield --no-input --run-id "demo-greenfield-$STAMP" || true
$AF approve "demo-greenfield-$STAMP" release --by "$WHO" --comment "release evidence reviewed"
$AF resume --no-input "demo-greenfield-$STAMP"

banner "2/3 BROWNFIELD: security fix + bug fix + feature on a legacy codebase"
$AF run brownfield --no-input --run-id "demo-brownfield-$STAMP" || true
$AF approve "demo-brownfield-$STAMP" impl.expiry --by "$WHO" --comment "additive nullable column; v0.4 upgrade test passes"
$AF resume --no-input "demo-brownfield-$STAMP" || true
$AF approve "demo-brownfield-$STAMP" release --by "$WHO"
$AF resume --no-input "demo-brownfield-$STAMP"

banner "3/3 AMBIGUOUS: 'safer, some analytics, fast'"
$AF run ambiguous --no-input --run-id "demo-ambiguous-$STAMP" || true
$AF answer "demo-ambiguous-$STAMP" Q-PRIVACY hashed --by "$WHO"
$AF resume --no-input "demo-ambiguous-$STAMP" || true
$AF approve "demo-ambiguous-$STAMP" analytics.unique_visitors --by "$WHO" --comment "no raw IPs; additive migration"
$AF resume --no-input "demo-ambiguous-$STAMP" || true
$AF approve "demo-ambiguous-$STAMP" release --by "$WHO"
$AF resume --no-input "demo-ambiguous-$STAMP"

banner "Audit chains and aggregate reliability metrics"
for s in greenfield brownfield ambiguous; do $AF verify "demo-$s-$STAMP"; done
$AF metrics | sed -n 1,12p
echo
echo "Reports: runs/demo-*-$STAMP/report.md   Generated code: runs/demo-*-$STAMP/workspace/"
