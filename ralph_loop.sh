#!/usr/bin/env bash
# ======================================================================
# ralph_loop.sh — Persistent Claude Code iteration loop
# (Ralph Wiggum pattern: keep re-prompting until the task is done)
#
# Runs Claude Code in a loop, feeding each iteration's output back as
# context for the next run, until Claude signals TASK_COMPLETE or the
# task file lands in Done/.
#
# USAGE
#   ./ralph_loop.sh /path/to/vault "Your prompt here"
#
# EXAMPLES
#   # Weekly CEO audit briefing
#   ./ralph_loop.sh /path/to/vault "Weekly audit: Read Accounting/, \
#     Stripe payments, Dashboard.md. Generate CEO Briefing with \
#     revenue, bottlenecks, suggestions."
#
#   # Process all pending tasks
#   ./ralph_loop.sh /path/to/vault "Process every file in Needs_Action/. \
#     Follow Company_Handbook.md rules. Move completed tasks to Done/."
#
# SCHEDULING (cron — every Monday at 8 AM)
#   0 8 * * 1  /path/to/ralph_loop.sh /path/to/vault "Weekly audit: ..."
#
# SCHEDULING (Windows Task Scheduler)
#   Program:   bash
#   Arguments: /path/to/ralph_loop.sh "E:\vault" "Weekly audit: ..."
# ======================================================================

set -euo pipefail

# ------------------------------------------------------------------
# Arguments
# ------------------------------------------------------------------

