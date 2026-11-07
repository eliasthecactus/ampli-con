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
- **A real player in the web interface.** A queue that follows the list you started from, shuffle, repeat,
  volume and seeking, songs added by hand, media keys and the lock screen. Shortcuts: space to pause, arrows to
  seek, shift and arrows to skip, M to mute.
- **For you.** The Search page opens with the sections of the YouTube Music home page: your recommendations with a
  connected account, what's popular without one.
- **Download.** One song, a whole album or a whole playlist, with live progress in the app and in the web
  interface. Files get proper tags (title, artists, album, album artist, numbers, year, genre, label, ISRC), a square
  cover and, if you want, lyrics.
- **Review before downloading** (optional): Amplicon looks songs up on Deezer, shows what it found and lets you pick
  another match, change every field or upload a cover. The download starts when you confirm.
- **Import**: copy your collection into a location and it waits in the Import tab, in almost any format (M4A, MP3,
  FLAC, WAV, AIFF, Ogg, Opus, WMA, AAC, APE, WavPack, Musepack, TAK, TTA, DSF). Importing gives files a profile and
  changes nothing on the disk.
- **Library**: every imported file and download in one list, with filters, sorting, a tag editor, bulk tools, a
  duplicate check and an AI tag helper through OpenRouter (bring your own key; you see every suggestion before it's
  written). Files deleted from the disk stay listed as deleted.
- **Files that match their profile**: the library shows which files have the wrong name, are outside the profile's
  location, lack tags the profile requires or have no cover, and Fix takes care of it: it moves and renames files and
  looks up what's missing. `{artist} - {title}` is the default file name, set per profile. Formats stay as they are.
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
also use your own playlists, show your recommendations and play songs that need a sign-in.

Google doesn't let self-hosted apps sign in with a password, so Amplicon uses your browser's sign-in cookies.
The web interface walks you through it (YouTube page): sign in to music.youtube.com in a private window, export
the cookies (or copy the `cookie` request header from the developer tools) and paste them. They are stored
encrypted in `/config` and refreshed when YouTube rotates them.

By default Amplicon plays and downloads without the account and only uses it when YouTube asks for a
sign-in. Settings has a switch to always use it.

### Download profiles and locations

On the first start, `/downloads` becomes the location "Downloads" and a profile "Standard" (M4A, files named
like `Artist - Title.m4a`) is created. Add more locations under **Locations** (any folder inside
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
| `{genre}` | Electro |
| `{label}` | Daft Life |
| `{playlist}` | Road Trip |
| `{position}`, `{position:03}` | 7, 007 |
| `{video_id}` | FGBhQbmPwH8 |

`/` makes folders. Text in `[ ]` disappears when a field inside is empty, e.g. `{album}[ ({year})]`.

When you change a template, Amplicon shows how many files would move and renames them when you confirm. Tag changes in
the library rename the file right away (Settings can turn that off). Files without a title or an artist keep their
name, a name that's already taken is reported instead of overwritten, and M3U playlists follow along. Only imported
files and downloads are renamed.

### Importing a collection

Copy your music into a location and press **Scan** in the Library (scans also run every 6 hours). The files show up in
the **Import** tab, folder by folder, and stay out of the library until you import them or choose to ignore them.
Importing assigns a profile and changes nothing on the disk, so it takes seconds, also for thousands of files.

The library then shows what doesn't match the profile. **Fix** does two things, and you decide about each:

- It moves files to the profile's location and gives them its file names.
- It looks up what's missing on Deezer, about two files per second, in the background. It uses the tags, and for files
  without tags the file name and folders. A `cover.jpg` next to a file is used as its cover. You see every change
  before it's written, and can choose to only fill empty tags (the default) or to replace them with the match.

Files keep their format. Songs you already have, also from an import, are marked in the review before a download, and
a song with the same YouTube id isn't downloaded again.

A file that disappears from the disk stays in the library as **Deleted**, with its tags, and comes back when the file
does. Moved by hand, it keeps its profile. You can take deleted files off the list, or download them again if Amplicon
downloaded them. A location that isn't mounted changes nothing.

### Metadata and the AI tag helper

Turn on **Settings › Review the metadata before every download** to check songs before they're saved. Look-ups use
Deezer's public API (no account needed) and, if you want, large covers from Apple. If a source is down, you still get
the review with YouTube Music's data.

For the AI helper, add an [OpenRouter](https://openrouter.ai/keys) key in Settings. The model and the default
instruction can be changed there. File names and tags of the songs you select are sent to OpenRouter, and nothing is
written until you apply the suggestions.

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
