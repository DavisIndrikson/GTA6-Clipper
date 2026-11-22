"""`clip render`: plan/clips.json -> finished vertical clips (+ separate layers for Resolve).

Deterministic: the plan holds every creative decision; this file only executes it.
Per clip it writes, under projects/<slug>/clips/<id>/ (new _vN names, never overwriting):
  final_vN.mp4      the finished clip (H.264 + AAC, -14 LUFS, <= -1 dBTP)
  base_vN.mp4       picture only, no text (Resolve V1)
  captions_vN.mov   captions with alpha (Resolve V2)
  gfx/*_vN.mov      hook title and each text/emoji pop, own clip each (Resolve V3+)
  program_vN.wav    clip audio after edits, mastered gain (Resolve A1)
  sfx/*_vN.wav      each sound effect at its final gain (Resolve A2/A3)
  render.json       manifest with frame positions of every layer (Resolve builder, QC)
and copies the final to projects/<slug>/exports/.
"""
from __future__ import annotations

import hashlib
import json
import math
import shutil
import subprocess
import time
from fractions import Fraction

import cv2
import numpy as np
from PIL import Image

from . import audio as A
from . import textfx as TX
from .util import (ClipError, ASSETS, fmt_t, fps_str, latest_version, load_json, next_version, parse_t, popen,
                   project_dir, safe_name, save_json, style, tool)

WARP = ("freeze", "slowmo", "replay")


# ====================================================================== timeline
def build_segments(c, fps):
    fr = lambda t: int(round(parse_t(t) * fps))
    a, b = fr(c["in"]), fr(c["out"])
    if b <= a:
        raise ClipError(f"{c['id']}: out must be after in")
    pieces = [[a, b]]
    for x, y in c.get("cut", []) or []:
        x, y = fr(x), fr(y)
        new = []
        for p0, p1 in pieces:
            if y <= p0 or x >= p1:
                new.append([p0, p1])
            else:
                if p0 < x:
                    new.append([p0, x])
                if y < p1:
                    new.append([y, p1])
        pieces = new
    segs = [{"type": "play", "f0": p0, "f1": p1} for p0, p1 in pieces if p1 > p0]
    warps = sorted([e for e in c.get("events", []) if e.get("fx") in WARP], key=lambda e: parse_t(e["at"]))
    for e in warps:
        fT = fr(e["at"])
        idx = next((i for i, s in enumerate(segs) if s["type"] == "play" and s["f0"] <= fT < s["f1"]), None)
        if idx is None:
            idx = next((i for i, s in enumerate(segs) if s["type"] == "play" and s["f0"] < fT <= s["f1"]), None)
        if idx is None:
            raise ClipError(f"{c['id']}: {e['fx']} at {e['at']} is not inside the kept clip")
        s = segs[idx]
        left = {"type": "play", "f0": s["f0"], "f1": fT}
        right = {"type": "play", "f0": fT, "f1": s["f1"]}
        if e["fx"] == "freeze":
            mid = [{"type": "freeze", "f": min(fT, s["f1"] - 1), "n": max(1, round(float(e.get("dur", 1.0)) * fps)),
                    "ev": e}]
        elif e["fx"] == "slowmo":
            fE = min(s["f1"], fT + max(2, round(float(e.get("dur", 1.5)) * fps)))
            mid = [{"type": "slow", "f0": fT, "f1": fE, "rate": float(e.get("rate", 0.5)), "ev": e}]
            right = {"type": "play", "f0": fE, "f1": s["f1"]}
        else:  # replay: the dur seconds before `at`, slowed, inserted at `at`
            f0 = max(a, fT - max(2, round(float(e.get("dur", 2.5)) * fps)))
            mid = [{"type": "slow", "f0": f0, "f1": fT, "rate": float(e.get("rate", 0.5)), "replay": True, "ev": e}]
        segs[idx: idx + 1] = [x for x in [left] + mid + [right] if x["type"] != "play" or x["f1"] > x["f0"]]
    return segs


def emoji_count(c):
    """Emoji in everything the viewer reads: hook, text pops, freeze/slow-mo text and emoji pops."""
    texts = [c.get("hook") or ""]
    for e in c.get("events", []) or []:
        texts += [e.get("text") or "", e.get("emoji") or ""]
    return sum(1 for t in texts for is_emoji, _ in TX.runs(t) if is_emoji)


def caption_dropped(c, w):
    """plan "caption_drop": source times of words the transcriber invented (e.g. a trailing "so" over silence)."""
    return any(abs(w["s"] - parse_t(t)) <= 0.12 for t in c.get("caption_drop", []) or [])


def plan_problems(c, segs, fps):
    """Plan errors under style.rules. segs from build_segments. Empty list = OK."""
    r = style()["rules"]
    probs = []
    freezes = [i for i, s in enumerate(segs) if s["type"] == "freeze"]
    if freezes and not r["end_freeze"]:
        i = freezes[-1]
        after = sum((s["f1"] - s["f0"]) / s.get("rate", 1.0) for s in segs[i + 1:] if s["type"] != "freeze")
        if after < r["end_min_play_s"] * float(fps):
            probs.append(f"freeze at {segs[i]['ev']['at']} ends the clip on a frozen frame: end on the reaction "
                         f"instead (drop the freeze; out ~0.4 s after the last word or when the payoff is over)")
    n = emoji_count(c)
    if n > r["max_emoji"]:
        probs.append(f"{n} emoji (max {r['max_emoji']} per clip, hook included): keep one only if it is funny or "
                     f"edgy in itself, never a literal picture of a word")
    for e in c.get("events", []) or []:
        if e.get("emoji") and not e.get("why"):
            probs.append(f"emoji {e['emoji']} at {e.get('at')} has no 'why'")
    return probs


def frame_map(segs):
    out, seg_out = [], []
    for i, s in enumerate(segs):
        seg_out.append(len(out))
        if s["type"] == "play":
            out += [(i, f) for f in range(s["f0"], s["f1"])]
        elif s["type"] == "freeze":
            out += [(i, s["f"])] * s["n"]
        else:
            n = max(1, int(round((s["f1"] - s["f0"]) / s["rate"])))
            s["n"] = n
            out += [(i, s["f0"] + min(s["f1"] - s["f0"] - 1, int(k * s["rate"]))) for k in range(n)]
    return out, seg_out


