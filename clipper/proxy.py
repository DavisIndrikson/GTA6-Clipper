"""`clip proxy <slug> [--ids K01,..] [--add a-b ..]`: a sparse, fast-seeking copy of a fragmented VOD video.

YouTube serves finished live streams (post_live / was_live) as fragmented MP4 with one moof+mdat pair per
frame: 5 million fragments for a 24 h stream. ffmpeg 9 needs minutes per seek on such a file and remuxing it
(yt-dlp's merge) runs at ~5-9 MB/s, i.e. hours. This module reads only the box headers, then copies ftyp +
moov + the first fragment (start_time stays 0) + every fragment inside the wanted windows into
source/video_proxy.mp4. tfdt timestamps are kept, so source times stay valid; seeks take ~1.5 s.
The proxy has no audio: render and caption ASR read files.audio (project video.has_audio = False).
Windows come from candidate ids (their scan window) and/or plan/clips.json in/out, plus explicit ranges.
"""
from __future__ import annotations

import struct
from pathlib import Path

from .util import ClipError, load_json, parse_t, project_dir, update_project, video_info

PAD = 6.0


def _child(buf, path):
    """(offset, size, header_len) of the first box at `path` inside an in-memory box `buf`."""
    off, end = 0, len(buf)
    for i, want in enumerate(path):
        while off < end:
            size, typ = struct.unpack(">I4s", buf[off:off + 8])
            hlen = 8
            if size == 1:
                size, hlen = struct.unpack(">Q", buf[off + 8:off + 16])[0], 16
            if typ == want.encode():
                if i == len(path) - 1:
                    return off, size, hlen
                end, off = off + size, off + hlen
                break
            off += size
        else:
            return None
    return None


def index(src):
    """Top-level scan: (head_bytes, timescale, [(t_seconds, offset, bytes)] per moof+mdat pair)."""
    head, frags, ts, pend = [], [], None, None
    with open(src, "rb") as f:
        f.seek(0, 2)
        fend = f.tell()
        pos = 0
        while pos < fend:
            f.seek(pos)
            hdr = f.read(16)
            if len(hdr) < 8:
                break
            size, typ = struct.unpack(">I4s", hdr[:8])
            if size == 1:
                size = struct.unpack(">Q", hdr[8:16])[0]
            elif size == 0:
                size = fend - pos
            if size < 8 or pos + size > fend:
                break  # truncated tail of a partial download: keep the complete fragments before it
            if typ in (b"ftyp", b"moov", b"moof"):
                f.seek(pos)
                data = f.read(size)
                if typ == b"moof":
                    r = _child(data, ["moof", "traf", "tfdt"])
                    o = r[0] + r[2]
                    bmdt = struct.unpack(">Q", data[o + 4:o + 12])[0] if data[o] == 1 else \
                        struct.unpack(">I", data[o + 4:o + 8])[0]
                    th = _child(data, ["moof", "traf", "tfhd"])
                    if int.from_bytes(data[th[0] + th[2] + 1:th[0] + th[2] + 4], "big") & 0x1:
                        raise ClipError("tfhd carries an absolute base_data_offset: fragments can't be byte-copied")
                    pend = (bmdt / ts, pos)
                else:
                    head.append(data)
                    if typ == b"moov":
                        r = _child(data, ["moov", "trak", "mdia", "mdhd"])
                        o = r[0] + r[2]
                        k = 20 if data[o] == 1 else 12
                        ts = struct.unpack(">I", data[o + k:o + k + 4])[0]
            elif typ == b"mdat" and pend:
                frags.append((pend[0], pend[1], pos + size - pend[1]))
                pend = None
            pos += size
    if not frags:
        raise ClipError(f"{src} is not a fragmented MP4")
    return head, ts, frags


def is_fragmented(src, probe_bytes=64 << 20, min_moofs=200):
    """True when the first 64 MB hold many moof boxes (the slow per-frame DASH layout)."""
    n, pos = 0, 0
    with open(src, "rb") as f:
        while pos < probe_bytes:
            f.seek(pos)
            hdr = f.read(16)
            if len(hdr) < 8:
                break
            size, typ = struct.unpack(">I4s", hdr[:8])
            if size == 1:
                size = struct.unpack(">Q", hdr[8:16])[0]
            if size < 8:
                break
            n += typ == b"moof"
            pos += size
    return n >= min_moofs


