"""Animehighbo: safe publisher for explicitly authorized direct media feeds.

Only direct video links on configured allow-listed hosts are downloaded.
Torrent pages, HTML pages, magnets, and unapproved hosts are rejected.
"""
from __future__ import annotations

import hashlib
import html
import json
import logging
import os
import re
import shutil
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import unquote, urlparse

import requests

LOG = logging.getLogger("animehighbo")
VIDEO_EXTENSIONS = {".mkv", ".mp4", ".webm", ".mov"}
USER_AGENT = "AnimehighboPublisher/1.0 (+authorized-media-only)"


def load_config(path: str = "config.json") -> dict:
    config_path = Path(path)
    if not config_path.exists():
        raise RuntimeError(
            "config.json is missing. Copy config.example.json to config.json "
            "and add feeds and host allow-lists for content you may republish."
        )
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(config.get("feeds"), list):
        raise ValueError("'feeds' must be a list")
    if not isinstance(config.get("allowed_download_hosts"), list):
        raise ValueError("'allowed_download_hosts' must be a list")
    if not isinstance(config.get("torrent_feed_hosts", []), list):
        raise ValueError("'torrent_feed_hosts' must be a list")
    config.setdefault("torrent_feed_hosts", [])
    config.setdefault("max_episode_groups_per_run", 1)
    config.setdefault("max_file_size_mb", 45)
    max_size_override = os.getenv("ANIMEHIGHBO_MAX_FILE_SIZE_MB", "").strip()
    if max_size_override:
        try:
            max_size_mb = int(max_size_override)
        except ValueError as exc:
            raise ValueError("ANIMEHIGHBO_MAX_FILE_SIZE_MB must be an integer.") from exc
        if not 1 <= max_size_mb <= 1900:
            raise ValueError("ANIMEHIGHBO_MAX_FILE_SIZE_MB must be between 1 and 1900.")
        config["max_file_size_mb"] = max_size_mb
    config.setdefault("download_dir", "downloads")
    config.setdefault("state_file", "data/state.json")
    config.setdefault("dry_run", True)
    config.setdefault("mode", "links")
    config.setdefault("bilingual_subtitles", False)
    config.setdefault("subtitle_stream_index", None)
    config.setdefault("encode_h265", False)
    config.setdefault("h265_crf", 24)
    config.setdefault("h265_preset", "slow")
    config.setdefault("burn_persian_subtitles", False)
    if config["burn_persian_subtitles"] and not config["bilingual_subtitles"]:
        raise ValueError("'burn_persian_subtitles' requires 'bilingual_subtitles' to be enabled.")
    if config["burn_persian_subtitles"] and not config["encode_h265"]:
        raise ValueError("'burn_persian_subtitles' requires 'encode_h265' to be enabled.")
    config.setdefault("poll_limit_per_feed", 30)
    config.setdefault("request_timeout_seconds", 30)
    return config


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def parse_feed(xml_text: str, limit: int = 30) -> list[dict]:
    """Parse common RSS and Atom formats, preferring explicit media enclosures."""
    root = ET.fromstring(xml_text)
    items = []
    for node in root.iter():
        if local_name(node.tag) not in {"item", "entry"}:
            continue
        title = ""
        page_link = ""
        media_link = ""
        for child in list(node):
            name = local_name(child.tag)
            if name == "title" and not title:
                title = "".join(child.itertext()).strip()
            elif name == "enclosure" and not media_link:
                media_link = child.attrib.get("url", "").strip()
            elif name in {"content", "thumbnail"} and not media_link:
                candidate = child.attrib.get("url", "").strip()
                if Path(urlparse(candidate).path).suffix.lower() in VIDEO_EXTENSIONS:
                    media_link = candidate
            elif name == "link":
                href = child.attrib.get("href", "").strip()
                rel = child.attrib.get("rel", "alternate").lower()
                candidate = href or (child.text or "").strip()
                if candidate and rel == "enclosure" and not media_link:
                    media_link = candidate
                elif candidate and not page_link:
                    page_link = candidate
        link = media_link or page_link
        if title and link:
            items.append({"title": title, "url": link})
        if len(items) >= limit:
            break
    return items


def episode_details(title: str) -> tuple[str, str]:
    """Return a readable anime title and an episode label without guessing."""
    patterns = [
        r"(?i)\b(?:episode|ep)\s*[-.#:]?\s*(\d{1,4}(?:\.\d+)?)\b",
        r"第\s*(\d{1,4}(?:\.\d+)?)\s*[話话集期]",
        r"(?i)\bS\d{1,2}\s*E(\d{1,4})\b",
        # Common release naming: "[Group] Anime Title - 03 (1080p) [hash].mkv".
        r"\s[-–]\s*(\d{1,4})(?=\s*(?:\(|\[|$))",
    ]
    episode = ""
    clean = re.sub(r"(?i)^\s*\[SubsPlease\]\s*", "", title.strip())
    for pattern in patterns:
        match = re.search(pattern, clean)
        if match:
            episode = match.group(1)
            clean = re.sub(pattern, " ", clean).strip(" -|:[]()")
            break
    # Remove release-group wrappers, quality labels, file extensions, and hashes.
    clean = re.sub(r"(?i)\b(?:480|720|1080|1440|2160)p\b", " ", clean)
    clean = re.sub(r"(?i)\.(?:mkv|mp4|webm|mov)$", " ", clean)
    clean = re.sub(r"\[[^\]]*\]", " ", clean)
    clean = re.sub(r"\s+", " ", clean).strip(" -|:[]()")
    return (re.sub(r"\s+", " ", clean) or title.strip(), episode)


def parse_episode_feed(xml_text: str, limit: int = 30) -> list[dict]:
    """Parse RSS/Atom episode pages and poster image URLs, not video enclosures."""
    root = ET.fromstring(xml_text)
    entries = []
    for node in root.iter():
        if local_name(node.tag) not in {"item", "entry"}:
            continue
        title, page_url, image_url = "", "", ""
        for child in list(node):
            name = local_name(child.tag)
            if name == "title" and not title:
                title = "".join(child.itertext()).strip()
            elif name == "link":
                candidate = (child.attrib.get("href") or child.text or "").strip()
                rel = child.attrib.get("rel", "alternate").lower()
                if candidate and rel == "alternate" and not page_url:
                    page_url = candidate
                elif candidate and rel == "enclosure":
                    kind = child.attrib.get("type", "").lower()
                    if kind.startswith("image/") and not image_url:
                        image_url = candidate
            elif name in {"thumbnail", "image"} and not image_url:
                image_url = child.attrib.get("url", "").strip()
            elif name == "enclosure" and not image_url:
                kind = child.attrib.get("type", "").lower()
                if kind.startswith("image/"):
                    image_url = child.attrib.get("url", "").strip()
            elif name in {"description", "encoded", "summary", "content"} and not image_url:
                html = "".join(child.itertext())
                found = re.search(r"<img\b[^>]*?\bsrc=['\"]([^'\"]+)", html, re.I)
                if found:
                    image_url = found.group(1).strip()
        if title and page_url and urlparse(page_url).scheme in {"http", "https"}:
            entries.append({
                "title": title,
                "url": page_url,
                "image_url": image_url,
            })
        if len(entries) >= limit:
            break
    return entries


def page_metadata(page_url: str, timeout: int = 20) -> dict:
    """Read Open Graph/Twitter metadata from the episode page as a poster fallback."""
    response = requests.get(
        page_url, timeout=timeout, headers={"User-Agent": USER_AGENT},
        allow_redirects=True,
    )
    response.raise_for_status()
    final = urlparse(response.url)
    if final.scheme != "https":
        raise ValueError("Episode page redirected to a non-HTTPS URL.")
    html = response.text[:2_000_000]
    values = {}
    for match in re.finditer(r'<meta\b[^>]*>', html, re.I):
        tag = match.group(0)
        key_match = re.search(r"(?:property|name)=['\"]([^'\"]+)['\"]", tag, re.I)
        content = re.search(r"\bcontent=['\"]([^'\"]+)['\"]", tag, re.I)
        if key_match and content:
            values.setdefault(key_match.group(1).lower(), content.group(1).strip())
    image = values.get("og:image") or values.get("twitter:image") or values.get("twitter:image:src") or ""
    title = values.get("og:title") or values.get("twitter:title") or ""
    from urllib.parse import urljoin
    return {
        "title": title,
        "image_url": urljoin(response.url, image) if image else "",
    }