def src_to_out(f, segs, seg_out, total):
    for i, s in enumerate(segs):
        if s["type"] == "play" and s["f0"] <= f < s["f1"]:
            return seg_out[i] + (f - s["f0"])
        if s["type"] == "slow" and not s.get("replay") and s["f0"] <= f < s["f1"]:
            return seg_out[i] + (f - s["f0"]) / s["rate"]
    nxt = [seg_out[i] for i, s in enumerate(segs) if s["type"] in ("play", "slow") and not s.get("replay")
           and s["f0"] > f]
    return min(nxt) if nxt else total


# ====================================================================== geometry
def _cover(rect, aspect, anchor, ybias=0.45):
    x, y, w, h = rect
    if w / h > aspect:
        nw = h * aspect
        nx = min(max(anchor[0] - nw / 2, x), x + w - nw)
        return [nx, y, nw, h]
    nh = w / aspect
    ny = min(max(anchor[1] - nh * ybias, y), y + h - nh)
    return [x, ny, w, nh]


def geometry(layout, lay, opts, W, H, CW, CH, st):
    fc = (opts.get("face_rect") or (lay.get("facecam") or {}).get("rect"))
    face = opts.get("face") or (lay.get("facecam") or {}).get("face")
    panels = []
    if layout in ("stack", "face") and not fc:
        layout = "game"
    if layout == "stack":
        th = int(round(CH * st["layout"]["stack_split"] / 2) * 2)
        anchor = (face[0], face[1]) if face else (fc[0] + fc[2] / 2, fc[1] + fc[3] / 2)
        panels.append({"dst": (0, 0, CW, th), "src": _cover(fc, CW / th, anchor), "anchor": anchor, "tag": "face"})
        gh = CH - th
        gcx = float(opts.get("game_cx", W / 2))
        gcy = float(opts.get("game_cy", H / 2))
        gsrc = _cover([0, 0, W, H], CW / gh, (gcx, gcy), 0.5)
        panels.append({"dst": (0, th, CW, gh), "src": gsrc, "anchor": (gcx, gcy), "tag": "game"})
        zones = {"top": 0.17 * CH, "center": th, "bottom": 0.77 * CH, "hook": th, "cap": st["captions"]["y_stack"] * CH / 1920}
    elif layout == "face":
        anchor = (face[0], face[1]) if face else (fc[0] + fc[2] / 2, fc[1] + fc[3] / 2)
        panels.append({"dst": (0, 0, CW, CH), "src": _cover(fc, CW / CH, anchor), "anchor": anchor, "tag": "face"})
        zones = {"top": 0.2 * CH, "center": 0.5 * CH, "bottom": 0.77 * CH, "hook": 0.22 * CH,
                 "cap": st["captions"]["y_face"] * CH / 1920}
    elif layout == "full":
        cx = float(opts.get("cx", face[0] if face else W / 2))
        cy = float(opts.get("cy", face[1] if face else H / 2))
        panels.append({"dst": (0, 0, CW, CH), "src": _cover([0, 0, W, H], CW / CH, (cx, cy)), "anchor": (cx, cy),
                       "tag": "face"})
        zones = {"top": 0.2 * CH, "center": 0.5 * CH, "bottom": 0.77 * CH, "hook": 0.22 * CH,
                 "cap": st["captions"]["y_full"] * CH / 1920}
    else:  # game: fitted gameplay over a blurred copy of itself
        fh = CW * H / W
        cy = CH * st["layout"]["game_fit_y"]
        panels.append({"dst": (0, 0, CW, CH), "src": _cover([0, 0, W, H], CW / CH, (W / 2, H / 2), 0.5),
                       "anchor": (W / 2, H / 2), "tag": "bg", "blur": True})
        panels.append({"dst": (0, int(cy - fh / 2), CW, int(round(fh))), "src": [0, 0, W, H],
                       "anchor": (float(opts.get("game_cx", W / 2)), float(opts.get("game_cy", H / 2))), "tag": "game"})
        zones = {"top": cy - fh / 2 - 0.09 * CH, "center": cy, "bottom": cy + fh / 2 + 0.09 * CH,
                 "hook": cy - fh / 2 - 0.1 * CH, "cap": st["captions"]["y_game"] * CH / 1920}
    face_canvas = None
    if face and panels[0]["tag"] == "face":
        p = panels[0]
        s = p["dst"][2] / p["src"][2]
        face_canvas = ((face[0] - p["src"][0]) * s + p["dst"][0], (face[1] - p["src"][1]) * s + p["dst"][1],
                       face[2] * s)
    return layout, panels, zones, face_canvas


def _warp(frame, src, dst_wh, z, anchor, W, H):
    x, y, w, h = src
    z = max(1.0, z)
    ax, ay = anchor
    nw, nh = w / z, h / z
    nx = ax - (ax - x) / z
    ny = ay - (ay - y) / z
    nx = min(max(nx, x if z == 1 else 0), W - nw)
    ny = min(max(ny, y if z == 1 else 0), H - nh)
    nx, ny = max(0.0, nx), max(0.0, ny)
    dw, dh = dst_wh
    sx, sy = dw / nw, dh / nh
    M = np.float32([[sx, 0, -nx * sx], [0, sy, -ny * sy]])
    interp = cv2.INTER_CUBIC if sx > 1.05 else cv2.INTER_LINEAR
    return cv2.warpAffine(frame, M, (dw, dh), flags=interp, borderMode=cv2.BORDER_REPLICATE)


# ====================================================================== effect curves
def _zoom_curve(N, k0, n, z, kind, fps):
    arr = np.ones(N, np.float32)
    k1 = min(N, k0 + n)
    if k0 >= N or n <= 0:
        return arr
    idx = np.arange(k0, k1)
    p = (idx - k0) / max(1, n - 1)
    if kind == "push":
        e = p * p * (3 - 2 * p)
        arr[k0:k1] = 1 + (z - 1) * e
    elif kind == "snap":
        arr[k0:k1] = z
    else:  # punch: fast in, hold, quick out
        up = max(1, int(0.06 * fps))
        down = max(1, int(0.1 * fps))
        v = np.full(len(idx), z, np.float32)
        r = np.arange(len(idx))
        v = np.where(r < up, 1 + (z - 1) * (1 - (1 - (r + 1) / up) ** 3), v)
        v = np.where(r >= len(idx) - down, 1 + (z - 1) * ((len(idx) - r) / down), v)
        arr[k0:k1] = v
    return arr


