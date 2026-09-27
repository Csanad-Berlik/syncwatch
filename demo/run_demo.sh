#!/usr/bin/env bash
# The whole story, start to finish. Record this for the Loom.
#
#   ./demo/run_demo.sh           runs straight through
#   ./demo/run_demo.sh --pause   waits for Enter between scenes
#
# Each scene starts clean, takes a baseline, breaks one thing, then shows
# the sync's own log next to what the watchdog finds.

set -u
cd "$(dirname "$0")/.."
export PYTHONPATH="$PWD${PYTHONPATH:+:$PYTHONPATH}"

PAUSE=0
[ "${1:-}" = "--pause" ] && PAUSE=1

# The watchdog's output is piped through sed below, so it cannot see the
# terminal. Tell it to colour anyway when this script is in one.
[ -t 1 ] && export FORCE_COLOR=1

STATE="${SYNCWATCH_DEMO_DIR:-demo/state}"
mkdir -p "$STATE"

say()   { printf '\n\033[1m%s\033[0m\n' "$*"; }
pause() { [ "$PAUSE" = 1 ] && read -r -p "  [enter]" _ || true; }
demo()  { python3 -m demo.demo "$@"; }
watch() {
  python3 -m syncwatch check \
    --source "$STATE/crm.json" --dest "$STATE/sheet.json" \
    --mapping demo/mapping.json --runs "$STATE/runs.json" \
    --state "$STATE/watch-state.json"
}
baseline() {
  demo reset >/dev/null
  demo seed 20
  demo sync
  demo snapshot >/dev/null
  echo "watchdog:"
  watch | sed 's/^/  /;s/^  $//'
}

say "SCENE 0. A working sync, 20 contacts."
baseline
pause

say "SCENE 1. The webhook provider redelivers 5 events."
baseline >/dev/null
demo break duplicate
demo sync
demo snapshot >/dev/null
echo "watchdog:"
watch | sed 's/^/  /;s/^  $//'
pause

say "SCENE 2. Someone renames 'company' in the CRM. Nobody tells the sync."
baseline >/dev/null
demo break rename
demo sync
demo snapshot >/dev/null
echo "watchdog:"
watch | sed 's/^/  /;s/^  $//'
pause

say "SCENE 3. The sync's token expires. New contacts keep arriving."
baseline >/dev/null
demo break auth
demo sync
demo snapshot >/dev/null
echo "watchdog:"
watch | sed 's/^/  /;s/^  $//'

say "Every one of those runs was logged by the sync as a success."
