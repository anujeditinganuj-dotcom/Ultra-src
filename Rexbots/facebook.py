import os
import re
import time
import random
import asyncio
import subprocess
import http.cookiejar
import requests
from pyrogram import Client, filters, enums
from pyrogram.types import Message

from config import FB_COOKIES, CREDIT_USERNAME
from Rexbots.direct_utils import format_progress, format_media_caption, download_official_thumbnail
from logger import LOGGER

log = LOGGER(__name__)

try:
    import yt_dlp
except ImportError:
    yt_dlp = None

E_CHECK  = '<emoji id=5206607081334906820>✔️</emoji>'
E_CROSS  = '<emoji id=5210952531676504517>❌</emoji>'
E_ROCKET = '<emoji id=5456140674028019486>🚀</emoji>'
E_INFO   = '<emoji id=5334544901428229844>ℹ️</emoji>'

OUTPUT_FOLDER = "downloads/facebook"
os.makedirs(OUTPUT_FOLDER, exist_ok=True)

FB_PATTERN = re.compile(
    r"(https?://)?(www\.)?(facebook\.com|fb\.watch|fb\.com)/\S+", re.IGNORECASE
)

FETCH_HEADERS = {
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                  '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
}
DOWNLOAD_HEADERS = {
    'user-agent': 'Mozilla/5.0 (Windows NT 6.3; Win64; x64) AppleWebKit/537.36 '
                  '(KHTML, like Gecko) Chrome/86.0.4240.193 Safari/537.36'
}


def extract_fb_url(text: str):
    m = FB_PATTERN.search(text)
    return m.group(0) if m else None


# ---------------------------------------------------------------------------
# Primary path: yt-dlp. Facebook constantly reshuffles its page markup, so a
# hand-rolled HTML/regex scraper (the old approach) breaks every few weeks —
# that's exactly what "Could not find video ID" / "Could not find video
# stream data" mean: the JSON blob it was looking for moved or changed shape.
# yt-dlp's facebook extractor is actively maintained against those changes
# and also handles reels/stories/private videos via cookies, which the old
# scraper explicitly did not support.
# ---------------------------------------------------------------------------

def _make_progress_hook(loop: asyncio.AbstractEventLoop, status: Message, state: dict):
    """yt-dlp calls this from a worker thread, so we hop back onto the bot's
    event loop with run_coroutine_threadsafe instead of calling async code directly."""

    def hook(d):
        if d.get("status") != "downloading":
            return
        now = time.time()
        if now - state.get("last_edit", 0) < 3:   # throttle edits (Telegram rate limits)
            return
        state["last_edit"] = now

        total = d.get("total_bytes") or d.get("total_bytes_estimate")
        downloaded = d.get("downloaded_bytes") or 0
        pct = (downloaded / total * 100) if total else 0

        text = format_progress(
            pct,
            speed_bps=d.get("speed"),
            done_bytes=downloaded,
            total_bytes=total,
            elapsed_secs=d.get("elapsed"),
            eta_secs=d.get("eta"),
            title="Processing Task...",
        )

        async def _edit():
            try:
                await status.edit_text(text, parse_mode=enums.ParseMode.HTML)
            except Exception:
                pass

        asyncio.run_coroutine_threadsafe(_edit(), loop)

    return hook


def _ytdlp_download_fb(url: str, out_dir: str, progress_hook=None) -> str:
    ydl_opts = {
        "outtmpl": os.path.join(out_dir, "%(id)s.%(ext)s"),
        "format": "bestvideo+bestaudio/best",
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
    }
    if progress_hook:
        ydl_opts["progress_hooks"] = [progress_hook]
    if FB_COOKIES and os.path.exists(FB_COOKIES):
        ydl_opts["cookiefile"] = FB_COOKIES

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        filename = ydl.prepare_filename(info)
        if not os.path.exists(filename):
            root, _ = os.path.splitext(filename)
            candidate = root + ".mp4"
            if os.path.exists(candidate):
                filename = candidate
        return filename, info.get("id") or "video", info


# ---------------------------------------------------------------------------
# Fallback path: the original manual scraper, kept only in case yt-dlp itself
# ever fails to extract a given link (e.g. very old/unsupported URL shapes).
# ---------------------------------------------------------------------------

def _load_fb_session() -> requests.Session:
    session = requests.Session()
    if FB_COOKIES and os.path.exists(FB_COOKIES):
        jar = http.cookiejar.MozillaCookieJar()
        try:
            jar.load(FB_COOKIES, ignore_discard=True, ignore_expires=True)
            session.cookies.update(jar)
        except Exception:
            pass
    return session