def cut(src, dst, windows, pad=PAD, log=print):
    head, ts, frags = index(src)
    wins = [(a - pad, b + pad) for a, b in windows]
    keep = {0}
    for i, (t, _, _) in enumerate(frags):
        t1 = frags[i + 1][0] if i + 1 < len(frags) else t + 1.0
        if any(t < b and t1 > a for a, b in wins):
            keep.add(i)
    tmp = Path(str(dst) + ".part")
    with open(src, "rb") as f, open(tmp, "wb") as o:
        for h in head:
            o.write(h)
        for i in sorted(keep):
            _, off, n = frags[i]
            f.seek(off)
            o.write(f.read(n))
    tmp.replace(dst)
    log(f"proxy: {len(keep)} of {len(frags)} fragments ({sum(b - a for a, b in wins) / 60:.1f} min) -> {dst}")
    return dst


def build(slug, ids=None, extra=None, use_plan=True, log=print):
    pdir = project_dir(slug)
    pj = load_json(pdir / "project.json")
    f = pj.get("files") or {}
    src = pdir / "source"
    raw = f.get("video_raw") if f.get("video_raw") and Path(f["video_raw"]).exists() else None
    raw = raw or next((str(p) for p in sorted(list(src.glob("video_raw.*")) + list(src.glob("video.f*.mp4")))
                       if not p.name.endswith((".part", ".ytdl"))), None)
    partial = False
    if not raw:
        # an interrupted download (e.g. YouTube rate limit): its .part is a byte prefix of the stream, so the
        # complete fragments in it are usable. Re-run proxy once the download finishes.
        raw = next((str(p) for p in sorted(src.glob("video*.part"))), None)
        partial = raw is not None
    if not raw:
        raise ClipError("no raw video download (source/video_raw.*); run clip fetch --video-only first")
    windows = []
    if ids:
        cands = {c["id"]: c for c in load_json(pdir / "analysis" / "candidates.json")["candidates"]}
        for k in ids:
            if k not in cands:
                raise ClipError(f"unknown candidate {k}")
            windows.append(tuple(cands[k]["win"]))
    if use_plan and (pdir / "plan" / "clips.json").exists():
        for c in load_json(pdir / "plan" / "clips.json").get("clips", []):
            windows.append((parse_t(c["in"]), parse_t(c["out"])))
    for a, b in extra or []:
        windows.append((parse_t(a), parse_t(b)))
    if not windows:
        raise ClipError("nothing to cut: pass --ids, --add a-b or write plan/clips.json first")
    if partial:
        _, _, frags = index(raw)
        upto = frags[-1][0]
        late = [(a, b) for a, b in windows if b + PAD > upto]
        windows = [w for w in windows if w not in late]
        log(f"proxy: partial download {Path(raw).name} covers 0-{upto / 3600:.2f} h; skipping "
            f"{len(late)} window(s) past it: " + ", ".join(f"{a:.0f}-{b:.0f}" for a, b in late))
        if not windows:
            raise ClipError("all windows lie past the end of the partial download")
    dst = src / "video_proxy.mp4"
    if dst.exists():
        # Resolve may have imported the current proxy (build_selects) and holds it open: never overwrite it,
        # write the next _vN (2026-11-20: os.replace failed with WinError 5 on a proxy Resolve had imported)
        n = 2
        while (src / f"video_proxy_v{n}.mp4").exists():
            n += 1
        dst = src / f"video_proxy_v{n}.mp4"
    cut(raw, dst, windows, log=log)
    vi = video_info(dst)
    dur = float(pj.get("duration") or vi["duration"])
    update_project(slug, "files", {"video": str(dst)} if partial else {"video": str(dst), "video_raw": str(raw)})
    update_project(slug, "video", {"fps": [vi["fps"].numerator, vi["fps"].denominator], "width": vi["width"],
                                   "height": vi["height"], "duration": dur, "codec": vi["codec"],
                                   "frames": int(round(dur * float(vi["fps"]))), "has_audio": False,
                                   "proxy_windows": [[round(a, 2), round(b, 2)] for a, b in windows]})
    return dst
