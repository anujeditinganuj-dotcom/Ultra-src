import asyncio
from pyrogram import Client, filters, enums
from pyrogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
)
from config import YTDL_SEARCH_LIMIT, YTDL_SEARCH_PAGE_SIZE
from Rexbots.ytdl import _run, YOUTUBE_PATTERN, PLAYLIST_REGEX, GENERIC_URL_PATTERN

try:
    import yt_dlp
except ImportError:
    yt_dlp = None

E_ROCKET  = '<emoji id=5456140674028019486>🚀</emoji>'
E_CROSS   = '<emoji id=5210952531676504517>❌</emoji>'
E_SEARCH  = '<emoji id=5334544901428229844>🔍</emoji>'

# In-memory cache: search-results message id -> {"query": str, "results": [...]}.
# Callback data only carries a small index + this message's id (both fit
# comfortably under Telegram's 64-byte callback_data limit) so we don't need
# to stuff a full video id/url/query into callback_data or hit a database for it.
_SEARCH_CACHE: dict[int, dict] = {}


def _search_youtube(query: str, limit: int):
    """Flat (metadata-only) YouTube search — fast, no per-video info fetch."""
    probe_opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": "in_playlist",
        "skip_download": True,
        "default_search": "ytsearch",
    }
    with yt_dlp.YoutubeDL(probe_opts) as ydl:
        info = ydl.extract_info(f"ytsearch{limit}:{query}", download=False)

    entries = (info or {}).get("entries") or []
    results = []
    for entry in entries:
        if not entry:
            continue
        vid_id = entry.get("id")
        if not vid_id:
            continue
        title = (entry.get("title") or "Untitled")[:70]
        uploader = entry.get("uploader") or entry.get("channel") or ""
        duration = entry.get("duration")
        dur_str = ""
        if isinstance(duration, (int, float)) and duration > 0:
            m, s = divmod(int(duration), 60)
            h, m = divmod(m, 60)
            dur_str = f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
        results.append({"id": vid_id, "title": title, "uploader": uploader, "duration": dur_str})
    return results


