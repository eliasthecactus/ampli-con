"""yt-dlp binary management (install, background updates) and process execution."""
import asyncio
import hashlib
import json
import logging
import os
import re
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

import httpx

from . import config

log = logging.getLogger("amplicon.ytdlp")

REPOS = {"stable": "yt-dlp/yt-dlp", "nightly": "yt-dlp/yt-dlp-nightly-builds"}
CURRENT_FILE = "current"
PROG = "ACPROG "
DONE = "ACDONE "
DONE_FIELDS = ("filepath,id,title,channel,channel_id,uploader,description,artist,track,album,"
               "release_year,upload_date,duration,thumbnail")

state: dict[str, Any] = {
    "version": None, "path": None, "latest": None, "last_check": None,
    "last_result": None, "updating": False,
}
_active = 0                          # running yt-dlp processes
_idle = asyncio.Event()              # set when no process runs
_update_lock = asyncio.Lock()
_no_update = asyncio.Event()         # set when no update is in progress
_idle.set()
_no_update.set()


def _vkey(v: str | None) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", v or ""))


def binary() -> Path:
    if state["path"] and Path(state["path"]).exists():
        return Path(state["path"])
    cur = config.BIN_DIR / CURRENT_FILE
    if cur.exists():
        p = config.BIN_DIR / cur.read_text().strip()
        if p.exists():
            state["path"] = str(p)
            return p
    raise RuntimeError("yt-dlp is not installed yet")


async def _run_version(path: Path) -> str:
    proc = await asyncio.create_subprocess_exec(
        str(path), "--version", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    out, err = await asyncio.wait_for(proc.communicate(), 60)
    if proc.returncode != 0:
        raise RuntimeError(f"yt-dlp --version failed: {err.decode(errors='replace')[-300:]}")
    return out.decode().strip()


def _activate(path: Path, version: str) -> None:
    tmp = config.BIN_DIR / (CURRENT_FILE + ".tmp")
    tmp.write_text(path.name)
    os.replace(tmp, config.BIN_DIR / CURRENT_FILE)
    state.update(version=version, path=str(path))


def _cleanup_old() -> None:
    """Remove versions that are not current (only while nothing runs)."""
    if _active:
        return
    cur = state["path"] and Path(state["path"]).name
    for p in config.BIN_DIR.glob("yt-dlp-*"):
        if p.name != cur and not p.name.endswith(".download"):
            try:
                p.unlink()
            except OSError:
                pass


async def ensure_installed() -> None:
    """Make sure a usable yt-dlp exists in /config/bin. Copies the image's bundled one on first start."""
    config.BIN_DIR.mkdir(parents=True, exist_ok=True)
    try:
        p = binary()
        state["version"] = await _run_version(p)
        return
    except Exception:
        pass
    if config.BUNDLED_YTDLP.exists():
        version = await _run_version(config.BUNDLED_YTDLP)
        dest = config.BIN_DIR / f"yt-dlp-{version}"
        shutil.copy2(config.BUNDLED_YTDLP, dest)
        dest.chmod(0o755)
        _activate(dest, version)
        log.info("installed bundled yt-dlp %s", version)
        return
    await update(force=True)


async def latest_release(channel: str) -> dict:
    repo = REPOS.get(channel, REPOS["stable"])
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as c:
        r = await c.get(f"https://api.github.com/repos/{repo}/releases/latest",
                        headers={"Accept": "application/vnd.github+json"})
        r.raise_for_status()
        d = r.json()
    assets = {a["name"]: a["browser_download_url"] for a in d.get("assets", [])}
    return {"tag": d["tag_name"], "assets": assets}


async def update(force: bool = False, channel: str = "stable") -> dict:
    """Check GitHub for a newer yt-dlp and install it side by side (running processes keep their file)."""
    async with _update_lock:
        state["updating"] = True
        try:
            rel = await latest_release(channel)
            state["latest"] = rel["tag"]
            state["last_check"] = time.time()
            if not force and state["version"] and rel["tag"] == state["version"]:
                state["last_result"] = "up to date"
                return {**state, "updating": False}
            if "yt-dlp" not in rel["assets"]:
                raise RuntimeError("release has no yt-dlp asset")
            dest = config.BIN_DIR / f"yt-dlp-{rel['tag']}"
            part = dest.with_name(dest.name + ".download")
            async with httpx.AsyncClient(timeout=120, follow_redirects=True) as c:
                data = (await c.get(rel["assets"]["yt-dlp"])).raise_for_status().content
                sums = ""
                if "SHA2-256SUMS" in rel["assets"]:
                    sums = (await c.get(rel["assets"]["SHA2-256SUMS"])).raise_for_status().text
            if sums:
                want = next((ln.split()[0] for ln in sums.splitlines()
                             if ln.strip().endswith(" yt-dlp")), None)
                got = hashlib.sha256(data).hexdigest()
                if want and want != got:
                    raise RuntimeError(f"checksum mismatch for yt-dlp {rel['tag']}")
            part.write_bytes(data)
            part.chmod(0o755)
            version = await _run_version(part)
            os.replace(part, dest)
            old = state["version"]
            _activate(dest, version)
            _cleanup_old()
            state["last_result"] = f"updated {old or '-'} → {version}"
            log.info("yt-dlp %s", state["last_result"])
            return {**state, "updating": False}
        except Exception as e:
            state["last_result"] = f"error: {e}"
            state["last_check"] = time.time()
            log.warning("yt-dlp update failed: %s", e)
            raise
        finally:
            state["updating"] = False


# ---------------------------------------------------------------- execution

def cache_dir() -> str:
    d = config.CONFIG_DIR / "cache" / "yt-dlp"
    d.mkdir(parents=True, exist_ok=True)
    return str(d)


def base_args(cookies: str | None = None, proxy: str | None = None) -> list[str]:
    args = ["--ignore-config", "--color", "never", "--cache-dir", cache_dir()]
    if cookies:
        args += ["--cookies", cookies]
    if proxy:
        args += ["--proxy", proxy]
    return args


@dataclass
class RunResult:
    returncode: int
    stdout: list[str] = field(default_factory=list)
    stderr: list[str] = field(default_factory=list)
    done: list[dict] = field(default_factory=list)

    @property
    def error_text(self) -> str:
        errs = [ln for ln in self.stderr if "ERROR" in ln]
        return "\n".join(errs[-5:] or self.stderr[-8:])


async def _acquire() -> None:
    global _active
    await _no_update.wait()
    _active += 1
    _idle.clear()


def _release() -> None:
    global _active
    _active -= 1
    if _active <= 0:
        _active = 0
        _idle.set()


async def run(args: list[str], on_line: Callable[[str, str], Awaitable[None] | None] | None = None,
              timeout: float | None = None) -> RunResult:
    """Run yt-dlp with args. on_line(stream, line) gets every output line."""
    await _acquire()
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            str(binary()), *args,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            limit=64 * 1024 * 1024, env={**os.environ, "PYTHONUNBUFFERED": "1"},
            start_new_session=True,
        )
        res = RunResult(returncode=-1)

        async def pump(stream, name: str, sink: list[str]) -> None:
            while True:
                raw = await stream.readline()
                if not raw:
                    break
                line = raw.decode(errors="replace").rstrip("\r\n")
                if name == "stdout" and line.startswith(DONE):
                    try:
                        res.done.append(json.loads(line[len(DONE):]))
                    except ValueError:
                        pass
                if not line.startswith(PROG):
                    sink.append(line)
                    if len(sink) > 400:
                        del sink[:100]
                if on_line:
                    r = on_line(name, line)
                    if asyncio.iscoroutine(r):
                        await r

        await asyncio.wait_for(
            asyncio.gather(pump(proc.stdout, "stdout", res.stdout), pump(proc.stderr, "stderr", res.stderr),
                           proc.wait()),
            timeout)
        res.returncode = proc.returncode
        return res
    finally:
        if proc and proc.returncode is None:
            try:
                os.killpg(proc.pid, 15)
            except (ProcessLookupError, PermissionError):
                proc.kill()
            try:
                await asyncio.wait_for(proc.wait(), 10)
            except asyncio.TimeoutError:
                try:
                    os.killpg(proc.pid, 9)
                except ProcessLookupError:
                    pass
        _release()


