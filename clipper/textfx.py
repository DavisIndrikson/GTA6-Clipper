"""Text and emoji rendering with Pillow: captions, hook title, meme text pops, emoji pops.

Bundled fonts only (assets/fonts), so the look is identical on every machine.
Emoji are drawn with Noto Color Emoji (bitmap strike at 109 px, then scaled).
"""
from __future__ import annotations

import re
from functools import lru_cache

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .util import ASSETS, CONFIG, load_json

FONTS = ASSETS / "fonts"
EMOJI_PX = 109


@lru_cache(maxsize=64)
def font(name, px):
    return ImageFont.truetype(str(FONTS / name), int(px))


@lru_cache(maxsize=1)
def _emoji_font():
    return ImageFont.truetype(str(FONTS / "NotoColorEmoji.ttf"), EMOJI_PX)


def _is_emoji_cp(cp):
    return (0x1F000 <= cp <= 0x1FAFF or 0x2600 <= cp <= 0x27BF or 0x2190 <= cp <= 0x21FF or
            0x2B00 <= cp <= 0x2BFF or cp in (0x203C, 0x2049, 0x2122, 0x2139, 0x3030, 0x303D, 0x3297, 0x3299))


def runs(text):
    """Split into (is_emoji, string) runs; ZWJ, VS16 and skin tones stay with their emoji."""
    out = []
    i = 0
    while i < len(text):
        cp = ord(text[i])
        if _is_emoji_cp(cp):
            j = i + 1
            while j < len(text) and (ord(text[j]) in (0x200D, 0xFE0F) or 0x1F3FB <= ord(text[j]) <= 0x1F3FF or
                                     (j > 0 and ord(text[j - 1]) == 0x200D)):
                j += 1
            out.append((True, text[i:j]))
            i = j
        else:
            j = i
            while j < len(text) and not _is_emoji_cp(ord(text[j])):
                j += 1
            out.append((False, text[i:j].replace("️", "")))
            i = j
    return [r for r in out if r[1]]


