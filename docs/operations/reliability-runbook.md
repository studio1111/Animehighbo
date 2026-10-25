# Animehighbo Reliability Runbook

This runbook supports [CHANNEL_RULES.md](../../CHANNEL_RULES.md). It describes recovery behavior and verification, not a guarantee that external services can never fail.

## Expected pipeline

1. The dispatcher runs on a five-minute schedule, relevant catalog/config changes, manual dispatch, and completion of a worker.
2. It reads the archive manifest, publication state, manual exclusions, active worker runs, recent failures, quota cooldown, and publisher lock.
3. It selects up to five independent eligible episodes, respecting series order and the global concurrency cap.
4. A worker prepares the source, subtitles, and all required qualities. Preparation failures must not acquire the Telegram publisher lock.
5. The publisher acquires the global lock, refreshes the latest state, checks recorded quality/message IDs, publishes only missing pieces, and merges state without overwriting concurrent updates.
6. Worker completion triggers a fresh dispatcher run. The scheduled dispatcher remains the fallback if the event-driven refill is delayed.

## Triage when no episode is dispatched

- Is the dispatcher run itself successful, and what does the planner output say?
- Does the manifest contain entries dated from 2026-10-01 through the current source date?
- Are entries filtered by `data/archive_exclusions.json` only, with no approval/pending policy?
- Does the planner's completion check understand both aggregate episode records (`identity`) and per-quality records (`identity:1080p`, `identity:720p`, `identity:480p`) in `data/state.json`?
- Do queued/in-progress workflow runs match real workers, or are stale run records occupying slots?
- Is `data/gemini_quota_cooldown.json` still in the future?
- Does `data/publisher.lock` refer to an incomplete episode that can still be resumed?
- Are GitHub API errors being treated as an error rather than an empty active-worker list?
- Are the required repository secrets and Telegram target configured?

## Triage when an episode appears twice or is missing

- Compare the stable source identity, normalized episode identity, quality suffix, and recorded Telegram `message_id`.
- Check whether the send succeeded but the subsequent state commit failed. Telegram send and GitHub state persistence are not a single atomic transaction, so this failure window needs explicit reconciliation rather than blind resend.
- Verify the global publisher lock and the state merge commit history.
- Never erase publication records just to make the queue dispatch again.

## Recovery policy

- Retry transient network, package mirror, and Telegram API errors with bounded exponential backoff.
- Back off a failing series while allowing unrelated series to continue.
- Treat malformed state, ambiguous duplicate episode identities, and unknown lock ownership as visible integrity errors.
- Release a worker slot only after its workflow is completed or confirmed abandoned. Do not cancel a live publication in the middle of a Telegram thread without first checking the lock and message state.
- After a code change, inspect CI and workflow runs, then verify the state file and Telegram message IDs. A green CI badge alone is insufficient.

## Evidence to include in incident reports

Include the dispatcher run URL, worker run URL(s), planner summary, identity/title/episode, lock state, state-record keys, last successful Telegram message ID, retry/backoff decision, and whether publication was verified. Never include bot tokens, API hashes, or other secrets.
\n\n## Private release-alert destination\n\nThe two-hour metadata monitor sends new-release alerts only to `TELEGRAM_ALERT_CHAT_ID`, a separate GitHub Actions repository secret for a private administrator chat or alert group. Do not point it at the public anime channel. The secret must be configured before the next newly detected release; if it is missing, the monitor fails closed and does not advance the report, so it can retry after configuration. `TELEGRAM_CHAT_ID` remains the publication destination for the publisher workflow. Alerts contain metadata only and do not publish media.\n