#!/usr/bin/env bash
# scripts/demo.sh -- the plan.md §1 demo path end-to-end, against real
# Bedrock, in one command.
#
#   1. `brain think` with three captured thoughts on different topics.
#   2. `brain recall` with a query worded differently than the originals.
#   3. `brain resume` on a topic, continuing the conversation.
#   4. A restart-and-persist check: `brain` is CLI-only with no daemon
#      (plan.md §3), so every command above already runs as its own fresh
#      process -- this step just proves the data is still there by listing
#      and showing a conversation from a brand-new process.
#
# This needs live AWS credentials and access to the configured Bedrock
# models (see README.md / .env.example). It is a manual verification
# script, not part of the automated test suite -- similar in spirit to
# scripts/bedrock_smoke.py. Run it before a live demo:
#
#   scripts/demo.sh
#
# Uses HEYBRAIN_HOME from the environment/.env if set; otherwise brain's
# default (~/.heybrain) is used, so this writes real data there.

set -euo pipefail

BRAIN="${BRAIN_BIN:-brain}"

step() {
    printf '\n\033[1;36m==> %s\033[0m\n' "$1"
}

if ! command -v "$BRAIN" >/dev/null 2>&1; then
    echo "error: '$BRAIN' not found on PATH -- run 'pip install -e .' first." >&2
    exit 1
fi

# `brain think "<text>"` uses the given text as the first turn, then keeps
# the session open waiting for the next line of input, exactly like a real
# interactive session. Piping "exit" ends that follow-up turn the same way
# a user typing "exit" would, so the conversation closes normally, gets
# summarized, and its memories get extracted -- without a script needing a
# real tty.
capture() {
    "$BRAIN" think "$1" <<<"exit"
}

extract_conversation_id() {
    grep -o 'Saved conversation [^ ]*' | awk '{print $3}'
}

step "1/4 -- capturing three thoughts on different topics"

echo "--- thought 1: AI coding agents ---"
output1=$(capture "I've been thinking AI coding agents could handle whole PRs autonomously, not just single files.")
echo "$output1"
conversation_id=$(echo "$output1" | extract_conversation_id)

echo
echo "--- thought 2: Kafka / interview prep ---"
capture "I want to learn Kafka as part of my system design interview prep this quarter."

echo
echo "--- thought 3: a decision about this demo script ---"
capture "Decided to keep the demo script in bash instead of Python, for fewer moving parts during the live run."

step "2/4 -- recalling with different wording than the original thoughts"
"$BRAIN" recall "what have I been thinking about AI coding agents doing full pull requests?"

step "3/4 -- resuming a topic and continuing the conversation"
# No topic argument -> the numbered picker lists the most recently touched
# topic first; "1" selects it, "exit" ends the follow-up turn.
printf '1\nexit\n' | "$BRAIN" resume

step "4/4 -- restart-and-persist check"
echo "Listing conversations from a fresh 'brain' process:"
"$BRAIN" list

if [[ -n "${conversation_id:-}" ]]; then
    echo
    echo "Showing the first captured conversation ($conversation_id) from a fresh process:"
    "$BRAIN" show "$conversation_id"
fi

step "Demo complete"
echo "Everything above ran as separate 'brain' processes with no daemon --"
echo "kill your shell and re-run 'brain list' any time to confirm nothing was lost."
