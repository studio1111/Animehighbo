# Independent Anime Preparation and Publishing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Animehighbot prepare and publish each authorized episode independently, preserve per-series episode order, validate every output, and prove delivery with a real Telegram post.

**Architecture:** Keep the existing Python application and GitHub Actions, but separate episode preparation from publishing with a versioned prepared-bundle manifest and a durable repository-backed queue/state. Use a serialized publisher with short-lived artifacts and recovery triggers, while failed preparation workers remain isolated. Preserve existing state and historical archive records.

**Tech Stack:** Python 3, pytest, requests, ffmpeg/ffprobe, GitHub Actions, GitHub artifacts, Telegram Bot API, Gemini API.

**Spec:** `docs/superpowers/specs/2026-10-21-independent-anime-preparation-and-publishing-design.md`

## Global Constraints

- Process only configured, explicitly authorized direct-media sources and allow-listed hosts; do not enable torrent or unapproved-host downloads.
- Publish no episode until poster, metadata, 1080p/720p/480p videos, English subtitles, and Persian subtitles are all prepared and validated.
- Publish one poster, then reply with videos in 1080p, 720p, 480p order.
- Preserve per-series/per-season episode order; unrelated anime must remain independent.
- Use only `GEMINI_API_KEY`; retry HTTP 429 with bounded backoff and honor `Retry-After`; no key rotation and no automatic offline translator fallback.
- Do not reset or discard `data/state.json`, `data/archive_manifest.json`, or existing Telegram message IDs.
- Keep the latest 30 completed GitHub Actions runs and never delete active runs.
- No paid server or required Supabase project.
- Never log secrets or claim live delivery without confirming the workflow result and Telegram channel.

## Review Focus

- A failed translation/encode in one anime must not stop a fully prepared episode of another anime: test worker failure isolation and publish eligibility.
- Missing, malformed, or ambiguous season/episode identity must not cause guessed ordering: test quarantine/hold behavior.
- A manifest with traversal paths, bad hashes, absent tracks, invalid duration, corrupt media, or oversized files must be rejected before any Telegram call.
- A Telegram failure after poster or one quality must resume from recorded message IDs and must not resend confirmed messages.
- Concurrent publisher runs and stale repository state must not overwrite newer state or create competing sends; test serialization and conflict recovery.

---

### Task 1: Pin down episode identity, eligibility, and ordering

**Files:**
- Modify: `tools/episode_queue.py`
- Create: `tests/test_episode_queue_independent.py`
- Review: `data/state.json`, `data/archive_manifest.json`, existing queue tests

**Interfaces:**
- Produce `episode_identity(entry: dict) -> str` for a stable episode key.
- Produce `series_identity(entry: dict) -> str | None`, including season when known.
- Produce `eligible_prepared_episodes(entries: list[dict], state: dict) -> list[dict]`, returning independently eligible episodes in deterministic order.
- Ambiguous episode/season identity is held, not guessed.

- [ ] **Step 1: Write failing tests** for: A-2 held until A-1 is fully published; B-1 eligible while A-1 is failed; season 2 episode 1 distinct from season 1 episode 1; duplicate identities rejected/held; unknown episode order held.
- [ ] **Step 2: Run the focused tests** with `pytest tests/test_episode_queue_independent.py -q`; confirm failures expose current queue behavior.
- [ ] **Step 3: Implement identity and eligibility helpers** without deleting or rewriting historical state.
- [ ] **Step 4: Run focused and existing queue tests**; verify deterministic ordering and legacy-state compatibility.
- [ ] **Step 5: Commit** as `fix: enforce independent per-series episode ordering`.

### Task 2: Define and validate the prepared episode bundle

**Files:**
- Create: `tools/prepared_bundle.py`
- Create: `tests/test_prepared_bundle.py`
- Modify only if needed: `app.py` media inspection helpers

**Interfaces:**
- `build_manifest(episode: dict, bundle_dir: Path) -> dict` creates schema version 1 with stable identity, metadata/caption, poster, three quality records, file sizes, SHA-256, duration, codec, and subtitle-track information.
- `validate_manifest(manifest_path: Path, max_file_size_mb: int) -> dict` validates schema, bundle-contained paths, checksums, required files, media properties, and required English/Persian subtitle tracks; raises a descriptive `ValueError` for invalid bundles.
- Manifest paths must be relative to the bundle root and must not resolve outside it.

