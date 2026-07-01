#!/usr/bin/env python3
"""
EDOPro Standard/Rush HD Pics Downloader - tkinter Python GUI edition.

The app uses only Python standard library for GUI, SQLite .cdb reading,
downloads, settings, threading, and API lookups. Python's standard library
cannot decode/re-encode/resize JPG/PNG images, so Pillow is optional and used
only for conversion/resizing. Without Pillow, full-quality downloads are still
preserved when the downloaded format already matches the selected output format.
"""

from __future__ import annotations

import concurrent.futures
import configparser
import hashlib
import json
import os
import queue
import re
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from collections import Counter, defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import tkinter as tk
from tkinter import messagebox, ttk

try:
    from PIL import Image  # type: ignore
    PILLOW_AVAILABLE = True
except Exception:
    Image = None  # type: ignore
    PILLOW_AVAILABLE = False


# ==================== Paths and Constants ====================

SCRIPT_DIR = Path(__file__).resolve().parent
APP_VERSION = "1.0.0"
GITHUB_REPO = "TrainStream/EDOPro-Standard-Rush-HD-Pics-Downloader"
GITHUB_LATEST_RELEASE_API = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
GITHUB_RELEASES_URL = f"https://github.com/{GITHUB_REPO}/releases"
API_URL = "https://db.ygoprodeck.com/api/v7/cardinfo.php"
IMG_BASE_URL = "https://images.ygoprodeck.com/images/cards"
RUSH_HD_BASE_URL = "https://raw.githubusercontent.com/Yoshi80/Rush-HD-Pictures/master/pics"
RUSH_HD_ORR_BASE_URL = "https://raw.githubusercontent.com/Yoshi80/Rush-HD-ORR-Extension/master/pics"
FIELDS_GITHUB_BASE_URL = "https://raw.githubusercontent.com/TrainStream/EDOPro-Standard-Rush-HD-Pics-Downloader/main/Fields"
DEFAULT_PICS_DIR = SCRIPT_DIR / "pics"
RUSH_CDB_PATH = SCRIPT_DIR / "expansions" / "cards-rush.cdb"
REPOSITORIES_DIR = SCRIPT_DIR / "repositories"
SETTINGS_PATH = SCRIPT_DIR / "EDOPro-Standard-Rush-HD-Pics-Downloader.ini"
MAX_CONCURRENCY = 12
RETRY_COUNT = 3
TIMEOUT_SECONDS = 30
USER_AGENT = f"EDOPro-Standard-Rush-HD-Pics-Downloader/{APP_VERSION} (https://github.com/TrainStream/EDOPro-Standard-Rush-HD-Pics-Downloader)"
FIELD_TYPE_FLAG = 524288
YUGIPEDIA_CONCURRENCY = 3
YUGIPEDIA_SEMAPHORE = threading.BoundedSemaphore(YUGIPEDIA_CONCURRENCY)
YUGIPEDIA_CACHE_LOCK = threading.RLock()
YUGIPEDIA_RATE_LOCK = threading.Lock()
YUGIPEDIA_MIN_INTERVAL_SECONDS = 0.0
YUGIPEDIA_LAST_REQUEST_TIME = 0.0
YUGIPEDIA_IMAGE_CACHE: dict[str, Optional[str]] = {}
YUGIPEDIA_ALLIMAGES_CACHE: dict[str, Optional[str]] = {}
YUGIPEDIA_GALLERY_CACHE: dict[str, list["YugipediaGalleryCandidate"]] = {}
YUGIPEDIA_DUPLICATE_VARIANT_CACHE: dict[tuple[str, int, int], Optional[tuple[str, str]]] = {}
YGOPRODECK_RATE_LOCK = threading.Lock()
YGOPRODECK_REQUEST_TIMES: deque[float] = deque()
YGOPRODECK_MAX_REQUESTS_PER_SECOND = 20
YGOPRODECK_JSON_CACHE: dict[str, Any] = {}


# ==================== Data Models ====================

@dataclass(frozen=True)
class CardJob:
    id: int
    name: str = ""
    kind: str = "Rush"
    image_urls: tuple[str, ...] = ()
    is_field_spell: bool = False
    cropped_image_url: str = ""
    is_rush_field_spell: bool = False
    search_names: tuple[str, ...] = ()
    rush_duplicate_index: int = -1
    rush_duplicate_total: int = 0


@dataclass(frozen=True)
class CardResult:
    status: str
    card_id: int
    name: str
    kind: str
    yugipedia_lookup_name: str = ""
    yugipedia_file_title: str = ""


@dataclass(frozen=True)
class Options:
    force: bool
    use_rush_hd: bool
    use_ygo: bool
    use_yugipedia: bool
    use_fields_github: bool
    output_format: str
    resize_mode: str
    jpg_quality: str
    delete_opposite: bool
    skip_larger_existing: bool
    use_rush_duplicate_fallback_choice: bool
    prefer_rush_orr_extension: bool


class DownloadError(Exception):
    def __init__(self, message: str, status_code: int = 0):
        super().__init__(message)
        self.status_code = status_code


class NeedPillowError(DownloadError):
    pass


# ==================== Settings ====================

DEFAULT_SETTINGS = {
    "DefaultsVersion": "2",
    "ForceOverwrite": "1",
    "UseRushHd": "1",
    "UseYgoProDeck": "1",
    "UseYugipedia": "1",
    "UseFieldsGithub": "1",
    "DeleteOppositeBeforeStart": "1",
    "SkipLargerExisting": "1",
    "OutputFormat": "JPG",
    "OutputSize": "Full Resolution",
    "JpgQuality": "Balanced",
    "DownloadRegular": "1",
    "DownloadRush": "1",
    "ReportRushDuplicateNames": "0",
    "UseRushDuplicateFallbackChoice": "1",
    "PreferRushOrrExtension": "0",
}

FULL_RESOLUTION_SIZE = "Full Resolution"

CHECKBOX_SETTING_KEYS = {
    "ForceOverwrite",
    "UseRushHd",
    "UseYgoProDeck",
    "UseYugipedia",
    "UseFieldsGithub",
    "DeleteOppositeBeforeStart",
    "SkipLargerExisting",
    "DownloadRegular",
    "DownloadRush",
    "UseRushDuplicateFallbackChoice",
}


def load_settings() -> dict[str, str]:
    cfg = configparser.ConfigParser()
    values = dict(DEFAULT_SETTINGS)
    if SETTINGS_PATH.exists():
        cfg.read(SETTINGS_PATH, encoding="utf-8")
        if cfg.has_section("Settings"):
            values.update(dict(cfg.items("Settings")))
    lowered = {k.lower(): v for k, v in values.items()}
    loaded = {key: lowered.get(key.lower(), default) for key, default in DEFAULT_SETTINGS.items()}
    if lowered.get("defaultsversion") != DEFAULT_SETTINGS["DefaultsVersion"]:
        for key in CHECKBOX_SETTING_KEYS:
            loaded[key] = "1"
        loaded["DefaultsVersion"] = DEFAULT_SETTINGS["DefaultsVersion"]
    return loaded


def save_settings(values: dict[str, str]) -> None:
    cfg = configparser.ConfigParser()
    cfg["Settings"] = values
    with SETTINGS_PATH.open("w", encoding="utf-8") as f:
        cfg.write(f)


def as_bool(value: str, default: bool = False) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def normalize_resize_mode(resize_mode: str) -> str:
    return FULL_RESOLUTION_SIZE if resize_mode == "Full quality" else resize_mode


def clear_yugipedia_caches() -> None:
    with YUGIPEDIA_CACHE_LOCK:
        YUGIPEDIA_IMAGE_CACHE.clear()
        YUGIPEDIA_ALLIMAGES_CACHE.clear()
        YUGIPEDIA_GALLERY_CACHE.clear()
        YUGIPEDIA_DUPLICATE_VARIANT_CACHE.clear()
    with YGOPRODECK_RATE_LOCK:
        YGOPRODECK_REQUEST_TIMES.clear()
    YGOPRODECK_JSON_CACHE.clear()


# ==================== HTTP and Image Helpers ====================

def is_ygoprodeck_url(url: str) -> bool:
    host = urllib.parse.urlparse(url).netloc.lower()
    return host.endswith("ygoprodeck.com")


def throttle_ygoprodeck_request(url: str) -> None:
    if not is_ygoprodeck_url(url):
        return
    with YGOPRODECK_RATE_LOCK:
        now = time.monotonic()
        while YGOPRODECK_REQUEST_TIMES and now - YGOPRODECK_REQUEST_TIMES[0] >= 1.0:
            YGOPRODECK_REQUEST_TIMES.popleft()
        if len(YGOPRODECK_REQUEST_TIMES) >= YGOPRODECK_MAX_REQUESTS_PER_SECOND:
            wait = 1.0 - (now - YGOPRODECK_REQUEST_TIMES[0])
            if wait > 0:
                time.sleep(wait)
            now = time.monotonic()
            while YGOPRODECK_REQUEST_TIMES and now - YGOPRODECK_REQUEST_TIMES[0] >= 1.0:
                YGOPRODECK_REQUEST_TIMES.popleft()
        YGOPRODECK_REQUEST_TIMES.append(time.monotonic())

def retry_after_seconds(value: Optional[str], max_wait: float = 60.0) -> Optional[float]:
    if not value:
        return None
    value = value.strip()
    if not value:
        return None
    try:
        return max(0.0, min(float(value), max_wait))
    except ValueError:
        pass
    try:
        retry_at = datetime.strptime(value, "%a, %d %b %Y %H:%M:%S GMT").replace(tzinfo=timezone.utc)
        wait = (retry_at - datetime.now(timezone.utc)).total_seconds()
        return max(0.0, min(wait, max_wait))
    except ValueError:
        return None


def request_bytes(url: str, timeout: int = TIMEOUT_SECONDS, allow_small: bool = False, retries: int = RETRY_COUNT) -> tuple[bytes, str]:
    last_error: Optional[Exception] = None
    attempts = max(1, retries)
    for attempt in range(attempts):
        throttle_ygoprodeck_request(url)
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                content_type = resp.headers.get("Content-Type", "")
                data = resp.read()
            if not allow_small and len(data) < 1024:
                raise DownloadError("Downloaded file is unexpectedly small.")
            return data, content_type
        except urllib.error.HTTPError as exc:
            # Missing files are real misses. Retrying them just slows down large runs.
            if exc.code in {400, 404}:
                raise DownloadError(f"HTTP {exc.code} for {url}", exc.code) from exc
            last_error = DownloadError(f"HTTP {exc.code} for {url}", exc.code)
            server_wait = retry_after_seconds(exc.headers.get("Retry-After")) if exc.code in {429, 503} else None
        except Exception as exc:
            last_error = DownloadError(str(exc), 0)
            server_wait = None
        if attempt < attempts - 1:
            time.sleep(server_wait if server_wait is not None else 0.5 * (attempt + 1))
    if isinstance(last_error, DownloadError):
        raise last_error
    raise DownloadError(str(last_error or "Download failed."), 0)


def request_json(url: str, timeout: int = TIMEOUT_SECONDS) -> Any:
    if is_ygoprodeck_url(url):
        cached = YGOPRODECK_JSON_CACHE.get(url)
        if cached is not None:
            return cached
    data, _ = request_bytes(url, timeout, allow_small=True)
    parsed = json.loads(data.decode("utf-8"))
    if is_ygoprodeck_url(url):
        YGOPRODECK_JSON_CACHE[url] = parsed
    return parsed


# ==================== GitHub Update Check ====================

def parse_version_parts(value: str) -> tuple[int, ...]:
    parts = re.findall(r"\d+", value)
    return tuple(int(part) for part in parts) if parts else (0,)


