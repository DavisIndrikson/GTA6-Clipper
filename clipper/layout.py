"""`clip layout`: find the streamer's facecam (if any) so vertical clips can stack it over the gameplay.

Method: YuNet face detection on frames sampled across the stream; the facecam is
the face that keeps showing up in the same place. Its box comes from persistent
straight edges around that face (an overlay border is an edge in every frame,
gameplay edges move). If no border is found, a box around the face is used.
Writes plan/layout.json and review/layout.jpg (view it, fix the json if wrong).
"""
from __future__ import annotations

import cv2
import numpy as np

from .frames import grab_many, label, save_jpg, tile
from .util import ClipError, fmt_t, load_json, machine, project_dir, save_json

AW = 960   # analysis width


def _faces(frames):
    h, w = frames[0].shape[:2]
    det = cv2.FaceDetectorYN.create(machine()["models"]["yunet"], "", (w, h), 0.72, 0.3, 50)
    out = []
    for f in frames:
        _, faces = det.detect(f)
        out.append([] if faces is None else [tuple(map(float, x[:4])) + (float(x[-1]),) for x in faces])
    return out


def _cluster(faces, n_frames):
    pts = [(fi, x + w / 2, y + h / 2, w, h) for fi, fl in enumerate(faces) for (x, y, w, h, s) in fl]
    if not pts:
        return None
    best, best_n = None, 0
    for _, cx, cy, w, h in pts:
        mem = [p for p in pts if abs(p[1] - cx) < max(40, w * 0.6) and abs(p[2] - cy) < max(40, h * 0.6)]
        nf = len({p[0] for p in mem})
        if nf > best_n:
            best, best_n = mem, nf
    if best is None:
        return None
    arr = np.array([p[1:] for p in best])
    med = np.median(arr, axis=0)
    return {"cx": med[0], "cy": med[1], "w": med[2], "h": med[3], "presence": best_n / n_frames,
            "frames": sorted({p[0] for p in best})}


def _edge_box(frames, face):
    gx = np.zeros(frames[0].shape[:2], np.float32)
    gy = np.zeros_like(gx)
    for f in frames:
        g = cv2.cvtColor(f, cv2.COLOR_BGR2GRAY).astype(np.float32)
        gx += np.abs(cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3))
        gy += np.abs(cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3))
    gx /= len(frames)
    gy /= len(frames)
    H, W = gx.shape
    cx, cy, fw, fh = face["cx"], face["cy"], face["w"], face["h"]
    y0, y1 = int(max(0, cy - 1.5 * fh)), int(min(H, cy + 1.5 * fh))
    x0, x1 = int(max(0, cx - 1.5 * fw)), int(min(W, cx + 1.5 * fw))
    col = gx[y0:y1].mean(axis=0)
    row = gy[:, x0:x1].mean(axis=1)

    def peak(profile, lo, hi, edge_val):
        lo, hi = int(max(0, lo)), int(min(len(profile), hi))
        if hi - lo < 4:
            return edge_val, 0.0
        seg = profile[lo:hi]
        k = int(np.argmax(seg))
        prom = seg[k] / (np.median(seg) + 1e-6)
        return (lo + k, prom) if prom > 2.4 else (edge_val, prom)

    L, pl = peak(col, cx - 4.5 * fw, cx - 0.9 * fw, 0)
    R, pr = peak(col, cx + 0.9 * fw, cx + 4.5 * fw, W)
    T, pt = peak(row, cy - 4.0 * fh, cy - 0.9 * fh, 0)
    B, pb = peak(row, cy + 0.9 * fh, cy + 4.5 * fh, H)
    found = sum(p > 2.4 for p in (pl, pr, pt, pb))
    return (L, T, R, B), found


def detect(slug, samples=48):
    pdir = project_dir(slug)
    pj = load_json(pdir / "project.json")
    video = pj["files"].get("video")
    if not video:
        raise ClipError("video not downloaded yet")
    W, H = pj["video"]["width"], pj["video"]["height"]
    dur = pj["video"]["duration"]
    times = list(np.linspace(dur * 0.03, dur * 0.97, samples))
    cands = load_json(pdir / "analysis" / "candidates.json", {"candidates": []})["candidates"]
    times += [c["peak"] for c in cands[:24]]
    pairs = [(t, f) for t, f in zip(times, grab_many(video, times, AW, (W, H))) if f is not None]
    times = [p[0] for p in pairs]
    frames = [p[1] for p in pairs]
    if len(frames) < 8:
        raise ClipError("could not read frames for layout detection")
    s = W / AW
    faces = _faces(frames)
    cl = _cluster(faces, len(frames))
    lay = {"source_size": [W, H], "facecam": None, "default": "game", "game_center": [W / 2, H / 2],
           "samples": len(frames)}
    rect = None
    if cl and cl["presence"] >= 0.3:
        area = cl["w"] * cl["h"] / (frames[0].shape[0] * frames[0].shape[1])
        (L, T, R, B), found = _edge_box([frames[i] for i in cl["frames"]] or frames, cl)
        box_ok = found >= 2 and (R - L) > 1.4 * cl["w"] and (B - T) > 1.4 * cl["h"]
        if not box_ok:
            bw, bh = 3.4 * cl["w"], 3.0 * cl["h"]
            L, R = cl["cx"] - bw / 2, cl["cx"] + bw / 2
            T, B = cl["cy"] - bh * 0.45, cl["cy"] + bh * 0.55
        L, T = max(0, L), max(0, T)
        R, B = min(frames[0].shape[1], R), min(frames[0].shape[0], B)
        rect = [round(L * s), round(T * s), round((R - L) * s), round((B - T) * s)]
        big = (rect[2] * rect[3]) / (W * H) > 0.45 or area > 0.04
        lay["facecam"] = {"rect": rect, "face": [round(cl["cx"] * s), round(cl["cy"] * s), round(cl["w"] * s),
                                                 round(cl["h"] * s)],
                          "presence": round(cl["presence"], 2), "method": "edges" if box_ok else "face-box",
                          "fullframe_camera": bool(big)}
        lay["default"] = "full" if big else "stack"
    save_json(pdir / "plan" / "layout.json", lay)

    # debug sheet: 8 frames with the facecam box and detected faces
    pick = np.linspace(0, len(frames) - 1, 8).astype(int)
    tiles = []
    for i in pick:
        im = cv2.resize(frames[i], (480, int(480 * H / W)))
        k = 480 / AW
        for (x, y, w, h, sc) in faces[i]:
            cv2.rectangle(im, (int(x * k), int(y * k)), (int((x + w) * k), int((y + h) * k)), (0, 220, 255), 1)
        if rect:
            r = [v * k / s for v in rect]
            cv2.rectangle(im, (int(r[0]), int(r[1])), (int(r[0] + r[2]), int(r[1] + r[3])), (0, 255, 0), 2)
        tiles.append(label(im, fmt_t(times[i] if i < len(times) else 0)))
    save_jpg(pdir / "review" / "layout.jpg", tile([tiles[:4], tiles[4:]]))
    fc = lay["facecam"]
    print(f"layout: default '{lay['default']}', facecam "
          + (f"{fc['rect']} (face {fc['face']}, seen in {fc['presence']:.0%} of samples, {fc['method']})" if fc else "none"))
    print(f"view {pdir / 'review' / 'layout.jpg'} and correct plan/layout.json if the green box is wrong")
    return lay
