# Amplicon

Amplicon is a small self-hosted server that connects [Ampli](https://benavo.ch/apps/ampli) to YouTube Music.
Add it in Ampli as a server and you can search YouTube Music, play songs right away and save songs, albums
or playlists into your own music library, in the format and folder layout you choose.

It runs as a single Docker container with a web interface for everything around it: the YouTube sign-in,
download locations, download profiles and a live view of the queue.

> Most of Amplicon was written by Claude (Anthropic's AI) with me reviewing and testing it.

## What it does

- **Search and play.** Songs, albums, playlists and artists from YouTube Music, in the app and in the web
  interface. Amplicon resolves the audio with yt-dlp and passes it through to the player, with seeking and an
  on-disk cache, so a song played twice comes from the disk.
- **Download.** One song, a whole album or a whole playlist, with live progress in the app and in the web
  interface. Files get proper tags from YouTube Music (title, artists, album, album artist, track number,
  year), a square cover and, if you want, lyrics.
- **Download profiles**, like the quality profiles of Sonarr and Radarr: format (M4A or Opus as YouTube has
  it, or MP3), target location, file name templates for album songs, singles and playlists, an M3U file for
  playlists, SponsorBlock, what to do with files that are already there. Pick one when you download, or let
  the default one do it.
- **Several download locations**: every folder you mount into the container. A location can ask Navidrome
  or Jellyfin to scan after a download, so new songs show up in Ampli right away.
- **Your YouTube Music playlists.** With your YouTube account connected, Ampli lists your playlists and can
  create, rename and delete them, and add, remove and reorder songs.

## Setup

```yaml
services:
  amplicon:
    image: ghcr.io/eliasthecactus/ampli-con:latest
    container_name: amplicon
    restart: unless-stopped
    ports:
      - "8080:8080"
    environment:
      - PUID=1000
      - PGID=1000
      - TZ=Etc/UTC
      # Optional, see "The web user" below
      # - AMPLICON_USERNAME=admin
      # - AMPLICON_PASSWORD=change-me-please
    volumes:
      - ./container/amplicon/config:/config
      - ./container/amplicon/downloads:/downloads
      # More download locations, e.g. your Navidrome or Jellyfin music folder:
      # - /mnt/hdd/music:/music
```

Then open `http://<host>:8080` and sign in.

Put it behind your reverse proxy with https if Ampli connects from outside your home network. For the live
progress, the proxy must not buffer `/api/events` (in nginx: `proxy_buffering off;`; Nginx Proxy Manager
handles it with the default settings).

### The web user

There is one user. It is used for the web interface and for signing in from Ampli.

- With `AMPLICON_USERNAME` and `AMPLICON_PASSWORD` set, that user is created and kept in sync on every start.
- Without them, Amplicon creates `admin` with a random password on the first start and prints it once to
  the log:

  ```sh
  docker logs amplicon
  ```

- Forgot the password? This prints a new one and signs out all browsers and apps:

  ```sh
  docker exec -it amplicon amplicon reset-password
  # or choose it yourself:
  docker exec -it amplicon amplicon reset-password --password 'something long'
  ```

  With the environment variables set, they replace the password again on the next restart, so change them
  in docker compose instead.

### YouTube sign-in

Search, albums, public playlists, playing and downloading work without an account. With one, Amplicon can
also use your own playlists and play songs that need a sign-in.

Google doesn't let self-hosted apps sign in with a password, so Amplicon uses your browser's sign-in cookies.
The web interface walks you through it (YouTube page): sign in to music.youtube.com in a private window, export
the cookies (or copy the `cookie` request header from the developer tools) and paste them. They are stored
encrypted in `/config` and refreshed when YouTube rotates them.

By default Amplicon plays and downloads without the account and only uses it when YouTube asks for a
sign-in. Settings has a switch to always use it.

### Download profiles and locations

On the first start, `/downloads` becomes the location "Downloads" and a profile "Standard" (M4A, files named
like `Artist/Album (Year)/01 Title.m4a`) is created. Add more locations under **Locations** (any folder inside
a mounted volume) and more profiles under **Profiles**.

File name templates use these fields:

| Field | Example |
| --- | --- |
| `{artist}` | Daft Punk |
| `{albumartist}` | Daft Punk |
| `{album}` | Discovery |
| `{title}` | One More Time |
| `{track}`, `{track:02}` | 1, 01 |
| `{disc}` | 1 |
| `{year}` | 2001 |
| `{playlist}` | Road Trip |
| `{position}`, `{position:03}` | 7, 007 |
| `{video_id}` | FGBhQbmPwH8 |

`/` makes folders. Text in `[ ]` disappears when a field inside is empty, e.g. `{album}[ ({year})]`.

## In Ampli

Settings › Servers › Add › **Amplicon**, with the address you open in the browser and the web user. Then:

- Search has a **YouTube** tab next to your library.
- Songs, albums and playlists from YouTube have **Download to Server…** (with the profile to use) and
  **Add to Playlist…** (your YouTube playlists).
- Downloads shows the server's queue with live progress.

Each app gets its own sign-in; the web interface lists them under Settings and can sign them out.

## Development

```sh
# backend
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q            # uses a fake yt-dlp and a fake YouTube Music, no network

# frontend (proxies /api to 127.0.0.1:8765)
cd frontend
npm ci && npm run dev
```

Run the backend for the frontend with
`CONFIG_DIR=./data/config DOWNLOADS_DIR=./data/downloads .venv/bin/uvicorn app.main:app --port 8765`.

## License

MIT