# ====================================================================== encoders
def _hw_encoder():
    from .util import machine
    enc = style()["output"].get("encoder", "auto")
    if enc == "x264":
        return None
    return machine().get("hw_h264")


def _enc_h264(path, CW, CH, fps, crf, audio=None, preset="medium", kbps=320):
    cmd = [tool("ffmpeg"), "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{CW}x{CH}",
           "-r", fps_str(fps), "-i", "-"]
    if audio:
        cmd += ["-i", str(audio)]
    cmd += ["-map", "0:v"] + (["-map", "1:a"] if audio else [])
    hw = _hw_encoder()
    if hw == "h264_qsv":
        # Intel Quick Sync: ~5x faster than x264 on Intel laptops; ICQ quality ~= x264 crf+2
        cmd += ["-vf", "scale=out_color_matrix=bt709:out_range=tv,format=nv12", "-c:v", "h264_qsv",
                "-preset", "slower" if preset == "medium" else "medium", "-global_quality", str(crf + 2),
                "-profile:v", "high"]
    else:
        cmd += ["-vf", "scale=out_color_matrix=bt709:out_range=tv,format=yuv420p", "-c:v", "libx264", "-preset", preset,
                "-crf", str(crf), "-profile:v", "high"]
    cmd += ["-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", "tv"]
    if audio:
        cmd += ["-c:a", "aac", "-b:a", f"{kbps}k", "-ar", "48000"]
    cmd += ["-movflags", "+faststart", str(path)]
    return popen(cmd, stdin=subprocess.PIPE)


def _enc_alpha(path, CW, CH, fps):
    cmd = [tool("ffmpeg"), "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "bgra", "-s", f"{CW}x{CH}",
           "-r", fps_str(fps), "-i", "-", "-c:v", "qtrle", "-pix_fmt", "argb", str(path)]
    return popen(cmd, stdin=subprocess.PIPE)


def _decode(video, f0, n, fps, W, H):
    ss = max(0.0, (f0 - 0.25) / float(fps))
    p = popen([tool("ffmpeg"), "-v", "error", "-ss", f"{ss:.6f}", "-i", str(video), "-frames:v", str(n), "-an", "-sn",
               "-vf", "scale=in_color_matrix=bt709:in_range=tv,format=bgr24", "-f", "rawvideo", "-"],
              stdout=subprocess.PIPE, bufsize=W * H * 3 * 2)
    size = W * H * 3
    last = None
    for _ in range(n):
        buf = p.stdout.read(size)
        if len(buf) == size:
            last = np.frombuffer(buf, np.uint8).reshape(H, W, 3)
        if last is None:
            last = np.zeros((H, W, 3), np.uint8)
        yield last
    p.stdout.close()
    p.wait()


# ====================================================================== overlays
class Sprite:
    """RGBA image prepared for fast blending onto BGR uint8 with OpenCV's saturating SIMD ops."""
    __slots__ = ("bgra", "pm", "inv", "w", "h", "rgba")

    def __init__(self, im: Image.Image):
        arr = np.asarray(im.convert("RGBA"))
        self.rgba = arr
        self.bgra = np.ascontiguousarray(arr[..., [2, 1, 0, 3]])
        a = arr[..., 3:4].astype(np.float32) / 255.0
        self.inv = np.ascontiguousarray(np.repeat(255 - arr[..., 3:4], 3, axis=2))
        self.pm = np.ascontiguousarray((arr[..., 2::-1] * a + 0.5).astype(np.uint8))
        self.h, self.w = arr.shape[:2]


def _region(shape, sp, cx, cy):
    x0, y0 = int(round(cx - sp.w / 2)), int(round(cy - sp.h / 2))
    H, W = shape[:2]
    xa, ya, xb, yb = max(0, x0), max(0, y0), min(W, x0 + sp.w), min(H, y0 + sp.h)
    if xb <= xa or yb <= ya:
        return None, None
    return (ya, yb, xa, xb), (slice(ya - y0, yb - y0), slice(xa - x0, xb - x0))


def _blend(dst, sp: Sprite, cx, cy):
    r, s = _region(dst.shape, sp, cx, cy)
    if r is None:
        return
    ya, yb, xa, xb = r
    inv, pm = sp.inv[s], sp.pm[s]
    if not inv.flags.c_contiguous:
        inv, pm = np.ascontiguousarray(inv), np.ascontiguousarray(pm)
    dst[ya:yb, xa:xb] = cv2.add(cv2.multiply(dst[ya:yb, xa:xb], inv, scale=1 / 255.0), pm)


def _put_bgra(buf, sp: Sprite, cx, cy):
    """Place a sprite into an EMPTY transparent BGRA layer buffer (straight alpha copy)."""
    r, s = _region(buf.shape, sp, cx, cy)
    if r is None:
        return None
    ya, yb, xa, xb = r
    buf[ya:yb, xa:xb] = sp.bgra[s]
    return r


# ====================================================================== main render
def _plan_hash(c, lay):
    blob = json.dumps({"c": c, "lay": lay, "style": style()}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:12]


