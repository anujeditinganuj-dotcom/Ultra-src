import os
import re
import time
import shutil
import asyncio
import uuid
from pyrogram import Client, filters, enums
from pyrogram.types import Message
from config import YTDL_MAX_FILESIZE, YTDL_PLAYLIST_MAX, YT_COOKIES, INSTA_COOKIES, FB_COOKIES, CREDIT_USERNAME
from Rexbots.direct_utils import format_progress, format_media_caption, download_official_thumbnail

try:
    import yt_dlp
except ImportError:
    yt_dlp = None

E_ROCKET = '<emoji id=5456140674028019486>🚀</emoji>'
E_CROSS  = '<emoji id=5210952531676504517>❌</emoji>'
E_CHECK  = '<emoji id=5206607081334906820>✔️</emoji>'
E_BOLT   = '<emoji id=5456140674028019486>⚡️</emoji>'

DOWNLOAD_DIR = "yt_downloads"

# Matches instagram post/reel/story/tv links pasted directly without a command,
# e.g. https://www.instagram.com/reel/xxxxx/ or https://instagr.am/p/xxxxx
INSTA_PATTERN = re.compile(
    r"(https?://)?(www\.)?(instagram\.com|instagr\.am)/\S+", re.IGNORECASE
)

# Matches Pinterest pin links, e.g. https://www.pinterest.com/pin/xxxxx or
# the pin.it short-link form. Used for the explicit /pin command AND for
# smart auto-detect below, which probes the link first to decide whether it's
# a video pin (yt-dlp path, this file) or an image pin/board (gallery-dl path,
# gallery.py) before downloading anything.
PINTEREST_PATTERN = re.compile(
    r"(https?://)?(www\.)?(pinterest\.[a-z.]+|pin\.it)/\S+", re.IGNORECASE
)

# Matches bare YouTube links (watch, youtu.be, shorts, live) pasted without a
# command, e.g. https://www.youtube.com/watch?v=xxxx, https://youtu.be/xxxx,
# https://www.youtube.com/shorts/xxxx. Used only for auto-detect below — the
# /yt and /dl commands already accept any yt-dlp-supported URL directly.
YOUTUBE_PATTERN = re.compile(
    r"(https?://)?(www\.|m\.)?(youtube\.com/(watch|shorts|live)\S+|youtu\.be/\S+)",
    re.IGNORECASE,
)

# Pulls the video id out of any youtube.com link that carries a &v= / ?v=
# query param — this matches even when a &list= param is ALSO present (e.g. a
# video opened from inside a playlist), so a link like this is still treated
# as "just this one video" rather than the whole playlist.
VIDEO_REGEX = re.compile(r'(.*)youtube\.com/(.*)[&?]v=(?P<video>[^&]*)(.*)', re.IGNORECASE)

# Pulls the playlist id out of a youtube.com link that carries a &list= /
# ?list= query param. Only reached (see youtube_auto_detect below) when
# VIDEO_REGEX above did NOT match — i.e. the link has no v= param, so it's a
# pure playlist link like youtube.com/playlist?list=xxxx.
PLAYLIST_REGEX = re.compile(r'(.*)youtube\.com/(.*)[&?]list=(?P<playlist>[^&]*)(.*)', re.IGNORECASE)


def _cookies_for(url: str):
    if "instagram.com" in url and INSTA_COOKIES and os.path.exists(INSTA_COOKIES):
        return INSTA_COOKIES
    # Facebook needs its own session cookies — reusing YouTube's cookies (or none)
    # here made yt-dlp fetch Facebook as a logged-out session, which only exposes
    # a short preview/teaser clip instead of the real video.
    if ("facebook.com" in url or "fb.watch" in url) and FB_COOKIES and os.path.exists(FB_COOKIES):
        return FB_COOKIES
    # Pinterest pins are public — no login/cookies needed, and reusing
    # YouTube's cookiefile here has no benefit (wrong domain) so skip it.
    if "pinterest." in url or "pin.it" in url:
        return None
    if YT_COOKIES and os.path.exists(YT_COOKIES):
        return YT_COOKIES
    return None