def _extract_fb_links_sync(link: str):
    import json
    session = _load_fb_session()
    try:
        resp = session.get(link, headers=FETCH_HEADERS, timeout=30)
    except Exception as e:
        raise ValueError(f"Failed to fetch page: {e}")

    resolved_url = resp.url.split('?')[0]
    html = resp.text

    video_id = ''
    for seg in resolved_url.split('/'):
        if seg.isdigit():
            video_id = seg
            break
    if not video_id:
        raw_params = resp.url.split('?')
        if len(raw_params) > 1:
            for param in raw_params[1].split('&'):
                if param.startswith('v=') and param[2:].isdigit():
                    video_id = param[2:]
                    break
    if not video_id:
        raise ValueError("Could not find video ID. Is this a valid Facebook video link?")

    try:
        target = html.split(f'"id":"{video_id}"')[1].split('"dash_prefetch_experimental":[')[1].split(']')[0].strip()
    except IndexError:
        try:
            target = html.split(f'"video_id":"{video_id}"')[1].split('"dash_prefetch_experimental":[')[1].split(']')[0].strip()
        except IndexError:
            raise ValueError(
                "Could not find video stream data. It may be private, age-restricted, "
                "or a story/reel (not supported)."
            )

    try:
        sources = json.loads(f"[{target}]")
    except json.JSONDecodeError as e:
        raise ValueError(f"Failed to parse stream sources: {e}")
    if len(sources) < 2:
        raise ValueError(f"Expected at least 2 stream sources, got {len(sources)}.")

    try:
        video_link = html.split(f'"representation_id":"{sources[0]}"')[1].split('"base_url":"')[1].split('"')[0].replace('\\', '')
    except IndexError:
        raise ValueError("Could not extract video stream URL.")
    try:
        audio_link = html.split(f'"representation_id":"{sources[1]}"')[1].split('"base_url":"')[1].split('"')[0].replace('\\', '')
    except IndexError:
        raise ValueError("Could not extract audio stream URL.")

    return video_link, audio_link, video_id


def _download_file_sync(url: str, dest: str) -> bool:
    try:
        resp = requests.get(url, headers=DOWNLOAD_HEADERS, stream=True, timeout=60)
        resp.raise_for_status()
    except Exception:
        return False
    with open(dest, "wb") as f:
        for chunk in resp.iter_content(chunk_size=512 * 1024):
            if chunk:
                f.write(chunk)
    return True


def _merge_streams(video_path: str, audio_path: str, out_path: str) -> bool:
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", video_path,
           "-i", audio_path, "-c", "copy", "-y", out_path]
    try:
        subprocess.run(cmd, timeout=300, check=True)
        return True
    except Exception:
        return False


def _fallback_download(url: str, out_dir: str):
    video_url, audio_url, video_id = _extract_fb_links_sync(url)
    raw_video = os.path.join(out_dir, f"{video_id}_v.mp4")
    raw_audio = os.path.join(out_dir, f"{video_id}_a.mp4")
    merged    = os.path.join(out_dir, f"{video_id}.mp4")
    if not _download_file_sync(video_url, raw_video):
        raise ValueError("Video stream download failed.")
    if not _download_file_sync(audio_url, raw_audio):
        raise ValueError("Audio stream download failed.")
    if not _merge_streams(raw_video, raw_audio, merged):
        raise ValueError("Merge failed. Is ffmpeg installed on the host?")
    for f in (raw_video, raw_audio):
        try:
            os.remove(f)
        except Exception:
            pass
    return merged, video_id


# ---------------------------------------------------------------------------
# Shared post-processing (thumbnail + metadata via ffprobe/ffmpeg) + handler
# ---------------------------------------------------------------------------

def _extract_thumbnail(video_path: str, thumb_path: str) -> bool:
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", video_path],
        capture_output=True, text=True, timeout=30
    )
    try:
        duration = float(probe.stdout.strip() or "10")
    except ValueError:
        duration = 10.0
    seek = random.uniform(duration * 0.10, duration * 0.80)
    try:
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", str(seek),
             "-i", video_path, "-vframes", "1", "-vf", "scale=320:-1", "-y", thumb_path],
            timeout=30, check=True
        )
        return os.path.exists(thumb_path)
    except Exception:
        return False


def _get_video_metadata(video_path: str):
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


