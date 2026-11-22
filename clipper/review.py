"""Contact sheets: candidates (frames across each window) and rendered clips (with the safe zone)."""
from __future__ import annotations

import re

import cv2
import numpy as np

from .frames import grab, grab_many, label, save_jpg, tile
from .util import ClipError, fmt_t, latest_version, load_json, project_dir, style, video_info


def sheet(slug, ids=None, cols=6, rows_per_sheet=8):
    pdir = project_dir(slug)
    pj = load_json(pdir / "project.json")
    video = pj["files"].get("video")
    if not video:
        raise ClipError("video not downloaded yet")
    W, H = pj["video"]["width"], pj["video"]["height"]
    if ids and not any(re.fullmatch(r"K\d+", i) for i in ids):   # clip ids (SP01, KC03, C01...), not candidates
        return review_clips(slug, ids)
    cands = sorted(load_json(pdir / "analysis" / "candidates.json")["candidates"], key=lambda c: c["peak"])
    if ids:
        cands = [c for c in cands if c["id"] in ids]
    tw = 320
    paths = []
    for si in range(0, len(cands), rows_per_sheet):
        group = cands[si: si + rows_per_sheet]
        times, owners = [], []
        for c in group:
            a = max(c["win"][0], c["peak"] - 30)
            b = min(c["win"][1], c["peak"] + 10)
            for t in np.linspace(a, b, cols):
                times.append(float(t))
                owners.append(c["id"])
        imgs = grab_many(video, times, tw, (W, H))
        rows = []
        for r in range(len(group)):
            row = []
            for j in range(cols):
                im = imgs[r * cols + j]
                if im is not None:
                    im = label(im, f"{owners[r * cols + j]} {fmt_t(times[r * cols + j])}")
                row.append(im)
            rows.append(row)
        out = pdir / "review" / f"cands_{group[0]['id']}_{group[-1]['id']}.jpg"
        save_jpg(out, tile(rows))
        paths.append(out)
    for p in paths:
        print(p)
    return paths


def review_clips(slug, ids=None, n=7):
    pdir = project_dir(slug)
    plan = load_json(pdir / "plan" / "clips.json")["clips"]
    if ids:
        plan = [c for c in plan if c["id"] in ids]
    sf = style()["safe"]
    rows = []
    for c in plan:
        f, _ = latest_version(pdir / "clips" / c["id"], "final", ".mp4")
        if not f:
            continue
        vi = video_info(f)
        times = np.linspace(0.3, max(0.4, vi["duration"] - 0.3), n)
        row = []
        for t in times:
            im = grab(f, float(t), None, (vi["width"], vi["height"]))
            if im is None:
                row.append(None)
                continue
            k = 270 / vi["width"]
            cv2.rectangle(im, (sf["x0"], sf["y0"]), (sf["x1"], sf["y1"]), (0, 255, 255), 3)
            im = cv2.resize(im, (270, int(vi["height"] * k)), interpolation=cv2.INTER_AREA)
            row.append(label(im, f"{c['id']} {t:.1f}s", 0.45))
        rows.append(row)
    if not rows:
        raise ClipError("no rendered clips to review")
    paths = []
    for i in range(0, len(rows), 3):
        out = pdir / "review" / f"clips_{i // 3 + 1}.jpg"
        save_jpg(out, tile(rows[i: i + 3]))
        paths.append(out)
        print(out)
    return paths