def _make_progress_hook(loop: asyncio.AbstractEventLoop, status: Message, state: dict, prefix: str = ""):
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
            title=f"{prefix}Processing Task...",
        )

        async def _edit():
            try:
                await status.edit_text(text, parse_mode=enums.ParseMode.HTML)
            except Exception:
                pass  # e.g. MESSAGE_NOT_MODIFIED / flood wait — safe to skip a frame

        asyncio.run_coroutine_threadsafe(_edit(), loop)

    return hook


def _download(url: str, out_dir: str, audio_only: bool, progress_hook=None) -> str:
    ydl_opts = {
        "outtmpl": os.path.join(out_dir, "%(title).80s.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "max_filesize": YTDL_MAX_FILESIZE,
        "merge_output_format": "mp4",
    }
    if progress_hook:
        ydl_opts["progress_hooks"] = [progress_hook]

    cookies = _cookies_for(url)
    if cookies:
        ydl_opts["cookiefile"] = cookies

    if audio_only:
        ydl_opts["format"] = "bestaudio/best"
        ydl_opts["postprocessors"] = [{
            "key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"
        }]
    else:
        # Previous selector used best[filesize<N]/bestvideo[filesize<N]+bestaudio/best.
        # The [filesize<N] filter only matches formats that report an EXACT
        # "filesize" — most adaptive/DASH formats only report "filesize_approx",
        # so that filter silently excluded almost every format. And "best" alone
        # only matches a single pre-merged file, which many videos no longer have
        # at all (video-only + audio-only only) — so ALL three options could
        # come up empty, causing "Requested format is not available".
        # File size is already checked after download below, so there's no need
        # to filter by size here at all — just get the best video+audio and merge.
        ydl_opts["format"] = (
            "bestvideo[height<=1080]+bestaudio/best[height<=1080]/"
            "bestvideo+bestaudio/best"
        )

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        filename = ydl.prepare_filename(info)
        if audio_only:
            return os.path.splitext(filename)[0] + ".mp3", info
        if not os.path.exists(filename):
            # merge_output_format changed the extension (e.g. .webm -> .mp4)
            root, _ = os.path.splitext(filename)
            candidate = root + ".mp4"
            if os.path.exists(candidate):
                return candidate, info
        return filename, info


async def _download_and_send(client: Client, message: Message, url: str, audio_only: bool,
                              session_dir: str, status: Message,
                              index: int = None, total: int = None):
    """Download ONE video/audio and send it to the chat. Factored out of _run so a
    playlist download (below) can call this once per video, reusing the same status
    message and updating it with an [i/total] prefix instead of spamming a new
    status message per video."""
    prefix = f"[{index}/{total}] " if index and total else ""
    loop = asyncio.get_event_loop()
    hook = _make_progress_hook(loop, status, {"last_edit": 0.0}, prefix=prefix)

    filepath, info = await loop.run_in_executor(
        None, _download, url, session_dir, audio_only, hook
    )
    if not os.path.exists(filepath):
        raise FileNotFoundError("Download finished but file was not found (likely size limit).")

    size = os.path.getsize(filepath)
    if size > YTDL_MAX_FILESIZE:
        raise ValueError(f"File too large ({round(size / (1024*1024))} MB) to upload to Telegram.")

    await status.edit_text(f"<b>{E_BOLT} {prefix}Uploading...</b>", parse_mode=enums.ParseMode.HTML)
    caption = format_media_caption(info, credit=CREDIT_USERNAME)
    # Unique thumb filename — a playlist run reuses one session_dir across
    # videos, so a fixed "thumb.jpg" name would let each video's thumb clobber
    # (or accidentally reuse) the previous one.
    thumb_path = os.path.join(session_dir, f"thumb_{uuid.uuid4().hex}.jpg")
    has_thumb = not audio_only and await asyncio.to_thread(download_official_thumbnail, info, thumb_path)
    if audio_only:
        await client.send_audio(message.chat.id, filepath, caption=caption,
                                 reply_to_message_id=message.id, parse_mode=enums.ParseMode.HTML)
    else:
        await client.send_video(message.chat.id, filepath, caption=caption,
                                 thumb=thumb_path if has_thumb else None,
                                 reply_to_message_id=message.id, parse_mode=enums.ParseMode.HTML,
                                 supports_streaming=True)
    # Clean up this video's file/thumb immediately — matters for playlists,
    # where session_dir stays alive across many videos in a row.
    for p in (filepath, thumb_path):
        try:
            os.remove(p)
        except OSError:
            pass


async def _run(client: Client, message: Message, url: str, audio_only: bool):
    if yt_dlp is None:
        return await message.reply_text(
            f"<b>{E_CROSS} yt-dlp not installed.</b>\n<i>Run <code>pip install yt-dlp</code> on the host.</i>",
            parse_mode=enums.ParseMode.HTML
        )

    session_dir = os.path.join(DOWNLOAD_DIR, str(uuid.uuid4()))
    os.makedirs(session_dir, exist_ok=True)
    status = await message.reply_text(
        f"<b>{E_ROCKET} Downloading...</b>", parse_mode=enums.ParseMode.HTML
    )

    try:
        await _download_and_send(client, message, url, audio_only, session_dir, status)
        await status.delete()
    except Exception as e:
        await status.edit_text(f"<b>{E_CROSS} Download failed:</b>\n<code>{e}</code>", parse_mode=enums.ParseMode.HTML)
    finally:
        shutil.rmtree(session_dir, ignore_errors=True)


def _extract_playlist_video_urls(url: str):
    """Flat (fast, metadata-light) playlist listing — just gets each entry's video
    id/url so we know what to loop over. Full per-video info is fetched later by
    the normal _download() call for each one."""
    probe_opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": "in_playlist",
        "skip_download": True,
    }
    cookies = _cookies_for(url)
    if cookies:
        probe_opts["cookiefile"] = cookies

    with yt_dlp.YoutubeDL(probe_opts) as ydl:
        info = ydl.extract_info(url, download=False)

    entries = (info or {}).get("entries") or []
    urls = []
    for entry in entries:
        if not entry:
            continue
        vid_id = entry.get("id")
        vid_url = entry.get("url")
        if vid_url and vid_url.startswith("http"):
            urls.append(vid_url)
        elif vid_id:
            urls.append(f"https://www.youtube.com/watch?v={vid_id}")
    return urls


