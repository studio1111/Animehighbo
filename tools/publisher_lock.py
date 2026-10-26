"""Serialize Telegram episode publications repository-wide.

The lock is a single GitHub Contents API file. It is acquired only after an episode's
three validated videos are ready, and it remains owned by that episode if sending
fails so a later retry can finish the same poster thread before any other episode
publishes. Do not use a shared GitHub Actions concurrency group here: GitHub retains
only one pending run per group and can cancel earlier pending jobs.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import time
from datetime import datetime, timezone

import requests

# Every publisher worker uses this one repository-wide lock path.
LOCK_PATH = "data/publisher.lock"  # Intentionally ignore legacy per-series workflow overrides.
STATE_PATH = "data/state.json"
API_VERSION = "2022-11-28"


def _validate(identity: str, token: str | None, repository: str | None) -> tuple[str, str, str]:
    identity = str(identity or "").strip()
    token = str(token or os.getenv("GITHUB_TOKEN", "")).strip()
    repository = str(repository or os.getenv("GITHUB_REPOSITORY", "")).strip()
    if not identity:
        raise ValueError("Episode identity is required for the publisher lock.")
    if not token:
        raise ValueError("GITHUB_TOKEN is required for the publisher lock.")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ValueError("GITHUB_REPOSITORY must be in owner/repository form.")
    if not re.fullmatch(r"data/publisher-locks/[0-9a-f]{64}\.lock|data/publisher\.lock", LOCK_PATH):
        raise ValueError("ANIMEHIGHBO_PUBLISH_LOCK_PATH must be a safe publisher lock path.")
    return identity, token, repository


def _url(repository: str) -> str:
    return f"https://api.github.com/repos/{repository}/contents/{LOCK_PATH}"


def _headers(token: str) -> dict[str, str]:
    return {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": API_VERSION,
    }


def _read_lock(identity: str, token: str, repository: str) -> dict | None:
    response = requests.get(
        _url(repository),
        headers=_headers(token),
        params={"ref": "main"},
        timeout=20,
    )
    if response.status_code == 404:
        return None
    response.raise_for_status()
    try:
        payload = response.json()
        decoded = base64.b64decode(payload["content"]).decode("utf-8")
        record = json.loads(decoded)
        sha = str(payload["sha"])
    except (ValueError, KeyError, TypeError, UnicodeDecodeError) as exc:
        raise RuntimeError("The publisher lock file is malformed; refusing to publish.") from exc
    if not isinstance(record, dict) or not str(record.get("identity") or "").strip():
        raise RuntimeError("The publisher lock has no episode identity; refusing to publish.")
    return {**record, "sha": sha}


def _episode_publication_status(identity: str, token: str, repository: str) -> tuple[bool, bool]:
    """Return (complete, has_any_video_quality) from one persisted-state read."""
    response = requests.get(
        f"https://api.github.com/repos/{repository}/contents/{STATE_PATH}",
        headers=_headers(token),
        params={"ref": "main"},
        timeout=20,
    )
    if response.status_code == 404:
        return False, False
    response.raise_for_status()
    try:
        payload = response.json()
        decoded = base64.b64decode(payload["content"]).decode("utf-8")
        state = json.loads(decoded)
    except (ValueError, KeyError, TypeError, UnicodeDecodeError) as exc:
        raise RuntimeError("Publication state is malformed; refusing to reclaim the publisher lock.") from exc
    published = state.get("published", {}) if isinstance(state, dict) else {}
    if not isinstance(published, dict):
        raise RuntimeError("Publication state has an invalid published section.")
    record = published.get(identity, {})
    qualities = record.get("complete_qualities", []) if isinstance(record, dict) else []
    if not isinstance(qualities, list):
        qualities = []
    required = {"1080p", "720p", "480p"}
    complete = required.issubset(set(qualities))
    has_any_quality = bool(set(qualities) & required) or any(
        f"{identity}:{quality}" in published for quality in required
    )
    return complete, has_any_quality


def _episode_is_complete(identity: str, token: str, repository: str) -> bool:
    """Check persisted state before reclaiming a lock left by a completed publisher."""
    return _episode_publication_status(identity, token, repository)[0]


def acquire_lock(
    identity: str,
    token: str | None = None,
    repository: str | None = None,
    timeout_seconds: float = 21600,
    poll_seconds: float = 10,
) -> dict:
    """Acquire the global lock, or resume a retained lock for the same episode."""
    identity, token, repository = _validate(identity, token, repository)
    if timeout_seconds < 0 or poll_seconds <= 0:
        raise ValueError("Lock timeout must be non-negative and polling interval must be positive.")
    deadline = time.monotonic() + timeout_seconds
    headers = _headers(token)
    url = _url(repository)

    while True:
        current = _read_lock(identity, token, repository)
        if current:
            if current["identity"] == identity:
                print(f"Publisher lock acquired for retry of episode {identity}.", flush=True)
                return current
            if _episode_is_complete(current["identity"], token, repository):
                print(
                    f"Recovering stale publisher lock left by completed episode {current['identity']}.",
                    flush=True,
                )
                release_lock(current["identity"], token=token, repository=repository)
                continue
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(
                    f"Timed out waiting for the publisher lock held by {current['identity']}."
                )
            print(
                f"Waiting for publisher lock held by episode {current['identity']}.",
                flush=True,
            )
            time.sleep(min(poll_seconds, remaining))
            continue

        record = {
            "identity": identity,
            "run_id": os.getenv("GITHUB_RUN_ID", ""),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        encoded = base64.b64encode(
            json.dumps(record, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        ).decode("ascii")
        response = requests.put(
            url,
            headers=headers,
            json={
                "message": f"chore: acquire Telegram publisher lock for {identity}",
                "content": encoded,
                "branch": "main",
            },
            timeout=20,
        )
        if response.status_code in {200, 201}:
            content = response.json().get("content", {})
            sha = str(content.get("sha") or "")
            if not sha:
                raise RuntimeError("GitHub created the publisher lock but returned no file SHA.")
            print(f"Publisher lock acquired for episode {identity}.", flush=True)
            return {**record, "sha": sha}
        if response.status_code in {409, 422}:
            # Another worker may have created the file between GET and PUT.
            raced = _read_lock(identity, token, repository)
            if raced and raced["identity"] == identity:
                return raced
            if raced:
                continue
            if response.status_code == 409:
                continue
            raise RuntimeError("GitHub rejected publisher-lock creation and no lock file exists.")
        response.raise_for_status()
        raise RuntimeError(f"Unexpected HTTP {response.status_code} while acquiring publisher lock.")


def release_lock(
    identity: str,
    token: str | None = None,
    repository: str | None = None,
) -> bool:
    """Delete the lock only when this episode owns it and is complete."""
    identity, token, repository = _validate(identity, token, repository)
    url = _url(repository)
    headers = _headers(token)

    for _attempt in range(3):
        current = _read_lock(identity, token, repository)
        if current is None:
            return False
        if current["identity"] != identity:
            raise RuntimeError(
                f"Cannot release publisher lock owned by {current['identity']} from episode {identity}."
            )
        complete, has_any_quality = _episode_publication_status(identity, token, repository)
        if not complete and has_any_quality:
            print(
                f"Retaining publisher lock for {identity}; some video qualities were published and the episode must resume first.",
                flush=True,
            )
            return False
        if not complete:
            print(
                f"Releasing publisher lock for {identity}; no video quality was published, so the episode can retry safely.",
                flush=True,
            )
        response = requests.delete(
            url,
            headers=headers,
            json={
                "message": f"chore: release Telegram publisher lock for {identity}",
                "sha": current["sha"],
                "branch": "main",
            },
            timeout=20,
        )
        if response.status_code in {200, 204}:
            print(f"Publisher lock released for {identity}.", flush=True)
            return True
        if response.status_code == 404:
            return False
        if response.status_code == 409:
            continue
        response.raise_for_status()
        raise RuntimeError(f"Unexpected HTTP {response.status_code} while releasing publisher lock.")
    raise RuntimeError("Could not release the publisher lock after three attempts.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("acquire", "release"))
    parser.add_argument("--identity", required=True)
    args = parser.parse_args(argv)
    token = os.getenv("GITHUB_TOKEN", "")
    repository = os.getenv("GITHUB_REPOSITORY", "")
    if args.action == "acquire":
        acquire_lock(args.identity, token=token, repository=repository)
    else:
        release_lock(args.identity, token=token, repository=repository)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Publisher lock operation failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