async def _handle_fb_download(client: Client, message: Message, url: str):
    status = await message.reply_text(
        f"<b>{E_INFO} Facebook link detected — extracting...</b>", parse_mode=enums.ParseMode.HTML
    )

    merged = None
    video_id = "video"
    info = None
    loop = asyncio.get_event_loop()
    hook_state = {"last_edit": 0.0}
    try:
        if yt_dlp is not None:
            await status.edit_text(f"<b>{E_ROCKET} Downloading...</b>", parse_mode=enums.ParseMode.HTML)
            hook = _make_progress_hook(loop, status, hook_state)
            try:
                merged, video_id, info = await asyncio.to_thread(_ytdlp_download_fb, url, OUTPUT_FOLDER, hook)
            except Exception as e:
                # Previously swallowed silently, so the user only ever saw the
                # generic fallback-scraper error below and the real reason
                # yt-dlp failed (stale cookies, blocked, unsupported URL
                # shape, etc.) never made it into the logs.
                log.warning("yt-dlp failed for %s: %s", url, e)
                merged = None  # fall through to the manual scraper below

        if merged is None:
            await status.edit_text(f"<b>{E_ROCKET} Downloading (fallback method)...</b>", parse_mode=enums.ParseMode.HTML)
            merged, video_id = await asyncio.to_thread(_fallback_download, url, OUTPUT_FOLDER)
            info = None  # the legacy scraper has no title/views/etc. to show
    except ValueError as e:
        return await status.edit_text(
            f"<b>{E_CROSS} Extraction failed:</b>\n<code>{e}</code>", parse_mode=enums.ParseMode.HTML
        )
    except Exception as e:
        return await status.edit_text(
            f"<b>{E_CROSS} Extraction failed:</b>\n<code>{e}</code>", parse_mode=enums.ParseMode.HTML
        )

    thumb = os.path.join(OUTPUT_FOLDER, f"{video_id}.jpg")
    try:
        has_thumb = False
        if info:
            has_thumb = await asyncio.to_thread(download_official_thumbnail, info, thumb)
        if not has_thumb:
            # No metadata (legacy scraper path) or the site didn't list a
            # thumbnail — fall back to grabbing a frame from the video itself.
            has_thumb = await asyncio.to_thread(_extract_thumbnail, merged, thumb)
        duration, width, height = await asyncio.to_thread(_get_video_metadata, merged)

        await status.edit_text(f"<b>{E_ROCKET} Uploading...</b>", parse_mode=enums.ParseMode.HTML)
        if info:
            caption = format_media_caption(info, credit=CREDIT_USERNAME)
        else:
            caption = f"<b>{E_CHECK} Facebook Video</b>\n🆔 <code>{video_id}</code>"
            if CREDIT_USERNAME:
                caption += f"\n\nDownloaded by @{CREDIT_USERNAME}"
        await client.send_video(
            chat_id=message.chat.id,
            video=merged,
            thumb=thumb if has_thumb else None,
            duration=duration, width=width, height=height,
            caption=caption,
            reply_to_message_id=message.id,
            supports_streaming=True,
            parse_mode=enums.ParseMode.HTML
        )
        await status.delete()
    except Exception as e:
        await status.edit_text(f"<b>{E_CROSS} Error:</b>\n<code>{e}</code>", parse_mode=enums.ParseMode.HTML)
    finally:
        for f in (merged, thumb):
            try:
                os.remove(f)
            except Exception:
                pass


# Registered in a SEPARATE handler group (1) so it runs independently of the
# main t.me link-saving handler in start.py — both get a chance to process
# the same message instead of one silently swallowing the other.
#
# Excludes messages starting with "/" so this doesn't ALSO fire (in addition
# to the /yt or /fb command handlers) whenever someone runs a command whose
# argument happens to contain a facebook.com URL — that was causing every
# /yt <facebook link> to produce two separate, conflicting bot replies.
@Client.on_message(
    filters.text & filters.private & filters.regex(FB_PATTERN) & ~filters.regex(r"^/"),
    group=1,
)
async def facebook_auto_detect(client: Client, message: Message):
    url = extract_fb_url(message.text)
    if url:
        await _handle_fb_download(client, message, url)


@Client.on_message(filters.command("fb") & filters.private)
async def fb_command(client: Client, message: Message):
    if len(message.command) < 2:
        return await message.reply_text(
            f"<b>{E_INFO} Usage:</b> <code>/fb &lt;facebook video URL&gt;</code>\n"
            f"<i>Or just paste a facebook.com / fb.watch link directly.</i>",
            parse_mode=enums.ParseMode.HTML
        )
    url = extract_fb_url(message.command[1]) or message.command[1]
    await _handle_fb_download(client, message, url)