async def _run_playlist(client: Client, message: Message, url: str, audio_only: bool = False):
    if yt_dlp is None:
        return await message.reply_text(
            f"<b>{E_CROSS} yt-dlp not installed.</b>\n<i>Run <code>pip install yt-dlp</code> on the host.</i>",
            parse_mode=enums.ParseMode.HTML
        )

    status = await message.reply_text(
        f"<b>{E_ROCKET} Fetching playlist...</b>", parse_mode=enums.ParseMode.HTML
    )

    loop = asyncio.get_event_loop()
    try:
        video_urls = await loop.run_in_executor(None, _extract_playlist_video_urls, url)
    except Exception as e:
        return await status.edit_text(
            f"<b>{E_CROSS} Could not read playlist:</b>\n<code>{e}</code>", parse_mode=enums.ParseMode.HTML
        )

    if not video_urls:
        return await status.edit_text(
            f"<b>{E_CROSS} Playlist is empty, private, or unavailable.</b>", parse_mode=enums.ParseMode.HTML
        )

    # No more hard truncation — YTDL_PLAYLIST_MAX is now the BATCH size. A
    # playlist of any length (100, 300, whatever) is processed in chunks of
    # this size, with a short pause between chunks (see BATCH_PAUSE_SECS
    # below) instead of dropping everything past the first N videos.
    total = len(video_urls)
    batch_size = YTDL_PLAYLIST_MAX if YTDL_PLAYLIST_MAX > 0 else total
    BATCH_PAUSE_SECS = 3  # pause between batches (Telegram/YouTube breather)

    sent, failed = 0, 0
    for i, video_url in enumerate(video_urls, start=1):
        # Start of a new batch (but not before the very first video) — pause
        # briefly before continuing so we don't hammer Telegram/YouTube with
        # a huge uninterrupted run when the playlist is large.
        if i > 1 and (i - 1) % batch_size == 0:
            batch_num = (i - 1) // batch_size + 1
            try:
                await status.edit_text(
                    f"<b>{E_ROCKET} Batch {batch_num} — pausing {BATCH_PAUSE_SECS}s before continuing...</b>",
                    parse_mode=enums.ParseMode.HTML,
                )
            except Exception:
                pass
            await asyncio.sleep(BATCH_PAUSE_SECS)

        session_dir = os.path.join(DOWNLOAD_DIR, str(uuid.uuid4()))
        os.makedirs(session_dir, exist_ok=True)
        try:
            await status.edit_text(
                f"<b>{E_ROCKET} [{i}/{total}] Downloading...</b>", parse_mode=enums.ParseMode.HTML
            )
        except Exception:
            pass  # e.g. flood wait on rapid edits — not worth aborting the run for

        try:
            await _download_and_send(client, message, video_url, audio_only, session_dir, status,
                                      index=i, total=total)
            sent += 1
        except Exception as e:
            failed += 1
            try:
                await client.send_message(
                    message.chat.id,
                    f"<b>{E_CROSS} [{i}/{total}] Skipped:</b>\n<code>{e}</code>",
                    parse_mode=enums.ParseMode.HTML,
                )
            except Exception:
                pass
        finally:
            shutil.rmtree(session_dir, ignore_errors=True)

    summary = f"<b>{E_CHECK} Playlist done — {sent}/{total} sent.</b>"
    if failed:
        summary += f"\n<i>{failed} video(s) failed/skipped.</i>"
    try:
        await status.edit_text(summary, parse_mode=enums.ParseMode.HTML)
    except Exception:
        pass