def download_poster(image_url: str, timeout: int = 20, max_bytes: int = 8 * 1024 * 1024) -> tuple[bytes, str]:
    """Download a small HTTPS poster image suitable for Telegram sendPhoto."""
    parsed = urlparse(image_url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Poster URL must be a public HTTPS URL.")
    with requests.get(
        image_url, stream=True, timeout=timeout,
        headers={"User-Agent": USER_AGENT}, allow_redirects=True,
    ) as response:
        response.raise_for_status()
        final = urlparse(response.url)
        if final.scheme != "https":
            raise ValueError("Poster redirected to a non-HTTPS URL.")
        content_type = response.headers.get("Content-Type", "").split(";", 1)[0].lower()
        if not content_type.startswith("image/"):
            raise ValueError("Poster URL did not return an image.")
        declared = response.headers.get("Content-Length")
        if declared and int(declared) > max_bytes:
            raise ValueError("Poster image exceeds the 8 MB limit.")
        chunks, total = [], 0
        for chunk in response.iter_content(chunk_size=64 * 1024):
            if not chunk:
                continue
            total += len(chunk)
            if total > max_bytes:
                raise ValueError("Poster image exceeded the 8 MB limit.")
            chunks.append(chunk)
        data = b"".join(chunks)
        if not data:
            raise ValueError("Poster image is empty.")
        return data, content_type


def translate_title_to_persian(title: str, timeout: int = 10) -> str:
    """Translate an English anime title with two free services and never return an empty field."""
    clean_title = re.sub(r"\s+", " ", str(title or "")).strip()
    if not clean_title:
        return "عنوان فارسی در دسترس نیست"
    try:
        response = requests.get(
            "https://translate.googleapis.com/translate_a/single",
            params={"client": "gtx", "sl": "en", "tl": "fa", "dt": "t", "q": clean_title},
            timeout=timeout,
            headers={"User-Agent": USER_AGENT},
        )
        response.raise_for_status()
        data = response.json()
        translated = "".join(
            part[0] for part in (data[0] if data and data[0] else [])
            if part and isinstance(part[0], str)
        ).strip()
        if translated and translated.lower() != clean_title.lower():
            return translated
    except Exception as exc:
        LOG.warning("Google Persian title translation unavailable for %r: %s", clean_title, exc)
    try:
        response = requests.get(
            "https://api.mymemory.translated.net/get",
            params={"q": clean_title, "langpair": "en|fa"},
            timeout=timeout,
            headers={"User-Agent": USER_AGENT},
        )
        response.raise_for_status()
        translated = str((response.json().get("responseData") or {}).get("translatedText") or "").strip()
        if translated and translated.lower() != clean_title.lower():
            return html.unescape(translated)
    except Exception as exc:
        LOG.warning("MyMemory Persian title translation unavailable for %r: %s", clean_title, exc)
    # Show the original title rather than an unavailable-message placeholder.
    return clean_title


def anime_japanese_title(title: str, media: dict | None = None, timeout: int = 10) -> str:
    """Return the Japanese-language title in Latin/Romaji writing, not Japanese characters."""
    media = media if isinstance(media, dict) else {}
    titles = media.get("title") or {}
    # AniList's romaji title is the preferred Japanese pronunciation in Latin letters.
    romaji = str(titles.get("romaji") or "").strip()
    if romaji:
        return romaji
    search_title = str(titles.get("english") or title or "").strip()
    if not search_title:
        return "Japanese title unavailable"
    try:
        response = requests.get(
            "https://api.jikan.moe/v4/anime",
            params={"q": search_title, "limit": 5, "sfw": "true"},
            timeout=timeout,
            headers={"User-Agent": USER_AGENT},
        )
        response.raise_for_status()
        entries = response.json().get("data") or []
        target = re.sub(r"[^a-z0-9]+", "", search_title.lower())
        for entry in entries:
            candidates = [
                str(entry.get("title_english") or ""),
                str(entry.get("title") or ""),
                *[str(item.get("title") or "") for item in (entry.get("titles") or []) if isinstance(item, dict)],
            ]
            normalized = [re.sub(r"[^a-z0-9]+", "", item.lower()) for item in candidates]
            if target and target in normalized:
                # Jikan exposes title_japanese in Japanese script and title in Romaji/Latin.
                latin_title = str(entry.get("title") or "").strip()
                if latin_title and not re.search(r"[\u3040-\u30ff\u3400-\u9fff]", latin_title):
                    return latin_title
    except Exception as exc:
        LOG.warning("Japanese Romaji title lookup unavailable for %r: %s", search_title, exc)
    # AniList's romaji field is preferable; this last fallback is never blank.
    return search_title


def anime_catalog_metadata(title: str, timeout: int = 12) -> dict:
    """Fetch anime facts from AniList; unknown data is never guessed."""
    query = """
    query ($search: String) {
      Media(search: $search, type: ANIME) {
        title { english romaji native }
        coverImage { extraLarge large medium }
        season seasonYear episodes genres format status averageScore source studios { nodes { name } } isAdult description(asHtml: false)
      }
    }
    """
    try:
        response = requests.post(
            "https://graphql.anilist.co",
            json={"query": query, "variables": {"search": title}},
            timeout=timeout,
            headers={"User-Agent": USER_AGENT, "Content-Type": "application/json"},
        )
        response.raise_for_status()
        payload = response.json()
        media = (payload.get("data") or {}).get("Media")
        if not isinstance(media, dict):
            return {}
        return media
    except Exception as exc:
        LOG.warning("Anime metadata lookup failed for %r: %s", title, exc)
        return {}


def _quality_from_title(title: str) -> str:
    match = re.search(r"(?i)\b(480|720|1080|1440|2160)\s*p\b", title)
    return f"{match.group(1)}p" if match else "نامشخص"


def _season_label(title: str, media: dict) -> str:
    catalog_title = ((media.get("title") or {}).get("english") or "")
    combined = f"{title} {catalog_title}".strip()
    normalized = combined.lower()
    if re.search(r"\b(ova|oav)\b", normalized) or media.get("format") == "OVA":
        return "OVA"
    if re.search(r"\b(ona)\b", normalized) or media.get("format") == "ONA":
        return "ONA"
    if re.search(r"\b(movie|film)\b", normalized) or media.get("format") == "MOVIE":
        return "فیلم"
    match = re.search(r"(?i)\b(?:season|s)\s*[- ]?(\d{1,2})\b", combined)
    if match:
        return f"فصل {match.group(1)}"
    ordinal = re.search(r"(?i)\b(2nd|3rd|4th|5th|second|third|fourth|fifth)\s+season\b", combined)
    if ordinal:
        number = {"2nd": "2", "second": "2", "3rd": "3", "third": "3",
                  "4th": "4", "fourth": "4", "5th": "5", "fifth": "5"}[ordinal.group(1).lower()]
        return f"فصل {number}"
    if re.search(r"(?i)\bfinal season\b", combined):
        part = re.search(r"(?i)\bpart\s*(\d+)\b", combined)
        return f"فصل پایانی، بخش {part.group(1)}" if part else "فصل پایانی"
    if media.get("format") == "SPECIAL":
        return "قسمت ویژه"
    if media.get("format") == "TV":
        return "فصل اول"
    return "نامشخص"


def _season_fa(season: str | None) -> str:
    return {
        "WINTER": "زمستان",
        "SPRING": "بهار",
        "SUMMER": "تابستان",
        "FALL": "پاییز",
    }.get(str(season or "").upper(), "نامشخص")


def _safe_caption_text(value: str) -> str:
    return html.escape(value, quote=False)


def translate_anime_synopsis(description: str) -> str:
    """Translate and localize an anime synopsis into fluent, conversational Persian."""
    source = html.unescape(re.sub(r"<[^>]+>", " ", str(description or "")))
    source = re.sub(r"\s+", " ", source).strip()
    if not source:
        return "خلاصهٔ این انیمه فعلاً در دسترس نیست."
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if api_key:
        model = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite").strip()
        endpoint = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        prompt = (
            "این متن معرفی یک انیمه است. آن را به یک خلاصهٔ فارسی روان، طبیعی و محاوره‌ای "
            "برای مخاطب ایرانی بازنویسی کن. معنی و نکات اصلی را حفظ کن، چیزی از خودت اضافه نکن، "
            "اسپویل مهم نده و فقط متن خلاصه را برگردان. حدود ۲ تا ۴ جمله و حداکثر ۵۰۰ نویسه باشد.\n\n"
            + source[:5000]
        )
        try:
            response = requests.post(
                endpoint, params={"key": api_key},
                json={"contents": [{"role": "user", "parts": [{"text": prompt}]}],
                      "generationConfig": {"temperature": 0.3, "maxOutputTokens": 220}},
                timeout=(15, 45),
            )
            response.raise_for_status()
            data = response.json()
            result = "".join(
                part.get("text", "") for part in data["candidates"][0]["content"]["parts"]
            ).strip()
            if result:
                return re.sub(r"\s+", " ", result)[:500]
        except (requests.RequestException, KeyError, IndexError, TypeError, ValueError) as exc:
            LOG.warning("Gemini synopsis localization failed: %s", exc)
    try:
        response = requests.get(
            "https://translate.googleapis.com/translate_a/single",
            params={"client": "gtx", "sl": "en", "tl": "fa", "dt": "t", "q": source[:4000]},
            timeout=15, headers={"User-Agent": USER_AGENT},
        )
        response.raise_for_status()
        data = response.json()
        translated = "".join(
            part[0] for part in (data[0] if data and data[0] else [])
            if part and isinstance(part[0], str)
        ).strip()
        if translated:
            return re.sub(r"\s+", " ", translated)[:500]
    except Exception as exc:
        LOG.warning("Fallback synopsis translation failed: %s", exc)
    return "خلاصهٔ این انیمه فعلاً در دسترس نیست."


def build_episode_caption(title: str, episode: str, page_url: str = "", metadata: dict | None = None) -> str:
    """Build a detailed poster caption with Telegram's expandable blockquote for the synopsis."""
    media = metadata if isinstance(metadata, dict) else {}
    titles = media.get("title") or {}
    english_title = str(titles.get("english") or titles.get("romaji") or episode_details(title)[0]).strip()
    persian_title = translate_title_to_persian(english_title)
    episode_number = episode or episode_details(title)[1]
    synopsis = translate_anime_synopsis(str(media.get("description") or ""))
    details = []
    season = _season_label(title, media)
    if season != "نامشخص":
        details.append(season)
    year = media.get("seasonYear")
    if year:
        details.append(f"سال پخش: {year}")
    if media.get("episodes"):
        details.append(f"تعداد قسمت‌ها: {media['episodes']}")
    genres = media.get("genres") or []
    if genres:
        details.append("ژانر: " + "، ".join(str(item) for item in genres[:6]))
    anime_format = str(media.get("format") or "").strip()
    if anime_format:
        format_fa = {"TV": "سریالی", "MOVIE": "فیلم سینمایی", "OVA": "OVA", "ONA": "ONA", "SPECIAL": "قسمت ویژه"}.get(anime_format, anime_format)
        details.append(f"قالب: {format_fa}")
    status = str(media.get("status") or "").strip()
    if status:
        status_fa = {"FINISHED": "پایان‌یافته", "RELEASING": "در حال پخش", "NOT_YET_RELEASED": "هنوز پخش نشده", "CANCELLED": "لغوشده", "HIATUS": "متوقف‌شده"}.get(status, status)
        details.append(f"وضعیت پخش: {status_fa}")
    score = media.get("averageScore")
    if score:
        details.append(f"امتیاز: {float(score) / 10:.1f}/10")
    studios = ((media.get("studios") or {}).get("nodes") or [])
    if studios:
        names = [str(item.get("name", "")) for item in studios[:3] if item.get("name")]
        if names:
            details.append("استودیو: " + "، ".join(names))
    if media.get("isAdult") is True:
        details.append("رده‌بندی: بزرگسال")
    japanese_title = anime_japanese_title(title, media)
    lines = [
        f"🎬 <b>English:</b> {_safe_caption_text(english_title)}",
        f"🇮🇷 <b>فارسی:</b> {_safe_caption_text(persian_title)}",
        f"🇯🇵 <b>日本語 (Romaji):</b> {_safe_caption_text(japanese_title)}",
    ]
    if episode_number:
        lines.append(f"📺 قسمت {_safe_caption_text(str(episode_number))}")
    lines.extend(f"ℹ️ {_safe_caption_text(item)}" for item in details)
    lines.append(f"<blockquote expandable>📝 {_safe_caption_text(synopsis)}</blockquote>")
    lines.append("@Animehighbo")
    caption = "\n".join(lines)
    if len(caption) > 1024:
        overflow = len(caption) - 1024
        synopsis = synopsis[:max(0, len(synopsis) - overflow - 1)].rstrip()
        lines[-2] = f"<blockquote expandable>📝 {_safe_caption_text(synopsis)}…</blockquote>"
        caption = "\n".join(lines)
    if len(caption) > 1024:
        lines = lines[:2] + [line for line in lines[2:-2] if len(line) < 120][:5] + lines[-2:]
        caption = "\n".join(lines)
    if len(caption) > 1024:
        caption = caption[:1000] + "…\n@Animehighbo"
    return caption

def _quality_rank(title: str) -> int:
    quality = _quality_from_title(title)
    return {"1080p": 0, "720p": 1, "480p": 2}.get(quality, 3)


def _preferred_source(entries: list[dict]) -> dict | None:
    """Choose one source only: 1080p first, then 720p; never use lower resolutions."""
    eligible = [
        entry for entry in entries
        if _quality_from_title(str(entry.get("title", ""))) in {"1080p", "720p"}
    ]
    if not eligible:
        return None
    return min(
        eligible,
        key=lambda entry: (_quality_rank(str(entry.get("title", ""))), str(entry.get("title", "")).casefold()),
    )


def _episode_record_complete(record: dict) -> bool:
    """An episode is complete only after all three requested video qualities are published."""
    return {"1080p", "720p", "480p"}.issubset(set(record.get("complete_qualities", [])))


def _episode_group(title: str) -> tuple[str, str, str]:
    anime_title, episode = episode_details(title)
    normalized = re.sub(r"\s+", " ", anime_title).strip().casefold()
    group_id = hashlib.sha256(f"{normalized}|{episode}".encode("utf-8")).hexdigest()
    return group_id, anime_title, episode


def processed_filename(title: str, quality: str, extension: str = ".mkv") -> str:
    """Return the requested minimal filename: anime title, resolution, channel handle."""
    anime_title, _ = episode_details(title)
    clean = re.sub(r"\[[^\]]*\]|\([^)]*\)", " ", anime_title)
    clean = re.sub(r"[^\w .'-]+", " ", clean, flags=re.UNICODE)
    clean = re.sub(r"\s+", " ", clean).strip(" ._-") or "Anime"
    quality = quality if quality in {"480p", "720p", "1080p", "1440p", "2160p"} else "quality-unknown"
    extension = extension if extension.startswith(".") else f".{extension}"
    return f"{clean} {quality} @Animehighbo{extension}"


def telegram_send_anime_poster(token: str, chat_id: str, title: str, episode: str,
                               metadata: dict) -> str:
    """Send the anime poster and Persian synopsis before any quality files."""
    cover = metadata.get("coverImage") or {}
    image_url = cover.get("extraLarge") or cover.get("large") or cover.get("medium") or ""
    if not image_url:
        raise RuntimeError("No poster image found in anime metadata; refusing to send files before poster.")
    image_data, image_type = download_poster(str(image_url))
    caption = build_episode_caption(title, episode, metadata=metadata)
    endpoint = telegram_api_endpoint(token, "sendPhoto")
    response = requests.post(
        endpoint,
        data={"chat_id": chat_id, "caption": caption, "parse_mode": "HTML"},
        files={"photo": ("anime-poster.jpg", image_data, image_type)},
        timeout=(30, 60),
    )
    response.raise_for_status()
    result = response.json()
    if not isinstance(result, dict) or result.get("ok") is not True:
        detail = result.get("description", "Unexpected Telegram API response.") if isinstance(result, dict) else "Unexpected Telegram API response."
        raise RuntimeError(f"Telegram sendPhoto failed: {detail}")
    return str(result.get("result", {}).get("message_id", ""))

def telegram_send_episode(token: str, chat_id: str, image_data: bytes, image_type: str,
                          title: str, episode: str, page_url: str) -> str:
    """Publish one episode poster and only the requested Persian metadata."""
    if not token or not chat_id:
        raise RuntimeError("Telegram bot token or channel ID is missing.")
    anime_title, inferred_episode = episode_details(title)
    episode = episode or inferred_episode
    catalog_title = re.sub(r"(?i)\b(?:480|720|1080|1440|2160)p\b", " ", anime_title)
    catalog_title = re.sub(r"\[[^\]]*\]|\([^)]*\)", " ", catalog_title)
    catalog_title = re.sub(r"\s+", " ", catalog_title).strip(" -_")
    media = anime_catalog_metadata(catalog_title or anime_title)
    caption = build_episode_caption(title, episode, page_url, media)
    extension = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}.get(image_type, "jpg")
    endpoint = telegram_api_endpoint(token, "sendPhoto")
    response = requests.post(
        endpoint,
        data={
            "chat_id": chat_id,
            "caption": caption,
            "parse_mode": "HTML",
        },
        files={"photo": (f"anime-poster.{extension}", image_data, image_type)},
        timeout=(30, 60),
    )
    response.raise_for_status()
    result = response.json()
    if not isinstance(result, dict) or result.get("ok") is not True:
        description = result.get("description", "Unexpected Telegram API response.") if isinstance(result, dict) else "Unexpected Telegram API response."
        raise RuntimeError(f"Telegram sendPhoto failed: {description}")
    return str(result.get("result", {}).get("message_id", ""))

