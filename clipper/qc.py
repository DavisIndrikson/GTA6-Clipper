"""`clip qc`: a clip is not finished until a program has checked it."""
from __future__ import annotations

import json
import re
from fractions import Fraction

import numpy as np

from .frames import grab
from .util import (ClipError, latest_version, load_json, loudness, parse_t, project_dir, run, save_json, style, tool,
                   video_info)


def qc_clip(pdir, c):
    st = style()
    cdir = pdir / "clips" / c["id"]
    man = load_json(cdir / "render.json", {})
    if not man or man.get("draft"):
        return {"id": c["id"], "ok": False, "problems": ["not rendered"]}
    f = cdir / man["files"]["final"]
    probs, info = [], {}
    d = json.loads(run([tool("ffprobe"), "-v", "error", "-count_packets", "-show_streams", "-of", "json", f]).stdout)
    v = next((s for s in d["streams"] if s["codec_type"] == "video"), None)
    a = next((s for s in d["streams"] if s["codec_type"] == "audio"), None)
    if v is None:
        probs.append("no video stream")
    else:
        fps = Fraction(v["avg_frame_rate"])
        n = int(v.get("nb_read_packets", 0))
        info.update(size=[v["width"], v["height"]], fps=float(fps), frames=n)
        if [v["width"], v["height"]] != man["size"]:
            probs.append(f"size {v['width']}x{v['height']} != {man['size']}")
        if fps != Fraction(*man["fps"]):
            probs.append(f"fps {fps} != {Fraction(*man['fps'])}")
        if abs(n - man["frames"]) > 1:
            probs.append(f"{n} frames, expected {man['frames']}")
    if a is None:
        probs.append("no audio stream")
    else:
        I, TP = loudness(f)
        info.update(lufs=round(I, 2), true_peak=round(TP, 2))
        au = st["audio"]
        if abs(I - au["lufs"]) > au["tolerance"]:
            probs.append(f"loudness {I:.1f} LUFS (target {au['lufs']})")
        if TP > au["true_peak_max"]:
            probs.append(f"true peak {TP:.1f} dBTP > {au['true_peak_max']}")
    r = run([tool("ffmpeg"), "-v", "info", "-hide_banner", "-i", f, "-vf", "blackdetect=d=0:pix_th=0.06:pic_th=0.98",
             "-an", "-f", "null", "-"], check=False)
    fr = float(Fraction(*man["fps"]))
    blacks = [(float(x), float(y)) for x, y in re.findall(r"black_start:([\d.]+) black_end:([\d.]+)", r.stderr)]
    long_black = [b for b in blacks if (b[1] - b[0]) * fr > 2.5]
    if long_black:
        probs.append(f"{len(long_black)} black runs > 2 frames (first at {long_black[0][0]:.2f} s)")
    # black edges: outer 3 px nearly black and flat on sampled frames
    vi = video_info(f)
    edge_hits = 0
    for t in np.linspace(0.2, max(0.3, vi["duration"] - 0.2), 8):
        im = grab(f, float(t), None, (vi["width"], vi["height"]))
        if im is None:
            continue
        for strip in (im[:3], im[-3:], im[:, :3], im[:, -3:]):
            if strip.mean() < 6 and strip.std() < 3:
                edge_hits += 1
    warns = []
    # editing rules on the current plan (render refuses these too; this catches renders made before the rule)
    from .render import build_segments, caption_dropped, plan_problems
    pj = load_json(pdir / "project.json")
    src_fps = Fraction(*pj["video"]["fps"])
    probs += plan_problems(c, build_segments(c, src_fps), src_fps)
    # ending: the clip should stop shortly after the last word, unless the picture still shows the payoff
    wj = load_json(cdir / "words.json", {})
    a_src, b_src = parse_t(c["in"]), parse_t(c["out"])
    if wj.get("range") == [a_src, b_src]:
        ends = [w["e"] for w in wj["words"] if a_src <= w["s"] < b_src and not caption_dropped(c, w)]
        if ends:
            tail = b_src - min(max(ends), b_src)
            lo, hi = st["rules"]["tail_s"]
            info["tail_s"] = round(tail, 2)
            if tail < lo:
                warns.append(f"ends {tail:.2f} s after the last word (may clip it)")
            elif tail > hi:
                warns.append(f"ends {tail:.1f} s after the last word (fine only if the picture still shows the payoff)")
    if edge_hits >= 3:
        # geometry clamps crops inside the source and replicates edges, so black edges here are dark content
        # (black screens, night scenes); flag for the contact-sheet review instead of failing
        warns.append(f"dark frame edges on {edge_hits} sampled strips (check the sheet)")
    res = {"id": c["id"], "ok": not probs, "problems": probs, "warnings": warns, **info, "file": str(f)}
    save_json(cdir / "qc.json", res)
    return res


def qc_all(slug, ids=None):
    pdir = project_dir(slug)
    clips = load_json(pdir / "plan" / "clips.json")["clips"]
    if ids:
        clips = [c for c in clips if c["id"] in ids]
    bad = 0
    for c in clips:
        r = qc_clip(pdir, c)
        if r["ok"]:
            print(f"{c['id']}: OK  {r['size'][0]}x{r['size'][1]} {r['fps']:.2f} fps {r['frames']} fr "
                  f"{r['lufs']} LUFS {r['true_peak']} dBTP" + (f"  (warning: {'; '.join(r['warnings'])})"
                                                                  if r.get("warnings") else ""))
        else:
            bad += 1
            print(f"{c['id']}: FAIL {'; '.join(r['problems'])}")
    print(f"qc: {len(clips) - bad}/{len(clips)} passed")
    return 1 if bad else 0
