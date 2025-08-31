#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Yandex Music Organizer (API token version)

This script is a re-implementation of `yandex_music_organizer.py` but works with an
API token instead of browser cookies. It downloads recently added tracks from a
Yandex Music playlist and maintains a local collection with sequential numeric
IDs (zero-padded to 4 digits) in the filename.

Key differences to the cookie-based organiser:
* Uses `yandex_music.Client` with a token (API_KEY) taken from the .env file.
* Downloads at most MAX_DOWNLOADS tracks per run (default 20).
* Relies solely on the official MarshalX/yandex-music-api library that ships
  with this repository.
"""
from __future__ import annotations

import logging
import os
import re
import sys
import time
import datetime as dt
from pathlib import Path
from typing import Dict, List, Tuple

import requests
from dotenv import load_dotenv
import mutagen
from mutagen.mp3 import MP3
from mutagen.id3 import ID3, COMM, TALB, TCON, TDRC, TIT2, TPE1, TPE2, TRCK, USLT, APIC, WOAF
from mutagen.id3._specs import ID3TimeStamp, PictureType
from mutagen.easyid3 import EasyID3
from yandex_music import Client, DownloadInfo, Playlist, Track, YandexMusicModel, Album



MAGIC_BYTES = (
    ("image/jpeg", bytes((0xFF, 0xD8, 0xFF))),
    ("image/png", bytes((0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A))),
)


def get_image_type(data: bytes) -> str:
    """Return MIME type for *data* based on magic bytes.

    The function currently recognises JPEG and PNG headers and returns a
    string suitable for the ``mime`` field of an ID3 *APIC* frame.  When
    the header is not recognised the function falls back to
    ``"image/jpeg"`` because most players will still understand it.

    Parameters
    ----------
    data: bytes
        Raw bytes of the image (or at least its first few bytes).

    Returns
    -------
    str
        ``"image/jpeg"`` or ``"image/png"``.
    """
    for mime_type, magic_bytes in MAGIC_BYTES:
        if data.startswith(magic_bytes):
            return mime_type
    
    return "image/jpeg"

# ---------------------------------------------------------------------------
# Configuration & constants
# ---------------------------------------------------------------------------

FILENAME_RE = re.compile(r"^(\d+).\s*(.+?)\s-\s*(.*?).mp3$", re.IGNORECASE)

logger = logging.getLogger("ym_organizer_api")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def load_config() -> Dict[str, str]:
    """Read mandatory and optional parameters from .env/ environment."""
    load_dotenv()
    cfg = {
        "API_KEY": os.getenv("API_KEY", ""),
        "PLAYLIST_URL": os.getenv("PLAYLIST_URL", ""),
        "TARGET_DIR": os.path.expanduser(os.getenv("TARGET_DIR", "~/Music/Library")),
        "MAX_DOWNLOADS": os.getenv("MAX_DOWNLOADS", 20),
    }
    missing = [k for k, v in cfg.items() if not v]
    if missing:
        logger.error("Missing required configuration keys: %s", ", ".join(missing))
        sys.exit(1)
    return cfg


def parse_playlist_url(url: str) -> Tuple[str, int]:
    """Return (owner, kind) extracted from *url*.

    Expected form: https://music.yandex.ru/users/{owner}/playlists/{kind}[/*]
    """
    m = re.match(r"https?://music\.yandex\.ru/users/([^/]+)/playlists/(\d+)", url)
    if not m:
        raise ValueError(f"Unsupported playlist url: {url}")
    owner, kind_str = m.groups()
    return owner, int(kind_str)


def sanitize_component(text: str) -> str:
    """Replace illegal filename characters with hyphen."""
    return re.sub(r"[\\/:*?\"<>|]+", "-", text.strip())


def get_highest_local_id(target_dir: Path) -> int:
    """Return the largest numeric ID already present in *target_dir*."""
    if not target_dir.exists():
        return 0
    max_id = 0
    for file in target_dir.glob("*.mp3"):
        m = FILENAME_RE.match(file.name)
        if m:
            try:
                max_id = max(max_id, int(m.group(1)))
            except ValueError:
                pass
    return max_id


def build_track_identifier(track: Track) -> str:
    """Create unique key '<artist>-<title>' in lower case for comparison."""
    artist = track.artists[0].name if track.artists else ""
    return sanitize_component(f"{artist}-{track.title}".lower())


def choose_best_download_info(infos: List[DownloadInfo]) -> DownloadInfo:
    """Pick the highest-bit-rate MP3 from *infos*.

    Parameters
    ----------
    infos: list[yandex_music.DownloadInfo]
        Collection returned by :pyfunc:`Track.get_download_info`.

    Returns
    -------
    yandex_music.DownloadInfo
        The item with the greatest ``bitrate_in_kbps``.
    """
    mp3_infos = [i for i in infos if i.codec == "mp3"] or infos
    return max(mp3_infos, key=lambda i: i.bitrate_in_kbps)


def download_track(track: Track, dest: Path) -> Path | None:
    """Download *track* to *dest* (directory) and return resulting path or None."""
    try:
        infos = track.get_download_info()
        best = choose_best_download_info(infos)
        dest.mkdir(parents=True, exist_ok=True)
        tmp_path = dest / "temp_download.mp3"
        best.download(str(tmp_path))
        return tmp_path
    except Exception as exc:  # broad but easier for cli tool
        logger.error("Failed to download track %s: %s", track.id, exc)
        return None


def full_title(obj: YandexMusicModel) -> str:
    """Return *obj.title* taking possible *version* suffix into account."""
    result = obj["title"]
    if result is None:
        return ""
    if version := obj["version"]:
        result += f" ({version})"
    return result


def set_tags(path: Path, track: Track, id: int):
    """Populate ID3 tags for *track* stored at *path*.

    Besides the standard frames (title, album, artists, year, track
    number) the function embeds:

    * complete synced/unsynced lyrics when available,
    * the original cover art in full resolution,
    * ``WOAF`` frame with a canonical web link to the track.

    Parameters
    ----------
    path: pathlib.Path
        Path to the freshly downloaded MP3 file.
    track: yandex_music.Track
        Full track object with metadata.
    id: int
        Sequential numeric identifier used in the filename.
    """
    album = track.albums[0] if track.albums else Album()
    track_artists = [a.name for a in track.artists if a.name]
    album_artists = [a.name for a in album.artists if a.name]
    tag = MP3(path)
    album_title = full_title(album)
    track_title = full_title(track)
    iso8601_release_date = None
    release_year: str = None
    if album.release_date is not None:
        iso8601_release_date = dt.datetime.fromisoformat(album.release_date).astimezone(
            dt.timezone.utc
        )
        release_year = str(iso8601_release_date.year)
        iso8601_release_date = iso8601_release_date.strftime("%Y-%m-%d %H:%M:%S")
    if year := album.year:
        release_year = str(year)
    track_url = f"https://music.yandex.ru/album/{album.id}/track/{track.id}"

    tag["TIT2"] = TIT2(encoding=3, text=track_title)
    tag["TALB"] = TALB(encoding=3, text=album_title)
    tag["TPE1"] = TPE1(encoding=3, text=track_artists)
    tag["TPE2"] = TPE2(encoding=3, text=album_artists)

    if tdrc_text := iso8601_release_date or release_year:
        tag["TDRC"] = TDRC(encoding=3, text=[ID3TimeStamp(tdrc_text)])
    if id:
        tag["TRCK"] = TRCK(encoding=3, text=str(id))
    
    if track.lyrics_info != None and track.lyrics_info.has_available_text_lyrics:
        if track_lyrics := track.get_lyrics(format_="TEXT"):
            text_lyrics = track_lyrics.fetch_lyrics()
            tag["USLT"] = USLT(encoding=3, text=text_lyrics)

    if track.cover_uri is not None:
        cover_bytes = track.download_cover_bytes(size="orig")
        mime_type = get_image_type(cover_bytes)
        if mime_type is not None:
            tag["APIC"] = APIC(
            encoding=3,
            mime=mime_type,
            type=3,
            data=cover_bytes)        

    tag["WOAF"] = WOAF(
        encoding=3,
        text=track_url)

    tag.save()

# ---------------------------------------------------------------------------
# Main orchestration
# ---------------------------------------------------------------------------

def main() -> None:
    """Entry-point for CLI execution."""
    cfg = load_config()
    owner, kind = parse_playlist_url(cfg["PLAYLIST_URL"])

    client = Client(token=cfg["API_KEY"]).init()
    playlist: Playlist = client.users_playlists(kind, owner)  # type: ignore[arg-type]
    logger.info("Fetched playlist '%s' (%d tracks)", playlist.title, len(playlist.tracks))

    # Build mapping of local collection
    target_dir = Path(cfg["TARGET_DIR"]).expanduser()
    highest_id = get_highest_local_id(target_dir)
    logger.info("Highest local id: %d", highest_id)

    # Determine tracks to fetch (newest first)
    existing_identifiers = set()
    for file in target_dir.glob("*.mp3"):
        m = FILENAME_RE.match(file.name)
        if not m:
            continue
        identifier = f"{m.group(2)}-{m.group(3)}".lower()
        existing_identifiers.add(identifier)

    new_tracks: List[Track] = []
    for item in playlist.tracks:
        # each item is TrackShort
        full = item.fetch_track()
        ident = build_track_identifier(full)
        if ident in existing_identifiers:
            logger.info("Encountered already present track '%s' – stopping scan", ident)
            break
        new_tracks.append(full)
        if len(new_tracks) >= int(cfg["MAX_DOWNLOADS"]):
            logger.info("Maximum number of tracks to download reached.")
            break

    if not new_tracks:
        logger.info("No new tracks to download.")
        return

    logger.info("Will download %d new tracks", len(new_tracks))

    # Download and move with proper ids (oldest gets smallest new id)
    new_tracks.reverse()  # oldest first for id assignment
    next_id = highest_id + 1
    for track in new_tracks:
        temp_path = download_track(track, target_dir)
        if not temp_path:
            continue
        set_tags(temp_path, track, next_id)
        artist = sanitize_component(track.artists[0].name if track.artists else "")
        title = sanitize_component(track.title)
        id_str = f"{next_id:04d}"
        final_name = f"{id_str}. {artist} - {title}.mp3"
        final_path = os.path.join(target_dir, final_name)
        temp_path.replace(final_path)
        logger.info(f"Saved {final_name}")
        next_id += 1
        # be polite
        time.sleep(1)

    logger.info("Done. Collection now contains %d items.", next_id - 1)


if __name__ == "__main__":
    main()
