"""Refresh cached anime metadata and synopsis without publishing Telegram messages."""
from __future__ import annotations
import json, logging, os, time
from pathlib import Path
from typing import Callable
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import anime_catalog_metadata
LOG = logging.getLogger("animehighbo.metadata_refresh")

def merge_nonempty(old: dict, new: dict) -> dict:
    """Merge verified non-empty catalog values without erasing useful cached fields."""
    result = dict(old) if isinstance(old, dict) else {}
    curated_source = str(result.get("metadata_source") or "").casefold()
    curated = any(marker in curated_source for marker in ("exact-season record", "exact-series record", "current-season record", "official key art"))
    for key, value in (new.items() if isinstance(new, dict) else []):
        if isinstance(value, dict):
            current = result.get(key, {})
            current = dict(current) if isinstance(current, dict) else {}
            if curated:
                for subkey, subvalue in value.items():
                    if current.get(subkey) in (None, "", [], {}) and subvalue not in (None, "", [], {}):
                        current[subkey] = subvalue
                result[key] = current
            else:
                result[key] = merge_nonempty(current, value)
        elif value not in (None, "", [], {}):
            if curated and result.get(key) not in (None, "", [], {}):
                continue
            if result.get(key) in (None, "", [], {}) or key in {"description", "genres", "seasonYear", "episodes", "format", "status", "averageScore", "studios"}:
                result[key] = value
    return result

def refresh_records(records: dict, lookup: Callable[[str], dict], pause_seconds: float = 0.0) -> tuple[int, int]:
    """Refresh each named anime; preserve poster URLs and retain old metadata on outages."""
    refreshed = with_synopsis = 0
    for record in records.values():
        if not isinstance(record, dict): continue
        title = str(record.get("title") or "").strip()
        if not title: continue
        try: metadata = lookup(title)
        except Exception as exc:
            LOG.warning("Metadata lookup failed for %s: %s", title, exc)
            continue
        if not isinstance(metadata, dict) or not metadata: continue
        old = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
        merged = merge_nonempty(old, metadata)
        if merged != old:
            record["metadata"] = merged
            refreshed += 1
        if str(merged.get("description") or "").strip(): with_synopsis += 1
        cover = merged.get("coverImage") if isinstance(merged.get("coverImage"), dict) else {}
        if not record.get("poster_url"):
            record["poster_url"] = cover.get("extraLarge") or cover.get("large") or cover.get("medium") or ""
        if pause_seconds: time.sleep(pause_seconds)
    return refreshed, with_synopsis

def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cache_path = Path(os.environ.get("ANIMEHIGHBO_POSTER_CACHE", "data/anime_poster_state.json"))
    try: payload = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc: raise SystemExit(f"Could not read anime poster cache {cache_path}: {exc}")
    records = payload.get("published") if isinstance(payload, dict) else None
    if not isinstance(records, dict) or not records: raise SystemExit("Anime poster cache has no published series records.")
    refreshed, with_synopsis = refresh_records(records, lambda title: anime_catalog_metadata(title, timeout=15), pause_seconds=0.15)
    cache_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Checked {len(records)} anime; refreshed {refreshed} metadata records; {with_synopsis} have a cached synopsis.")
    return 0

if __name__ == "__main__": raise SystemExit(main())
