"""Pictures sent to her: download from Discord's CDN only, shrink, hand to the vision model as JPEG frames."""
from __future__ import annotations

import asyncio
import base64
import io
import logging
from urllib.parse import urlsplit

import discord

log = logging.getLogger("latochan")
IMG_EXT = (".png", ".jpg", ".jpeg", ".webp", ".gif")
MAX_IMAGE_BYTES = 15_000_000      # per download
MAX_IMAGE_PIXELS = 25_000_000     # e.g. 5000x5000; bigger is refused (decompression-bomb guard)


def has_media(msg) -> bool:
    return bool(msg.attachments or msg.stickers or any(e.image or e.thumbnail for e in msg.embeds))


def is_discord_media(url: str | None) -> bool:
    """Only download from Discord's own CDN / media proxy: never arbitrary hosts (no LAN access via link embeds)."""
    try:
        u = urlsplit(url or "")
    except ValueError:
        return False
    host = (u.hostname or "").lower()
    return u.scheme == "https" and (host in ("discordapp.com", "discordapp.net")
                                    or host.endswith((".discordapp.com", ".discordapp.net")))


def to_frames(data: bytes) -> list[str]:
    """Image bytes -> base64 JPEGs. GIFs give 3 frames (start/middle/end); everything is shrunk to 768px."""
    from PIL import Image
    Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS  # PIL refuses images over 2x this outright
    im = Image.open(io.BytesIO(data))
    if im.width * im.height > MAX_IMAGE_PIXELS:
        raise ValueError(f"image too large ({im.width}x{im.height})")
    n = getattr(im, "n_frames", 1)
    out = []
    for i in ([0] if n < 3 else [0, n // 2, n - 1]):
        im.seek(i)
        f = im.convert("RGB")
        f.thumbnail((768, 768))
        b = io.BytesIO()
        f.save(b, "JPEG", quality=85)
        out.append(base64.b64encode(b.getvalue()).decode())
    return out


async def collect_images(http, msg):
    urls = []
    for a in msg.attachments:
        if ((a.content_type or "").startswith("image/") or a.filename.lower().endswith(IMG_EXT)) and a.size <= MAX_IMAGE_BYTES:
            urls.append(a.url)
    for e in msg.embeds:  # proxy_url = Discord's cached copy; the plain url can point anywhere
        u = (e.image and e.image.proxy_url) or (e.thumbnail and e.thumbnail.proxy_url)
        if u:
            urls.append(u)
    for st in msg.stickers:
        if st.format in (discord.StickerFormatType.png, discord.StickerFormatType.apng, discord.StickerFormatType.gif):
            urls.append(st.url)
    imgs, kinds = [], []
    for u in [u for u in urls if is_discord_media(u)][:3]:
        try:
            async with http.get(u, allow_redirects=False) as r:
                if r.status != 200 or (r.content_length or 0) > MAX_IMAGE_BYTES:
                    continue
                data = bytearray()
                async for chunk in r.content.iter_chunked(64 * 1024):
                    data += chunk
                    if len(data) > MAX_IMAGE_BYTES:
                        raise ValueError("image too large")
            frames = await asyncio.to_thread(to_frames, bytes(data))
            imgs += frames
            kinds.append(f"gif ({len(frames)} frames)" if len(frames) > 1 else "image")
        except Exception as e:
            log.warning("image fetch failed for %s: %r", u, e)
    return imgs[:5], kinds