def is_newer_version(remote: str, local: str) -> bool:
    remote_parts = parse_version_parts(remote)
    local_parts = parse_version_parts(local)
    width = max(len(remote_parts), len(local_parts))
    remote_parts += (0,) * (width - len(remote_parts))
    local_parts += (0,) * (width - len(local_parts))
    return remote_parts > local_parts


def get_latest_github_release(timeout: int = TIMEOUT_SECONDS) -> tuple[str, str]:
    try:
        data = request_json(GITHUB_LATEST_RELEASE_API, timeout)
    except DownloadError as exc:
        if exc.status_code == 404:
            raise DownloadError("No GitHub Releases are published for this repository yet.") from exc
        raise
    if not isinstance(data, dict):
        raise DownloadError("GitHub returned an unexpected response.")
    tag = str(data.get("tag_name") or "").strip()
    if not tag:
        raise DownloadError("GitHub latest release has no tag name.")
    url = str(data.get("html_url") or GITHUB_RELEASES_URL)
    return tag, url


def is_png(data: bytes) -> bool:
    return len(data) >= 8 and data[:8] == b"\x89PNG\r\n\x1a\n"


def is_jpeg(data: bytes) -> bool:
    return len(data) >= 2 and data[:2] == b"\xff\xd8"


def output_matches(format_name: str, data: bytes, content_type: str) -> bool:
    fmt = format_name.upper()
    ct = content_type.lower()
    if fmt == "PNG":
        return is_png(data) or "png" in ct
    return is_jpeg(data) or "jpeg" in ct or "jpg" in ct


def parse_resize_mode(resize_mode: str) -> Optional[tuple[int, int]]:
    m = re.fullmatch(r"(\d+)x(\d+)", resize_mode)
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


def source_extension(data: bytes, content_type: str, url: str = "") -> str:
    ct = content_type.lower()
    path = urllib.parse.urlparse(url).path.lower()
    if is_png(data) or "png" in ct or path.endswith(".png"):
        return "png"
    return "jpg"


def output_extension_for(format_name: str, data: bytes = b"", content_type: str = "", url: str = "") -> str:
    if format_name.upper() == "ORIGINAL":
        return source_extension(data, content_type, url)
    return "png" if format_name.upper() == "PNG" else "jpg"


def opposite_extension(extension: str) -> str:
    return "jpg" if extension == "png" else "png"


def convert_image_bytes(data: bytes, format_name: str, resize_mode: str, jpg_quality: str = "Balanced") -> bytes:
    if not PILLOW_AVAILABLE:
        raise NeedPillowError(
            "Pillow is required for JPG/PNG conversion or resizing. Install with: py -m pip install Pillow"
        )
    assert Image is not None
    from io import BytesIO

    with Image.open(BytesIO(data)) as img:
        out = img.convert("RGB") if format_name.upper() == "JPG" else img.copy()
        target = parse_resize_mode(resize_mode)
        if target:
            tw, th = target
            # Fixed sizes are output caps, not upscaling targets.
            if out.width >= tw and out.height >= th and (out.width, out.height) != (tw, th):
                out = out.resize((tw, th), Image.Resampling.LANCZOS)
        buf = BytesIO()
        if format_name.upper() == "PNG":
            out.save(buf, "PNG")
        else:
            if jpg_quality == "Balanced":
                out.save(buf, "JPEG", quality=75, optimize=True, subsampling=2)
            else:
                out.save(buf, "JPEG", quality=95, optimize=True, subsampling=0)
        return buf.getvalue()


def save_downloaded_image(url: str, outfile: Path, timeout: int, format_name: str, resize_mode: str, jpg_quality: str = "Balanced") -> Path:
    data, content_type = request_bytes(url, timeout)
    resize_mode = normalize_resize_mode(resize_mode)
    if format_name.upper() == "ORIGINAL":
        outfile = outfile.with_suffix("." + output_extension_for(format_name, data, content_type, url))
        converted = data
    else:
        full_quality = resize_mode == FULL_RESOLUTION_SIZE
        fmt = format_name.upper()
        if full_quality and output_matches(fmt, data, content_type):
            converted = data
        else:
            converted = convert_image_bytes(data, fmt, resize_mode, jpg_quality)

    outfile.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=outfile.name + ".", suffix=".tmp", dir=str(outfile.parent))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(converted)
        os.replace(tmp_name, outfile)
    finally:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
    return outfile


def existing_image_at_least_target(path: Path, resize_mode: str) -> bool:
    target = parse_resize_mode(resize_mode)
    if not target or not path.is_file() or not PILLOW_AVAILABLE:
        return False
    assert Image is not None
    try:
        with Image.open(path) as img:
            return img.width >= target[0] and img.height >= target[1]
    except Exception:
        return False


# ==================== Card Sources ====================

def get_regular_cards_from_web(log) -> list[CardJob]:
    log("Retrieving Standard card IDs from YGOPRODeck API...")
    data = request_json(API_URL, TIMEOUT_SECONDS)
    if isinstance(data, dict) and data.get("error"):
        raise RuntimeError(f"API Error: {data['error']}")
    jobs: list[CardJob] = []
    for card in data.get("data", []):
        for image in card.get("card_images", []) or []:
            if image.get("id") and image.get("image_url"):
                jobs.append(CardJob(
                    id=int(image["id"]),
                    name=str(card.get("name") or ""),
                    kind="Regular",
                    image_urls=(str(image["image_url"]),),
                    is_field_spell=str(card.get("humanReadableCardType") or "") == "Field Spell",
                    cropped_image_url=str(image.get("image_url_cropped") or ""),
                ))
    log(f"Found {len(jobs)} Standard card image IDs.")
    return jobs


def get_github_pic_ids(repo: str, log) -> set[int]:
    log(f"Reading picture list from GitHub: {repo}")
    data = request_json(f"https://api.github.com/repos/{repo}/git/trees/master?recursive=1", TIMEOUT_SECONDS)
    ids: set[int] = set()
    for item in data.get("tree", []) if isinstance(data, dict) else []:
        path = str(item.get("path") or "")
        if item.get("type") == "blob":
            match = re.fullmatch(r"pics/(\d+)\.(?:png|jpg|jpeg)", path, re.IGNORECASE)
            if match:
                ids.add(int(match.group(1)))
    return ids

def find_rush_cdb_paths() -> list[Path]:
    paths: list[Path] = []
    if RUSH_CDB_PATH.is_file():
        paths.append(RUSH_CDB_PATH)
    if REPOSITORIES_DIR.is_dir():
        paths.extend(REPOSITORIES_DIR.rglob("*cards-rush*.cdb"))
    seen: set[str] = set()
    unique: list[Path] = []
    for p in paths:
        key = str(p.resolve()).lower()
        if key not in seen:
            seen.add(key)
            unique.append(p)
    return unique


def get_rush_cards_from_cdb(log) -> list[CardJob]:
    log("Reading Rush Duel IDs from EDOPro databases...")
    cdb_paths = find_rush_cdb_paths()
    if not cdb_paths:
        raise RuntimeError(f"Could not find any Rush Duel .cdb files. Checked: {RUSH_CDB_PATH} and {REPOSITORIES_DIR}")
    cards: dict[int, dict[str, Any]] = {}
    for cdb_path in cdb_paths:
        try:
            with sqlite3.connect(cdb_path) as con:
                rows = con.execute("""
                    select t.id, t.name, d.type
                    from texts t
                    left join datas d on d.id = t.id
                    where t.id between 160000000 and 160999999
                    order by t.id
                """).fetchall()
            is_repository_db = cdb_path != RUSH_CDB_PATH
            for card_id, name, card_type in rows:
                cid = int(card_id)
                name = str(name or "")
                is_field = bool((int(card_type or 0) & FIELD_TYPE_FLAG) != 0)
                if cid not in cards:
                    cards[cid] = {"name": name, "names": [], "is_field": is_field, "display_name_from_repository": is_repository_db}
                if name.strip() and name not in cards[cid]["names"]:
                    cards[cid]["names"].append(name)
                # The primary Rush DB can contain older names. Let the first repository
                # override supply the display name, but keep every known name for lookup.
                if is_repository_db and name.strip() and not cards[cid].get("display_name_from_repository"):
                    cards[cid]["name"] = name
                    cards[cid]["display_name_from_repository"] = True
                if is_field:
                    cards[cid]["is_field"] = True
        except Exception:
            continue
    jobs = []
    for cid, v in sorted(cards.items()):
        lookup_names = list(dict.fromkeys([v["name"], *v.get("names", [])]))
        jobs.append(CardJob(
            id=cid,
            name=v["name"],
            kind="Rush",
            is_rush_field_spell=v["is_field"],
            search_names=tuple(name for name in lookup_names if str(name).strip()),
        ))
    if not jobs:
        raise RuntimeError("No Rush Duel card IDs were found in cards-rush.cdb.")
    log(f"Found {len(jobs)} Rush Duel card IDs across {len(cdb_paths)} database file(s).")
    return jobs



def with_rush_duplicate_fallback_indexes(cards: list[CardJob]) -> list[CardJob]:
    groups: dict[str, list[CardJob]] = defaultdict(list)
    for card in cards:
        if card.kind != "Rush" or not card.name.strip():
            continue
        key = normalized_title(card.name)
        if key:
            groups[key].append(card)
    duplicate_lookup: dict[int, tuple[int, int]] = {}
    for group in groups.values():
        if len(group) <= 1:
            continue
        ordered = sorted(group, key=lambda c: c.id)
        total = len(ordered)
        for index, card in enumerate(ordered):
            duplicate_lookup[card.id] = (index, total)
    updated: list[CardJob] = []
    for card in cards:
        index, total = duplicate_lookup.get(card.id, (-1, 0))
        updated.append(CardJob(
            id=card.id,
            name=card.name,
            kind=card.kind,
            image_urls=card.image_urls,
            is_field_spell=card.is_field_spell,
            cropped_image_url=card.cropped_image_url,
            is_rush_field_spell=card.is_rush_field_spell,
            search_names=card.search_names,
            rush_duplicate_index=index,
            rush_duplicate_total=total,
        ))
    return updated

# ==================== Yugipedia Aliases and Field Artwork Denylist ====================

def yugipedia_title_aliases(card_name: str) -> list[str]:
    titles: list[str] = []
    if not card_name.strip():
        return titles
    titles.append(card_name)
    if re.search(r"\[[LR]\]", card_name):
        titles.append(card_name.replace("[L]", "(L)").replace("[R]", "(R)"))
    if re.search(r"^Mean Mutt ", card_name):
        titles.append(re.sub(r"^Mean Mutt ", "Heartless Hound ", card_name))
    replacements = {
        "Tune Cornetless": "Tune Cornetlass",
        "Wooly Wunderworld Thunder Lambda": "Wooly Wonderland Thunder Rambda",
        "Saturnchamomille": "Saturnchamomile",
        "Vegetation of Blisstopia": "Evergreen of Blisstopia",

    }
    for old, new in replacements.items():
        if old in card_name:
            titles.append(card_name.replace(old, new))
    if re.search("[–—]", card_name):
        titles.append(re.sub("[–—]", "-", card_name))
    if card_name.endswith(" and Flowers"):
        titles.append(re.sub(r" and Flowers$", ", and Flowers", card_name))
    if "#" in card_name:
        titles.append(card_name.replace("#", ""))
    ascii_name = "".join(
        ch for ch in unicodedata.normalize("NFKD", card_name)
        if not unicodedata.combining(ch)
    )
    if ascii_name != card_name:
        titles.append(ascii_name)
    m = re.match(r"^(.*) \(Rush\)$", card_name)
    if m:
        base = m.group(1)
        titles.extend([f"{base} (Rush Duel)", base])
        if re.search(r"\[[LR]\]", base):
            titles.append(base.replace("[L]", "(L)").replace("[R]", "(R)"))
        if "#" in base:
            titles.append(base.replace("#", ""))
    return list(dict.fromkeys(titles))


