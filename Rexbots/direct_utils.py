"""
Shared helpers for direct-link downloader plugins (catbox, pixeldrain, gofile,
mediafire, streamtape, terabox, mega, gdrive, torrent, gallery).

Every plugin extracts a direct download URL (or list of files) on its own,
then calls into this module to actually stream the download to disk,
build a thumbnail/metadata for videos, and upload the result to Telegram.
"""

import os
import time
import random
import asyncio
import subprocess
import aiohttp
import requests
from pyrogram import enums

E_CHECK  = '<emoji id=5206607081334906820>✔️</emoji>'
E_CROSS  = '<emoji id=5210952531676504517>❌</emoji>'
E_ROCKET = '<emoji id=5456140674028019486>🚀</emoji>'
E_INFO   = '<emoji id=5334544901428229844>ℹ️</emoji>'
E_BOLT   = '⚡️'
E_SIZE   = '💯'
E_CLOCK  = '⌛'
E_ETA    = '💡'

DEFAULT_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                  '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
}

def fmt_bytes(n):
    if not n:
        return "0B"
    n = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f}{unit}" if unit != "B" else f"{n:.0f}{unit}"
        n /= 1024
    return f"{n:.1f}TB"


def fmt_duration(seconds):
    if seconds is None:
        return "0s"
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m}m {s}s"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


def draw_bar(percent, length=12):
    percent = max(0, min(100, percent or 0))
    filled = round(length * percent / 100)
    return "▓" * filled + "░" * (length - filled)


def format_progress(percent, speed_bps=None, done_bytes=None, total_bytes=None,
                     elapsed_secs=None, eta_secs=None, title="Processing Task...") -> str:
    """Renders the standard progress card used across every downloader:

    ⚡️ Processing Task...

    Progress: ▓▓▓▓░░░░░░░░ 33.0%
    🚀 Speed: 1.2MB/s
    💯 Size: 12.0 MB of 36.4 MB
    ⌛ Elapsed: 8s
    💡 ETA: 16s
    """
    pct = percent if percent is not None else 0.0
    bar = draw_bar(pct)
    speed = f"{fmt_bytes(speed_bps)}/s" if speed_bps else "0B/s"
    done = fmt_bytes(done_bytes) if done_bytes is not None else "?"
    total = fmt_bytes(total_bytes) if total_bytes else "?"
    elapsed = fmt_duration(elapsed_secs)
    eta = fmt_duration(eta_secs)
    return (
        f"<b>{E_BOLT} {title}</b>\n\n"
        f"<b>Progress:</b> <code>{bar}</code> {pct:.1f}%\n"
        f"{E_ROCKET} <b>Speed:</b> {speed}\n"
        f"{E_SIZE} <b>Size:</b> {done} of {total}\n"
        f"{E_CLOCK} <b>Elapsed:</b> {elapsed}\n"
        f"{E_ETA} <b>ETA:</b> {eta}"
    )
def fmt_count(n):
    if n is None:
        return None
    try:
        return f"{int(n):,}"
    except (TypeError, ValueError):
        return None


def fmt_upload_date(date_str):
    if not date_str or len(date_str) != 8:
        return None
    return f"{date_str[0:4]}-{date_str[4:6]}-{date_str[6:8]}"


def format_media_caption(info: dict, credit: str = None) -> str:
    """Builds the rich metadata caption (title/channel/views/etc.) from a
    yt-dlp info dict. Only fields that are actually present are shown, since
    not every site (or every video) exposes all of them.

    📹 <title>
    👤 <uploader> @<handle>
    👥 <follower count>
    ⏱ <duration>
    👁 <views> | 👍 <likes> | 💬 <comments>
    🏷 <category>
    📅 <upload date>

    Downloaded by @<credit>
    """
    if not info:
        info = {}
    lines = []

    # Different extractors populate different fields — Instagram/Facebook
    # often leave "title" empty and put the caption text in "description"
    # instead, so fall through several fields rather than showing nothing.
    title = (
        info.get("title")
        or info.get("fulltitle")
        or (info.get("description") or "").strip().split("\n")[0][:100]
        or None
    )
    if title:
        lines.append(f"📹 <b>{title}</b>")

    uploader = (
        info.get("uploader") or info.get("channel") or info.get("uploader_id")
    )
    handle = info.get("uploader_id") or info.get("channel_id") or info.get("channel_handle")
    if uploader or handle:
        who = uploader or ""
        if handle and str(handle) != str(uploader):
            handle_str = f"@{handle}" if not str(handle).startswith("@") else str(handle)
            who = f"{who} {handle_str}".strip()
        lines.append(f"👤 {who}")

    followers = fmt_count(info.get("channel_follower_count"))
    if followers:
        lines.append(f"👥 {followers}")

    duration = info.get("duration")
    if duration:
        lines.append(f"⏱ {fmt_duration(duration)}")

    views = fmt_count(info.get("view_count"))
    likes = fmt_count(info.get("like_count"))
    comments = fmt_count(info.get("comment_count"))
    stats = [f"👁 {v}" for v in [views] if v]
    if likes:
        stats.append(f"👍 {likes}")
    if comments:
        stats.append(f"💬 {comments}")
    if stats:
        lines.append(" | ".join(stats))

    categories = info.get("categories") or ([info["category"]] if info.get("category") else None)
    if categories:
        lines.append(f"🏷 {categories[0]}")

    date_str = fmt_upload_date(info.get("upload_date"))
    if date_str:
        lines.append(f"📅 {date_str}")

    caption = "\n".join(lines) if lines else f"<b>{E_CHECK} Downloaded via yt-dlp</b>"
    if credit:
        caption += f"\n\nDownloaded by @{credit}"
    return caption