@Client.on_message(filters.command(["yt", "dl", "insta", "ig", "reel", "pin", "pinterest"]) & filters.private)
async def yt_video_command(client: Client, message: Message):
    if len(message.command) < 2:
        return await message.reply_text(
            f"<b>{E_BOLT} Usage:</b> <code>/yt &lt;video URL&gt;</code>\n"
            f"<i>Supports YouTube, Instagram, Pinterest, and most yt-dlp-compatible sites.</i>\n"
            f"<i>Instagram also works via <code>/insta</code> or <code>/ig</code>.</i>\n"
            f"<i>Pinterest also works via <code>/pin</code> or <code>/pinterest</code> — "
            f"video pins download directly, image pins auto-fallback to the gallery path.</i>",
            parse_mode=enums.ParseMode.HTML
        )
    url = message.command[1]
    command_used = message.command[0].lower()
    if command_used in ("pin", "pinterest"):
        is_video = await asyncio.to_thread(_pinterest_is_video, url)
        if not is_video:
            from Rexbots.gallery import _handle as gallery_handle
            return await gallery_handle(client, message, url)
    await _run(client, message, url, audio_only=False)


@Client.on_message(filters.command(["pl", "playlist", "ytpl"]) & filters.private)
async def yt_playlist_command(client: Client, message: Message):
    if len(message.command) < 2:
        return await message.reply_text(
            f"<b>{E_BOLT} Usage:</b> <code>/pl &lt;playlist URL&gt;</code>\n"
            f"<i>Downloads every video in a YouTube playlist, "
            f"in batches of {YTDL_PLAYLIST_MAX} with a short pause between batches.</i>",
            parse_mode=enums.ParseMode.HTML
        )
    url = message.command[1]
    playlist_match = PLAYLIST_REGEX.search(url)
    if not playlist_match:
        return await message.reply_text(
            f"<b>{E_CROSS} That doesn't look like a YouTube playlist link.</b>\n"
            f"<i>Expected something like <code>youtube.com/playlist?list=...</code></i>",
            parse_mode=enums.ParseMode.HTML
        )
    playlist_url = f"https://www.youtube.com/playlist?list={playlist_match.group('playlist')}"
    await _run_playlist(client, message, playlist_url, audio_only=False)