def _format_results_text(query: str, results: list[dict], page: int) -> str:
    start = page * YTDL_SEARCH_PAGE_SIZE
    end = min(start + YTDL_SEARCH_PAGE_SIZE, len(results))
    total_pages = max(1, (len(results) + YTDL_SEARCH_PAGE_SIZE - 1) // YTDL_SEARCH_PAGE_SIZE)

    lines = [f"{E_SEARCH} <b>Search results for:</b> <i>{query}</i>\n"]
    for i, r in enumerate(results[start:end], start=1):
        meta = " — ".join(x for x in (r["uploader"], r["duration"]) if x)
        line = f"{i}. {r['title']}"
        if meta:
            line += f"\n    <i>{meta}</i>"
        lines.append(line)
    if total_pages > 1:
        lines.append(f"\n<i>Page {page + 1}/{total_pages} — tap a number to download, or use the buttons to see more.</i>")
    else:
        lines.append("\n<i>Tap a number below to download that video.</i>")
    return "\n".join(lines)


def _results_keyboard(results: list[dict], page: int) -> InlineKeyboardMarkup:
    start = page * YTDL_SEARCH_PAGE_SIZE
    end = min(start + YTDL_SEARCH_PAGE_SIZE, len(results))
    total_pages = max(1, (len(results) + YTDL_SEARCH_PAGE_SIZE - 1) // YTDL_SEARCH_PAGE_SIZE)

    buttons, row = [], []
    # Number buttons are page-relative (1..page size) but carry the video's
    # absolute index into `results` so the download callback doesn't need to
    # know which page it came from.
    for abs_idx in range(start, end):
        label = str(abs_idx - start + 1)
        row.append(InlineKeyboardButton(label, callback_data=f"ytsr:{abs_idx}"))
        if len(row) == 5:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    if total_pages > 1:
        nav_row = []
        if page > 0:
            nav_row.append(InlineKeyboardButton("◀️ Previous", callback_data=f"ytsrpg:{page - 1}"))
        nav_row.append(InlineKeyboardButton(f"{page + 1}/{total_pages}", callback_data="ytsr:noop"))
        if page < total_pages - 1:
            nav_row.append(InlineKeyboardButton("Next ▶️", callback_data=f"ytsrpg:{page + 1}"))
        buttons.append(nav_row)

    return InlineKeyboardMarkup(buttons)


async def _do_search(client: Client, message: Message, query: str):
    if yt_dlp is None:
        return await message.reply_text(
            f"<b>{E_CROSS} yt-dlp not installed.</b>\n<i>Run <code>pip install yt-dlp</code> on the host.</i>",
            parse_mode=enums.ParseMode.HTML
        )

    status = await message.reply_text(
        f"<b>{E_ROCKET} Searching YouTube...</b>", parse_mode=enums.ParseMode.HTML
    )
    loop = asyncio.get_event_loop()
    try:
        results = await loop.run_in_executor(None, _search_youtube, query, YTDL_SEARCH_LIMIT)
    except Exception as e:
        return await status.edit_text(
            f"<b>{E_CROSS} Search failed:</b>\n<code>{e}</code>", parse_mode=enums.ParseMode.HTML
        )

    if not results:
        return await status.edit_text(
            f"<b>{E_CROSS} No results found for:</b> <i>{query}</i>", parse_mode=enums.ParseMode.HTML
        )

    await status.edit_text(
        _format_results_text(query, results, page=0),
        parse_mode=enums.ParseMode.HTML,
        reply_markup=_results_keyboard(results, page=0),
    )
    # Cache keyed by the results message id so callbacks (which only know
    # which button/message was tapped) can look the video id / query back up
    # — the query is needed to re-render the header text on page changes.
    _SEARCH_CACHE[status.id] = {"query": query, "results": results}
    # Simple cap so this dict can't grow unbounded over a long-running process.
    if len(_SEARCH_CACHE) > 500:
        oldest_key = next(iter(_SEARCH_CACHE))
        _SEARCH_CACHE.pop(oldest_key, None)


@Client.on_message(filters.command(["search", "yts", "song"]) & filters.private)
async def search_command(client: Client, message: Message):
    if len(message.command) < 2:
        return await message.reply_text(
            f"<b>{E_ROCKET} Usage:</b> <code>/search &lt;song or video name&gt;</code>\n"
            f"<i>e.g. <code>/search pal pal song</code></i>",
            parse_mode=enums.ParseMode.HTML
        )
    query = message.text.split(None, 1)[1].strip()
    await _do_search(client, message, query)


# Plain-text fallback: any private-chat text that ISN'T a command and isn't
# already a recognized link (YouTube/playlist/any other yt-dlp-supported URL,
# all handled by dedicated handlers in ytdl.py and friends) is treated as a
# YouTube search query. Registered last (group=3) so it only ever fires after
# every more specific handler had its chance to match.
@Client.on_message(
    filters.text & filters.private
    & ~filters.regex(r"^/")
    & ~filters.regex(YOUTUBE_PATTERN)
    & ~filters.regex(PLAYLIST_REGEX)
    & ~filters.regex(GENERIC_URL_PATTERN),
    group=3,
)
async def plain_text_search(client: Client, message: Message):
    query = (message.text or "").strip()
    if not query or len(query) > 150:
        return
    await _do_search(client, message, query)


@Client.on_callback_query(filters.regex(r"^ytsr:noop$"))
async def search_page_indicator_callback(client: Client, callback_query: CallbackQuery):
    # The "current page / total pages" button in the nav row — purely
    # informational, does nothing when tapped.
    await callback_query.answer()


@Client.on_callback_query(filters.regex(r"^ytsr:(\d+)$"))
async def search_result_callback(client: Client, callback_query: CallbackQuery):
    cached = _SEARCH_CACHE.get(callback_query.message.id)
    if not cached:
        return await callback_query.answer(
            "This search result has expired — please search again.", show_alert=True
        )
    results = cached["results"]

    idx = int(callback_query.matches[0].group(1))
    if idx < 0 or idx >= len(results):
        return await callback_query.answer("Invalid selection.", show_alert=True)

    await callback_query.answer("Starting download...")
    video_id = results[idx]["id"]
    video_url = f"https://www.youtube.com/watch?v={video_id}"
    await _run(client, callback_query.message, video_url, audio_only=False)


@Client.on_callback_query(filters.regex(r"^ytsrpg:(\d+)$"))
async def search_page_callback(client: Client, callback_query: CallbackQuery):
    cached = _SEARCH_CACHE.get(callback_query.message.id)
    if not cached:
        return await callback_query.answer(
            "This search result has expired — please search again.", show_alert=True
        )
    results = cached["results"]

    page = int(callback_query.matches[0].group(1))
    total_pages = max(1, (len(results) + YTDL_SEARCH_PAGE_SIZE - 1) // YTDL_SEARCH_PAGE_SIZE)
    if page < 0 or page >= total_pages:
        return await callback_query.answer("No more results.", show_alert=False)

    await callback_query.answer()
    await callback_query.message.edit_text(
        _format_results_text(cached["query"], results, page=page),
        parse_mode=enums.ParseMode.HTML,
        reply_markup=_results_keyboard(results, page=page),
    )
