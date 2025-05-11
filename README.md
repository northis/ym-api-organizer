# Yandex-Music Organizer (fork)

A lightweight CLI tool that keeps a local copy of your favourite Yandex Music
playlist.  It is built on top of the excellent
[`yandex-music-api`](https://github.com/MarshalX/yandex-music-api) library but
works with an **API token** instead of browser cookies.  The script downloads
only **new** tracks added to the playlist since the previous run, tags them and
stores as `NNNN. Artist – Title.mp3` in the folder you choose.

> This repository is a fork of MarshalX/yandex-music-api and therefore contains
> the original library sources.  The original upstream documentation can still
> be found in [`README_ORIGINAL.md`](README_ORIGINAL.md).

## Features

* Pure-Python (3.8+) implementation – no external FFmpeg or youtube-dl needed.
* Uses official MarshalX library objects (`Client`, `Track`, …) – any API change
  is automatically picked up with future library updates.
* Embeds full metadata: cover art, lyrics (if available) and a web link to the
  track.
* Respects a configurable per-run download limit (defaults to **20**).
* Filenames are prefixed with a zero-padded sequential id so that new songs are
  always ordered after the existing collection.

## Quick Start

```bash
pip install -r requirements-вум.txt  # installs mutagen & python-dotenv only

cp .env.template .env            # then edit .env with your values
python yandex_music_organizer_api.py
```

## Configuration (.env)

| Variable      | Required | Description                                   |
|---------------|----------|-----------------------------------------------|
| `API_KEY`     | yes      | Yandex Music personal access token            |
| `PLAYLIST_URL`| yes      | Full URL of the playlist to mirror            |
| `TARGET_DIR`  | yes      | Directory where downloaded tracks are stored  |
| `MAX_DOWNLOADS` | no     | Override hard cap of tracks per execution     |

See `.env.template` shipped with the repo for an example.

## How it works

1. The script reads `.env` and initialises `yandex_music.Client` with your
   token.
2. It fetches the playlist, determines which tracks are *not* present locally
   (based on artist-title match) and applies the per-run limit.
3. Each new track is downloaded (highest-bit-rate MP3), tagged and renamed to
   `NNNN. Artist – Title.mp3` where `NNNN` is the next id.
4. That’s it.  Future invocations will pick up only the delta.

## License

This fork inherits the original LGPL-3.0 license of yandex-music-api.