DENIED_FIELD_ARTWORK_FILES = {
    "File:RagingWaves-DBR-JP-VG-artwork.png",
    "File:RagingWaves-G002-JP-VG-artwork.png",
}


def normalized_title(title: str) -> str:
    title = "".join(
        ch for ch in unicodedata.normalize("NFKD", title)
        if not unicodedata.combining(ch)
    )
    return re.sub(r"[^A-Za-z0-9]", "", title).lower()


def yugipedia_file_stem(title: str) -> str:
    title = re.sub(r" \(Rush(?: Duel)?\)$", "", title)
    return re.sub(r"[^A-Za-z0-9]", "", title)


def yugipedia_api(params: dict[str, Any], timeout: int) -> Any:
    global YUGIPEDIA_LAST_REQUEST_TIME
    query = urllib.parse.urlencode(params)
    with YUGIPEDIA_SEMAPHORE:
        with YUGIPEDIA_RATE_LOCK:
            now = time.monotonic()
            wait_time = YUGIPEDIA_MIN_INTERVAL_SECONDS - (now - YUGIPEDIA_LAST_REQUEST_TIME)
            if wait_time > 0:
                time.sleep(wait_time)
            YUGIPEDIA_LAST_REQUEST_TIME = time.monotonic()
        data = request_json(f"https://yugipedia.com/api.php?{query}", timeout)
    if isinstance(data, dict) and data.get("error"):
        error = data["error"]
        raise DownloadError(str(error.get("info") or error.get("code") or "Yugipedia API error"))
    return data


def yugipedia_media_url_from_file_title(file_title: str) -> Optional[str]:
    filename = file_title.removeprefix("File:")
    if not filename:
        return None
    digest = hashlib.md5(filename.encode("utf-8")).hexdigest()
    return f"https://ms.yugipedia.com//{digest[0]}/{digest[:2]}/{urllib.parse.quote(filename)}"


def is_yugipedia_card_file_title(file_title: str) -> bool:
    return (
        bool(re.search(r"\.(jpg|jpeg|png|webp)$", file_title, re.I))
        and not re.search(r"\.svg$|artwork|VG-|DuelLinks|SkillCard|GameMat|ConceptArt", file_title, re.I)
    )


def choose_yugipedia_card_file(file_titles: list[str], normalized_names: list[str], prefer_rush: bool = False) -> Optional[str]:
    candidates: list[tuple[int, str]] = []
    for file_title in dict.fromkeys(file_titles):
        normalized_file = normalized_title(file_title)
        if normalized_names and not any(name and name in normalized_file for name in normalized_names):
            continue
        score = 1000
        is_rush_file = bool(re.search(r"-(RD|RD[A-Z0-9]+|DULI|RDDL|ORP|ORP4|VSP|VSP1)", file_title))
        if "-RD" in file_title:
            score += 300
        if prefer_rush and is_rush_file:
            score += 400
        if prefer_rush and not is_rush_file:
            score -= 400
        if re.search(r"-(JP|EN)-", file_title):
            score += 80
        if re.search(r"-(OP|ORR|UR|SR|R|N|SE|P|C)\.", file_title):
            score += 50
        if re.search(r"-OW\.|[-]OW-", file_title):
            score -= 500
        if re.search(r"Anime|Manga", file_title):
            score -= 500
        candidates.append((score, file_title))
    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][1]


def get_yugipedia_card_image_from_allimages(card_name: str, timeout: int) -> Optional[str]:
    stem = yugipedia_file_stem(card_name)
    if not stem:
        return None
    cache_key = normalized_title(card_name)
    with YUGIPEDIA_CACHE_LOCK:
        if cache_key in YUGIPEDIA_ALLIMAGES_CACHE:
            return YUGIPEDIA_ALLIMAGES_CACHE[cache_key]
    prefer_rush = bool(re.search(r" \(Rush(?: Duel)?\)$", card_name))
    normalized_names = [
        normalized_title(re.sub(r" \(Rush(?: Duel)?\)$", "", alias))
        for alias in yugipedia_title_aliases(card_name)
    ]
    try:
        data = yugipedia_api({"action": "query", "list": "allimages", "aiprefix": stem, "ailimit": 50, "format": "json"}, timeout)
    except Exception:
        return None
    urls_by_title = {
        f"File:{item.get('name', '')}": str(item.get("url") or "")
        for item in data.get("query", {}).get("allimages", [])
    }
    file_titles = list(urls_by_title)
    file_titles = [title for title in file_titles if is_yugipedia_card_file_title(title)]
    file_title = choose_yugipedia_card_file(file_titles, normalized_names, prefer_rush)
    if not file_title:
        with YUGIPEDIA_CACHE_LOCK:
            YUGIPEDIA_ALLIMAGES_CACHE[cache_key] = None
        return None
    result = urls_by_title.get(file_title) or None
    with YUGIPEDIA_CACHE_LOCK:
        YUGIPEDIA_ALLIMAGES_CACHE[cache_key] = result
    return result


def url_exists(url: str, timeout: int) -> bool:
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return 200 <= resp.status < 400
    except urllib.error.HTTPError as exc:
        return exc.code not in {400, 404}
    except Exception:
        return False



@dataclass(frozen=True)
class YugipediaGalleryCandidate:
    file_title: str
    rarity: str
    region: str
    card_number: str
    is_official_proxy: bool
    order: int


YUGIPEDIA_RARITY_ORDER = {
    "N": 10,
    "C": 20,
    "P": 25,
    "NP": 28,
    "NPR": 30,
    "R": 40,
    "RR": 45,
    "SPR": 50,
    "SR": 60,
    "UR": 70,
    "ScR": 80,
    "SE": 80,
    "GRR": 90,
    "ORR": 100,
    "ORRBlack": 110,
}


def parse_card_gallery_attrs(header: str) -> dict[str, str]:
    attrs: dict[str, str] = {}
    for part in header.split("|"):
        if "=" in part:
            key, value = part.split("=", 1)
            attrs[key.strip().lower()] = value.strip()
    return attrs


def parse_card_gallery_entry(line: str) -> Optional[tuple[str, str, bool]]:
    line = line.strip()
    if not line or line.startswith("{{") or line.startswith("}}") or line.startswith("|"):
        return None
    if "//" in line:
        line = line.split("//", 1)[0].strip()
    if not line or re.search(r"\.(png|jpg|jpeg|webp)\b", line, re.I):
        return None
    is_proxy = bool(re.search(r"::\s*OP\b", line, re.I))
    clean = re.sub(r"::.*$", "", line).strip()
    parts = [part.strip() for part in clean.split(";")]
    if not parts or not parts[0].startswith("RD/"):
        return None
    rarity = parts[-1] if len(parts) >= 3 else ""
    if rarity.upper() == "OP":
        is_proxy = True
    return parts[0], rarity, is_proxy


def rarity_rank(rarity: str) -> int:
    normalized = rarity.strip()
    is_alternate_art = bool(re.search(r"(?:^|-)AA$", normalized, re.I))
    base = re.sub(r"-AA$", "", normalized, flags=re.I)
    if base in YUGIPEDIA_RARITY_ORDER:
        rank = YUGIPEDIA_RARITY_ORDER[base]
    else:
        upper = base.upper()
        rank = 55
        for key, value in YUGIPEDIA_RARITY_ORDER.items():
            if key.upper() == upper:
                rank = value
                break
    if is_alternate_art:
        rank += 5
    return rank

def choose_variant_index(index: int, source_total: int, candidate_total: int) -> int:
    if candidate_total <= 1 or source_total <= 1 or index < 0:
        return 0
    index = max(0, min(index, source_total - 1))
    return round(index * (candidate_total - 1) / (source_total - 1))



def is_transient_yugipedia_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return (
        "database query error" in text
        or "exception caught" in text
        or "timeout" in text
        or "temporarily unavailable" in text
        or "connection reset" in text
        or "remote end closed" in text
    )


def retry_delay(attempt: int) -> float:
    return 0.75 * attempt


def infer_gallery_rarity_from_file_title(rarity: str, file_title: str) -> str:
    if rarity.strip().upper() != "AA":
        return rarity
    match = re.search(r"-([A-Za-z0-9]+-AA)\.(?:png|jpg|jpeg|webp)$", file_title, re.I)
    if match:
        return match.group(1)
    return rarity

def get_yugipedia_gallery_candidates(card_name: str, timeout: int) -> list[YugipediaGalleryCandidate]:
    cache_key = normalized_title(card_name)
    with YUGIPEDIA_CACHE_LOCK:
        if cache_key in YUGIPEDIA_GALLERY_CACHE:
            return list(YUGIPEDIA_GALLERY_CACHE[cache_key])
    for alias in yugipedia_title_aliases(card_name):
        gallery_title = f"Card Gallery:{alias}"
        try:
            data = yugipedia_api({"action": "parse", "page": gallery_title, "prop": "wikitext|images", "format": "json"}, timeout)
        except Exception as exc:
            if is_transient_yugipedia_error(exc):
                raise
            continue
        parse = data.get("parse", {}) if isinstance(data, dict) else {}
        wikitext = str(parse.get("wikitext", {}).get("*", ""))
        images = [f"File:{name}" for name in parse.get("images", []) if is_yugipedia_card_file_title(f"File:{name}")]
        if not wikitext or not images:
            continue
        entries: list[tuple[str, str, str, bool]] = []
        for match in re.finditer(r"\{\{Card gallery\|(?P<header>[^\n{}]*)\n(?P<body>.*?)\n\}\}", wikitext, re.S):
            attrs = parse_card_gallery_attrs(match.group("header"))
            region = attrs.get("region", "")
            gallery_type = attrs.get("type", "")
            if gallery_type != "rush" or region not in {"JP", "EN"}:
                continue
            for line in match.group("body").splitlines():
                parsed = parse_card_gallery_entry(line)
                if parsed:
                    card_number, rarity, is_proxy = parsed
                    entries.append((region, card_number, rarity, is_proxy))
        if not entries:
            continue
        candidates: list[YugipediaGalleryCandidate] = []
        for order, (entry, file_title) in enumerate(zip(entries, images)):
            region, card_number, rarity, is_proxy = entry
            rarity = infer_gallery_rarity_from_file_title(rarity, file_title)
            if "-OP" in file_title:
                is_proxy = True
            candidates.append(YugipediaGalleryCandidate(file_title, rarity, region, card_number, is_proxy, order))
        if candidates:
            with YUGIPEDIA_CACHE_LOCK:
                YUGIPEDIA_GALLERY_CACHE[cache_key] = list(candidates)
            return candidates
    with YUGIPEDIA_CACHE_LOCK:
        YUGIPEDIA_GALLERY_CACHE[cache_key] = []
    return []


def filtered_yugipedia_gallery_candidates(card_name: str, timeout: int) -> list[YugipediaGalleryCandidate]:
    candidates = get_yugipedia_gallery_candidates(card_name, timeout)
    if not candidates:
        return []
    preferred_region = "JP" if any(c.region == "JP" for c in candidates) else "EN"
    candidates = [c for c in candidates if c.region == preferred_region]
    non_proxy_numbers = {c.card_number for c in candidates if not c.is_official_proxy}
    filtered = [c for c in candidates if not (c.is_official_proxy and c.card_number in non_proxy_numbers)]
    if not filtered:
        filtered = candidates
    return sorted(filtered, key=lambda c: (rarity_rank(c.rarity), c.order, c.file_title))