@Client.on_message(filters.command(["yta", "song", "adl"]) & filters.private)
async def yt_audio_command(client: Client, message: Message):
    if len(message.command) < 2:
        return await message.reply_text(
            f"<b>{E_BOLT} Usage:</b> <code>/yta &lt;video URL&gt;</code> — extracts audio (mp3)",
            parse_mode=enums.ParseMode.HTML
        )
    await _run(client, message, message.command[1], audio_only=True)


def _extract_insta_url(text: str):
    m = INSTA_PATTERN.search(text)
    return m.group(0) if m else None


# Mirrors facebook.py's auto-detect handler: lets a bare Instagram link
# (pasted with no /insta or /yt command) get downloaded automatically.
# Registered in its own group so it doesn't interfere with other handlers,
# and excludes commands so /yt or /insta messages aren't processed twice.
@Client.on_message(
    filters.text & filters.private & filters.regex(INSTA_PATTERN) & ~filters.regex(r"^/"),
    group=1,
)
async def insta_auto_detect(client: Client, message: Message):
    url = _extract_insta_url(message.text)
    if url:
        await _run(client, message, url, audio_only=False)


def _extract_youtube_url(text: str):
    m = YOUTUBE_PATTERN.search(text)
    return m.group(0) if m else None


# Combined filter so the handler fires on: watch/shorts/live/youtu.be links
# (YOUTUBE_PATTERN) OR a youtube.com link carrying &list= even without those
# path segments, e.g. youtube.com/playlist?list=xxxx (PLAYLIST_REGEX).
_YOUTUBE_OR_PLAYLIST = filters.regex(YOUTUBE_PATTERN) | filters.regex(PLAYLIST_REGEX)


# Bare YouTube link (no /yt, /dl command) — same pattern as the Instagram
# auto-detect above. Registered in group=1 so it doesn't clash with other
# handlers, and excludes commands so /yt messages aren't processed twice.
#
# Routing:
#   - Link has a v= param (VIDEO_REGEX) -> just that one video, even if it
#     ALSO carries a &list= param (e.g. a video opened from inside a
#     playlist) — matches the request: "video link = sirf wahi video".
#   - Otherwise, link has a list= param (PLAYLIST_REGEX) -> whole playlist.
#   - Otherwise, fall back to the plain YOUTUBE_PATTERN match (youtu.be,
#     /shorts/, /live/ links etc. that don't use v=/list= query params).
@Client.on_message(
    filters.text & filters.private & _YOUTUBE_OR_PLAYLIST & ~filters.regex(r"^/"),
    group=1,
)
async def youtube_auto_detect(client: Client, message: Message):
    text = message.text

    video_match = VIDEO_REGEX.search(text)
    if video_match:
        video_url = f"https://www.youtube.com/watch?v={video_match.group('video')}"
        return await _run(client, message, video_url, audio_only=False)

    playlist_match = PLAYLIST_REGEX.search(text)
    if playlist_match:
        playlist_url = f"https://www.youtube.com/playlist?list={playlist_match.group('playlist')}"
        return await _run_playlist(client, message, playlist_url, audio_only=False)

    url = _extract_youtube_url(text)
    if url:
        await _run(client, message, url, audio_only=False)


def _extract_pinterest_url(text: str):
    m = PINTEREST_PATTERN.search(text)
    return m.group(0) if m else None


def _pinterest_is_video(url: str) -> bool:
    """Probe-only extract_info (no download) to tell a Pinterest video pin
    apart from an image pin/board. yt-dlp only reports 'formats'/'url' when
    there's actual media to pull; image-only pins either raise (yt-dlp has
    nothing to extract) or come back with no playable formats — both cases
    mean "let gallery-dl handle it instead"."""
    if yt_dlp is None:
        return False
    try:
        probe_opts = {"quiet": True, "no_warnings": True, "skip_download": True, "noplaylist": True}
        with yt_dlp.YoutubeDL(probe_opts) as ydl:
            info = ydl.extract_info(url, download=False)
        return bool(info and (info.get("formats") or info.get("url")))
    except Exception:
        return False