AUDIO_EXTS = ('.mp3', '.m4a', '.wav', '.flac', '.ogg', '.opus')
PHOTO_EXTS = ('.jpg', '.jpeg', '.png', '.webp')


def make_output_folder(service: str) -> str:
    folder = os.path.join("downloads", service)
    os.makedirs(folder, exist_ok=True)
    return folder


def safe_filename(name: str, fallback: str) -> str:
    name = (name or "").strip().strip("/\\")
    if not name:
        return fallback
    # strip characters that break filesystem paths
    return "".join(c for c in name if c not in '\\/:*?"<>|') or fallback


async def run_subprocess_with_progress(cmd, status, label, parse_line, interval: float = 3.0):
    """Runs `cmd`, streaming its combined stdout live instead of buffering the
    whole output with communicate() (which is why mega/torrent/gallery had no
    progress before — communicate() only returns once the process exits).

    Tools like aria2c/megatools redraw their progress line in place using
    carriage returns ('\\r') rather than newlines, so we read raw bytes and
    split on both '\\r' and '\\n' to catch every update.

    parse_line(line: str, elapsed: float) -> str | None
        Given one output line and seconds elapsed since the process started,
        return an HTML status text to show (typically built with
        format_progress()), or None to ignore that line. Edits are throttled
        to `interval` seconds so we don't hit Telegram's rate limit.

    Returns (returncode, last_stderr_tail).
    """
    start = time.monotonic()
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )

    buf = b""
    last_edit = 0.0
    tail = b""

    while True:
        chunk = await proc.stdout.read(4096)
        if not chunk:
            break
        buf += chunk
        tail = (tail + chunk)[-2000:]  # keep a tail in case we need it for an error message

        while True:
            cut = -1
            for sep in (b"\r", b"\n"):
                idx = buf.find(sep)
                if idx != -1 and (cut == -1 or idx < cut):
                    cut = idx
            if cut == -1:
                break
            line, buf = buf[:cut], buf[cut + 1:]
            elapsed = time.monotonic() - start
            try:
                text = parse_line(line.decode(errors="replace").strip(), elapsed)
            except Exception:
                text = None
            if text:
                now = time.monotonic()
                if status is not None and now - last_edit >= interval:
                    last_edit = now
                    try:
                        await status.edit_text(text, parse_mode=enums.ParseMode.HTML)
                    except Exception:
                        pass

    await proc.wait()
    return proc.returncode, tail.decode(errors="replace").strip()


async def stream_download(url: str, dest: str, status, label: str,
                           headers: dict = None, timeout: int = 300) -> int:
    """Streams url -> dest with periodic status.edit_text progress updates.
    Returns total bytes downloaded. Raises on HTTP/network failure."""
    headers = headers or DEFAULT_HEADERS
    downloaded = 0
    last_edit = 0.0
    start = time.monotonic()
    timeout_cfg = aiohttp.ClientTimeout(total=timeout)

    async with aiohttp.ClientSession(timeout=timeout_cfg) as session:
        async with session.get(url, headers=headers) as resp:
            if resp.status not in (200, 206):
                raise ValueError(f"HTTP {resp.status} while downloading")
            total = int(resp.headers.get("Content-Length", 0))

            with open(dest, "wb") as f:
                async for chunk in resp.content.iter_chunked(1024 * 1024):
                    if not chunk:
                        continue
                    f.write(chunk)
                    downloaded += len(chunk)

                    now = time.monotonic()
                    if status is not None and now - last_edit >= 3:
                        last_edit = now
                        elapsed = now - start
                        speed = downloaded / elapsed if elapsed > 0 else 0
                        pct = (downloaded / total * 100) if total else 0
                        eta = ((total - downloaded) / speed) if (total and speed > 0) else None
                        try:
                            await status.edit_text(
                                format_progress(pct, speed_bps=speed, done_bytes=downloaded,
                                                 total_bytes=total or None, elapsed_secs=elapsed,
                                                 eta_secs=eta, title=label),
                                parse_mode=enums.ParseMode.HTML
                            )
                        except Exception:
                            pass
    return downloaded