async def run_json(args: list[str], timeout: float = 600) -> tuple[dict | None, RunResult]:
    res = await run(args, timeout=timeout)
    data = None
    for line in res.stdout:
        if line.startswith("{"):
            try:
                data = json.loads(line)
                break
            except ValueError:
                continue
    return data, res


async def wait_idle_and_block_updates():
    """Used by the updater: block new processes, wait until running ones finish."""
    _no_update.clear()
    await _idle.wait()


def unblock_updates():
    _no_update.set()


def active_count() -> int:
    return _active


# ---------------------------------------------------------------- errors

_PATTERNS = [
    ("proxy", r"ProxyError|Unable to connect to proxy|Tunnel connection failed|Cannot connect to proxy|"
              r"SOCKS\d? proxy|proxy server|407 Proxy"),
    ("blocked", r"HTTP Error 429|Too Many Requests|confirm you.?re not a bot|confirm you are not a bot|"
                r"rate.?limit|try again later|IP is likely being blocked|Sign in to confirm you.re not"),
    ("auth", r"cookies are no longer valid|Sign in to confirm your age|This playlist is private|"
             r"The playlist does not exist|login required|use --cookies|account cookies|"
             r"Please sign in|requires authentication|members of this channel"),
    ("unavailable", r"Video unavailable|Private video|This video (?:has been removed|is no longer available|"
                    r"is unavailable|is private|is not available)|members.only|Join this channel|"
                    r"account associated with this video has been terminated|copyright|"
                    r"not available in your country|This live event|Premieres in|removed by the uploader|"
                    r"\[Deleted video\]|\[Private video\]|Offline\.|is not a valid URL"),
]


def classify(text: str, used_proxy: bool = False) -> str:
    for kind, pat in _PATTERNS:
        if kind == "proxy" and not used_proxy:
            continue
        if re.search(pat, text, re.I):
            return kind
    return "transient"


RETRYABLE = {"transient", "blocked", "proxy"}