@lru_cache(maxsize=256)
def emoji_img(cluster, px):
    f = _emoji_font()
    im = Image.new("RGBA", (EMOJI_PX * 2, EMOJI_PX * 2), (0, 0, 0, 0))
    ImageDraw.Draw(im).text((EMOJI_PX // 4, EMOJI_PX // 4), cluster, font=f, embedded_color=True)
    bb = im.getbbox()
    if not bb:
        return None
    im = im.crop(bb)
    s = px / EMOJI_PX
    return im.resize((max(1, round(im.width * s)), max(1, round(im.height * s))), Image.LANCZOS)


def text_img(text, font_name, px, fill="#FFFFFF", stroke=0, stroke_fill="#000000", shadow=0):
    """One line of text (with emoji) as a tight RGBA image. Returns (image, baseline_y)."""
    f = font(font_name, px)
    asc, desc = f.getmetrics()
    pad = stroke + shadow + 4
    pieces = []
    x = pad
    for is_e, s in runs(text):
        if is_e:
            em = emoji_img(s, int(px * 1.05))
            if em is not None:
                pieces.append(("e", em, x))
                x += em.width + int(px * 0.06)
        else:
            w = f.getlength(s)
            pieces.append(("t", s, x))
            x += int(round(w))
    W = int(x + pad)
    H = int(asc + desc + 2 * pad)
    base = pad + asc
    im = Image.new("RGBA", (max(W, 2), max(H, 2)), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    for kind, obj, px_ in pieces:
        if kind == "t":
            if shadow:
                d.text((px_ + shadow, base + shadow), obj, font=f, fill=(0, 0, 0, 150), anchor="ls",
                       stroke_width=stroke, stroke_fill=(0, 0, 0, 150))
            d.text((px_, base), obj, font=f, fill=fill, anchor="ls", stroke_width=stroke, stroke_fill=stroke_fill)
        else:
            y = int(base - obj.height + px * 0.12)
            im.alpha_composite(obj, (int(px_), max(0, y)))
    return im, base


# ------------------------------------------------------------------ censor
def _censor_cfg():
    return load_json(CONFIG / "censor.json", {"mask": [], "bleep": []})


@lru_cache(maxsize=1)
def _patterns():
    c = _censor_cfg()
    return ([re.compile(p, re.I) for p in c.get("mask", [])], [re.compile(p, re.I) for p in c.get("bleep", [])])


def mask_word(w):
    mask, bleep = _patterns()
    core = re.sub(r"[^\w']", "", w)
    for p in bleep + mask:
        if core and p.fullmatch(core):
            keep = core[0]
            return w.replace(core, keep + "*" * (len(core) - 1))
    return w


def needs_bleep(w):
    _, bleep = _patterns()
    core = re.sub(r"[^\w']", "", w)
    return any(core and p.fullmatch(core) for p in bleep)


def fix_lexicon(text):
    fix = load_json(CONFIG / "lexicon.json", {}).get("fix", {})
    low = text.lower()
    for k, v in fix.items():
        if low == k:
            return v
    return text


# ------------------------------------------------------------------ captions
_GLUE = re.compile(r"^(,\d|\.\d|'[a-z]|n't\b|%|’[a-z])", re.I)


def merge_tokens(words):
    """Re-join ASR fragments: '30' ',000' -> '30,000'; 'y' ''all' -> "y'all"."""
    out = []
    for w in words:
        if out and _GLUE.match(w["raw"]) and w["t0"] - out[-1]["t1"] < 0.35:
            out[-1]["raw"] += w["raw"]
            out[-1]["t1"] = max(out[-1]["t1"], w["t1"])
        else:
            out.append(dict(w))
    return out


def caption_chunks(words, cfg):
    """words: [{w, t0, t1}] in output seconds -> list of chunks [{words:[...], t0, t1}]."""
    chunks, cur = [], []
    for i, w in enumerate(words):
        if cur:
            gap = w["t0"] - cur[-1]["t1"]
            chars = sum(len(x["w"]) + 1 for x in cur) + len(w["w"])
            if len(cur) >= cfg["max_words"] or chars > cfg["max_chars"] or gap > cfg["gap_break_s"] or \
                    cur[-1]["w"][-1:] in ".?!,":
                chunks.append(cur)
                cur = []
        cur.append(w)
    if cur:
        chunks.append(cur)
    out = []
    for k, ch in enumerate(chunks):
        t0 = ch[0]["t0"]
        t1 = ch[-1]["t1"] + 0.25
        if k + 1 < len(chunks):
            t1 = min(t1, chunks[k + 1][0]["t0"])
        out.append({"words": ch, "t0": t0, "t1": max(t1, t0 + 0.2)})
    return out


def clean_caption_word(w, cfg):
    w = fix_lexicon(w.strip())
    w = re.sub(r"^[\"'(\[]+|[\"')\],;:]+$", "", w)
    w = w.rstrip(".")
    if cfg.get("censor", True):
        w = mask_word(w)
    return w.upper() if cfg.get("uppercase", True) else w


def caption_img(chunk, t, cfg, max_w):
    """Caption chunk at time t: active word coloured and popped. Returns RGBA."""
    active = 0
    for i, w in enumerate(chunk["words"]):
        if w["t0"] <= t:
            active = i
    pop_s = cfg["pop_frames"] / 30.0
    imgs = []
    for i, w in enumerate(chunk["words"]):
        is_a = i == active
        scale = 1.0
        if is_a:
            p = (t - w["t0"]) / pop_s if pop_s > 0 else 1
            scale = 1.0 + 0.12 * max(0.0, 1 - p) if p >= 0 else 1.0
        im, base = _word_img(w["text"], cfg["font"], int(round(cfg["px"] * scale)), cfg["active"] if is_a else cfg["color"],
                             cfg["stroke"], cfg["stroke_color"])
        imgs.append((im, base))
    space = int(cfg["px"] * 0.22)
    lines, cur, cw = [], [], 0
    for im, base in imgs:
        if cur and cw + space + im.width > max_w:
            lines.append(cur)
            cur, cw = [], 0
        cur.append((im, base))
        cw += im.width + (space if len(cur) > 1 else 0)
    if cur:
        lines.append(cur)
    lh = int(cfg["px"] * 1.12)
    W = max(sum(im.width for im, _ in ln) + space * (len(ln) - 1) for ln in lines)
    asc = max(b for ln in lines for _, b in ln)
    H = lh * (len(lines) - 1) + max(im.height for ln in lines for im, _ in ln) + 10
    out = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    for li, ln in enumerate(lines):
        lw = sum(im.width for im, _ in ln) + space * (len(ln) - 1)
        x = (W - lw) // 2
        for im, base in ln:
            out.alpha_composite(im, (x, max(0, li * lh + asc - base)))
            x += im.width + space
    return out


@lru_cache(maxsize=2048)
def _word_img(text, font_name, px, fill, stroke, stroke_fill):
    return text_img(text, font_name, px, fill, stroke, stroke_fill)


# ------------------------------------------------------------------ hook / pops
def _wrap(text, font_name, px, max_w):
    f = font(font_name, px)
    words = text.split()
    lines, cur = [], []
    for w in words:
        trial = " ".join(cur + [w]).replace("*", "")
        if cur and f.getlength(trial) > max_w:
            lines.append(cur)
            cur = [w]
        else:
            cur.append(w)
    if cur:
        lines.append(cur)
    return lines


def hook_img(text, cfg, max_w=900):
    """Rounded dark box with 1-3 lines; words wrapped in *stars* use the accent colour."""
    px = cfg["px"]
    lines = _wrap(text, cfg["font"], px, max_w - 60)
    while len(lines) > cfg["max_lines"] and px > 40:
        px -= 6
        lines = _wrap(text, cfg["font"], px, max_w - 60)
    rendered = []
    for ln in lines:
        parts = []
        for w in ln:
            acc = w.startswith("*") or w.endswith("*")
            parts.append(text_img(w.strip("*"), cfg["font"], px, cfg["accent"] if acc else cfg["color"])[0])
        rendered.append(parts)
    space = int(px * 0.24)
    lh = int(px * 1.15)
    W = min(max_w, max(sum(p.width for p in ln) + space * (len(ln) - 1) for ln in rendered) + 56)
    H = lh * len(rendered) + 36
    box = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(box)
    rgb = tuple(int(cfg["box"][i: i + 2], 16) for i in (1, 3, 5))
    d.rounded_rectangle((0, 0, W - 1, H - 1), radius=cfg["radius"], fill=rgb + (int(255 * cfg["box_alpha"]),))
    y = 18
    for ln in rendered:
        lw = sum(p.width for p in ln) + space * (len(ln) - 1)
        x = (W - lw) // 2
        for p in ln:
            box.alpha_composite(p, (x, y))
            x += p.width + space
        y += lh
    return box


def pop_text_img(text, cfg, px=None, max_w=960):
    px = px or cfg["px"]
    lines = _wrap(text, cfg["font"], px, max_w)
    while len(lines) > 2 and px > 50:
        px -= 8
        lines = _wrap(text, cfg["font"], px, max_w)
    imgs = [text_img(" ".join(ln).replace("*", ""), cfg["font"], px, cfg["color"], cfg["stroke"], "#000000", shadow=6)[0]
            for ln in lines]
    W = max(i.width for i in imgs)
    H = sum(i.height for i in imgs)
    out = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    y = 0
    for i in imgs:
        out.alpha_composite(i, ((W - i.width) // 2, y))
        y += i.height - int(px * 0.15)
    return out


def ease_pop(p):
    """0..1 -> scale with overshoot (back-out)."""
    p = min(max(p, 0.0), 1.0)
    c1 = 1.9
    c3 = c1 + 1
    return 1 + c3 * (p - 1) ** 3 + c1 * (p - 1) ** 2


def transform(im, scale=1.0, angle=0.0, alpha=1.0):
    if scale != 1.0:
        im = im.resize((max(1, int(im.width * scale)), max(1, int(im.height * scale))), Image.BILINEAR)
    if angle:
        im = im.rotate(angle, resample=Image.BICUBIC, expand=True)
    if alpha < 1.0:
        a = np.asarray(im).copy()
        a[..., 3] = (a[..., 3].astype(np.float32) * alpha).astype(np.uint8)
        im = Image.fromarray(a, "RGBA")
    return im