def run_link_mode(config: dict, state: dict, dry_run: bool, token: str, chat_id: str) -> int:
    """Publish episode links with downloaded posters, without downloading episode videos."""
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    published_count = 0
    failure_count = 0
    for feed in config["feeds"]:
        feed_url = str(feed.get("url", "")).strip()
        feed_name = str(feed.get("name", "Anime feed")).strip()
        parsed = urlparse(feed_url)
        if parsed.scheme != "https" or not parsed.hostname:
            failure_count += 1
            LOG.error("Skipping feed with invalid HTTPS URL: %s", feed_name)
            continue
        try:
            response = session.get(feed_url, timeout=int(config["request_timeout_seconds"]), allow_redirects=True)
            response.raise_for_status()
            entries = parse_episode_feed(response.text, int(config["poll_limit_per_feed"]))
        except Exception as exc:
            failure_count += 1
            LOG.exception("Episode feed %s failed: %s", feed_name, exc)
            continue

        for entry in reversed(entries):
            page_url = entry["url"]
            identity = hashlib.sha256(page_url.rstrip("/").encode("utf-8")).hexdigest()
            if identity in state["published"]:
                continue
            try:
                metadata = {}
                image_url = entry.get("image_url", "")
                if not image_url:
                    metadata = page_metadata(page_url, int(config["request_timeout_seconds"]))
                    image_url = metadata.get("image_url", "")
                if not image_url:
                    LOG.warning("No poster image found; skipping episode: %s", entry["title"])
                    continue
                from urllib.parse import urljoin
                image_url = urljoin(page_url, image_url)
                title = entry["title"]
                if metadata.get("title") and len(title) < 4:
                    title = metadata["title"]
                anime_title, episode = episode_details(title)
                if dry_run:
                    LOG.info("[DRY RUN] Episode link: %s | episode=%s | poster=%s | %s",
                             anime_title, episode or "unknown", image_url, page_url)
                    continue
                image_data, image_type = download_poster(image_url, int(config["request_timeout_seconds"]))
                message_id = telegram_send_episode(token, chat_id, image_data, image_type, title, episode, page_url)
                state["published"][identity] = {
                    "title": anime_title,
                    "episode": episode or None,
                    "url": page_url,
                    "poster_url": image_url,
                    "feed": feed_name,
                    "message_id": message_id,
                    "published_at": int(time.time()),
                }
                save_state(config["state_file"], state)
                published_count += 1
                LOG.info("Published episode link: %s | episode=%s", anime_title, episode or "unknown")
            except Exception as exc:
                failure_count += 1
                LOG.exception("Failed to publish episode link %s: %s", entry["title"], exc)
    if failure_count:
        raise RuntimeError(
            f"Link-mode run had {failure_count} feed/post failure(s); "
            f"published {published_count} episode link(s)."
        )
    LOG.info("Link-mode run complete. Published %d episode link(s).", published_count)
    return published_count


