import re
import aiohttp
from pyrogram import Client, filters, enums
from pyrogram.types import Message

from Rexbots.direct_utils import (
    make_output_folder, safe_filename, stream_download, upload_file,
    DEFAULT_HEADERS, E_CHECK, E_CROSS, E_INFO
)

PATTERN = re.compile(r"(https?://)?(www\.)?mediafire\.com/\S+", re.IGNORECASE)


def extract_url(text: str):
    m = PATTERN.search(text)
    return m.group(0) if m else None


async def _extract_direct_url(link: str):
    async with aiohttp.ClientSession() as session:
        async with session.get(link, headers=DEFAULT_HEADERS) as resp:
            if resp.status != 200:
                raise ValueError(f"Could not open Mediafire page (HTTP {resp.status})")
            html = await resp.text()

    m = re.search(r'href="(https?://download\d*\.mediafire\.com/[^"]+)"', html)
    if not m:
        raise ValueError("Could not find Mediafire download link. Link may be dead or restricted.")
    direct_url = m.group(1)

    name_m = re.search(r'<div class="filename"[^>]*>([^<]+)</div>', html)
    filename = name_m.group(1).strip() if name_m else direct_url.split("/")[-1].split("?")[0]
    return direct_url, filename


async def _handle(client: Client, message: Message, url: str):
    status = await message.reply_text(f"<b>{E_INFO} Mediafire link detected...</b>", parse_mode=enums.ParseMode.HTML)
    try:
        direct_url, filename = await _extract_direct_url(url)
        filename = safe_filename(filename, "mediafire_file")
        folder = make_output_folder("mediafire")
        dest = f"{folder}/{message.id}_{filename}"
        await stream_download(direct_url, dest, status, "Downloading from Mediafire")
        await upload_file(client, message, dest, status, f"<b>{E_CHECK} Mediafire File</b>\n<code>{filename}</code>")
    except Exception as e:
        await status.edit_text(f"<b>{E_CROSS} Error:</b>\n<code>{e}</code>", parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.text & filters.private & filters.regex(PATTERN), group=1)
async def mediafire_auto_detect(client: Client, message: Message):
    url = extract_url(message.text)
    if url:
        await _handle(client, message, url)


@Client.on_message(filters.command("mf") & filters.private)
async def mediafire_command(client: Client, message: Message):
    if len(message.command) < 2:
        return await message.reply_text(
            f"<b>{E_INFO} Usage:</b> <code>/mf &lt;mediafire URL&gt;</code>",
            parse_mode=enums.ParseMode.HTML
        )
    url = extract_url(message.command[1]) or message.command[1]
    await _handle(client, message, url)