def get_yugipedia_duplicate_variant_image(card_name: str, timeout: int, variant_index: int, variant_total: int) -> Optional[tuple[str, str]]:
    cache_key = (normalized_title(card_name), variant_index, variant_total)
    with YUGIPEDIA_CACHE_LOCK:
        if cache_key in YUGIPEDIA_DUPLICATE_VARIANT_CACHE:
            return YUGIPEDIA_DUPLICATE_VARIANT_CACHE[cache_key]
    candidates = filtered_yugipedia_gallery_candidates(card_name, timeout)
    if not candidates:
        with YUGIPEDIA_CACHE_LOCK:
            YUGIPEDIA_DUPLICATE_VARIANT_CACHE[cache_key] = None
        return None
    chosen_index = choose_variant_index(variant_index, variant_total, len(candidates))
    file_title = candidates[chosen_index].file_title
    info = yugipedia_api({"action": "query", "titles": file_title, "prop": "imageinfo", "iiprop": "url|size", "format": "json"}, timeout)
    pages = list(info.get("query", {}).get("pages", {}).values())
    imageinfo = pages[0].get("imageinfo", []) if pages else []
    if imageinfo and imageinfo[0].get("url"):
        result = (str(imageinfo[0]["url"]), file_title)
        with YUGIPEDIA_CACHE_LOCK:
            YUGIPEDIA_DUPLICATE_VARIANT_CACHE[cache_key] = result
        return result
    with YUGIPEDIA_CACHE_LOCK:
        YUGIPEDIA_DUPLICATE_VARIANT_CACHE[cache_key] = None
    return None


def get_yugipedia_image_url_from_page(title: str, normalized_names: list[str], timeout: int) -> Optional[str]:
    data = yugipedia_api({"action": "query", "titles": title, "redirects": 1, "prop": "images", "imlimit": 50, "format": "json"}, timeout)
    pages = list(data.get("query", {}).get("pages", {}).values())
    if not pages or "missing" in pages[0]:
        return None
    image_titles = [img.get("title", "") for img in pages[0].get("images", [])]
    image_titles = [t for t in image_titles if is_yugipedia_card_file_title(t)]
    file_title = choose_yugipedia_card_file(image_titles, normalized_names)
    if not file_title:
        return None
    info = yugipedia_api({"action": "query", "titles": file_title, "prop": "imageinfo", "iiprop": "url|size", "format": "json"}, timeout)
    file_pages = list(info.get("query", {}).get("pages", {}).values())
    imageinfo = file_pages[0].get("imageinfo", []) if file_pages else []
    if imageinfo and imageinfo[0].get("url"):
        return str(imageinfo[0]["url"])
    return None

def get_yugipedia_image_url(card_name: str, timeout: int) -> Optional[str]:
    if not card_name.strip():
        return None
    cache_key = normalized_title(card_name)
    with YUGIPEDIA_CACHE_LOCK:
        if cache_key in YUGIPEDIA_IMAGE_CACHE:
            return YUGIPEDIA_IMAGE_CACHE[cache_key]
    titles = yugipedia_title_aliases(card_name)
    base_name = re.sub(r" \(Rush(?: Duel)?\)$", "", card_name)
    normalized_names = [
        normalized_title(re.sub(r" \(Rush(?: Duel)?\)$", "", alias))
        for alias in titles
    ]
    candidate_titles = list(dict.fromkeys(titles))
    for title in dict.fromkeys(candidate_titles):
        try:
            result = get_yugipedia_image_url_from_page(title, normalized_names, timeout)
            if result:
                with YUGIPEDIA_CACHE_LOCK:
                    YUGIPEDIA_IMAGE_CACHE[cache_key] = result
                return result
        except Exception:
            pass
    for search_text in titles:
        try:
            data = yugipedia_api({"action": "query", "list": "search", "srsearch": search_text, "format": "json", "srlimit": 5}, timeout)
            for result in data.get("query", {}).get("search", []):
                title = str(result.get("title") or "")
                if not re.search(r"\((anime|manga|Duel Links|LP)\)$", title):
                    candidate_titles.append(title)
        except Exception:
            pass
    for title in dict.fromkeys(candidate_titles):
        try:
            result = get_yugipedia_image_url_from_page(title, normalized_names, timeout)
            if result:
                with YUGIPEDIA_CACHE_LOCK:
                    YUGIPEDIA_IMAGE_CACHE[cache_key] = result
                return result
        except Exception:
            continue
    for title in dict.fromkeys(titles):
        fallback = get_yugipedia_card_image_from_allimages(title, timeout)
        if fallback:
            with YUGIPEDIA_CACHE_LOCK:
                YUGIPEDIA_IMAGE_CACHE[cache_key] = fallback
            return fallback
    with YUGIPEDIA_CACHE_LOCK:
        YUGIPEDIA_IMAGE_CACHE[cache_key] = None
    return None




def get_yugipedia_cropped_field_image_url(card_name: str, timeout: int) -> Optional[str]:
    if not card_name.strip():
        return None
    aliases = yugipedia_title_aliases(card_name)
    titles: list[str] = []
    normalized_names: list[str] = []
    for alias in aliases:
        titles.extend([alias, f"Card Gallery:{alias}"])
        normalized_names.append(normalized_title(re.sub(r" \(Rush(?: Duel)?\)$", "", alias)))
    file_titles: list[str] = []
    for title in dict.fromkeys(titles):
        try:
            data = yugipedia_api({"action": "query", "titles": title, "redirects": 1, "prop": "images", "imlimit": 100, "format": "json"}, timeout)
            for page in data.get("query", {}).get("pages", {}).values():
                if "missing" in page:
                    continue
                for image in page.get("images", []) or []:
                    image_title = str(image.get("title") or "")
                    if re.search(r"\.(jpg|jpeg|png|webp)$", image_title, re.I) and not re.search(r"\.svg$|SkillCard|DuelLinks", image_title, re.I):
                        file_titles.append(image_title)
        except Exception:
            pass
    for alias in aliases:
        stem = yugipedia_file_stem(alias)
        if stem:
            file_titles.extend([
                f"File:{stem}-DBR-JP-VG-artwork.png",
                f"File:{stem}-G002-JP-VG-artwork.png",
                f"File:{stem}-OW.png",
            ])
    candidates: list[tuple[int, int, str]] = []
    for file_title in dict.fromkeys(file_titles):
        try:
            if file_title in DENIED_FIELD_ARTWORK_FILES:
                continue
            nfile = normalized_title(file_title)
            if not any(n and n in nfile for n in dict.fromkeys(normalized_names)):
                continue
            direct_url = yugipedia_media_url_from_file_title(file_title)
            if direct_url and re.search(r"((DBR|G002)-JP-VG-artwork|[-]OW)\.png$", file_title) and url_exists(direct_url, min(timeout, 2)):
                candidates.append((1500, 0, direct_url))
                continue
            info = yugipedia_api({"action": "query", "titles": file_title, "prop": "imageinfo", "iiprop": "url|size", "format": "json"}, timeout)
            pages = list(info.get("query", {}).get("pages", {}).values())
            imageinfo = pages[0].get("imageinfo", []) if pages else []
            if not imageinfo or not imageinfo[0].get("url"):
                continue
            width, height = int(imageinfo[0].get("width") or 0), int(imageinfo[0].get("height") or 0)
            if width <= 0 or height <= 0 or abs((width / float(height)) - 1.0) > 0.12:
                continue
            score = 1000
            if re.search(r"(VG-artwork|[-]OW\.|[-]OW-|MasterDuel|MD-)", file_title):
                score += 500
            if re.search(r"(artwork|NC|SV|Anime|Manga)", file_title):
                score += 100
            if "-RD" in file_title:
                score += 50
            if re.search(r"(SkillCard|DuelLinks)", file_title):
                score -= 1000
            if score > 0:
                candidates.append((score, width * height, str(imageinfo[0]["url"])))
        except Exception:
            pass
    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][2]




# ==================== Field Cropped Image Handling ====================

def label_for(card: CardJob) -> str:
    return f"{card.id} ({card.name})" if card.name.strip() else str(card.id)


def save_regular_field(card: CardJob, options: Options, field_results: queue.Queue) -> None:
    if not card.is_field_spell:
        return
    label = label_for(card)
    field_file = DEFAULT_PICS_DIR / "field" / f"{card.id}.jpg"

    def fallback(reason: str, failure_status: str = "FieldMissing") -> bool:
        if not options.use_fields_github:
            field_results.put((failure_status, "Regular", label, reason + " and Fields GitHub fallback is disabled."))
            return False
        try:
            save_downloaded_image(f"{FIELDS_GITHUB_BASE_URL}/{card.id}.png", field_file, TIMEOUT_SECONDS, "JPG", FULL_RESOLUTION_SIZE)
            field_results.put(("FieldSuccess", "Regular", label, ""))
            return True
        except Exception as exc:
            field_results.put((failure_status, "Regular", label, reason + f"; Fields GitHub fallback failed: {exc}"))
            return False

    if not card.cropped_image_url.strip():
        fallback("YGOPRODeck did not provide a cropped Field image URL")
        return
    if field_file.exists() and not options.force:
        field_results.put(("FieldSkipped", "Regular", label, ""))
        return
    try:
        save_downloaded_image(card.cropped_image_url, field_file, TIMEOUT_SECONDS, "JPG", FULL_RESOLUTION_SIZE)
        field_results.put(("FieldSuccess", "Regular", label, ""))
    except Exception as exc:
        fallback(str(exc), "FieldError")


def save_rush_field(card: CardJob, options: Options, field_results: queue.Queue) -> None:
    if not card.is_rush_field_spell:
        return
    label = label_for(card)
    field_file = DEFAULT_PICS_DIR / "field" / f"{card.id}.jpg"
    if field_file.exists() and not options.force:
        field_results.put(("FieldSkipped", "Rush", label, ""))
        return

    def github_fallback(reason: str, status: str = "FieldMissing") -> bool:
        if not options.use_fields_github:
            field_results.put((status, "Rush", label, reason))
            return False
        try:
            save_downloaded_image(f"{FIELDS_GITHUB_BASE_URL}/{card.id}.png", field_file, TIMEOUT_SECONDS, "JPG", FULL_RESOLUTION_SIZE)
            field_results.put(("FieldSuccess", "Rush", label, ""))
            return True
        except Exception as exc:
            field_results.put((status, "Rush", label, f"{reason}; Fields GitHub fallback failed: {exc}"))
            return False

    try:
        cropped_url = None
        for search_name in card.search_names or (card.name,):
            cropped_url = get_yugipedia_cropped_field_image_url(search_name, TIMEOUT_SECONDS)
            if cropped_url:
                break
        if not cropped_url:
            github_fallback("Yugipedia no matching cropped image", "FieldMissing")
            return
        save_downloaded_image(cropped_url, field_file, TIMEOUT_SECONDS, "JPG", FULL_RESOLUTION_SIZE)
        field_results.put(("FieldSuccess", "Rush", label, ""))
    except Exception as exc:
        github_fallback(f"Yugipedia failed: {exc}", "FieldError")


# ==================== Download Worker ====================

def refresh_rush_art_from_url(card: CardJob, source_url: str, options: Options, stop_event: threading.Event) -> CardResult:
    if stop_event.is_set():
        return CardResult("Skipped", card.id, card.name, "Rush")
    extension = output_extension_for(options.output_format)
    outfile = DEFAULT_PICS_DIR / f"{card.id}.{extension}"
    try:
        saved_file = save_downloaded_image(source_url, outfile, TIMEOUT_SECONDS, options.output_format, options.resize_mode, options.jpg_quality)
        other = DEFAULT_PICS_DIR / f"{card.id}.{opposite_extension(saved_file.suffix.lstrip('.').lower())}"
        try:
            other.unlink()
        except FileNotFoundError:
            pass
        return CardResult("RushHD", card.id, card.name, "Rush")
    except Exception:
        return CardResult("Error", card.id, card.name, "Rush")

