"""Render a 1080x1080 Instagram card with Pillow.

Text is the whole point of a news card, so sizing is done by measurement, not
guesswork: wrap to the box, then step the font size down until the wrapped
block fits. Fonts come from a candidate list so the same code works on the
Ubuntu Actions runner and on a Windows dev box.
"""
from __future__ import annotations

import os
import textwrap
from datetime import datetime

from PIL import Image, ImageDraw, ImageFont

from .fetch import IST, truncate_words


def _hex(color: str) -> tuple:
    c = color.lstrip("#")
    if len(c) == 3:
        c = "".join(ch * 2 for ch in c)
    return tuple(int(c[i : i + 2], 16) for i in (0, 2, 4))


def resolve_font(candidates: list[str], size: int) -> ImageFont.FreeTypeFont:
    for path in candidates:
        if path and os.path.exists(path):
            return ImageFont.truetype(path, size)
    # Absolute last resort: PIL's built-in bitmap font (ugly but never crashes).
    return ImageFont.load_default(size)


def _wrap(draw, text: str, font, max_w: int) -> list[str]:
    """Greedy word wrap measured against real glyph widths."""
    lines, line = [], ""
    for word in text.split():
        trial = f"{line} {word}".strip()
        if draw.textlength(trial, font=font) <= max_w or not line:
            line = trial
        else:
            lines.append(line)
            line = word
    if line:
        lines.append(line)
    return lines


def _fit_block(
    draw,
    text: str,
    box_w: int,
    box_h: int,
    bold_paths: list[str],
    start: int = 76,
    minimum: int = 34,
    leading: float = 1.18,
) -> tuple[ImageFont.FreeTypeFont, list[str]]:
    """Largest font (<= start) whose wrapped block fits the box."""
    for size in range(start, minimum - 1, -2):
        font = resolve_font(bold_paths, size)
        lines = _wrap(draw, text, font, box_w)
        block_h = int(len(lines) * size * leading)
        widest = max((draw.textlength(ln, font=font) for ln in lines), default=0)
        if block_h <= box_h and widest <= box_w:
            return font, lines
    font = resolve_font(bold_paths, minimum)
    return font, _wrap(draw, text, font, box_w)[:6]


def _badge(draw, xy, text, font, fill, text_fill, pad=(22, 11)):
    x, y = xy
    tw = draw.textlength(text, font=font)
    bbox = font.getbbox(text)
    th = bbox[3] - bbox[1]
    w = tw + pad[0] * 2
    h = th + pad[1] * 2
    draw.rounded_rectangle([x, y, x + w, y + h], radius=h // 2, fill=fill)
    draw.text((x + pad[0], y + pad[1] - bbox[1]), text, font=font, fill=text_fill)
    return w


def render(article, meta: dict, cfg: dict, out_path: str) -> str:
    """Write the card image and return its path."""
    w = int(cfg["image"]["width"])
    h = int(cfg["image"]["height"])
    m = int(cfg["image"]["margin"])
    pal = cfg["image"]["palettes"][article.category]
    fonts_r = cfg["image"]["font_regular_candidates"]
    fonts_b = cfg["image"]["font_bold_candidates"]

    bg = _hex(pal["bg"])
    img = Image.new("RGB", (w, h), bg)
    draw = ImageDraw.Draw(img)

    # --- backdrop: large soft panel + accent stripe ---------------------
    draw.rounded_rectangle(
        [m // 2, m // 2, w - m // 2, h - m // 2], radius=46,
        fill=_hex(pal["panel"]),
    )
    draw.rectangle([0, 0, w, 14], fill=_hex(pal["accent"]))
    draw.rectangle([0, h - 14, w, h], fill=_hex(pal["accent_alt"]))

    # --- header: category badge + source --------------------------------
    y = m + 6
    label = "MARKET" if article.category == "market" else "AI"
    badge_font = resolve_font(fonts_b, 30)
    bw = _badge(
        draw, (m, y), label, badge_font,
        fill=_hex(pal["accent"]),
        text_fill=_hex(pal["bg"]),
    )
    src_font = resolve_font(fonts_r, 30)
    draw.text(
        (m + bw + 22, y + 11), article.source,
        font=src_font, fill=_hex(pal["muted"]),
    )

    # --- body group: headline + accent bar + deck ------------------------
    # Measure the whole group first, then centre it between header and
    # footer. Top-anchoring this is what made the first drafts look hollow
    # in the lower third.
    headline = meta.get("headline") or article.title
    region_top = y + 116
    footer_top = h - m - 116
    max_block = footer_top - region_top - 36

    font, lines = _fit_block(draw, headline, w - 2 * m, max_block, fonts_b)
    lead = int(font.size * 1.18)
    headline_h = len(lines) * lead

    deck_font = resolve_font(fonts_r, 30)
    deck_lines: list[str] = []
    # Prefer the deck the LLM wrote; fall back to the feed summary. The feed
    # value is already cleaned in fetch.clean_summary(), so a Google News
    # entry (whose <description> is just "Headline - Publisher") lands here as
    # "" instead of repeating the headline under itself.
    deck_src = str(meta.get("deck") or "").strip() or article.summary
    if deck_src:
        deck = textwrap.shorten(deck_src, width=190, placeholder=" …")
        deck_lines = _wrap(draw, deck, deck_font, w - 2 * m)[:4]
    deck_h = len(deck_lines) * 42

    gap1, bar_h, gap2 = 14, 9, 22
    group_h = headline_h + gap1 + bar_h + gap2 + deck_h
    ty = region_top + max(0, (max_block - group_h) // 2)

    for ln in lines:
        draw.text((m, ty), ln, font=font, fill=_hex(pal["text"]))
        ty += lead

    bar_y = ty + gap1
    draw.rounded_rectangle([m, bar_y, m + 168, bar_y + bar_h], radius=5,
                           fill=_hex(pal["accent"]))

    dy = bar_y + bar_h + gap2
    for ln in deck_lines:
        draw.text((m, dy), ln, font=deck_font, fill=_hex(pal["muted"]))
        dy += 42

    # --- footer: handle / date / disclaimer ------------------------------
    foot_font = resolve_font(fonts_r, 27)
    handle_font = resolve_font(fonts_b, 30)
    date_str = datetime.now(IST).strftime("%d %b %Y").upper()

    fy = h - m - 74
    draw.line([m, fy - 20, w - m, fy - 20], fill=_hex(pal["panel"]), width=2)
    draw.text((m, fy), cfg["brand"]["handle"], font=handle_font,
              fill=_hex(pal["accent_alt"]))
    date_w = draw.textlength(date_str, font=foot_font)
    draw.text((w - m - date_w, fy + 4), date_str, font=foot_font,
              fill=_hex(pal["muted"]))

    disc = cfg["brand"]["disclaimer"]
    disc_font = resolve_font(fonts_r, 22)
    disc_lines = textwrap.wrap(disc, width=74)[:2]
    dy = fy + 48
    for ln in disc_lines:
        draw.text((m, dy), ln, font=disc_font, fill=_hex(pal["muted"]))
        dy += 27

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    img.save(out_path, "JPEG", quality=92, optimize=True, progressive=True)
    return out_path


def slugify(text: str, limit: int = 52) -> str:
    keep = [c.lower() if c.isalnum() else "-" for c in text]
    slug = "".join(keep)
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug.strip("-")[:limit].rstrip("-") or "post"
