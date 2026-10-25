# Animehighbo Reliability Audit and Remediation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans or superpowers:subagent-driven-development to execute this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Make the Animehighbo archive dispatcher self-reconciling, duplicate-resistant, observable, and free of per-anime approval gates.

**Architecture:** Keep the existing GitHub Actions dispatcher/worker architecture. Fix mismatches between catalog identities and persisted publication state, then harden worker-slot accounting and publication recovery with regression tests. Use the manual exclusions file only as an opt-out list.

**Tech Stack:** Python 3.12, unittest, GitHub Actions, Telegram Bot API, GitHub repository state.

**Spec:** `CHANNEL_RULES.md`

## Global constraints

- No per-anime approval gate; all otherwise valid catalog entries default to queue-eligible.
- Do not disable source validation or platform safeguards.
- Do not claim publication without persisted Telegram message IDs.
- Do not overwrite concurrent worker state with stale snapshots.
- No duplicate Telegram post on retries where prior message IDs can be reconciled.

## Review focus

- Aggregate episode completion versus per-quality state keys.
- Stale queued/in-progress workflow runs occupying worker slots.
- Missing manual-exclusion path in dispatcher push triggers.
- Send succeeds but state persistence fails, causing duplicate resend risk.
- Publisher lock remains after a worker is canceled or fails mid-thread.

## Tasks

- [ ] Inspect dispatcher, worker, queue planner, publisher lock, Telegram send logic, state merge, and every related test.
- [ ] Add regression tests first for default allow/manual exclusions, state schema compatibility, duplicate suppression, and worker refill after completion/failure.
- [ ] Run tests to confirm new regressions fail for the intended reason.
- [ ] Implement minimal fixes to state reconciliation, queue selection diagnostics, workflow triggers, and retry/recovery paths.
- [ ] Run the complete unittest suite, compileall, YAML validation, and relevant workflow runs.
- [ ] Review the diff for hidden approval gates, duplicate sends, lost state updates, or secret leakage.
- [ ] Confirm the live dispatcher and worker outcomes before reporting completion.

## Known investigation findings

- The dispatcher push trigger watches `data/anime_publication_approvals.json`, even though the queue now uses `data/archive_exclusions.json`. The five-minute schedule is a fallback, but exclusion changes should trigger reconciliation directly.
- The queue helper still uses approval-oriented names and a `pending` counter even though default policy is `allow`; this is misleading and makes the removed gate harder to audit.
- The actual state file stores both per-quality records and aggregate episode records. Completion logic must be tested against the exact live schema, not only synthetic test fixtures.
- A Markdown policy file is not technically immutable by itself. Repository rulesets/branch protection are required to enforce change control.
