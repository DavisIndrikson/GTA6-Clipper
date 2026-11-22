"""Grab single frames quickly (keyframe seek + decode) for analysis and contact sheets."""
from __future__ import annotations

import subprocess
from concurrent.futures import ThreadPoolExecutor

import numpy as np

from .util import ClipError, tool, tool_env


def grab(video, t, width=None, src_size=None):
    """BGR frame at time t (s). width rescales keeping aspect."""
    vf = "scale=in_color_matrix=bt709:in_range=tv"
    if width:
        vf += f":w={width}:h=-2"
    cmd = [tool("ffmpeg"), "-v", "error", "-ss", f"{max(0.0, t):.3f}", "-i", str(video), "-frames:v", "1",
           "-vf", vf + ",format=bgr24", "-f", "rawvideo", "-"]
    p = subprocess.run(cmd, capture_output=True, env=tool_env())
    if p.returncode != 0 or not p.stdout:
        return None
    W, H = src_size
    if width:
        h = int(round(H * width / W / 2) * 2)
        w = width
    else:
        w, h = W, H
    n = w * h * 3
    if len(p.stdout) < n:
        return None
    return np.frombuffer(p.stdout[:n], np.uint8).reshape(h, w, 3).copy()


def grab_many(video, times, width, src_size, workers=8):
    with ThreadPoolExecutor(workers) as ex:
        return list(ex.map(lambda t: grab(video, t, width, src_size), times))


def label(img, text, scale=0.55, color=(255, 255, 255)):
    import cv2
    cv2.rectangle(img, (0, 0), (min(img.shape[1], 14 + int(len(text) * 11 * scale / 0.55)), 22), (0, 0, 0), -1)
    cv2.putText(img, text, (6, 16), cv2.FONT_HERSHEY_SIMPLEX, scale, color, 1, cv2.LINE_AA)
    return img


def tile(rows, gap=4, bg=(24, 24, 24)):
    """rows: list of lists of equally sized BGR images (None = blank)."""
    h = max(im.shape[0] for r in rows for im in r if im is not None)
    w = max(im.shape[1] for r in rows for im in r if im is not None)
    ncol = max(len(r) for r in rows)
    out = np.full(((h + gap) * len(rows) + gap, (w + gap) * ncol + gap, 3), bg, np.uint8)
    for i, r in enumerate(rows):
        for j, im in enumerate(r):
            if im is None:
                continue
            y, x = gap + i * (h + gap), gap + j * (w + gap)
            out[y: y + im.shape[0], x: x + im.shape[1]] = im
    return out


def save_jpg(path, img, q=88):
    import cv2
    path.parent.mkdir(parents=True, exist_ok=True)
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, q])
    if not ok:
        raise ClipError(f"could not encode {path}")
    path.write_bytes(buf.tobytes())