if [ $# -lt 2 ]; then
    echo "Usage: $0 <vault_path> <initial_prompt>"
    echo ""
    echo "  vault_path      Path to the Obsidian vault root"
    echo "  initial_prompt   The task prompt for Claude Code"
    exit 1
fi

VAULT_PATH="$(realpath "$1")"
INITIAL_PROMPT="$2"

# ------------------------------------------------------------------
# Configuration
# ------------------------------------------------------------------

MAX_ITERATIONS=20
LOGS_DIR="${VAULT_PATH}/Logs"
BRIEFINGS_DIR="${VAULT_PATH}/Briefings"
DONE_DIR="${VAULT_PATH}/Done"
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
SESSION_ID="ralph_${TIMESTAMP}"

# Completion signals Claude can emit
COMPLETION_TAG="<promise>TASK_COMPLETE</promise>"

# Ensure directories exist
mkdir -p "$LOGS_DIR" "$BRIEFINGS_DIR" "$DONE_DIR"

# Temp file for capturing Claude output per iteration
TMPFILE="$(mktemp)"
trap 'rm -f "$TMPFILE"' EXIT

# ------------------------------------------------------------------
# Logging helpers
# ------------------------------------------------------------------

log() {
    local level="$1"
    shift
    local msg="$*"
    local ts
    ts="$(date '+%Y-%m-%d %H:%M:%S')"
    echo "${ts} [${level}] ${msg}"
    echo "${ts} [${level}] ${msg}" >> "${LOGS_DIR}/${SESSION_ID}.log"
}

log_separator() {
    local sep
    sep="$(printf '=%.0s' {1..60})"
    log "INFO" "$sep"
}

# ------------------------------------------------------------------
# Snapshot: count Done/ files before we start
# ------------------------------------------------------------------

count_done_files() {
    find "$DONE_DIR" -maxdepth 1 -name '*.md' -type f 2>/dev/null | wc -l | tr -d ' '
}

DONE_COUNT_BEFORE="$(count_done_files)"

# ------------------------------------------------------------------
# Build the context prompt for each iteration
# ------------------------------------------------------------------

build_prompt() {
    local iteration="$1"
    local previous_output="$2"

    local prompt=""

    if [ "$iteration" -eq 1 ]; then
        prompt="$INITIAL_PROMPT

IMPORTANT RULES:
- Read Company_Handbook.md before taking any action.
- Follow every rule in the handbook.
- Log decisions to Logs/.
- When the task is FULLY complete, output exactly: ${COMPLETION_TAG}
- If you need more iterations, describe what remains to be done."
    else
        prompt="Continue the task from where you left off.

ORIGINAL TASK:
${INITIAL_PROMPT}

PREVIOUS OUTPUT (iteration $((iteration - 1))):
${previous_output}

Continue working. If the task is FULLY complete, output exactly: ${COMPLETION_TAG}
If not complete, do the next step and describe what remains."
    fi

    echo "$prompt"
}

# ------------------------------------------------------------------
# Check completion conditions
# ------------------------------------------------------------------

check_complete() {
    local output="$1"

    # Condition 1: Claude emitted the completion tag
    if echo "$output" | grep -qF "$COMPLETION_TAG"; then
        log "INFO" "Completion signal detected: TASK_COMPLETE"
        return 0
    fi

    # Condition 2: New files appeared in Done/
    local done_count_now
    done_count_now="$(count_done_files)"
    if [ "$done_count_now" -gt "$DONE_COUNT_BEFORE" ]; then
        local new_files=$(( done_count_now - DONE_COUNT_BEFORE ))
        log "INFO" "Completion detected: ${new_files} new file(s) in Done/"
        return 0
    fi

    # Condition 3: Output contains strong completion language
    if echo "$output" | grep -qiE '(all tasks? (are |)(complete|done|finished)|nothing (left|remaining|more)|fully complete)'; then
        log "INFO" "Completion detected: natural language signal"
        return 0
    fi

    return 1
}

# ------------------------------------------------------------------
# Run Claude Code for one iteration
# ------------------------------------------------------------------

run_claude() {
    local prompt="$1"
    local iteration="$2"
    local iter_log="${LOGS_DIR}/ralph_iteration_${TIMESTAMP}_${iteration}.log"

    log "INFO" "Invoking Claude Code (iteration ${iteration}/${MAX_ITERATIONS})"

    local exit_code=0
    claude -p "$prompt" \
        --allowedTools "Read,Write,Edit,Glob,Grep" \
        > "$TMPFILE" 2>&1 || exit_code=$?

    # Save iteration log
    {
        echo "=== Ralph Loop Iteration ${iteration} ==="
        echo "Timestamp: $(date '+%Y-%m-%d %H:%M:%S')"
        echo "Exit code: ${exit_code}"
        echo ""
        echo "--- PROMPT ---"
        echo "$prompt" | head -50
        echo ""
        echo "--- OUTPUT ---"
        cat "$TMPFILE"
    } > "$iter_log"

    if [ "$exit_code" -ne 0 ]; then
        log "ERROR" "Claude exited with code ${exit_code}"
        log "ERROR" "Check ${iter_log} for details"
        return 1
    fi

    local output_size
    output_size="$(wc -c < "$TMPFILE" | tr -d ' ')"
    log "INFO" "Claude output: ${output_size} bytes"

    return 0
}

# ------------------------------------------------------------------
# Generate CEO briefing if this was an audit task
# ------------------------------------------------------------------

generate_briefing_summary() {
    local all_output="$1"

    # Only generate if the original prompt looks like an audit/briefing task
    if ! echo "$INITIAL_PROMPT" | grep -qiE '(audit|briefing|report|summary|review)'; then
        return
    fi

    local briefing_file="${BRIEFINGS_DIR}/CEO_Briefing_${TIMESTAMP}.md"
    local date_now
    date_now="$(date '+%Y-%m-%d')"

    cat > "$briefing_file" << BRIEFING_EOF
---
type: ceo_briefing
generated: $(date -Iseconds)
source: ralph_loop
session_id: ${SESSION_ID}
---

# CEO Briefing
**Generated:** ${date_now}
**Source:** Automated weekly audit (Ralph Loop)

## Summary

${all_output}

---
*Generated automatically by ralph_loop.sh*
*Session: ${SESSION_ID}*
BRIEFING_EOF

    log "INFO" "CEO Briefing saved: ${briefing_file}"
}

# ------------------------------------------------------------------
# Main loop
# ------------------------------------------------------------------

main() {
    log_separator
    log "INFO" "Ralph Loop starting"
    log "INFO" "Session:    ${SESSION_ID}"
    log "INFO" "Vault:      ${VAULT_PATH}"
    log "INFO" "Max iters:  ${MAX_ITERATIONS}"
    log "INFO" "Prompt:     ${INITIAL_PROMPT:0:100}..."
    log_separator

    local iteration=1
    local previous_output=""
    local all_output=""
    local completed=false

    while [ "$iteration" -le "$MAX_ITERATIONS" ]; do
        log_separator
        log "INFO" "ITERATION ${iteration} OF ${MAX_ITERATIONS}"
        log_separator

        # Build the prompt for this iteration
        local prompt
        prompt="$(build_prompt "$iteration" "$previous_output")"

        # Run Claude
        if ! run_claude "$prompt" "$iteration"; then
            log "ERROR" "Claude failed on iteration ${iteration} — retrying"
            iteration=$((iteration + 1))
            sleep 5
            continue
        fi

        # Capture output
        local output
        output="$(cat "$TMPFILE")"
        previous_output="$output"

        # Keep only the last 3000 chars of accumulated output to avoid
        # blowing up the prompt size on long-running tasks
        all_output="${all_output}

--- Iteration ${iteration} ---
${output}"
        if [ "${#all_output}" -gt 15000 ]; then
            all_output="${all_output: -12000}"
        fi

        # Check if we're done
        if check_complete "$output"; then
            completed=true
            break
        fi

        log "INFO" "Task not yet complete — continuing to next iteration"
        iteration=$((iteration + 1))

        # Brief pause between iterations to avoid hammering the API
        sleep 3
    done

    log_separator

    if [ "$completed" = true ]; then
        log "INFO" "TASK COMPLETED after ${iteration} iteration(s)"

        # Generate briefing if applicable
        generate_briefing_summary "$all_output"
    else
        log "WARN" "MAX ITERATIONS (${MAX_ITERATIONS}) reached — task may be incomplete"
        log "WARN" "Review logs at: ${LOGS_DIR}/${SESSION_ID}.log"

        # Create a Needs_Action alert for the CEO
        local alert_file="${VAULT_PATH}/Needs_Action/ALERT_ralph_incomplete_${TIMESTAMP}.md"
        cat > "$alert_file" << ALERT_EOF
---
type: ralph_loop_alert
session_id: ${SESSION_ID}
created: $(date -Iseconds)
priority: high
status: pending
---

## Ralph Loop Did Not Complete

The automated task loop reached its maximum of ${MAX_ITERATIONS} iterations
without completing.

**Original prompt:** ${INITIAL_PROMPT}

## Suggested Actions

- [ ] Check logs at \`Logs/${SESSION_ID}.log\`
- [ ] Review iteration logs at \`Logs/ralph_iteration_${TIMESTAMP}_*.log\`
- [ ] Determine if the task needs manual intervention
- [ ] Re-run with a more specific prompt if needed
ALERT_EOF

        log "INFO" "Incomplete alert created: ${alert_file}"
    fi

    # Final summary
    local done_count_after
    done_count_after="$(count_done_files)"
    local new_done=$(( done_count_after - DONE_COUNT_BEFORE ))

    log_separator
    log "INFO" "SESSION SUMMARY"
    log "INFO" "  Iterations run:    ${iteration}"
    log "INFO" "  Completed:         ${completed}"
    log "INFO" "  New files in Done: ${new_done}"
    log "INFO" "  Session log:       ${LOGS_DIR}/${SESSION_ID}.log"
    log_separator

    if [ "$completed" = true ]; then
        exit 0
    else
        exit 1
    fi
}

main