def download_card(card: CardJob, options: Options, stop_event: threading.Event, log_q: queue.Queue, field_q: queue.Queue, failure_q: queue.Queue, retry_q: queue.Queue) -> CardResult:
    cid = card.id
    label = label_for(card)

    def done(status: str, yugipedia_lookup_name: str = "", yugipedia_file_title: str = "") -> CardResult:
        return CardResult(status, cid, card.name, card.kind, yugipedia_lookup_name, yugipedia_file_title)


    def duplicate_variant_lookup_with_retries(search_name: str) -> Optional[tuple[str, str]]:
        retry_count = 0
        last_error = ""
        for attempt in range(1, 4):
            try:
                result = get_yugipedia_duplicate_variant_image(
                    search_name,
                    TIMEOUT_SECONDS,
                    card.rush_duplicate_index,
                    card.rush_duplicate_total,
                )
                if retry_count:
                    retry_q.put((label, retry_count, last_error or "Succeeded after retry"))
                return result
            except Exception as exc:
                if not is_transient_yugipedia_error(exc):
                    raise
                last_error = str(exc)
                if attempt >= 3:
                    retry_q.put((label, retry_count + 1, last_error))
                    return None
                retry_count += 1
                time.sleep(retry_delay(attempt))
        return None

    if stop_event.is_set():
        return done("Skipped")

    extension = output_extension_for(options.output_format)
    outfile = DEFAULT_PICS_DIR / f"{cid}.{extension}"
    other = DEFAULT_PICS_DIR / f"{cid}.{opposite_extension(extension)}"
    if options.output_format.upper() == "ORIGINAL":
        existing_outputs = (DEFAULT_PICS_DIR / f"{cid}.jpg", DEFAULT_PICS_DIR / f"{cid}.png")
    else:
        existing_outputs = (outfile,)
    if options.skip_larger_existing and any(existing_image_at_least_target(path, options.resize_mode) for path in existing_outputs):
        save_regular_field(card, options, field_q)
        save_rush_field(card, options, field_q)
        return done("Skipped")
    if options.delete_opposite and options.output_format.upper() != "ORIGINAL":
        try:
            other.unlink()
        except FileNotFoundError:
            pass
    if any(path.exists() for path in existing_outputs) and not options.force:
        save_regular_field(card, options, field_q)
        save_rush_field(card, options, field_q)
        return done("Skipped")

    ygo_url = card.image_urls[0] if card.kind == "Regular" and card.image_urls else f"{IMG_BASE_URL}/{cid}.jpg"
    rush_hd_url = f"{RUSH_HD_BASE_URL}/{cid}.png"
    rush_orr_url = f"{RUSH_HD_ORR_BASE_URL}/{cid}.png"
    last_error: Optional[Exception] = None

    if card.kind == "Rush" and options.use_rush_hd:
        rush_hd_sources = [
            ("Rush-HD ORR Extension", rush_orr_url),
            ("Rush-HD GitHub", rush_hd_url),
        ] if options.prefer_rush_orr_extension else [
            ("Rush-HD GitHub", rush_hd_url),
            ("Rush-HD ORR Extension", rush_orr_url),
        ]
        for source_name, source_url in rush_hd_sources:
            for attempt in range(RETRY_COUNT):
                if stop_event.is_set():
                    return done("Skipped")
                try:
                    saved_file = save_downloaded_image(source_url, outfile, TIMEOUT_SECONDS, options.output_format, options.resize_mode, options.jpg_quality)
                    other = DEFAULT_PICS_DIR / f"{cid}.{opposite_extension(saved_file.suffix.lstrip('.').lower())}"
                    try:
                        other.unlink()
                    except FileNotFoundError:
                        pass
                    save_rush_field(card, options, field_q)
                    return done("RushHD")
                except DownloadError as exc:
                    last_error = exc
                    if exc.status_code in {400, 404}:
                        break
                    if attempt < RETRY_COUNT - 1:
                        time.sleep(0.75 * (attempt + 1))
                except Exception as exc:
                    last_error = exc
                    if attempt < RETRY_COUNT - 1:
                        time.sleep(0.75 * (attempt + 1))
            if last_error and not (isinstance(last_error, DownloadError) and last_error.status_code in {400, 404}):
                log_q.put(f"{source_name} failed for {label} - {last_error}")
    use_ygo_for_card = options.use_ygo and card.kind == "Regular"
    if use_ygo_for_card:
        for attempt in range(RETRY_COUNT):
            if stop_event.is_set():
                return done("Skipped")
            try:
                saved_file = save_downloaded_image(ygo_url, outfile, TIMEOUT_SECONDS, options.output_format, options.resize_mode, options.jpg_quality)
                other = DEFAULT_PICS_DIR / f"{cid}.{opposite_extension(saved_file.suffix.lstrip('.').lower())}"
                try:
                    other.unlink()
                except FileNotFoundError:
                    pass
                save_regular_field(card, options, field_q)
                save_rush_field(card, options, field_q)
                return done("Success")
            except Exception as exc:
                last_error = exc
                status = exc.status_code if isinstance(exc, DownloadError) else 0
                if attempt >= RETRY_COUNT - 1 or status == 404:
                    break
                time.sleep(0.5 * (attempt + 1))

    status_code = last_error.status_code if isinstance(last_error, DownloadError) else 0
    if (card.kind == "Rush" or status_code == 404 or not use_ygo_for_card) and options.use_yugipedia:
        try:
            fallback_url = None
            fallback_search_name = ""
            fallback_file_title = ""
            for search_name in card.search_names or (card.name,):
                if (
                    options.use_rush_duplicate_fallback_choice
                    and card.kind == "Rush"
                    and card.rush_duplicate_total > 1
                    and card.rush_duplicate_index >= 0
                ):
                    duplicate_choice = duplicate_variant_lookup_with_retries(search_name)
                    if duplicate_choice:
                        fallback_url, fallback_file_title = duplicate_choice
                if not fallback_url:
                    fallback_url = get_yugipedia_image_url(search_name, TIMEOUT_SECONDS)
                if fallback_url:
                    fallback_search_name = search_name
                    break
            if fallback_url:
                for attempt in range(RETRY_COUNT):
                    try:
                        saved_file = save_downloaded_image(fallback_url, outfile, TIMEOUT_SECONDS, options.output_format, options.resize_mode, options.jpg_quality)
                        other = DEFAULT_PICS_DIR / f"{cid}.{opposite_extension(saved_file.suffix.lstrip('.').lower())}"
                        try:
                            other.unlink()
                        except FileNotFoundError:
                            pass
                        save_rush_field(card, options, field_q)
                        return done("Fallback", fallback_search_name, fallback_file_title)
                    except Exception:
                        if attempt >= RETRY_COUNT - 1:
                            raise
                        time.sleep(0.75 * (attempt + 1))
            if card.kind == "Rush":
                reason = "Rush-HD GitHub/ORR Extension did not have this card" if options.use_rush_hd else "Rush-HD GitHub is disabled"
            else:
                reason = "YGOPRODeck returned 404" if options.use_ygo else "YGOPRODeck is disabled"
            failure_q.put(("Missing online", f"{reason} and no matching Yugipedia card image was found.", label))
        except Exception as exc:
            failure_q.put(("Yugipedia fallback failed", str(exc), label))
        return done("Missing")
    if status_code == 404:
        failure_q.put(("Missing online", "YGOPRODeck returned 404 and Yugipedia is disabled.", label))
        return done("Missing")
    if last_error:
        log_q.put(f"Error downloading {label} - {last_error}")
        failure_q.put(("Download error", str(last_error), label))
    return done("Error")


# ==================== GUI ====================