def torrent_source_uri(source: dict) -> str:
    """Read the URI field used by older configs or the URL alias used by newer ones."""
    uri = str(source.get("uri") or "").strip()
    url = str(source.get("url") or "").strip()
    if uri and url and uri != url:
        raise ValueError("Torrent source has conflicting 'uri' and 'url' values.")
    return uri or url


def has_authorized_torrent_source(config: dict) -> bool:
    """Return whether the configuration contains an explicitly authorized source."""
    for source in config.get("torrent_sources", []):
        if not isinstance(source, dict) or source.get("authorized_for_redistribution") is not True:
            continue
        try:
            if torrent_source_uri(source):
                return True
        except ValueError:
            continue
    return False


def has_file_media_source(config: dict) -> bool:
    """Require an allow-listed direct-media feed, opt-in torrent RSS feed, or static torrent."""
    torrent_feed_hosts = {
        str(host).strip().lower().rstrip(".")
        for host in config.get("torrent_feed_hosts", [])
        if str(host).strip()
    }
    torrent_feed_configured = any(
        urlparse(str(feed.get("url", ""))).scheme == "https"
        and (urlparse(str(feed.get("url", ""))).hostname or "").lower().rstrip(".") in torrent_feed_hosts
        for feed in config.get("feeds", [])
        if isinstance(feed, dict)
    )
    return bool(
        (config.get("feeds") and config.get("allowed_download_hosts"))
        or torrent_feed_configured
        or has_authorized_torrent_source(config)
    )


def allowed_media_url(url: str, allowed_hosts: list[str]) -> bool:
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower().rstrip(".")
        allowed = {str(h).lower().strip().rstrip(".") for h in allowed_hosts}
        if parsed.scheme != "https" or not host or not allowed:
            return False
        if host not in allowed:
            return False
        if parsed.username or parsed.password:
            return False
        if parsed.path.lower().endswith(".torrent") or parsed.scheme == "magnet":
            return False
        return Path(unquote(parsed.path)).suffix.lower() in VIDEO_EXTENSIONS
    except (ValueError, TypeError):
        return False


def load_state(path: str) -> dict:
    state_path = Path(path)
    if not state_path.exists():
        return {"published": {}, "seen": {}, "posters": {}}
    state = json.loads(state_path.read_text(encoding="utf-8"))
    if not isinstance(state, dict):
        raise ValueError("State file must contain a JSON object")
    state.setdefault("published", {})
    state.setdefault("seen", {})
    state.setdefault("posters", {})
    return state


def save_state(path: str, state: dict) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(target)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_filename(title: str, url: str) -> str:
    suffix = Path(unquote(urlparse(url).path)).suffix.lower()
    stem = re.sub(r"[^\w .()-]+", "_", title, flags=re.UNICODE).strip(" ._")
    stem = re.sub(r"\s+", " ", stem)[:100] or "anime-video"
    return stem + suffix