def download_official_thumbnail(info: dict, thumb_path: str) -> bool:
    """Downloads the actual thumbnail image the site provides (from yt-dlp's
    info dict) and converts/scales it to a Telegram-friendly JPEG, instead of
    grabbing a random frame out of the video with ffmpeg.

    Picks the highest-resolution thumbnail listed, since yt-dlp's "thumbnails"
    list is ordered from lowest to highest quality.
    """
    if not info:
        return False
    thumb_url = None
    thumbs = info.get("thumbnails") or []
    if thumbs:
        thumb_url = thumbs[-1].get("url")
    thumb_url = thumb_url or info.get("thumbnail")
    if not thumb_url:
        return False

    raw_path = thumb_path + ".src"
    try:
        resp = requests.get(thumb_url, timeout=20)
        resp.raise_for_status()
        with open(raw_path, "wb") as f:
            f.write(resp.content)
    except Exception:
        return False

    try:
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", raw_path,
             "-vf", "scale=320:-1", "-y", thumb_path],
            timeout=30, check=True
        )
        return os.path.exists(thumb_path)
    except Exception:
        return False
    finally:
        try:
            os.remove(raw_path)
        except Exception:
            pass


def extract_thumbnail(video_path: str, thumb_path: str) -> bool:
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", video_path],
        capture_output=True, text=True, timeout=30
    )
    try:
        duration = float(probe.stdout.strip() or "10")
    except ValueError:
        duration = 10.0
    seek = random.uniform(duration * 0.1, duration * 0.8) if duration > 1 else 0
    try:
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", str(seek),
             "-i", video_path, "-vframes", "1", "-vf", "scale=320:-1", "-y", thumb_path],
            timeout=30, check=True
        )
        return os.path.exists(thumb_path)
    except Exception:
        return False


def get_video_metadata(video_path: str):
    dur = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", video_path],
        capture_output=True, text=True, timeout=30
    )
    try:
        duration = int(float(dur.stdout.strip() or "0"))
    except ValueError:
        duration = 0
    dim = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0", video_path],
        capture_output=True, text=True, timeout=30
    )
    try:
        w, h = dim.stdout.strip().split(",")
        width, height = int(w), int(h)
    except Exception:
        width, height = 1280, 720
    return duration, width, height


async def upload_file(client, message, path: str, status, caption: str):
    """Sends a downloaded file to Telegram as video/audio/photo/document
    based on its extension, then cleans up the local copy."""
    ext = os.path.splitext(path)[1].lower()
    thumb = None
    try:
        await status.edit_text(f"<b>{E_ROCKET} Uploading...</b>", parse_mode=enums.ParseMode.HTML)

        if ext in VIDEO_EXTS:
            thumb = path + ".jpg"
            has_thumb = await asyncio.to_thread(extract_thumbnail, path, thumb)
            duration, width, height = await asyncio.to_thread(get_video_metadata, path)
            await client.send_video(
                chat_id=message.chat.id, video=path,
                thumb=thumb if has_thumb else None,
                duration=duration, width=width, height=height,
                caption=caption, reply_to_message_id=message.id,
                supports_streaming=True, parse_mode=enums.ParseMode.HTML
            )
        elif ext in AUDIO_EXTS:
            await client.send_audio(
                chat_id=message.chat.id, audio=path,
                caption=caption, reply_to_message_id=message.id,
                parse_mode=enums.ParseMode.HTML
            )
        elif ext in PHOTO_EXTS:
            await client.send_photo(
                chat_id=message.chat.id, photo=path,
                caption=caption, reply_to_message_id=message.id,
                parse_mode=enums.ParseMode.HTML
            )
        else:
            await client.send_document(
                chat_id=message.chat.id, document=path,
                caption=caption, reply_to_message_id=message.id,
                parse_mode=enums.ParseMode.HTML
            )
        await status.delete()
    finally:
        for f in (path, thumb):
            if f:
                try:
                    os.remove(f)
                except Exception:
                    pass