# Bare Pinterest link (no /pin, /yt, or /gallery command) — probe first,
# then route: video pins go through this file's yt-dlp path (real thumbnail +
# rich caption), image pins/boards get handed off to gallery.py's gallery-dl
# handler. Registered in the same group as the other auto-detects.
@Client.on_message(
    filters.text & filters.private & filters.regex(PINTEREST_PATTERN) & ~filters.regex(r"^/"),
    group=1,
)
async def pinterest_auto_detect(client: Client, message: Message):
    url = _extract_pinterest_url(message.text)
    if not url:
        return
    is_video = await asyncio.to_thread(_pinterest_is_video, url)
    if is_video:
        await _run(client, message, url, audio_only=False)
    else:
        from Rexbots.gallery import _handle as gallery_handle
        await gallery_handle(client, message, url)


# Any other bare link that isn't already covered by a dedicated handler
# (YouTube/Instagram/Pinterest above, or Facebook/Mega/GDrive/GoFile/
# Mediafire/Pixeldrain/Streamtape/Catbox/Terabox/gallery-sites/torrent in
# their own files) falls through to here. yt-dlp supports 1000+ sites
# (Twitch, TikTok, Vimeo, SoundCloud, Dailymotion, X/Twitter video, etc.) so
# instead of hardcoding every domain we just probe the link — if yt-dlp
# recognizes it and reports playable formats, download it; otherwise stay
# silent (no reply) since the message may just be an ordinary link/text with
# nothing for yt-dlp to extract.
GENERIC_URL_PATTERN = re.compile(r"https?://\S+", re.IGNORECASE)

# Domains already owned by a more specific handler elsewhere — skipped here
# so a link isn't downloaded twice (once by its dedicated handler, once by
# this generic fallback).
_EXCLUDED_DOMAINS = (
    "youtube.com", "youtu.be", "instagram.com", "instagr.am",
    "pinterest.", "pin.it",
    "facebook.com", "fb.watch", "fb.com",
    "mega.nz", "drive.google.com", "gofile.io", "mediafire.com",
    "pixeldrain.com", "streamtape.", "stape.", "catbox.moe", "litterbox.catbox.moe",
    "terabox.com", "1024terabox.com", "teraboxapp.com", "freeterabox.com",
    "nephobox.com", "4funbox.com",
    "twitter.com", "x.com", "pixiv.net", "deviantart.com", "artstation.com",
    "flickr.com", "tumblr.com", "reddit.com", "imgur.com",
    "danbooru.donmai.us", "gelbooru.com", "konachan.com", "yande.re",
    "safebooru.org", "zerochan.net", "furaffinity.net", "bsky.app",
)


def _extract_generic_url(text: str):
    m = GENERIC_URL_PATTERN.search(text)
    if not m:
        return None
    url = m.group(0)
    lower = url.lower()
    if any(d in lower for d in _EXCLUDED_DOMAINS):
        return None
    return url


def _generic_probe(url: str):
    """Probe-only (no download) to check whether yt-dlp has an extractor for
    this URL and it actually has playable media — same idea as
    _pinterest_is_video, generalized to any site."""
    if yt_dlp is None:
        return False
    try:
        probe_opts = {"quiet": True, "no_warnings": True, "skip_download": True, "noplaylist": True}
        with yt_dlp.YoutubeDL(probe_opts) as ydl:
            info = ydl.extract_info(url, download=False)
        return bool(info and (info.get("formats") or info.get("url")))
    except Exception:
        return False


@Client.on_message(
    filters.text & filters.private & filters.regex(GENERIC_URL_PATTERN) & ~filters.regex(r"^/"),
    group=2,  # runs after the more specific group=1 handlers
)
async def generic_ytdlp_auto_detect(client: Client, message: Message):
    url = _extract_generic_url(message.text)
    if not url:
        return
    is_supported = await asyncio.to_thread(_generic_probe, url)
    if is_supported:
        await _run(client, message, url, audio_only=False)