def render_clip(slug, c, draft=False, layers=True, force=False):
    t_start = time.time()
    pdir = project_dir(slug)
    pj = load_json(pdir / "project.json")
    lay = load_json(pdir / "plan" / "layout.json", {"facecam": None, "default": "game"})
    st = style()
    video = pj["files"]["video"]
    vi = pj["video"]
    # a video-only proxy (clip proxy) has no audio track: decode the clip audio from the audio download instead
    asrc = pj["files"]["audio"] if vi.get("has_audio") is False else video
    W, H = vi["width"], vi["height"]
    src_fps = Fraction(*vi["fps"])
    fps = src_fps
    if float(fps) > st["output"]["max_fps"] + 0.5:
        raise ClipError("source above max_fps is not supported yet")
    cdir = pdir / "clips" / c["id"]
    cdir.mkdir(parents=True, exist_ok=True)
    plan_h = _plan_hash(c, lay) + ("-draft" if draft else "")
    man_old = load_json(cdir / "render.json", {})
    if not force and man_old.get("hash") == plan_h and not draft:
        f_old = man_old.get("files", {}).get("final")
        if f_old and (cdir / f_old).exists():
            print(f"{c['id']}: unchanged, skipped (use --force to re-render)")
            return man_old

    sc = 0.5 if draft else 1.0
    CW, CH = int(st["output"]["width"] * sc), int(st["output"]["height"] * sc)
    layout = c.get("layout") or lay.get("default", "game")
    layout, panels, zones, face_canvas = geometry(layout, lay, c.get("layout_opts", {}), W, H, CW, CH, st)
    if c.get("captions_y"):
        zones["cap"] = float(c["captions_y"]) * sc   # e.g. move captions off on-screen text in the gameplay

    segs = build_segments(c, fps)
    probs = plan_problems(c, segs, fps)
    if probs:
        raise ClipError(f"{c['id']}: " + "; ".join(probs))
    fmap, seg_out = frame_map(segs)
    N = len(fmap)
    dur = N / float(fps)
    if dur > 180:
        raise ClipError(f"{c['id']}: {dur:.0f} s is too long for a short")

    def out_frame(t_src):
        return src_to_out(parse_t(t_src) * float(fps), segs, seg_out, N)

    # ---------------- words for captions (precise pass, cached)
    wpath = cdir / "words.json"
    a_src, b_src = parse_t(c["in"]), parse_t(c["out"])
    wj = load_json(wpath, {})
    # "caption_src": "scan" takes the stream's window transcript (analysis/words.json) instead of the precise
    # pass: once the beam-5 pass fused two sentences across a cut and captioned game NPCs.
    csrc = c.get("caption_src", "clip")
    if wj.get("range") != [a_src, b_src] or wj.get("src", "clip") != csrc:
        if csrc == "scan":
            allw = load_json(project_dir(slug) / "analysis" / "words.json")["words"]
            save_json(wpath, {"model": "scan", "src": "scan", "range": [a_src, b_src],
                              "words": [w for w in allw if w["e"] > a_src and w["s"] < b_src]})
        else:
            from .asr import clip_words
            clip_words(slug, a_src, b_src, wpath)
        wj = load_json(wpath)
    fixes = [(parse_t(t), txt) for t, txt in c.get("caption_fix", []) or []]
    cap_words = []
    for w in wj["words"]:
        if caption_dropped(c, w):
            continue
        fx = next((txt for t, txt in fixes if abs(w["s"] - t) <= 0.12), None)
        if fx is not None:   # plan "caption_fix": [["m:ss.ss", "word"]] for a clear mishearing ("Chad" for "chat")
            w = dict(w, w=fx)
        f_s = w["s"] * float(fps)
        f_e = w["e"] * float(fps)
        seg_i = next((i for i, s in enumerate(segs) if s["type"] in ("play", "slow") and not s.get("replay")
                      and s["f0"] <= f_s < s["f1"]), None)
        if seg_i is None:
            continue
        s = segs[seg_i]
        r = s.get("rate", 1.0)
        k0 = seg_out[seg_i] + (f_s - s["f0"]) / r
        k1 = seg_out[seg_i] + (min(f_e, s["f1"]) - s["f0"]) / r
        cap_words.append({"raw": w["w"], "t0": k0 / float(fps), "t1": max(k1, k0 + 2) / float(fps)})
    cap_words = TX.merge_tokens(cap_words)
    ccfg = dict(st["captions"])
    ccfg["censor"] = st["censor"]["captions"]
    for w in cap_words:
        w["w"] = w["text"] = TX.clean_caption_word(w["raw"], ccfg)
    cap_words = [w for w in cap_words if w["w"]]
    if sc != 1.0:
        ccfg["px"] = int(ccfg["px"] * sc)
        ccfg["stroke"] = max(2, int(ccfg["stroke"] * sc))
    chunks = TX.caption_chunks(cap_words, ccfg) if c.get("captions", True) else []
    # hide captions during freezes and replays
    blocked = [(seg_out[i], seg_out[i] + (s.get("n") or 0)) for i, s in enumerate(segs)
               if s["type"] == "freeze" or s.get("replay")]

    # ---------------- effect curves
    zf = np.ones(N, np.float32)   # face panel
    zg = np.ones(N, np.float32)   # game panel
    za = np.ones(N, np.float32)   # everything
    shake = np.zeros(N, np.float32)
    flash = np.zeros(N, np.float32)
    fry = np.zeros(N, bool)
    frozen = np.zeros(N, np.float32)
    boosts, sfx_events, pops = [], [], []
    for i, s in enumerate(segs):
        if s["type"] == "freeze":
            k0, n = seg_out[i], s["n"]
            za[k0:k0 + n] *= 1 + 0.07 * np.linspace(0, 1, n)
            frozen[k0:k0 + n] = 1
            e = s["ev"]
            if e.get("text"):
                pops.append({"kind": "text", "text": e["text"], "k0": k0, "n": n, "pos": e.get("pos", "center")})
            if e.get("sfx"):
                sfx_events.append((k0, e["sfx"], e))
            if e.get("emoji"):
                pops.append({"kind": "emoji", "text": e["emoji"], "k0": k0, "n": n, "pos": e.get("emoji_pos", "face")})
        if s["type"] == "slow":
            e = s["ev"]
            k0, n = seg_out[i], s["n"]
            if e.get("text"):
                pops.append({"kind": "text", "text": e["text"], "k0": k0, "n": min(n, int(2.0 * float(fps))),
                             "pos": e.get("pos", "top")})
            if e.get("sfx"):
                sfx_events.append((k0, e["sfx"], e))
            if e.get("zoom"):
                za[k0:k0 + n] *= 1 + (float(e["zoom"]) - 1) * np.linspace(0, 1, n)
    for e in c.get("events", []):
        if e.get("fx") in WARP:
            continue
        k0 = int(round(out_frame(e["at"]) + float(e.get("offset", 0)) * float(fps)))
        n = max(1, int(round(float(e.get("dur", 0.8)) * float(fps))))
        fx = e.get("fx")
        if fx == "zoom":
            curve = _zoom_curve(N, k0, n, float(e.get("z", 1.3)), e.get("ease", "punch"), float(fps))
            tgt = e.get("target", "face")
            if tgt == "face":
                zf *= curve
            elif tgt == "game":
                zg *= curve
            else:
                za *= curve
        elif fx == "shake":
            amp = float(e.get("amp", 18)) * sc
            k1 = min(N, k0 + n)
            if k0 < N:
                shake[k0:k1] = np.maximum(shake[k0:k1], amp * np.exp(-np.arange(k1 - k0) / max(1, n / 2.5)))
        elif fx == "flash":
            k1 = min(N, k0 + max(n, 6))
            if k0 < N:
                flash[k0:k1] = np.maximum(flash[k0:k1], 0.85 * np.exp(-np.arange(k1 - k0) / 2.5))
        elif fx in ("bassboost", "deepfry"):
            k1 = min(N, k0 + n)
            fry[k0:k1] = True
            boosts.append((k0, k1, float(e.get("amount", 1.0))))
        if e.get("sfx"):
            sfx_events.append((k0 + int(round(float(e.get("sfx_offset", 0)) * float(fps))), e["sfx"], e))
        if e.get("text") and fx is None:
            pops.append({"kind": "text", "text": e["text"], "k0": k0, "n": n, "pos": e.get("pos", "center")})
        if e.get("emoji"):
            pops.append({"kind": "emoji", "text": e["emoji"], "k0": k0, "n": n, "pos": e.get("pos", "face")})

    # ---------------- audio
    t_audio = time.time()
    adir = cdir / "audio_work"
    adir.mkdir(exist_ok=True)
    SR = A.SR
    total_s = int(round(N / float(fps) * SR))
    parts = []
    prev_end = None
    for i, s in enumerate(segs):
        n_out = int(round(((seg_out[i + 1] if i + 1 < len(segs) else N) - seg_out[i]) / float(fps) * SR))
        if s["type"] == "freeze":
            x = np.zeros((n_out, 2), np.float32)
            cont = False
        else:
            x = A.read(asrc, s["f0"] / float(fps), (s["f1"] - s["f0"]) / float(fps))
            if s["type"] == "slow":
                from scipy import signal as ss_
                x = ss_.resample(x, n_out, axis=0).astype(np.float32)
            cont = prev_end is not None and s["type"] == "play" and prev_end == s["f0"]
            prev_end = s["f1"] if s["type"] == "play" else None
        if len(x) < n_out:
            x = np.vstack([x, np.zeros((n_out - len(x), 2), np.float32)])
        x = x[:n_out]
        jf = int(st["audio"]["join_fade_ms"] / 1000 * SR)
        if not cont:
            x = A.fade(x, jf, 0)
            if parts:
                parts[-1] = A.fade(parts[-1], 0, jf)
        parts.append(x)
    prog = np.concatenate(parts) if parts else np.zeros((total_s, 2), np.float32)
    prog = np.vstack([prog, np.zeros((max(0, total_s - len(prog)), 2), np.float32)])[:total_s]
    for k0, k1, amt in boosts:
        a0, a1 = int(k0 / float(fps) * SR), int(k1 / float(fps) * SR)
        seg = prog[a0:a1]
        if len(seg) > 64:
            boosted = A.bass_boost(seg, amt)
            xf = min(len(seg) // 4, int(0.02 * SR))
            ramp = np.ones(len(seg), np.float32)
            ramp[:xf] = np.linspace(0, 1, xf)
            ramp[-xf:] = np.linspace(1, 0, xf)
            prog[a0:a1] = seg * (1 - ramp[:, None]) + boosted * ramp[:, None]
    bleeps = 0
    if st["censor"]["bleep_slurs"]:
        for w in cap_words:
            if TX.needs_bleep(w["raw"]):
                a0, a1 = int((w["t0"] - 0.02) * SR), int((w["t1"] + 0.02) * SR)
                a0, a1 = max(0, a0), min(total_s, a1)
                tt = np.arange(a1 - a0) / SR
                tone = (0.18 * np.sin(2 * np.pi * 1000 * tt)).astype(np.float32)
                prog[a0:a1] = A.fade(np.stack([tone, tone], 1), 96, 96)
                bleeps += 1
    sfx_idx = load_json(ASSETS / "sfx" / "index.json", {})
    sst = st["sfx"]
    placed = []
    sfx_bus = np.zeros_like(prog)
    for k, (k0, name, e) in enumerate(sorted(sfx_events, key=lambda x: x[0])):
        meta = sfx_idx.get(name)
        if not meta:
            raise ClipError(f"{c['id']}: unknown sfx '{name}' (run `clip sfx`; names: {', '.join(sfx_idx)})")
        x = A.load_wav(meta["file"])
        a0 = int(max(0, k0) / float(fps) * SR)
        if a0 >= total_s:
            continue
        lo, hi = max(0, a0 - int(1.5 * SR)), min(total_s, a0 + int(1.5 * SR))
        ref = A.window_loudness(prog[lo:hi])
        if ref < -45:
            ref = -24.0
        below = float(e.get("sfx_below", sst["target_lu_below"]))
        g = (ref - below) - meta["loud_m"] + float(e.get("sfx_gain", 0))
        g = min(g, 6.0)
        x = x * A.db(g)
        n = min(len(x), total_s - a0)
        sfx_bus[a0:a0 + n] += x[:n]
        placed.append({"name": name, "k0": int(k0), "a0": a0, "n": n, "gain_db": round(g, 2), "ref_lufs": round(ref, 1),
                       "x": x[:n]})
    fi, fo = int(st["audio"]["fade_in_ms"] / 1000 * SR), int(st["audio"]["fade_out_ms"] / 1000 * SR)
    mix = A.fade(prog + sfx_bus, fi, fo)
    mix_pre = adir / "mix_pre.wav"
    A.write(mix_pre, mix, subtype="FLOAT")
    master = next_version(cdir, "master", ".wav")
    # plan "limiter_db" lowers the ceiling for one clip whose AAC encode overshoots true peak (qc measures the MP4)
    G, I, TP = A.master(str(mix_pre), str(master), st["audio"]["lufs"], float(c.get("limiter_db", st["audio"]["limiter_db"])),
                        st["audio"]["comp"])
    t_audio = time.time() - t_audio

    # Resolve audio layers. The master chain (compressor + limiter) is a broadband, time-varying gain, so measure
    # it (master envelope / pre-master envelope, 5 ms) and apply the same gain curve to every layer: the layers
    # then add up to the master and each effect is exactly as loud in Resolve as in the finished clip.
    files = {"master": master.name}
    layer_check = None
    if layers:
        mast_v = A.load_wav(master)[:total_s]
        n_m = len(mast_v)
        fenv = A.fade(np.ones((total_s, 1), np.float32), fi, fo)[:n_m]
        pre = mix[:n_m] * A.db(G)
        win = max(8, int(0.005 * SR))

        def _env(x):
            pw = np.mean(x.astype(np.float64) ** 2, axis=1)
            cs = np.concatenate([[0.0], np.cumsum(pw)])
            e = (cs[win:] - cs[:-win]) / win
            return np.sqrt(np.concatenate([e, np.full(win - 1, e[-1] if len(e) else 0.0)]))

        em, ep = _env(mast_v), _env(pre)
        gr = np.full(n_m, np.nan)
        ok = ep > 1e-4
        gr[ok] = em[ok] / ep[ok]
        idx = np.where(ok, np.arange(n_m), 0)
        np.maximum.accumulate(idx, out=idx)
        gr = np.where(np.isnan(gr), gr[idx] if ok.any() else 1.0, gr)
        gr = np.nan_to_num(np.clip(gr, 0.0, 1.5), nan=1.0)
        gr = np.convolve(gr, np.ones(win) / win, mode="same").astype(np.float32)[:, None]
        g_lin = A.db(G)
        prog_layer = prog[:n_m] * fenv * g_lin * gr
        layer_sum = prog_layer.copy()
        sfx_layers = []
        for p in placed:
            sl = slice(p["a0"], min(n_m, p["a0"] + p["n"]))
            lay = p["x"][: sl.stop - sl.start] * fenv[sl] * g_lin * gr[sl]
            layer_sum[sl] += lay
            sfx_layers.append((p, lay))
        pk = float(np.max(np.abs(layer_sum)) + 1e-9)
        trim = min(1.0, A.db(-1.2) / pk)            # tiny safety trim if a transient got past 5 ms windows
        prog_out = next_version(cdir, "program", ".wav")
        A.write(prog_out, prog_layer * trim)
        files["program"] = prog_out.name
        (cdir / "sfx").mkdir(exist_ok=True)
        for k, (p, lay) in enumerate(sfx_layers):
            f = next_version(cdir / "sfx", f"sfx{k + 1:02d}_{p['name'].replace(':', '_')}", ".wav")
            A.write(f, lay * trim)
            p["file"] = f"sfx/{f.name}"
            p["layer_gain_db"] = round(float(p["gain_db"] + G), 2)
        layer_sum *= trim
        layer_check = {"lufs": round(float(A.integrated(layer_sum)), 2),
                       "peak_dbfs": round(float(20 * np.log10(np.max(np.abs(layer_sum)) + 1e-9)), 2),
                       "trim_db": round(float(20 * np.log10(trim)), 2),
                       "diff_rms_db": round(float(20 * np.log10(np.sqrt(np.mean((layer_sum - mast_v) ** 2)) /
                                                                 (np.sqrt(np.mean(mast_v ** 2)) + 1e-12) + 1e-12)), 1)}
    for p in placed:
        p.pop("x", None)

    # ---------------- sprites
    hcfg = dict(st["hook"])
    pcfg = dict(st["pop_text"])
    if sc != 1.0:
        hcfg["px"] = int(hcfg["px"] * sc)
        hcfg["radius"] = int(hcfg["radius"] * sc)
        pcfg["px"] = int(pcfg["px"] * sc)
        pcfg["stroke"] = max(2, int(pcfg["stroke"] * sc))
    safe = {k: v * sc for k, v in st["safe"].items()}
    hook_n = int(round(hcfg["dur_s"] * float(fps)))
    hook_sp = Sprite(TX.hook_img(c["hook"], hcfg, max_w=int(920 * sc))) if c.get("hook") else None

    def pos_xy(pos, sp_h=0):
        if isinstance(pos, (list, tuple)):
            return pos[0] * sc, pos[1] * sc
        if pos == "face" and face_canvas:
            return (min(safe["x1"] - 110 * sc, face_canvas[0] + face_canvas[2] * 0.9),
                    max(safe["y0"] + 110 * sc, face_canvas[1] - face_canvas[2] * 0.2))
        y = zones.get(pos, zones["center"])
        return CW / 2, min(max(y, safe["y0"] + sp_h / 2), safe["y1"] - sp_h / 2)

    pop_items = []
    for j, p in enumerate(sorted(pops, key=lambda p: p["k0"])):
        if p["kind"] == "text":
            base = TX.pop_text_img(p["text"], pcfg, max_w=int(900 * sc))
        else:
            base = TX.emoji_img(p["text"], int(230 * sc))
            if base is None:
                continue
        cx, cy = pos_xy(p["pos"], base.height)
        ang = (-4, 3, -2, 4)[j % 4] if p["kind"] == "text" else 0
        pop_items.append({"k0": p["k0"], "n": p["n"], "base": base, "cx": cx, "cy": cy, "ang": ang, "kind": p["kind"],
                          "name": f"pop{j + 1:02d}_{p['kind']}", "cache": {}})

    def pop_sprite(it, k):
        r = k - it["k0"]
        pin = max(1, int(0.23 * float(fps)))
        pout = max(1, int(0.13 * float(fps)))
        scale = TX.ease_pop(r / pin) if r < pin else 1.0
        alpha = 1.0 if r < it["n"] - pout else max(0.0, (it["n"] - r) / pout)
        dy = -60 * sc * (r / max(1, it["n"])) if it["kind"] == "emoji" else 0
        key = (round(scale, 2), round(alpha, 2))
        sp = it["cache"].get(key)
        if sp is None:
            sp = Sprite(TX.transform(it["base"], max(0.05, scale), it["ang"], alpha))
            it["cache"][key] = sp
        return sp, it["cx"], it["cy"] + dy

    cap_cache = {}

    def caption_sprite(t):
        for ci, ch in enumerate(chunks):
            if ch["t0"] <= t < ch["t1"]:
                act = max(i for i, w in enumerate(ch["words"]) if w["t0"] <= t) if ch["words"][0]["t0"] <= t else 0
                pop_s = ccfg["pop_frames"] / 30.0
                ph = min(4, int((t - ch["words"][act]["t0"]) / pop_s * 4)) if pop_s else 4
                key = (ci, act, ph)
                sp = cap_cache.get(key)
                if sp is None:
                    if len(cap_cache) > 300:
                        cap_cache.clear()
                    sp = Sprite(TX.caption_img(ch, ch["words"][act]["t0"] + ph / 4 * pop_s, ccfg, int(900 * sc)))
                    cap_cache[key] = sp
                return sp
        return None

    def hook_sprite(k):
        if hook_sp is None or k >= hook_n:
            return None
        pin = max(1, int(0.2 * float(fps)))
        pout = max(1, int(0.2 * float(fps)))
        if k < pin:
            return Sprite(TX.transform(Image.fromarray(hook_sp.rgba, "RGBA"), TX.ease_pop(k / pin)))
        if k >= hook_n - pout:
            return Sprite(TX.transform(Image.fromarray(hook_sp.rgba, "RGBA"), 1.0, 0, (hook_n - k) / pout))
        return hook_sp

    # ---------------- encoders
    crf = 23 if draft else st["output"]["crf"]
    final = next_version(cdir, "final", ".mp4") if not draft else cdir / "draft.mp4"
    enc_final = _enc_h264(final, CW, CH, fps, crf, audio=master, preset="veryfast" if draft else "medium",
                          kbps=st["output"]["audio_kbps"])
    encs = {"final": enc_final}
    layer_files = {}
    if layers:
        base = next_version(cdir, "base", ".mp4")
        encs["base"] = _enc_h264(base, CW, CH, fps, 16, preset="fast")
        files["base"] = base.name
        if chunks:
            capf = next_version(cdir, "captions", ".mov")
            encs["captions"] = _enc_alpha(capf, CW, CH, fps)
            files["captions"] = capf.name
        (cdir / "gfx").mkdir(exist_ok=True)
    gfx_items = []
    if hook_sp is not None:
        gfx_items.append({"name": "hook", "k0": 0, "n": min(hook_n, N), "fn": lambda k: (hook_sprite(k), CW / 2,
                                                                                     zones["hook"])})
    for it in pop_items:
        gfx_items.append({"name": it["name"], "k0": it["k0"], "n": min(it["n"], N - it["k0"]),
                          "fn": (lambda it_: (lambda k: pop_sprite(it_, k)))(it)})
    gfx_open = {}
    layer_buf = np.zeros((CH, CW, 4), np.uint8) if layers else None

    # vignette for freeze frames
    yy, xx = np.mgrid[0:CH, 0:CW].astype(np.float32)
    vig = 1 - 0.55 * np.clip(((xx - CW / 2) / (CW * 0.7)) ** 2 + ((yy - CH / 2) / (CH * 0.62)) ** 2, 0, 1)
    vig = vig[..., None]

    # ---------------- video loop
    blur_rects = [[int(v) for v in r] for r in (c.get("blur") or [])]
    t_vid = time.time()
    seg_frames = {}
    cur_seg, gen, cur_src, cur_img = None, None, None, None
    blur_cache = {}
    for k in range(N):
        si, sf = fmap[k]
        s = segs[si]
        if si != cur_seg:
            cur_seg = si
            if s["type"] == "freeze":
                gen = _decode(video, s["f"], 1, fps, W, H)
                cur_img = next(gen).copy()
                cur_src = s["f"]
                gen = None
            else:
                gen = _decode(video, s["f0"], s["f1"] - s["f0"], fps, W, H)
                cur_src = s["f0"] - 1
        if s["type"] != "freeze":
            while cur_src < sf:
                cur_img = next(gen)
                cur_src += 1
        frame = cur_img
        if blur_rects:   # privacy: pixelate emails, addresses, phone numbers shown on stream
            frame = frame.copy()
            for bx, by, bw, bh in blur_rects:
                roi = frame[by:by + bh, bx:bx + bw]
                if roi.size:
                    small = cv2.resize(roi, (max(1, roi.shape[1] // 18), max(1, roi.shape[0] // 18)),
                                       interpolation=cv2.INTER_AREA)
                    frame[by:by + bh, bx:bx + bw] = cv2.resize(small, (roi.shape[1], roi.shape[0]),
                                                               interpolation=cv2.INTER_NEAREST)
        canvas = np.empty((CH, CW, 3), np.uint8)
        for p in panels:
            x, y, w, h = p["dst"]
            if p.get("blur"):
                if cur_src not in blur_cache:
                    blur_cache.clear()
                    small = _warp(frame, p["src"], (max(8, w // 10), max(8, h // 10)), 1.0, p["anchor"], W, H)
                    small = cv2.GaussianBlur(small, (0, 0), 2.2)
                    blur_cache[cur_src] = (cv2.resize(small, (w, h), interpolation=cv2.INTER_LINEAR) * 0.5).astype(np.uint8)
                canvas[y:y + h, x:x + w] = blur_cache[cur_src]
                continue
            z = za[k] * (zf[k] if p["tag"] == "face" else zg[k] if p["tag"] == "game" else 1.0)
            y0c, y1c = max(0, y), min(CH, y + h)
            canvas[y0c:y1c, x:x + w] = _warp(frame, p["src"], (w, h), z, p["anchor"], W, H)[y0c - y:y1c - y]
        if fry[k]:
            g = cv2.cvtColor(canvas, cv2.COLOR_BGR2GRAY)
            g3 = cv2.cvtColor(g, cv2.COLOR_GRAY2BGR)
            canvas = cv2.addWeighted(canvas, 2.0, g3, -1.0, 0)
            canvas = cv2.convertScaleAbs(canvas, alpha=1.35, beta=-35)
            canvas[..., 2] = cv2.add(canvas[..., 2], 30)
        if frozen[k]:
            g3 = cv2.cvtColor(cv2.cvtColor(canvas, cv2.COLOR_BGR2GRAY), cv2.COLOR_GRAY2BGR)
            canvas = (cv2.addWeighted(canvas, 0.45, g3, 0.55, 0).astype(np.float32) * vig).astype(np.uint8)
        if shake[k] > 0.3:
            amp = shake[k]
            tt = k / float(fps)
            dx = amp * math.sin(2 * math.pi * 13 * tt + 0.7)
            dy = amp * math.sin(2 * math.pi * 17 * tt + 1.9)
            s_ = 1 + 2.2 * amp / CW
            M = np.float32([[s_, 0, dx + (1 - s_) * CW / 2], [0, s_, dy + (1 - s_) * CH / 2]])
            canvas = cv2.warpAffine(canvas, M, (CW, CH), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
        if flash[k] > 0.01:
            canvas = cv2.addWeighted(canvas, 1 - float(flash[k]), np.full_like(canvas, 255), float(flash[k]), 0)
        if "base" in encs:
            encs["base"].stdin.write(canvas.tobytes())

        t = k / float(fps)
        cap = None if any(b0 <= k < b1 for b0, b1 in blocked) else caption_sprite(t)
        cy_cap = zones["cap"]
        if cap is not None:
            _blend(canvas, cap, CW / 2, min(cy_cap, safe["y1"] - cap.h / 2))
            if "captions" in encs:
                reg = _put_bgra(layer_buf, cap, CW / 2, min(cy_cap, safe["y1"] - cap.h / 2))
                encs["captions"].stdin.write(layer_buf.tobytes())
                if reg:
                    layer_buf[reg[0]:reg[1], reg[2]:reg[3]] = 0
        elif "captions" in encs:
            encs["captions"].stdin.write(layer_buf.tobytes())
        for g in gfx_items:
            if g["k0"] <= k < g["k0"] + g["n"]:
                sp, gx, gy = g["fn"](k - g["k0"] if g["name"] == "hook" else k)
                if sp is None:
                    continue
                _blend(canvas, sp, gx, gy)
                if layers:
                    if g["name"] not in gfx_open:
                        f = next_version(cdir / "gfx", g["name"], ".mov")
                        gfx_open[g["name"]] = (_enc_alpha(f, CW, CH, fps), f)
                    reg = _put_bgra(layer_buf, sp, gx, gy)
                    gfx_open[g["name"]][0].stdin.write(layer_buf.tobytes())
                    if reg:
                        layer_buf[reg[0]:reg[1], reg[2]:reg[3]] = 0
            elif layers and g["name"] in gfx_open and k == g["k0"] + g["n"]:
                pass
        enc_final.stdin.write(canvas.tobytes())
    for e in list(encs.values()) + [v[0] for v in gfx_open.values()]:
        e.stdin.close()
    for e in list(encs.values()) + [v[0] for v in gfx_open.values()]:
        if e.wait() != 0:
            raise ClipError(f"{c['id']}: an encoder failed")
    t_vid = time.time() - t_vid
    files["final"] = final.name
    for g in gfx_items:
        if g["name"] in gfx_open:
            layer_files[g["name"]] = {"file": f"gfx/{gfx_open[g['name']][1].name}", "k0": g["k0"], "n": g["n"]}

    man = {
        "id": c["id"], "hash": plan_h, "title": c.get("title"), "hook": c.get("hook"), "layout": layout, "draft": draft,
        "fps": [fps.numerator, fps.denominator], "size": [CW, CH], "frames": N, "duration": round(dur, 3),
        "source": {"in": a_src, "out": b_src, "segments": [{k_: v for k_, v in s.items() if k_ != "ev"} for s in segs],
                   "seg_out": seg_out},
        "files": files, "gfx": layer_files, "sfx": placed, "bleeps": bleeps,
        "audio": {"gain_db": round(G, 2), "lufs": round(I, 2), "true_peak": round(TP, 2),
                  "resolve_layers": layer_check},
        "timing": {"audio_s": round(t_audio, 1), "video_s": round(t_vid, 1), "fps": round(N / max(t_vid, 1e-6), 1),
                   "total_s": round(time.time() - t_start, 1)},
    }
    if not draft:
        save_json(cdir / "render.json", man)
        exp = pdir / "exports" / f"{c['id']} - {safe_name(c.get('title') or c['id'], 60)}.mp4"
        shutil.copyfile(final, exp)
        man["export"] = str(exp)
        save_json(cdir / "render.json", man)
    print(f"{c['id']}: {dur:.1f} s, {N} frames, layout {layout}, {len(placed)} sfx, {len(pop_items)} pops, "
          f"{len(chunks)} caption chunks | {I:.1f} LUFS {TP:.1f} dBTP | video {N / max(t_vid, 1e-6):.0f} fps, "
          f"total {time.time() - t_start:.0f} s -> {final.name}")
    return man


def render_all(slug, ids=None, draft=False, layers=True, force=False):
    pdir = project_dir(slug)
    plan = load_json(pdir / "plan" / "clips.json")
    clips = plan["clips"]
    if ids:
        clips = [c for c in clips if c["id"] in ids]
    if not clips:
        raise ClipError("no clips selected")
    fails = 0
    prog = pdir / "review" / "render_progress.txt"
    for n, c in enumerate(clips, 1):
        prog.write_text(f"rendering {c['id']} ({n}/{len(clips)})\n", encoding="utf-8")
        try:
            render_clip(slug, c, draft=draft, layers=layers, force=force)
        except ClipError as e:
            fails += 1
            print(f"{c['id']}: FAILED: {e}")
        except Exception as e:  # keep the batch going; report the bug
            import traceback
            fails += 1
            tb = traceback.format_exc().strip().splitlines()
            print(f"{c['id']}: CRASHED: {type(e).__name__}: {e} | {' / '.join(tb[-3:])}")
    prog.write_text(f"done: {len(clips) - fails}/{len(clips)} ok\n", encoding="utf-8")
    return 1 if fails else 0
