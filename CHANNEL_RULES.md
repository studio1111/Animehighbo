# Animehighbo Channel Rules

**Status:** Permanent project operating contract  
**Effective:** 2026-10-25  
**Scope:** Archive catalog, episode queue, preparation workers, Telegram publishing, duplicate prevention, recovery and monitoring.

This file is the canonical operating policy for the Animehighbo channel. All implementation changes must preserve these invariants. A Markdown file cannot technically make itself immutable; repository administrators must enforce protection with GitHub branch rulesets and required CI checks. No automation may silently replace these rules with a per-anime approval workflow.

## Non-negotiable operating rules

1. **No per-anime approval gate.** A catalog entry is queue-eligible by default. Do not require the operator to approve every anime, episode, season, or future catalog entry before queueing.
2. **Manual exclusions are opt-out only.** `data/archive_exclusions.json` is the operator's optional denylist. An empty or missing file means no manual exclusions. An exclusion affects only the explicitly matching series/title; it must not block unrelated entries.
3. **Prevent duplicate channel posts.** Before sending, compute a stable episode identity and reconcile it with persisted publication state. Record each successfully sent quality and Telegram message ID. Retries must resume partial publication and must not blindly repost a quality already recorded as sent.
4. **October archive coverage.** The catalog should cover eligible releases from 2026-10-01 through the latest available date. Curated backfill episodes before October may remain when needed to provide a series start, but must follow the configured catalog policy. A static manifest must not be described as a complete live-site catalog unless it has been reconciled against the source.
5. **Order within a series.** Do not publish a later numbered episode before earlier episodes already present in the catalog are complete. An unavailable episode may delay later episodes of that same series, but must not stop unrelated anime.
6. **Keep the queue moving.** A worker failure, timeout, invalid source, translation failure, or encoding failure must be recorded with actionable diagnostics and must not permanently block unrelated series. Use bounded retries, backoff, and scheduled reconciliation rather than infinite tight retry loops.
7. **Recover interrupted Telegram threads.** The global publisher lock protects poster-plus-video grouping. A retained lock must be checked against actual publication state and recovered safely. A stale or malformed lock must not cause silent permanent starvation or duplicate posting.
8. **Persist state without lost updates.** Concurrent workers must merge publication records against the latest repository state. Never replace the shared state file wholesale with a stale worker snapshot.
9. **Fail visibly, not silently.** Every dispatcher run must report catalog count, eligible count, excluded count, active worker count, selected identities, and why no work was dispatched. Errors in GitHub API calls, malformed state, or unexpected workflow configuration must be surfaced.
10. **Verify end to end.** A successful GitHub Actions run is not proof of a Telegram post. Confirm persisted message IDs and publication state before reporting an episode as published.
11. **Do not promise perfect uptime.** External torrent/feed availability, GitHub Actions limits, Telegram API limits, credentials, and network failures are outside the program's control. The engineering goal is automatic detection, safe recovery, and no permanent silent stall, not an impossible guarantee that failures can never occur.
12. **Preserve source and platform safeguards.** Removing an operator approval gate does not mean bypassing source validation, credentials, Telegram limits, or applicable rights restrictions. Only process and republish media the channel is authorized to distribute.

## Definition of done for reliability changes

A reliability change is not complete until:
- automated tests cover its failure mode and duplicate-prevention behavior;
- the full unit-test suite and workflow syntax/compile checks pass in CI;
- the live dispatcher is observed selecting or explicitly explaining an empty queue;
- a worker's success/failure path demonstrably refills the queue;
- partial publication recovery is verified against persisted Telegram message IDs;
- no per-anime approval state or pending-approval branch has been introduced;
- documentation and operational alerts match the implemented behavior.

## Change control

Changes to this contract require an explicit repository-owner decision and a reviewed pull request. To enforce this technically, configure GitHub rulesets on `main` to require pull requests and passing CI, disallow force-push/deletion, and restrict bypass privileges. Do not claim the file is technically immutable until those repository settings have been checked and enabled.