class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("EDOPro Standard/Rush HD Pics Downloader")
        self.geometry("1040x720")
        self.minsize(1000, 620)
        self.settings = load_settings()
        self.log_q: queue.Queue = queue.Queue()
        self.result_q: queue.Queue = queue.Queue()
        self.field_q: queue.Queue = queue.Queue()
        self.failure_q: queue.Queue = queue.Queue()
        self.retry_q: queue.Queue = queue.Queue()
        self.stop_event = threading.Event()
        self.executor: Optional[concurrent.futures.ThreadPoolExecutor] = None
        self.total = 0
        self.results: Counter[str] = Counter()
        self.kind_results: Counter[tuple[str, str]] = Counter()
        self.card_results: list[CardResult] = []
        self.field_results: Counter[str] = Counter()
        self.regular_field_failures: list[tuple[str, str]] = []
        self.rush_field_failures: list[tuple[str, str]] = []
        self.download_failures: list[tuple[str, str, str]] = []
        self.yugipedia_retry_details: list[tuple[str, int, str]] = []
        self.running = False
        self.operation_mode = "download"
        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.log("========================================")
        self.log("EDOPro Standard/Rush HD Pics Downloader")
        self.log(f"Python Version: {sys.version.split()[0]}")
        self.log(f"Python Pillow available: {'yes' if PILLOW_AVAILABLE else 'no - fixed-size output and format conversion will show clear errors'}")
        self.log(f"Pics folder: {DEFAULT_PICS_DIR}")
        self.log(f"Primary Rush database: {RUSH_CDB_PATH}")
        self.log(f"Repository databases: {REPOSITORIES_DIR}")
        self.log("========================================")
        self.log("Application started. Ready to download.")

    def _build_ui(self) -> None:
        top = ttk.Frame(self)
        top.pack(fill="x", padx=10, pady=10)
        self.force_var = tk.BooleanVar(value=as_bool(self.settings["ForceOverwrite"], True))
        self.regular_var = tk.BooleanVar(value=as_bool(self.settings["DownloadRegular"], True))
        self.rush_var = tk.BooleanVar(value=as_bool(self.settings["DownloadRush"], True))
        self.rush_hd_var = tk.BooleanVar(value=as_bool(self.settings["UseRushHd"], True))
        self.ygo_var = tk.BooleanVar(value=as_bool(self.settings["UseYgoProDeck"], True))
        self.yugipedia_var = tk.BooleanVar(value=as_bool(self.settings["UseYugipedia"], True))
        self.fields_var = tk.BooleanVar(value=as_bool(self.settings["UseFieldsGithub"], True))
        self.delete_var = tk.BooleanVar(value=as_bool(self.settings["DeleteOppositeBeforeStart"], False))
        self.skip_larger_var = tk.BooleanVar(value=as_bool(self.settings["SkipLargerExisting"], True))
        self.report_duplicates_var = tk.BooleanVar(value=as_bool(self.settings["ReportRushDuplicateNames"], False))
        self.duplicate_fallback_choice_var = tk.BooleanVar(value=as_bool(self.settings["UseRushDuplicateFallbackChoice"], True))
        self.prefer_orr_extension_var = tk.BooleanVar(value=as_bool(self.settings["PreferRushOrrExtension"], False))

        button_row = ttk.Frame(top)
        button_row.pack(anchor="w")
        self.start_btn = ttk.Button(button_row, text="Download Cards", command=self.start)
        self.start_btn.pack(side="left")
        self.cancel_btn = ttk.Button(button_row, text="Cancel", command=self.cancel, state="disabled")
        self.cancel_btn.pack(side="left", padx=(8, 0))
        self.update_btn = ttk.Button(button_row, text="Check App Updates", command=self.check_for_updates)
        self.update_btn.pack(side="left", padx=(8, 0))
        ttk.Label(button_row, text=f"Version {APP_VERSION}").pack(side="left", padx=(14, 0))

        group_row = ttk.Frame(top)
        group_row.pack(anchor="w", pady=(8, 0))
        ttk.Checkbutton(group_row, text="Standard cards", variable=self.regular_var).pack(side="left")
        ttk.Checkbutton(group_row, text="Rush cards", variable=self.rush_var).pack(side="left", padx=(14, 0))

        output_row = ttk.Frame(top)
        output_row.pack(anchor="w", pady=(10, 0))
        ttk.Label(output_row, text="Output").pack(side="left")
        fmt = self.settings.get("OutputFormat", "JPG")
        self.format_var = tk.StringVar(value=fmt if fmt in {"JPG", "PNG", "Original"} else "JPG")
        self.format_combo = ttk.Combobox(output_row, textvariable=self.format_var, values=("JPG", "PNG", "Original"), width=10, state="readonly")
        self.format_combo.pack(side="left", padx=(6, 14))
        ttk.Label(output_row, text="Size").pack(side="left")
        size = normalize_resize_mode(self.settings.get("OutputSize", FULL_RESOLUTION_SIZE))
        self.size_var = tk.StringVar(value=size if size in {FULL_RESOLUTION_SIZE, "443x640", "421x614"} else FULL_RESOLUTION_SIZE)
        self.size_combo = ttk.Combobox(output_row, textvariable=self.size_var, values=(FULL_RESOLUTION_SIZE, "443x640", "421x614"), width=15, state="readonly")
        self.size_combo.pack(side="left", padx=(6, 14))
        ttk.Label(output_row, text="JPG quality").pack(side="left")
        jpg_quality = self.settings.get("JpgQuality", "Balanced")
        self.jpg_quality_var = tk.StringVar(value=jpg_quality if jpg_quality in {"High", "Balanced"} else "Balanced")
        self.jpg_quality_combo = ttk.Combobox(output_row, textvariable=self.jpg_quality_var, values=("Balanced", "High"), width=10, state="readonly")
        self.jpg_quality_combo.pack(side="left", padx=(6, 14))
        ttk.Checkbutton(output_row, text="Force Overwrite Existing", variable=self.force_var).pack(side="left", padx=(0, 14))
        ttk.Checkbutton(output_row, text="Delete other format first", variable=self.delete_var).pack(side="left", padx=(0, 14))
        self.skip_larger_check = ttk.Checkbutton(output_row, text="Skip same or larger images", variable=self.skip_larger_var)
        self.skip_larger_check.pack(side="left")

        self.skip_larger_note_var = tk.StringVar()
        self.skip_larger_note = ttk.Label(top, textvariable=self.skip_larger_note_var)
        self.size_var.trace_add("write", self.update_skip_larger_state)
        self.format_var.trace_add("write", self.update_jpg_quality_state)
        self.format_var.trace_add("write", self.update_size_state)
        self.update_skip_larger_state()
        self.update_size_state()
        self.update_jpg_quality_state()

        source = ttk.LabelFrame(self, text="Source queue - tried left to right")
        source.pack(fill="x", padx=10, pady=4)
        for text, var in (
            ("1. Rush-HD GitHub", self.rush_hd_var),
            ("2. YGOPRODeck", self.ygo_var),
            ("3. Yugipedia", self.yugipedia_var),
            ("Use Fields GitHub fallback", self.fields_var),
        ):
            ttk.Checkbutton(source, text=text, variable=var).pack(side="left", padx=10, pady=4)

        note_style = ttk.Style(self)
        note_style.configure("Helper.TLabel", foreground="#4b5563")

        pillow_row = ttk.Frame(self)
        pillow_row.pack(fill="x", padx=16, pady=(2, 4))
        self.pillow_note_var = tk.StringVar(value=self.pillow_note_text())
        ttk.Label(pillow_row, textvariable=self.pillow_note_var, style="Helper.TLabel").pack(side="left")
        self.install_pillow_btn = ttk.Button(pillow_row, text="Install Python Pillow", command=self.install_pillow)
        if not PILLOW_AVAILABLE:
            self.install_pillow_btn.pack(side="left", padx=(12, 0))

        duplicate_options = ttk.LabelFrame(self, text="Rush artwork options")
        duplicate_options.pack(fill="x", padx=10, pady=4)
        ttk.Checkbutton(duplicate_options, text="Match duplicate Rush fallback rarities (beta)", variable=self.duplicate_fallback_choice_var).pack(side="left", padx=10, pady=4)
        ttk.Checkbutton(duplicate_options, text="Prefer Over Rush Rare Artwork", variable=self.prefer_orr_extension_var).pack(side="left", padx=10, pady=4)
        self.apply_orr_btn = ttk.Button(duplicate_options, text="Update ORR Artwork", command=self.start_apply_orr_art_choice)
        self.apply_orr_btn.pack(side="left", padx=10, pady=4)
        ttk.Checkbutton(duplicate_options, text="Report Rush duplicate names", variable=self.report_duplicates_var).pack(side="left", padx=10, pady=4)
        ttk.Label(self, text="Update ORR Artwork refreshes Rush cards affected by the Over Rush Rare option only.", style="Helper.TLabel").pack(anchor="w", padx=16, pady=(0, 6))
        self.progress = ttk.Progressbar(self, maximum=100)
        self.progress.pack(fill="x", padx=10, pady=(8, 4))
        self.status_var = tk.StringVar(value="Ready to download")
        ttk.Label(self, textvariable=self.status_var).pack(fill="x", padx=10)
        self.log_box = tk.Text(self, height=22, wrap="word", state="disabled")
        self.log_box.pack(fill="both", expand=True, padx=10, pady=8)

    def show_centered_dialog(self, title: str, message: str, buttons: tuple[str, ...] = ("OK",), default: str = "OK") -> str:
        self.bell()
        dialog = tk.Toplevel(self)
        dialog.title(title)
        dialog.transient(self)
        dialog.resizable(False, False)
        result = tk.StringVar(value="")

        body = ttk.Frame(dialog, padding=14)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text=message, justify="left", wraplength=520).pack(fill="x")
        button_row = ttk.Frame(body)
        button_row.pack(anchor="e", pady=(14, 0))

        def choose(value: str) -> None:
            result.set(value)
            dialog.destroy()

        for text in buttons:
            button = ttk.Button(button_row, text=text, command=lambda value=text: choose(value))
            button.pack(side="left", padx=(8, 0))
            if text == default:
                button.focus_set()

        dialog.protocol("WM_DELETE_WINDOW", lambda: choose(default))
        dialog.bind("<Escape>", lambda _event: choose(default))
        dialog.update_idletasks()
        self.update_idletasks()
        parent_x = self.winfo_rootx()
        parent_y = self.winfo_rooty()
        parent_w = self.winfo_width()
        parent_h = self.winfo_height()
        dialog_w = dialog.winfo_reqwidth()
        dialog_h = dialog.winfo_reqheight()
        x = parent_x + max(0, (parent_w - dialog_w) // 2)
        y = parent_y + max(0, (parent_h - dialog_h) // 2)
        dialog.geometry(f"+{x}+{y}")
        dialog.grab_set()
        self.wait_window(dialog)
        return result.get() or default

    def show_centered_info(self, title: str, message: str) -> None:
        self.show_centered_dialog(title, message, ("OK",), "OK")

    def ask_centered_yes_no(self, title: str, message: str) -> bool:
        return self.show_centered_dialog(title, message, ("Yes", "No"), "No") == "Yes"

    def pillow_note_text(self) -> str:
        if PILLOW_AVAILABLE:
            return "Python Pillow available: image conversion and fixed-size resizing are enabled."
        return "Python Pillow is missing: full-quality same-format downloads work, but conversion/resizing needs Python Pillow."

    def update_skip_larger_state(self, *_args) -> None:
        if self.size_var.get() == FULL_RESOLUTION_SIZE or self.format_var.get() == "Original":
            self.skip_larger_var.set(False)
            self.skip_larger_check.configure(state="disabled")
            if self.format_var.get() == "Original":
                self.skip_larger_note_var.set("Skip same/larger is unavailable for Original output because source sizes vary.")
            else:
                self.skip_larger_note_var.set("Skip same/larger is only for fixed-size output; Full Resolution has no target size to compare against.")
            self.skip_larger_note.pack(anchor="w", pady=(4, 0))
        else:
            self.skip_larger_check.configure(state="normal")
            self.skip_larger_note_var.set("")
            self.skip_larger_note.pack_forget()

    def update_size_state(self, *_args) -> None:
        if self.format_var.get() == "Original":
            self.size_combo.configure(state="disabled")
        else:
            self.size_combo.configure(state="readonly")
        self.update_skip_larger_state()

    def update_jpg_quality_state(self, *_args) -> None:
        if self.format_var.get() == "JPG":
            self.jpg_quality_combo.configure(state="readonly")
        else:
            self.jpg_quality_combo.configure(state="disabled")

    def apply_output_control_states(self) -> None:
        self.format_combo.configure(state="readonly")
        self.update_size_state()
        self.update_jpg_quality_state()

    def check_for_updates(self) -> None:
        if self.running:
            messagebox.showwarning("Download Running", "Wait for the current download to finish before checking for updates.")
            return
        self.update_btn.configure(state="disabled")
        self.log("Checking GitHub Releases for updates...")

        def worker() -> None:
            try:
                latest_tag, release_url = get_latest_github_release()
                if is_newer_version(latest_tag, APP_VERSION):
                    self.after(0, lambda: self.log(f"Update available: {latest_tag} (current: {APP_VERSION})"))
                    self.after(0, lambda: self.show_update_available(latest_tag, release_url))
                else:
                    self.after(0, lambda: self.log(f"No update found. Current version: {APP_VERSION}; latest release: {latest_tag}."))
                    self.after(0, lambda: self.show_centered_info("No Update Found", f"You are using version {APP_VERSION}\n\nLatest GitHub release: {latest_tag}"))
            except Exception as exc:
                self.after(0, lambda exc=exc: self.log(f"Update check failed: {exc}"))
                self.after(0, lambda exc=exc: self.show_centered_info("Update Check Failed", f"Could not check GitHub Releases:\n{exc}"))
            finally:
                self.after(0, lambda: self.update_btn.configure(state="normal"))

        threading.Thread(target=worker, daemon=True).start()

    def show_update_available(self, latest_tag: str, release_url: str) -> None:
        if self.ask_centered_yes_no(
            "Update Available",
            f"A newer release is available.\n\nCurrent version: {APP_VERSION}\nLatest release: {latest_tag}\n\nOpen the GitHub Releases page?",
        ):
            webbrowser.open(release_url)

    def install_pillow(self) -> None:
        if self.running:
            messagebox.showwarning("Download Running", "Wait for the current download to finish before installing Python Pillow.")
            return
        if not messagebox.askyesno(
            "Install Python Pillow",
            "Install Python Pillow using this Python environment?\n\nThis runs:\npy -m pip install Pillow\n\nInternet access may be required.",
        ):
            return
        self.install_pillow_btn.configure(state="disabled")
        self.log("Installing Python Pillow with pip...")
        threading.Thread(target=self._install_pillow_worker, daemon=True).start()

    def _install_pillow_worker(self) -> None:
        try:
            result = subprocess.run(
                [sys.executable, "-m", "pip", "install", "Pillow"],
                capture_output=True,
                text=True,
                timeout=300,
                check=False,
            )
            if result.returncode != 0:
                details = (result.stderr or result.stdout or "pip returned a non-zero exit code.").strip()
                self.after(0, lambda: self.log(f"Python Pillow install failed: {details}"))
                self.after(0, lambda: self.install_pillow_btn.configure(state="normal"))
                return
            self.after(0, lambda: self.log("Python Pillow installed. Restart this app to enable conversion and resizing."))
            self.after(0, lambda: messagebox.showinfo("Python Pillow Installed", "Python Pillow installed successfully.\n\nRestart this app to enable conversion and resizing."))
        except Exception as exc:
            self.after(0, lambda exc=exc: self.log(f"Python Pillow install failed: {exc}"))
            self.after(0, lambda: self.install_pillow_btn.configure(state="normal"))

    def current_settings_dict(self) -> dict[str, str]:
        return {
            "DefaultsVersion": DEFAULT_SETTINGS["DefaultsVersion"],
            "ForceOverwrite": "1" if self.force_var.get() else "0",
            "UseRushHd": "1" if self.rush_hd_var.get() else "0",
            "UseYgoProDeck": "1" if self.ygo_var.get() else "0",
            "UseYugipedia": "1" if self.yugipedia_var.get() else "0",
            "UseFieldsGithub": "1" if self.fields_var.get() else "0",
            "DeleteOppositeBeforeStart": "1" if self.delete_var.get() else "0",
            "SkipLargerExisting": "1" if self.format_var.get() != "Original" and self.size_var.get() != FULL_RESOLUTION_SIZE and self.skip_larger_var.get() else "0",
            "OutputFormat": self.format_var.get(),
            "OutputSize": self.size_var.get(),
            "JpgQuality": self.jpg_quality_var.get(),
            "DownloadRegular": "1" if self.regular_var.get() else "0",
            "DownloadRush": "1" if self.rush_var.get() else "0",
            "ReportRushDuplicateNames": "1" if self.report_duplicates_var.get() else "0",
            "UseRushDuplicateFallbackChoice": "1" if self.duplicate_fallback_choice_var.get() else "0",
            "PreferRushOrrExtension": "1" if self.prefer_orr_extension_var.get() else "0",
        }

    def options(self) -> Options:
        return Options(
            force=self.force_var.get(),
            use_rush_hd=self.rush_hd_var.get(),
            use_ygo=self.ygo_var.get(),
            use_yugipedia=self.yugipedia_var.get(),
            use_fields_github=self.fields_var.get(),
            output_format=self.format_var.get(),
            resize_mode=self.size_var.get(),
            jpg_quality=self.jpg_quality_var.get(),
            delete_opposite=self.delete_var.get(),
            skip_larger_existing=self.format_var.get() != "Original" and self.size_var.get() != FULL_RESOLUTION_SIZE and self.skip_larger_var.get(),
            use_rush_duplicate_fallback_choice=self.duplicate_fallback_choice_var.get(),
            prefer_rush_orr_extension=self.prefer_orr_extension_var.get(),
        )

    def log(self, msg: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        self.log_box.configure(state="normal")
        self.log_box.insert("end", f"{msg}\n" if msg.startswith("=") else f"[{stamp}] {msg}\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def set_controls(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        for child in self.winfo_children():
            self._set_state_recursive(child, state)
        self.log_box.configure(state="disabled")
        self.start_btn.configure(state="normal" if enabled else "disabled")
        self.cancel_btn.configure(state="disabled" if enabled else "normal")
        if enabled:
            self.apply_output_control_states()

    def _set_state_recursive(self, widget, state: str) -> None:
        try:
            if widget is not self.log_box:
                widget.configure(state=state)
        except tk.TclError:
            pass
        for child in widget.winfo_children():
            self._set_state_recursive(child, state)

    def start(self) -> None:
        if self.running:
            self.log("Download already in progress.")
            return
        if not self.regular_var.get() and not self.rush_var.get():
            messagebox.showwarning("No Card Group Selected", "Select at least one card group: Standard cards, Rush cards, or both.")
            return
        if self.format_var.get() != "Original" and (self.size_var.get() != FULL_RESOLUTION_SIZE or self.format_var.get() == "JPG") and not PILLOW_AVAILABLE:
            self.log("Python Pillow note: JPG conversion and fixed-size output require Python Pillow. Full-quality matching-format files can still be copied without it.")
        try:
            DEFAULT_PICS_DIR.mkdir(parents=True, exist_ok=True)
            save_settings(self.current_settings_dict())
        except Exception as exc:
            messagebox.showerror("Error", f"Failed to initialize settings or pics directory:\n{exc}")
            return
        self.operation_mode = "download"
        self.stop_event.clear()
        self.results.clear()
        self.kind_results.clear()
        self.card_results.clear()
        self.field_results.clear()
        self.regular_field_failures.clear()
        self.rush_field_failures.clear()
        self.download_failures.clear()
        self.yugipedia_retry_details.clear()
        self.progress["value"] = 0
        self.set_controls(False)
        self.running = True
        threading.Thread(target=self.prepare_and_run, daemon=True).start()
        self.after(250, self.poll)

    def start_apply_orr_art_choice(self) -> None:
        if self.running:
            self.log("Download already in progress.")
            return
        if self.format_var.get() != "Original" and (self.size_var.get() != FULL_RESOLUTION_SIZE or self.format_var.get() == "JPG") and not PILLOW_AVAILABLE:
            self.log("Python Pillow note: JPG conversion and fixed-size output require Python Pillow. Full-quality matching-format files can still be copied without it.")
        try:
            DEFAULT_PICS_DIR.mkdir(parents=True, exist_ok=True)
            save_settings(self.current_settings_dict())
        except Exception as exc:
            messagebox.showerror("Error", f"Failed to initialize settings or pics directory:\n{exc}")
            return
        self.operation_mode = "orr_apply"
        self.stop_event.clear()
        self.results.clear()
        self.kind_results.clear()
        self.card_results.clear()
        self.field_results.clear()
        self.regular_field_failures.clear()
        self.rush_field_failures.clear()
        self.download_failures.clear()
        self.yugipedia_retry_details.clear()
        self.progress["value"] = 0
        self.set_controls(False)
        self.running = True
        threading.Thread(target=self.prepare_apply_orr_art_choice, daemon=True).start()
        self.after(250, self.poll)

    def prepare_apply_orr_art_choice(self) -> None:
        try:
            options = self.options()

            def thread_log(m):
                self.log_q.put(m)

            rush_cards = get_rush_cards_from_cdb(thread_log)
            rush_by_id = {card.id: card for card in rush_cards}
            orr_ids = get_github_pic_ids("Yoshi80/Rush-HD-ORR-Extension", thread_log)
            if options.prefer_rush_orr_extension:
                target_ids = sorted(set(rush_by_id).intersection(orr_ids))
                source_label = "ORR Extension"
                source_url = lambda cid: f"{RUSH_HD_ORR_BASE_URL}/{cid}.png"
            else:
                base_ids = get_github_pic_ids("Yoshi80/Rush-HD-Pictures", thread_log)
                target_ids = sorted(set(rush_by_id).intersection(orr_ids).intersection(base_ids))
                source_label = "normal Rush-HD"
                source_url = lambda cid: f"{RUSH_HD_BASE_URL}/{cid}.png"

            self.total = len(target_ids)
            self.log_q.put(f"Applying ORR art choice: {source_label}. Target Rush cards: {len(target_ids)}")
            if not target_ids:
                self.result_q.put(("__done__", 0))
                return

            self.executor = concurrent.futures.ThreadPoolExecutor(max_workers=MAX_CONCURRENCY)
            futures = {
                self.executor.submit(refresh_rush_art_from_url, rush_by_id[cid], source_url(cid), options, self.stop_event): cid
                for cid in target_ids
            }
            for fut in concurrent.futures.as_completed(futures):
                cid = futures[fut]
                try:
                    result = fut.result()
                except Exception as exc:
                    card = rush_by_id[cid]
                    self.log_q.put(f"Error applying ORR art choice for {label_for(card)} - {exc}")
                    result = CardResult("Error", card.id, card.name, "Rush")
                self.result_q.put(result)
                if self.stop_event.is_set():
                    break
            self.result_q.put(("__done__", self.total))
        except Exception as exc:
            self.log_q.put(f"ORR art refresh startup error: {exc}")
            self.log_q.put(traceback.format_exc())
            self.result_q.put(("__done__", 0))
    def prepare_and_run(self) -> None:
        try:
            clear_yugipedia_caches()
            cards: list[CardJob] = []

            def thread_log(m):
                self.log_q.put(m)

            if self.regular_var.get():
                cards.extend(get_regular_cards_from_web(thread_log))
            if self.rush_var.get():
                cards.extend(get_rush_cards_from_cdb(thread_log))
            if not cards:
                self.log_q.put("No cards were found.")
                self.result_q.put(("__done__", 0))
                return
            options = self.options()
            if options.use_rush_duplicate_fallback_choice:
                cards = with_rush_duplicate_fallback_indexes(cards)
            self.total = len(cards)
            self.log_q.put(f"Starting download of {len(cards)} image jobs...")
            self.executor = concurrent.futures.ThreadPoolExecutor(max_workers=MAX_CONCURRENCY)
            futures = {
                self.executor.submit(download_card, c, options, self.stop_event, self.log_q, self.field_q, self.failure_q, self.retry_q): c
                for c in cards
            }
            for fut in concurrent.futures.as_completed(futures):
                card = futures[fut]
                try:
                    self.result_q.put(fut.result())
                except Exception as exc:
                    self.log_q.put(f"Worker error: {exc}")
                    self.result_q.put(CardResult("Error", card.id, card.name, card.kind))
            self.result_q.put(("__done__", self.total))
        except Exception as exc:
            self.log_q.put(f"Startup error: {exc}")
            self.log_q.put(traceback.format_exc())
            self.result_q.put(("__done__", 0))

    def poll(self) -> None:
        while True:
            try:
                self.log(self.log_q.get_nowait())
            except queue.Empty:
                break
        while True:
            try:
                status, kind, label, reason = self.field_q.get_nowait()
                self.field_results[status] += 1
                if status in {"FieldMissing", "FieldError"}:
                    if kind == "Regular":
                        self.regular_field_failures.append((label, reason))
                    elif kind == "Rush":
                        self.rush_field_failures.append((label, reason))
            except queue.Empty:
                break
        while True:
            try:
                category, reason, label = self.failure_q.get_nowait()
                self.download_failures.append((category, reason, label))
            except queue.Empty:
                break
        while True:
            try:
                label, retry_count, reason = self.retry_q.get_nowait()
                self.yugipedia_retry_details.append((label, int(retry_count), str(reason)))
            except queue.Empty:
                break
        done_signal = False
        while True:
            try:
                item = self.result_q.get_nowait()
            except queue.Empty:
                break
            if isinstance(item, tuple) and item[0] == "__done__":
                done_signal = True
            else:
                if isinstance(item, CardResult):
                    result = item
                else:
                    status = item[0]
                    kind = item[1] if len(item) > 2 else "Unknown"
                    result = CardResult(status, 0, "", kind)
                self.card_results.append(result)
                self.results[result.status] += 1
                self.kind_results[(result.kind, result.status)] += 1
        finished = sum(self.results.values())
        if self.total:
            self.progress["value"] = min(100, int((finished / self.total) * 100))
        self.status_var.set(
            f"Processed: {finished}/{self.total} | Rush-HD: {self.results['RushHD']} | YGO: {self.results['Success']} | "
            f"Yugipedia: {self.results['Fallback']} | Missing: {self.results['Missing']} | Errors: {self.results['Error']} | "
            f"Field missing/errors: {self.field_results['FieldMissing']}/{self.field_results['FieldError']}"
        )
        if done_signal:
            if self.operation_mode == "orr_apply":
                self.finish_apply_orr_art_choice()
            else:
                self.finish()
        elif self.running:
            self.after(250, self.poll)

    def card_summary_lines(self, finished: int) -> list[str]:
        standard = self.kind_results
        lines = [
            f"Total jobs: {self.total}",
            f"Processed: {finished}",
            "",
            "Standard cards:",
            f"  YGOPRODeck: {standard[('Regular', 'Success')]}",
            f"  Yugipedia fallback: {standard[('Regular', 'Fallback')]}",
            f"  Skipped: {standard[('Regular', 'Skipped')]}",
            f"  Missing online: {standard[('Regular', 'Missing')]}",
            f"  Errors: {standard[('Regular', 'Error')]}",
            "",
            "Rush cards:",
            f"  Rush-HD GitHub: {standard[('Rush', 'RushHD')]}",
            f"  YGOPRODeck: {standard[('Rush', 'Success')]}",
            f"  Yugipedia fallback: {standard[('Rush', 'Fallback')]}",
            f"  Skipped: {standard[('Rush', 'Skipped')]}",
            f"  Missing online: {standard[('Rush', 'Missing')]}",
            f"  Errors: {standard[('Rush', 'Error')]}",
            "",
            "Fields:",
            f"  Downloaded: {self.field_results['FieldSuccess']}",
            f"  Skipped: {self.field_results['FieldSkipped']}",
            f"  Missing: {self.field_results['FieldMissing']}",
            f"  Errors: {self.field_results['FieldError']}",
        ]
        if self.yugipedia_retry_details:
            retried_cards = len({label for label, _count, _reason in self.yugipedia_retry_details})
            retry_attempts = sum(count for _label, count, _reason in self.yugipedia_retry_details)
            lines.extend(["", f"Yugipedia transient retries: {retried_cards} card(s), {retry_attempts} retry attempt(s)"])
        unknown = sum(count for (kind, _status), count in self.kind_results.items() if kind not in {"Regular", "Rush"})
        if unknown:
            lines.extend(["", f"Unknown card jobs: {unknown}"])
        return lines
    def source_label(self, result: CardResult) -> str:
        labels = {
            "RushHD": "Rush-HD GitHub",
            "Success": "YGOPRODeck",
            "Fallback": "Yugipedia fallback",
            "Skipped": "Skipped",
            "Missing": "Missing online",
            "Error": "Error",
        }
        return labels.get(result.status, result.status)

    def rush_duplicate_report_lines(self) -> list[str]:
        grouped: dict[str, list[CardResult]] = defaultdict(list)
        display_names: dict[str, str] = {}
        for result in self.card_results:
            if result.kind != "Rush" or not result.name.strip():
                continue
            key = normalized_title(result.name)
            if not key:
                continue
            grouped[key].append(result)
            display_names.setdefault(key, result.name)

        duplicate_groups = []
        for key, results in grouped.items():
            ids = {result.card_id for result in results}
            if len(ids) > 1:
                duplicate_groups.append((display_names[key], results))
        if not duplicate_groups:
            return ["Rush duplicate names:", "  None"]

        lines = ["Rush duplicate names:"]
        for name, results in sorted(duplicate_groups, key=lambda item: normalized_title(item[0])):
            lines.append(name)
            for result in sorted(results, key=lambda item: item.card_id):
                detail = f"  - {result.card_id}: {self.source_label(result)}"
                if result.yugipedia_lookup_name and result.yugipedia_lookup_name != result.name:
                    detail += f" via {result.yugipedia_lookup_name!r}"
                if result.yugipedia_file_title:
                    detail += f" -> {result.yugipedia_file_title}"
                lines.append(detail)
        return lines

    def has_rush_duplicate_report(self) -> bool:
        return self.report_duplicates_var.get() and len(self.rush_duplicate_report_lines()) > 2
    def yugipedia_alert_lines(self) -> list[str]:
        has_transient_retries = bool(self.yugipedia_retry_details)
        has_yugipedia_missing = any(
            "Yugipedia" in reason or "no matching Yugipedia" in reason
            for _category, reason, _label in self.download_failures
        )
        if not has_transient_retries and not has_yugipedia_missing:
            return []
        lines = [
            "",
            "Yugipedia alert:",
            "Some Yugipedia lookups failed or needed retries.",
            "Yugipedia may be unstable right now. Try running the downloader again later if pictures are missing.",
        ]
        return lines

    def finish_apply_orr_art_choice(self) -> None:
        self.running = False
        self.set_controls(True)
        finished = sum(self.results.values())
        if self.stop_event.is_set():
            self.status_var.set(f"ORR art refresh cancelled. Processed: {finished}/{self.total}")
            self.log("ORR art refresh cancelled by user.")
            return
        applied = self.results["RushHD"]
        errors = self.results["Error"]
        skipped = self.results["Skipped"]
        self.status_var.set(f"ORR art choice applied. Updated: {applied} | Skipped: {skipped} | Errors: {errors}")
        self.log("ORR art choice refresh completed.")
        self.log(f"Updated Rush cards: {applied}")
        self.log(f"Skipped: {skipped}")
        self.log(f"Errors: {errors}")
        mode_text = "ORR Extension art" if self.prefer_orr_extension_var.get() else "normal Rush-HD art"
        self.show_centered_info(
            "ORR Art Choice Applied",
            f"Applied {mode_text}.\n\nUpdated Rush cards: {applied}\nSkipped: {skipped}\nErrors: {errors}",
        )
    def finish(self) -> None:
        if self.executor:
            self.executor.shutdown(wait=False, cancel_futures=True)
            self.executor = None
        self.running = False
        self.set_controls(True)
        finished = sum(self.results.values())
        if self.stop_event.is_set():
            self.status_var.set(f"Download cancelled. Processed: {finished}/{self.total}")
            self.log("Download cancelled by user.")
            return
        self.log("Download completed.")
        for line in self.card_summary_lines(finished):
            self.log(line)

        if self.download_failures:
            for (category, reason), labels in sorted(self.grouped_download_failures().items(), key=lambda kv: (-len(set(kv[1])), kv[0][0], kv[0][1])):
                self.log(self.format_grouped_failure_line(category, reason, labels))
        if self.regular_field_failures:
            grouped: defaultdict[str, list[str]] = defaultdict(list)
            for label, reason in self.regular_field_failures:
                grouped[reason].append(label)
            for reason, labels in sorted(grouped.items(), key=lambda kv: len(kv[1]), reverse=True):
                self.log(self.format_grouped_failure_line("Regular Field cropped images failed", reason, labels))
        if self.rush_field_failures:
            grouped_rush: defaultdict[str, list[str]] = defaultdict(list)
            for label, reason in self.rush_field_failures:
                grouped_rush[reason].append(label)
            for reason, labels in sorted(grouped_rush.items(), key=lambda kv: len(kv[1]), reverse=True):
                self.log(self.format_grouped_failure_line("Rush Field cropped images missing/failed", reason, labels))
        report_path = self.write_report(finished)
        if report_path:
            self.log(f"Report written: {report_path}")
        popup_lines = self.card_summary_lines(finished) + self.yugipedia_alert_lines()
        self.show_centered_info("Download Complete", "Download completed!\n\n" + "\n".join(popup_lines))

    def grouped_download_failures(self) -> dict[tuple[str, str], list[str]]:
        grouped: dict[tuple[str, str], list[str]] = defaultdict(list)
        for category, reason, label in self.download_failures:
            grouped[(category, reason)].append(label)
        return grouped

    def format_grouped_failure_line(self, title: str, reason: str, labels: list[str]) -> str:
        unique = sorted(set(labels))
        reason_text = reason.strip() if reason and reason.strip() else "No detailed reason provided."
        return f"{title} ({reason_text}): {', '.join(unique)}. Total: {len(unique)}"

    def has_reportable_failures(self) -> bool:
        return (
            bool(self.download_failures)
            or bool(self.regular_field_failures)
            or bool(self.rush_field_failures)
            or self.results["Missing"] > 0
            or self.results["Error"] > 0
            or self.field_results["FieldMissing"] > 0
            or self.field_results["FieldError"] > 0
            or self.has_rush_duplicate_report()
        )

    def write_report(self, finished: int) -> Optional[Path]:
        if not self.has_reportable_failures():
            return None
        report_path = SCRIPT_DIR / f"EDOPro-HD-Pics-Download-Report-{datetime.now():%Y%m%d-%H%M%S}.txt"
        lines: list[str] = []
        lines.append("EDOPro Standard/Rush HD Pics Downloader Report")
        lines.append(f"Generated: {datetime.now():%Y-%m-%d %H:%M:%S}")
        lines.append("")
        lines.append("Summary")
        lines.extend(self.card_summary_lines(finished))
        lines.append("")
        if self.report_duplicates_var.get():
            lines.extend(self.rush_duplicate_report_lines())
            lines.append("")

        if self.yugipedia_retry_details:
            lines.append("Yugipedia transient retry details")
            grouped_retries: defaultdict[str, list[tuple[int, str]]] = defaultdict(list)
            for label, retry_count, reason in self.yugipedia_retry_details:
                grouped_retries[label].append((retry_count, reason))
            total_attempts = sum(count for label_entries in grouped_retries.values() for count, _reason in label_entries)
            lines.append(f"Total retried cards: {len(grouped_retries)}")
            lines.append(f"Total retry attempts: {total_attempts}")
            for label, entries in sorted(grouped_retries.items()):
                attempts = sum(count for count, _reason in entries)
                reason = entries[-1][1] if entries else "Transient Yugipedia API error"
                lines.append(f"  - {label}: {attempts} retry attempt(s); last reason: {reason}")
            lines.append("")

        grouped_downloads = self.grouped_download_failures()
        lines.append("Regular card image failures grouped by reason")
        if grouped_downloads:
            for (category, reason), labels in sorted(grouped_downloads.items(), key=lambda kv: (-len(set(kv[1])), kv[0][0], kv[0][1])):
                unique = sorted(set(labels))
                lines.append(f"{category} ({reason}): Total: {len(unique)}")
                for label in unique:
                    lines.append(f"  - {label}")
                lines.append("")
        else:
            lines.append("None")
            lines.append("")

        lines.append("Regular Field cropped image failures grouped by reason")
        if self.regular_field_failures:
            grouped_regular: defaultdict[str, list[str]] = defaultdict(list)
            for label, reason in self.regular_field_failures:
                grouped_regular[reason].append(label)
            for reason, labels in sorted(grouped_regular.items(), key=lambda kv: (-len(set(kv[1])), kv[0])):
                unique = sorted(set(labels))
                lines.append(f"Regular Field cropped images failed ({reason}): Total: {len(unique)}")
                for label in unique:
                    lines.append(f"  - {label}")
                lines.append("")
        else:
            lines.append("None")
            lines.append("")

        lines.append("Rush Field cropped image failures")
        if self.rush_field_failures:
            grouped_rush: defaultdict[str, list[str]] = defaultdict(list)
            for label, reason in self.rush_field_failures:
                grouped_rush[reason].append(label)
            for reason, labels in sorted(grouped_rush.items(), key=lambda kv: (-len(set(kv[1])), kv[0])):
                unique = sorted(set(labels))
                lines.append(f"Rush Field cropped images missing/failed ({reason}): Total: {len(unique)}")
                for label in unique:
                    lines.append(f"  - {label}")
                lines.append("")
        else:
            lines.append("None")
        report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return report_path

    def cancel(self) -> None:
        if self.running and messagebox.askyesno("Confirm Cancellation", "Are you sure you want to cancel the download?"):
            self.stop_event.set()
            self.cancel_btn.configure(state="disabled")
            self.status_var.set("Cancelling download...")
            self.log("Cancellation requested...")

    def on_close(self) -> None:
        try:
            save_settings(self.current_settings_dict())
        except Exception:
            pass
        if self.running:
            if not messagebox.askyesno("Confirm Exit", "A download is in progress. Are you sure you want to exit?"):
                return
            self.stop_event.set()
            if self.executor:
                self.executor.shutdown(wait=False, cancel_futures=True)
        self.destroy()


if __name__ == "__main__":
    App().mainloop()
































