- [ ] **Step 1: Write failing tests** for valid bundle acceptance and rejection of traversal paths, checksum mismatch, missing poster/quality, corrupt or zero-duration video, missing subtitle tracks, unsupported codecs, and file-size limits.
- [ ] **Step 2: Run `pytest tests/test_prepared_bundle.py -q`** and confirm expected failures.
- [ ] **Step 3: Implement manifest creation and strict validation**, using ffprobe for stream and duration inspection when available.
- [ ] **Step 4: Run focused tests**, including mocked ffprobe results and actual tiny generated media when ffmpeg is available.
- [ ] **Step 5: Commit** as `feat: validate complete prepared episode bundles`.

### Task 3: Enforce one-key Gemini policy and fail closed

**Files:**
- Modify: `tools/bilingual_subtitles.py`
- Modify: `tests/test_bilingual_subtitles.py` and/or existing subtitle tests
- Modify: `requirements.txt` only if tests reveal a required dependency

**Interfaces:**
- Translator construction reads only `GEMINI_API_KEY`.
- HTTP 429 retry is bounded, uses the same key, respects valid `Retry-After`, and has a capped fallback delay.
- Missing key, exhausted retries, malformed translation, or incomplete cues raises a sanitized error; no offline fallback is installed or selected automatically.

- [ ] **Step 1: Add failing tests** for one-key-only behavior, same-key 429 retries, `Retry-After` handling, retry exhaustion, secret redaction, and no Argos fallback.
- [ ] **Step 2: Run focused subtitle tests** and confirm the current multi-key/offline behavior fails the new contract.
- [ ] **Step 3: Implement the single-key fail-closed policy** and remove workflow-side fallback-key injection.
- [ ] **Step 4: Run all subtitle tests** and confirm cue counts/timing are preserved.
- [ ] **Step 5: Commit** as `fix: use one Gemini key with safe quota retries`.

### Task 4: Isolate preparation failures per episode

**Files:**
- Modify: `app.py`
- Create or modify: `tests/test_episode_failure_isolation.py`
- Review all callers of `publish_prepared_episodes`, `main`, episode grouping, and exception handling before editing.

**Interfaces:**
- Extract a single-episode preparation boundary that returns a complete bundle or a structured failure for that episode.
- A failure for one series/episode must not break iteration over unrelated episodes.
- Incomplete bundles never become publishable artifacts.
- Keep compatibility with the existing command-line entry points and dry-run behavior.

- [ ] **Step 1: Write failing regression tests** where A-1 translation/encode fails while B-1 still prepares and becomes publishable; A-2 stays held until A-1 is published.
- [ ] **Step 2: Run the focused tests** and confirm the existing outer-loop `break`/batch gate causes failure.
- [ ] **Step 3: Refactor the orchestration** into per-episode error boundaries, returning structured results and continuing unrelated work.
- [ ] **Step 4: Run application and regression tests** to ensure Telegram posting cannot begin for an incomplete episode.
- [ ] **Step 5: Commit** as `fix: isolate anime episode preparation failures`.

### Task 5: Rework GitHub Actions into independent preparation and durable publishing

**Files:**
- Modify: `.github/workflows/publish.yml`
- Modify: `tools/episode_queue.py`
- Create or modify: `tests/test_workflow_contract.py`
- Review all workflow triggers, artifact lifetimes, permissions, concurrency groups, state commit/rebase behavior, and retention workflow before editing.

**Interfaces:**
- Preparation workers produce one validated bundle per episode and report success/failure independently.
- Each completed valid bundle can enter the durable publish queue without requiring every matrix worker to succeed.
- Publishing is serialized against shared state and can recover queued bundles after transient failure or artifact expiration; it must not depend on a stale pending workflow run.
- Keep workflow permissions least-privilege, and avoid exposing artifact-download credentials or secrets in logs.
- Do not make Supabase mandatory.

- [ ] **Step 1: Write failing workflow-contract tests** for independent matrix failures, per-episode completion, publisher serialization, recovery trigger, one Gemini secret, no offline fallback, least-privilege permissions, and 30-run retention.
- [ ] **Step 2: Run `pytest tests/test_workflow_contract.py -q`** and verify the old all-matrix-success gate and fallback-key settings fail.
- [ ] **Step 3: Implement independent preparation and a durable queue handoff.** Ensure the queue stores enough metadata to recover a prepared bundle, or deliberately re-prepares it after artifact expiry rather than referring to an expired artifact.
- [ ] **Step 4: Verify workflow YAML syntax and event semantics**; test that one failed worker cannot suppress publication of a different completed episode.
- [ ] **Step 5: Run queue, workflow, and retention tests**; confirm active runs are never deleted.
- [ ] **Step 6: Commit** as `fix: decouple episode preparation from publishing`.