def download_media(url: str, title: str, config: dict) -> Path:
    if not allowed_media_url(url, config["allowed_download_hosts"]):
        raise ValueError(f"Rejected non-allow-listed or non-direct media URL: {url}")
    target_dir = Path(config["download_dir"])
    target_dir.mkdir(parents=True, exist_ok=True)
    final_path = target_dir / safe_filename(title, url)
    temp_path = final_path.with_suffix(final_path.suffix + ".part")
    max_bytes = int(config["max_file_size_mb"]) * 1024 * 1024
    timeout = int(config["request_timeout_seconds"])

    try:
        with requests.get(
            url, stream=True, timeout=timeout,
            headers={"User-Agent": USER_AGENT}, allow_redirects=False
        ) as response:
            if response.status_code in (301, 302, 303, 307, 308):
                raise ValueError("Redirected media URLs are rejected; configure the final authorized host.")
            response.raise_for_status()
            declared = response.headers.get("Content-Length")
            if declared and int(declared) > max_bytes:
                raise ValueError("Media exceeds configured size limit.")
            written = 0
            with temp_path.open("wb") as output:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if not chunk:
                        continue
                    written += len(chunk)
                    if written > max_bytes:
                        raise ValueError("Media exceeded configured size limit during download.")
                    output.write(chunk)
        if temp_path.stat().st_size < 1024:
            raise ValueError("Downloaded file is too small to be a valid video.")
        with temp_path.open("rb") as check:
            header = check.read(12)
        suffix = final_path.suffix.lower()
        is_matroska = header.startswith(bytes.fromhex("1A45DFA3"))
        is_mp4_family = len(header) >= 8 and header[4:8] == b"ftyp"
        if suffix in {".mkv", ".webm"} and not is_matroska:
            raise ValueError("Downloaded content does not have a Matroska/WebM file signature.")
        if suffix in {".mp4", ".mov"} and not is_mp4_family:
            raise ValueError("Downloaded content does not have an MP4/MOV file signature.")
        temp_path.replace(final_path)
        return final_path
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise


def telegram_api_endpoint(token: str, method: str) -> str:
    """Build an API endpoint for Telegram cloud API or a local Bot API server.

    Plain HTTP is allowed only for loopback hosts because local Bot API servers
    normally listen on HTTP. Remote API endpoints must use HTTPS.
    """
    base = os.getenv("TELEGRAM_API_BASE_URL", "").strip().rstrip("/") or "https://api.telegram.org"
    parsed = urlparse(base)
    is_loopback_http = parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
    if not parsed.hostname or (parsed.scheme != "https" and not is_loopback_http):
        raise ValueError("TELEGRAM_API_BASE_URL must be HTTPS, except for a local loopback Bot API server.")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("TELEGRAM_API_BASE_URL must not contain credentials, query parameters, or a fragment.")
    return f"{base}/bot{token}/{method}"


def telegram_method_for_file(filename: str) -> str:
    """Use Telegram's video method only for MP4; keep other containers as documents."""
    return "sendVideo" if Path(filename).suffix.lower() == ".mp4" else "sendDocument"


def telegram_upload_and_send(token: str, chat_id: str, path: Path, caption: str,
                            reply_to_message_id: str | int | None = None) -> str:
    """Upload a verified media file as a reply under its anime poster."""
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN secret is not configured.")
    if not chat_id:
        raise RuntimeError("TELEGRAM_CHAT_ID secret is not configured.")
    method = telegram_method_for_file(path.name)
    endpoint = telegram_api_endpoint(token, method)
    media_field = "video" if method == "sendVideo" else "document"
    payload = {"chat_id": chat_id, "caption": caption[:1024]}
    if reply_to_message_id:
        payload["reply_to_message_id"] = str(reply_to_message_id)
    if method == "sendVideo":
        payload["supports_streaming"] = "true"

    with path.open("rb") as media:
        response = requests.post(
            endpoint,
            data=payload,
            files={media_field: (path.name, media, "application/octet-stream")},
            # Large MKV uploads to a local Bot API server can legitimately take several minutes.
            timeout=(30, 1800),
        )
    response.raise_for_status()
    result = response.json()
    if not isinstance(result, dict) or result.get("ok") is not True:
        description = result.get("description", "Unexpected Telegram API response.") if isinstance(result, dict) else "Unexpected Telegram API response."
        raise RuntimeError(f"Telegram {method} failed: {description}")
    message = result.get("result", {})
    return str(message.get("message_id", ""))