### Task 6: Make Telegram publishing resumable and conflict-safe

**Files:**
- Modify: `app.py` publisher/state persistence
- Create or modify: `tests/test_telegram_publish_resume.py`
- Review Telegram send helpers and existing `data/state.json` records before changes.

**Interfaces:**
- Validate a complete bundle immediately before any send.
- Send one poster and then 1080p, 720p, 480p as replies to that poster.
- Persist each returned Telegram message ID immediately.
- Resume from confirmed message IDs; never resend a confirmed poster/video.
- Serialize publishers and rebase/merge state without dropping newer data. Record ambiguous sends when Telegram may have accepted a request but no response ID was received.

- [ ] **Step 1: Write failing tests** for poster success then video failure, each quality failure point, existing legacy message IDs, concurrent publisher lock, and state commit conflict.
- [ ] **Step 2: Run focused tests** and confirm the existing publisher does not satisfy all resume/race cases.
- [ ] **Step 3: Implement resumable sends and conflict-safe state persistence** while preserving all existing state keys and archive entries.
- [ ] **Step 4: Run publisher regression tests** with mocked Telegram responses; assert no incomplete bundle can send.
- [ ] **Step 5: Commit** as `fix: resume Telegram episode publishing safely`.

### Task 7: Update README and repository operating instructions

**Files:**
- Modify: `README.md`
- Review: `config.example.json`, `.github/workflows/retention.yml`, existing Ponytail/agent instruction files

- [ ] **Step 1: Add documentation checks** for episode ordering, independent anime, complete-bundle gate, quality order, Gemini policy, and recovery behavior.
- [ ] **Step 2: Update README and example configuration** to match the actual implemented behavior; do not publish secret names/values beyond expected variable names.
- [ ] **Step 3: Run documentation/workflow contract tests** and verify the 30-completed-run retention policy remains intact.
- [ ] **Step 4: Commit** as `docs: describe independent preparation and safe publishing`.

### Task 8: Full verification, live workflow, and Telegram proof

**Files:**
- Modify tests only for verified regressions.
- Preserve existing state and archive records.

- [ ] **Step 1: Run the full test suite** with `pytest -q`; expected: all tests pass.
- [ ] **Step 2: Run compile and YAML validation** with `python -m compileall -q .` plus a YAML parser or GitHub workflow validation; expected: no syntax/schema errors.
- [ ] **Step 3: Run static/security checks available in CI** and inspect the complete diff for secrets, unauthorized sources, destructive state changes, and untested behavior.
- [ ] **Step 4: Trigger preparation for one authorized real episode** using repository secrets, and inspect its manifest and media validation outputs without printing secret values.
- [ ] **Step 5: Trigger or observe the serialized publisher** and verify the Telegram channel contains exactly one complete poster with metadata and three reply videos in the required order, with playable MKV/H.265 media and English/Persian subtitle tracks.
- [ ] **Step 6: Verify rerun recovery** does not duplicate confirmed messages and inspect the workflow result plus persisted message IDs.
- [ ] **Step 7: If live publication is blocked by external quota, missing secret, source availability, or Telegram access, record the exact verified blocker and do not claim completion. Otherwise confirm the publisher schedule is active and CI remains green.
- [ ] **Step 8: Commit final fixes and evidence-oriented README status** without including credentials, private logs, or media files.

---

## Completion checklist

- [ ] All tests pass and YAML/compile checks pass.
- [ ] One episode's failure does not block other anime.
- [ ] Same-series/season ordering is enforced.
- [ ] All published bundles pass checksums, media, duration, size, and subtitle-track validation.
- [ ] Single Gemini key policy is enforced with no offline fallback.
- [ ] Telegram publication resumes safely from saved message IDs.
- [ ] Existing state and archive history are preserved.
- [ ] README matches actual behavior.
- [ ] Retention keeps latest 30 completed runs and preserves active runs.
- [ ] A real Telegram delivery is confirmed, or a verified external blocker is recorded.