def load_archive_manifest(path: str) -> list[dict]:
    """Load a strictly ordered backfill queue of episodes authorized for redistribution."""
    manifest_path = Path(path)
    if not manifest_path.exists():
        raise FileNotFoundError(f"Archive manifest not found: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, list):
        raise ValueError("Archive manifest must be a JSON list.")
    result = []
    seen_sources = set()
    for index, item in enumerate(manifest):
        if not isinstance(item, dict):
            raise ValueError(f"Archive entry {index + 1} must be an object.")
        title = str(item.get("title", "")).strip()
        uri = torrent_source_uri(item)
        if not title or not uri:
            raise ValueError(f"Archive entry {index + 1} requires title and url/uri.")
        parsed_uri = urlparse(uri)
        uri_path = unquote(parsed_uri.path).lower()
        is_torrent_source = (
            parsed_uri.scheme.lower() == "magnet"
            or (parsed_uri.scheme.lower() == "https" and uri_path.endswith(".torrent"))
        )
        if parsed_uri.scheme.lower() not in {"https", "magnet"}:
            raise ValueError(f"Archive entry {index + 1} must use HTTPS or a Magnet URI.")
        if parsed_uri.scheme.lower() == "https" and not is_torrent_source and Path(uri_path).suffix.lower() not in VIDEO_EXTENSIONS:
            raise ValueError(
                f"Archive entry {index + 1} HTTPS media URL must end in a supported video extension or .torrent."
            )
        if item.get("authorized_for_redistribution") is not True:
            raise PermissionError(
                f"Archive entry {index + 1} must explicitly set authorized_for_redistribution=true."
            )
        if "[batch]" in title.casefold():
            raise ValueError(f"Archive entry {index + 1} must contain a single episode, not a batch.")
        _, episode = episode_details(title)
        if not episode:
            raise ValueError(f"Archive entry {index + 1} has no detectable episode number: {title}")
        identity = hashlib.sha256(("archive:" + uri).encode("utf-8")).hexdigest()
        if identity in seen_sources:
            raise ValueError(f"Duplicate archive source at entry {index + 1}: {title}")
        seen_sources.add(identity)
        result.append({
            "title": title,
            "url": uri,
            "feed": "episode archive manifest",
            "identity": identity,
            "torrent_source": is_torrent_source,
            "authorized_for_redistribution": True,
            "release_date": str(item.get("release_date") or "").strip(),
            "archive_order": index,
        })
    return result


def _poster_identity(group_id: str, archive_mode: bool = False) -> str:
    """Use fresh poster state for a channel archive rebuild after old posts are deleted."""
    return f"archive:{group_id}" if archive_mode else group_id



def publish_prepared_episodes(config: dict, state: dict, dry_run: bool, token: str, chat_id: str) -> int:
    """Publish a fully prepared source-upload-date batch in stable title order."""
    root = Path(os.getenv("ANIMEHIGHBO_PREPARED_DIR", "prepared-artifacts"))
    expected_day = os.getenv("ANIMEHIGHBO_RELEASE_DATE", "").strip()
    manifest_paths = sorted(root.glob("episode-*/manifest.json"))
    if not manifest_paths:
        raise RuntimeError(f"No prepared episode manifests found under {root}.")

    episodes = []
    invalid_bundle_count = 0
    for manifest_path in manifest_paths:
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            day = str(manifest.get("release_date") or "").strip()
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
                raise ValueError(f"Prepared episode has no valid source upload date: {manifest_path}")
            if expected_day and day != expected_day:
                raise ValueError(f"Mixed upload-day batch: expected {expected_day}, found {day}.")
            identity = str(manifest.get("identity") or "")
            if not identity:
                raise ValueError(f"Prepared episode manifest is missing identity: {manifest_path}")
            if _episode_record_complete(state["published"].get(identity, {})):
                continue
            files = {}
            for row in manifest.get("files", []):
                quality = str(row.get("quality") or "")
                filename = str(row.get("filename") or "")
                if quality not in {"1080p", "720p", "480p"} or not filename:
                    raise ValueError(f"Invalid prepared quality entry in {manifest_path}.")
                path = manifest_path.parent / filename
                if not path.is_file() or path.stat().st_size < 1024:
                    raise ValueError(f"Prepared {quality} file is missing or too small: {path}")
                if path.stat().st_size > int(config["max_file_size_mb"]) * 1024 * 1024:
                    raise ValueError(f"Prepared {quality} file exceeds configured size limit: {path}")
                actual_hash = sha256_file(path)
                if actual_hash != str(row.get("sha256") or ""):
                    raise ValueError(f"Prepared {quality} checksum mismatch: {path}")
                files[quality] = {
                    "path": path,
                    "sha256": actual_hash,
                    "output_identity": str(row.get("output_identity") or f"{identity}:{quality}"),
                }
            if len(files) != len(manifest.get("files", [])):
                raise ValueError(f"Duplicate quality in prepared manifest: {manifest_path}")
            episodes.append({**manifest, "manifest_path": manifest_path, "files": files})

        except Exception as exc:
            invalid_bundle_count += 1
            LOG.exception("Skipping invalid prepared episode bundle %s; unrelated episodes will continue: %s",
                          manifest_path.parent.name, exc)

    episodes.sort(key=lambda item: (
        item["release_date"],
        str(item.get("title") or "").casefold(),
        str(item.get("identity") or ""),
    ))

    if invalid_bundle_count:
        LOG.error("Skipped %d invalid prepared episode bundle(s); they remain eligible for a later retry.",
                  invalid_bundle_count)

    # Validate the entire episode tier before publishing the first poster.
    for episode in episodes:
        identity = episode["identity"]
        existing = state["published"].get(identity, {})
        poster_identity = str(episode.get("poster_identity") or "")
        poster_exists = bool(state["posters"].get(poster_identity, {}).get("message_id"))
        for quality in ("1080p", "720p", "480p"):
            output_identity = f"{identity}:{quality}"
            if output_identity in state["published"]:
                continue
            if quality not in episode["files"]:
                raise RuntimeError(
                    f"Upload-date batch is incomplete: {episode.get('title')} is missing {quality}."
                )
        if not poster_exists and any(
            f"{identity}:{quality}" in state["published"]
            for quality in ("1080p", "720p", "480p")
        ):
            raise RuntimeError(f"Episode {episode.get('title')} has delivered files but no recorded poster.")

    if dry_run:
        LOG.info("[DRY RUN] Prepared %d episodes for episode tier %s; no Telegram posts sent.",
                 len(episodes), expected_tier if expected_tier is not None else "unknown")
        return 0

    published_count = 0
    publication_failure_count = 0
    for episode in episodes:
        try:
            identity = episode["identity"]
            title = str(episode.get("title") or "")
            episode_number = str(episode.get("episode") or "")
            poster_identity = str(episode.get("poster_identity") or "")
            group_id = str(episode.get("group_id") or "")
            if not poster_identity or not group_id:
                raise ValueError(f"Prepared episode is missing poster identity: {title}")

            if not state["posters"].get(poster_identity, {}).get("message_id"):
                metadata = anime_catalog_metadata(title)
                poster_message_id = telegram_send_anime_poster(
                    token, chat_id, title, episode_number, metadata
                )
                state["posters"][poster_identity] = {
                    "title": title,
                    "episode": episode_number or None,
                    "message_id": poster_message_id,
                    "published_at": int(time.time()),
                    "release_date": episode["release_date"],
                }
                save_state(config["state_file"], state)

            poster_message_id = state["posters"][poster_identity].get("message_id")
            if not poster_message_id:
                raise RuntimeError(f"Poster message ID missing for {title}; refusing unthreaded upload.")

            for quality in ("1080p", "720p", "480p"):
                output_identity = f"{identity}:{quality}"
                if output_identity in state["published"]:
                    continue
                output = episode["files"].get(quality)
                if output is None:
                    raise RuntimeError(f"Missing prepared {quality} output for {title}.")
                file_hash = output["sha256"]
                duplicate_of = state["seen"].get(file_hash)
                if duplicate_of and duplicate_of != output_identity:
                    raise ValueError(f"Prepared {quality} file duplicates {duplicate_of}; refusing duplicate upload.")
                message_id = telegram_upload_and_send(
                    token, chat_id, output["path"], f"{title} {quality} @Animehighbo",
                    reply_to_message_id=poster_message_id,
                )
                state["seen"][file_hash] = output_identity
                state["published"][output_identity] = {
                    "title": title,
                    "quality": quality,
                    "source_quality": episode.get("source_quality", "1080p"),
                    "feed": episode.get("feed", "episode archive manifest"),
                    "file_sha256": file_hash,
                    "message_id": message_id,
                    "published_at": int(time.time()),
                    "bilingual_subtitles": bool(config.get("bilingual_subtitles", False)),
                }
                save_state(config["state_file"], state)
                published_count += 1
                LOG.info("Published %s %s as a reply to poster %s", title, quality, poster_message_id)

            state["published"][identity] = {
                "title": title,
                "complete_qualities": ["1080p", "720p", "480p"],
                "source_quality": episode.get("source_quality", "1080p"),
                "published_at": int(time.time()),
                "release_date": episode["release_date"],
            }
            save_state(config["state_file"], state)
            LOG.info("Completed episode %s (episode tier %s, source upload date %s).",
                     title, episode.get("episode"), episode["release_date"])
        except OSError:
            # State-file I/O failures are global safety failures; stop rather than
            # continue with unpersisted duplicate-prevention state.
            raise
        except Exception as exc:
            publication_failure_count += 1
            LOG.exception("Publishing episode %s failed; continuing with unrelated episodes: %s",
                          episode.get("title", episode.get("identity", "unknown")), exc)
    if publication_failure_count:
        LOG.error("%d prepared episode(s) failed during Telegram publishing and remain eligible for retry.",
                  publication_failure_count)
    return published_count

def run() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    config = load_config()
    state = load_state(config["state_file"])
    dry_run_setting = os.getenv("ANIMEHIGHBO_DRY_RUN", os.getenv("ANIMEHIGH_DRY_RUN", "")).strip().lower()
    if dry_run_setting:
        dry_run = dry_run_setting not in {"0", "false", "no", "off"}
    else:
        dry_run = bool(config["dry_run"])
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    prepare_only = os.getenv("ANIMEHIGHBO_PREPARE_ONLY", "").strip().lower() in {"1", "true", "yes", "on"}
    publish_prepared = os.getenv("ANIMEHIGHBO_PUBLISH_PREPARED", "").strip().lower() in {"1", "true", "yes", "on"}

    if publish_prepared:
        if not dry_run and (not token or not chat_id):
            raise RuntimeError("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID secrets before live publishing.")
        return 0 if not has_file_media_source(config) else publish_prepared_episodes(config, state, dry_run, token, chat_id)

    if config.get("mode", "links") == "links":
        if not config["feeds"]:
            LOG.warning("No RSS feeds configured. Nothing to process.")
            return 0
        if not dry_run and (not token or not chat_id):
            raise RuntimeError("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID secrets before live publishing.")
        try:
            run_link_mode(config, state, dry_run, token, chat_id)
        except RuntimeError as exc:
            LOG.error("Link-mode publisher failed: %s", exc)
            return 1
        return 0

    if not has_file_media_source(config):
        LOG.warning(
            "No authorized file source configured. Add a feed with an exact allowed_download_hosts entry "
            "or an explicitly authorized torrent source."
        )
        return 0

    if not prepare_only and not dry_run and (not token or not chat_id):
        raise RuntimeError("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID secrets before live publishing.")

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    published_count = 0
    failure_count = 0
    pending = []
    archive_mode = bool(config.get("archive_mode", False))
    if archive_mode:
        try:
            pending.extend(load_archive_manifest(str(config.get("archive_manifest", "data/archive_manifest.json"))))
        except Exception as exc:
            LOG.exception("Could not load archive manifest: %s", exc)
            return 1
        LOG.info("Archive mode enabled; loaded %d ordered episode entries.", len(pending))
    for feed in ([] if archive_mode else config["feeds"]):
        feed_url = str(feed.get("url", "")).strip()
        feed_name = str(feed.get("name", "Authorized feed")).strip()
        parsed_feed_url = urlparse(feed_url)
        if parsed_feed_url.scheme != "https" or not parsed_feed_url.hostname:
            LOG.error("Skipping feed with invalid HTTPS URL: %s", feed_name)
            continue
        try:
            response = session.get(
                feed_url,
                timeout=int(config["request_timeout_seconds"]),
                allow_redirects=True,
            )
            response.raise_for_status()
            entries = parse_feed(response.text, int(config["poll_limit_per_feed"]))
        except Exception as exc:
            failure_count += 1
            LOG.exception("Feed %s failed: %s", feed_name, exc)
            continue

        feed_host = (parsed_feed_url.hostname or "").lower().rstrip(".")
        torrent_feed_hosts = {
            str(host).strip().lower().rstrip(".")
            for host in config.get("torrent_feed_hosts", [])
        }
        is_torrent_feed = feed_host in torrent_feed_hosts
        # SubsPlease puts the newest release first. Preserve that order for the
        # opt-in torrent feed; direct-media feeds retain the existing ordering.
        ordered_entries = entries if is_torrent_feed else reversed(entries)
        for entry in ordered_entries:
            entry_url = str(entry.get("url", "")).strip()
            if allowed_media_url(entry_url, config["allowed_download_hosts"]):
                pending.append({**entry, "feed": feed_name})
                continue
            if is_torrent_feed and urlparse(entry_url).scheme.lower() == "magnet":
                if "[batch]" in str(entry.get("title", "")).lower():
                    LOG.info("Skipping multi-episode batch release: %s", entry["title"])
                    continue
                _, episode_number = episode_details(str(entry.get("title", "")))
                if not episode_number:
                    LOG.info("Skipping release without a detectable episode number: %s", entry["title"])
                    continue
                try:
                    from tools.torrent_downloader import validate_torrent_source
                    validate_torrent_source(entry_url, config.get("allowed_torrent_hosts", []))
                except (ValueError, ImportError) as exc:
                    LOG.warning("Skipping invalid magnet in feed %s: %s", feed_name, exc)
                    continue
                identity = hashlib.sha256(("torrent:" + entry_url).encode("utf-8")).hexdigest()
                pending.append({
                    **entry, "feed": feed_name, "identity": identity,
                    "torrent_source": True, "authorized_for_redistribution": True,
                })
            else:
                LOG.info("Skipping non-direct, non-magnet, or non-allow-listed entry: %s", entry["title"])

    # Static one-off torrent sources are ignored in archive mode so they cannot jump the queue.
    for source in ([] if archive_mode else config.get("torrent_sources", [])):
        if not isinstance(source, dict) or source.get("authorized_for_redistribution") is not True:
            LOG.warning("Skipping torrent source without explicit redistribution authorization.")
            continue
        title = str(source.get("title", "")).strip()
        try:
            uri = torrent_source_uri(source)
        except ValueError as exc:
            LOG.error("Skipping invalid torrent source %s: %s", title or "(untitled)", exc)
            continue
        if not title or not uri:
            LOG.warning("Skipping torrent source missing title or URI/URL.")
            continue
        identity = hashlib.sha256(("torrent:" + uri).encode("utf-8")).hexdigest()
        if identity not in state["published"] or not _episode_record_complete(state["published"][identity]):
            pending.append({
                "title": title, "url": uri, "feed": "authorized torrent",
                "identity": identity, "torrent_source": True,
                "authorized_for_redistribution": True,
            })

    requested_identity = os.getenv("ANIMEHIGHBO_EPISODE_ID", "").strip()
    if prepare_only:
        if not requested_identity:
            raise RuntimeError("ANIMEHIGHBO_PREPARE_ONLY requires ANIMEHIGHBO_EPISODE_ID.")
        pending = [
            entry for entry in pending
            if str(entry.get("identity") or hashlib.sha256(("archive:" + str(entry.get("url") or "")).encode("utf-8")).hexdigest())
            == requested_identity
        ]
        if not pending:
            raise RuntimeError(f"Requested episode identity was not found in the current manifest: {requested_identity}")

    groups = {}
    for entry in pending:
        group_id, anime_title, episode = _episode_group(entry["title"])
        identity = entry.get("identity") or hashlib.sha256(entry["url"].encode("utf-8")).hexdigest()
        existing_record = state["published"].get(identity, {})
        if _episode_record_complete(existing_record):
            continue
        group = groups.setdefault(group_id, {
            "title": anime_title, "episode": episode, "entries": [],
            "release_date": str(entry.get("release_date") or ""),
        })
        group["entries"].append({**entry, "identity": identity})

    # Process oldest source-upload dates first. The coordinator normally narrows
    # workers to one date; this ordering is also a safe fallback for single runs.
    groups = dict(sorted(groups.items(), key=lambda pair: (
        pair[1].get("release_date") or "9999-12-31",
        pair[1]["title"].casefold(),
        int(pair[1]["episode"] or 0),
    )))
    max_groups = max(1, int(config.get("max_episode_groups_per_run", 1)))
    groups = dict(list(groups.items())[:max_groups])

    # Each anime/episode group is handled independently: poster first, then 1080p, 720p, 480p.
    for group_id, group in groups.items():
        entries = sorted(
            group["entries"],
            key=lambda item: (_quality_rank(item["title"]), item["title"].casefold()),
        )
        selected_source = _preferred_source(entries)
        if selected_source is None:
            LOG.warning("No 1080p or 720p release available for %s; skipping only this group.", group["title"])
            continue
        # Download one source only. Lower-resolution releases are never downloaded.
        entries = [selected_source]
        LOG.info("Selected single %s source for %s.", _quality_from_title(selected_source["title"]), group["title"])
        group_failed = False
        try:
            poster_identity = _poster_identity(group_id, archive_mode)
            if poster_identity not in state["posters"] and dry_run:
                LOG.info(
                    "[DRY RUN] Would prepare the source and bilingual subtitles, then send poster + qualities: %s (%s)",
                    group["title"], ", ".join(_quality_from_title(item["title"]) for item in entries),
                )
                continue

            for entry in entries:
                identity = entry["identity"]
                existing_record = state["published"].get(identity, {})
                if {"1080p", "720p", "480p"}.issubset(set(existing_record.get("complete_qualities", []))):
                    continue
                media_path = None
                publish_path = None
                generated_paths = []
                try:
                    if entry.get("torrent_source"):
                        from tools.torrent_downloader import download_authorized_torrent
                        media_path = download_authorized_torrent(
                            entry["url"], entry["title"], config,
                            authorized_for_redistribution=entry.get("authorized_for_redistribution") is True,
                        )
                    else:
                        media_path = download_media(entry["url"], entry["title"], config)
                    publish_path = media_path
                    if config.get("bilingual_subtitles", False):
                        from tools.bilingual_subtitles import process as process_bilingual_subtitles
                        bilingual_path = media_path.with_name(media_path.stem + ".bilingual.mkv")
                        english_srt = bilingual_path.with_name(bilingual_path.stem + ".en.srt")
                        persian_srt = bilingual_path.with_name(bilingual_path.stem + ".fa.srt")
                        persian_ass = bilingual_path.with_name(bilingual_path.stem + ".fa.ass")
                        generated_paths.extend([bilingual_path, english_srt, persian_srt, persian_ass])
                        process_bilingual_subtitles(media_path, bilingual_path, config.get("subtitle_stream_index"))
                        publish_path = bilingual_path
                        LOG.info("Prepared bilingual MKV with English and Persian subtitle tracks.")

                    # Prepare and validate every required quality before sending a new poster.
                    # This prevents a poster from appearing when any encode is invalid or oversized.
                    source_quality = _quality_from_title(entry["title"])
                    variants = [
                        ("1080p", 1080, str(config.get("h265_bitrate_1080", "1800k"))),
                        ("720p", 720, str(config.get("h265_bitrate_720", "1000k"))),
                        ("480p", 480, str(config.get("h265_bitrate_480", "550k"))),
                    ]
                    if not config.get("encode_h265", False):
                        variants = [(source_quality, None, "")]
                    from tools.video_encoder import encode_h265
                    persian_srt = publish_path.with_name(publish_path.stem + ".fa.srt")
                    burn_subtitle = persian_srt if config.get("burn_persian_subtitles", False) else None
                    completed_qualities = []
                    prepared_outputs = {}

                    for quality, target_height, video_bitrate in variants:
                        output_identity = f"{identity}:{quality}"
                        if output_identity in state["published"]:
                            previous = state["published"][output_identity]
                            if previous.get("duplicate_of"):
                                raise ValueError(
                                    f"{quality} was previously marked as a duplicate; refusing to count it as a delivered file."
                                )
                            completed_qualities.append(quality)
                            continue

                        variant_source = publish_path
                        if config.get("encode_h265", False):
                            encoded_path = publish_path.with_name(
                                processed_filename(entry["title"], quality, ".mkv")
                            )
                            generated_paths.append(encoded_path)
                            encode_h265(
                                variant_source, encoded_path,
                                crf=int(config.get("h265_crf", 24)),
                                preset=str(config.get("h265_preset", "medium")),
                                burn_subtitle=burn_subtitle,
                                video_bitrate=video_bitrate,
                                audio_bitrate=str(config.get("h265_audio_bitrate", "128k")),
                                target_height=target_height,
                            )
                            output_path = encoded_path
                            LOG.info(
                                "Encoded and validated %s H.265 output with video bitrate %s and AAC audio %s.",
                                quality, video_bitrate, config.get("h265_audio_bitrate", "128k"),
                            )
                        else:
                            final_name = processed_filename(
                                entry["title"], quality, variant_source.suffix.lower()
                            )
                            output_path = variant_source.with_name(final_name)
                            if output_path != variant_source:
                                variant_source.replace(output_path)
                                generated_paths.append(output_path)

                        if output_path.stat().st_size > int(config["max_file_size_mb"]) * 1024 * 1024:
                            raise ValueError(f"{quality} output exceeds the configured media size limit.")
                        file_hash = sha256_file(output_path)
                        duplicate_of = state["seen"].get(file_hash)
                        if duplicate_of and duplicate_of != output_identity:
                            raise ValueError(
                                f"{quality} output duplicates {duplicate_of}; refusing to publish a poster or mark the quality complete."
                            )
                        prepared_outputs[quality] = (output_path, file_hash, output_identity)

                    # Preparation workers leave all validated outputs as an artifact.
                    # Only the single publisher job is allowed to contact Telegram.
                    if prepare_only:
                        if poster_identity not in state["posters"] and (
                            completed_qualities or len(prepared_outputs) != len(variants)
                        ):
                            raise RuntimeError(
                                "Refusing to hand off an episode until all three quality files are prepared."
                            )
                        prepared_dir = Path("prepared") / identity
                        prepared_dir.mkdir(parents=True, exist_ok=True)
                        file_rows = []
                        for quality, (output_path, file_hash, output_identity) in prepared_outputs.items():
                            target_path = prepared_dir / output_path.name
                            shutil.copy2(output_path, target_path)
                            file_rows.append({
                                "quality": quality,
                                "filename": target_path.name,
                                "sha256": file_hash,
                                "output_identity": output_identity,
                            })
                        prepared_manifest = {
                            "identity": identity,
                            "group_id": group_id,
                            "poster_identity": poster_identity,
                            "title": group["title"],
                            "episode": group["episode"],
                            "release_date": str(selected_source.get("release_date") or group.get("release_date") or ""),
                            "feed": selected_source.get("feed", "episode archive manifest"),
                            "source_quality": source_quality,
                            "files": sorted(file_rows, key=lambda row: _quality_rank(row["quality"])),
                        }
                        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", prepared_manifest["release_date"]):
                            raise ValueError(f"Source upload date is missing or invalid for {group['title']}.")
                        (prepared_dir / "manifest.json").write_text(
                            json.dumps(prepared_manifest, ensure_ascii=False, indent=2) + "\\n",
                            encoding="utf-8",
                        )
                        LOG.info("Prepared episode artifact for %s (%s): %d missing quality file(s).",
                                 group["title"], prepared_manifest["release_date"], len(file_rows))
                        continue

                    # A new poster must have all required, validated output files ready.
                    if poster_identity not in state["posters"]:
                        if completed_qualities or len(prepared_outputs) != len(variants):
                            raise RuntimeError(
                                "Refusing to publish a new poster until all three quality files are prepared."
                            )
                        metadata = anime_catalog_metadata(group["title"])
                        poster_message_id = telegram_send_anime_poster(
                            token, chat_id, group["title"], group["episode"], metadata
                        )
                        state["posters"][poster_identity] = {
                            "title": group["title"], "episode": group["episode"] or None,
                            "message_id": poster_message_id, "published_at": int(time.time()),
                        }
                        save_state(config["state_file"], state)

                    poster_message_id = state["posters"][poster_identity].get("message_id")
                    if not poster_message_id:
                        raise RuntimeError("Poster state has no Telegram message ID; refusing to send ungrouped files.")

                    for quality, _target_height, _video_bitrate in variants:
                        output_identity = f"{identity}:{quality}"
                        if output_identity in state["published"]:
                            if quality not in completed_qualities:
                                completed_qualities.append(quality)
                            continue
                        output_path, file_hash, _ = prepared_outputs[quality]
                        file_caption = f"{group['title']} {quality} @Animehighbo"
                        message_id = telegram_upload_and_send(
                            token, chat_id, output_path, file_caption,
                            reply_to_message_id=poster_message_id,
                        )
                        state["seen"][file_hash] = output_identity
                        state["published"][output_identity] = {
                            "title": group["title"], "quality": quality, "source_quality": source_quality,
                            "feed": entry["feed"], "file_sha256": file_hash, "message_id": message_id,
                            "published_at": int(time.time()),
                            "bilingual_subtitles": bool(config.get("bilingual_subtitles", False)),
                        }
                        save_state(config["state_file"], state)
                        published_count += 1
                        completed_qualities.append(quality)
                        LOG.info("Published %s %s as %s", group["title"], quality, output_path.name)
                    if len(set(completed_qualities)) == len(variants):
                        state["published"][identity] = {
                            "title": group["title"], "complete_qualities": completed_qualities,
                            "source_quality": source_quality, "published_at": int(time.time()),
                        }
                        save_state(config["state_file"], state)
                except Exception as exc:
                    failure_count += 1
                    group_failed = True
                    LOG.exception("Failed to process %s; continuing with unrelated anime: %s", entry["title"], exc)
                finally:
                    cleanup = set(generated_paths)
                    if media_path:
                        cleanup.add(media_path)
                    if publish_path:
                        cleanup.add(publish_path)
                    for path in cleanup:
                        if path.exists():
                            path.unlink(missing_ok=True)
                if group_failed:
                    break
            if group_failed:
                continue
        except Exception as exc:
            failure_count += 1
            LOG.exception("Could not complete anime group %s; continuing with unrelated groups: %s",
                          group["title"], exc)
            continue
    if failure_count:
        LOG.error(
            "Run completed with %d failed media item(s); published %d item(s). "
            "Returning a non-zero exit code so GitHub Actions exposes the failure.",
            failure_count, published_count,
        )
        return 1
    LOG.info("Run complete. Published %d item(s).", published_count)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(run())
    except Exception as error:
        logging.basicConfig(level=logging.INFO)
        logging.error("%s", error)
        raise SystemExit(1)
