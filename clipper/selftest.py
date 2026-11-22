"""`clip selftest`: frame-exact decoding and timeline maths on synthetic media.

The test clip's frames carry their own index as 12 black/white blocks (readable after compression),
encoded with 2-second GOPs like YouTube. Runs in a folder whose name has a space and a non-ASCII letter.
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from fractions import Fraction
from pathlib import Path

import numpy as np

from .render import _decode, build_segments, frame_map, src_to_out
from .util import ClipError, popen, tool

W, H, BITS = 640, 360, 12


def _frame(i):
    im = np.full((H, W, 3), 90, np.uint8)
    bw = W // BITS
    for b in range(BITS):
        v = 255 if (i >> b) & 1 else 0
        im[100:260, b * bw + 4:(b + 1) * bw - 4] = v
    return im


def _read_index(im):
    bw = W // BITS
    val = 0
    for b in range(BITS):
        if im[150:210, b * bw + 12:(b + 1) * bw - 12].mean() > 128:
            val |= 1 << b
    return val


def run_selftest():
    root = Path(tempfile.gettempdir()) / "clipper selftest ü"
    root.mkdir(exist_ok=True)
    fps = Fraction(60)
    n = 600
    src = root / "index clip.mp4"
    p = popen([tool("ffmpeg"), "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{W}x{H}",
               "-r", "60", "-i", "-", "-c:v", "libx264", "-crf", "20", "-g", "120", "-pix_fmt", "yuv420p", str(src)],
              stdin=subprocess.PIPE)
    for i in range(n):
        p.stdin.write(_frame(i).tobytes())
    p.stdin.close()
    if p.wait() != 0:
        raise ClipError("could not write the test clip")
    bad = 0
    ranges = [(0, 7), (59, 75), (119, 122), (301, 333), (480, 599)]
    for f0, f1 in ranges:
        got = [_read_index(fr) for fr in _decode(src, f0, f1 - f0, fps, W, H)]
        want = list(range(f0, f1))
        if got != want:
            bad += 1
            print(f"decode [{f0},{f1}): got {got[:6]}... want {want[:6]}...")
    print(f"decode: {len(ranges) - bad}/{len(ranges)} ranges frame-exact (incl. GOP boundaries 120/240/360/480)")

    # timeline maths
    c = {"id": "T", "in": 1.0, "out": 9.0, "cut": [[3.0, 4.0]],
         "events": [{"at": 2.0, "fx": "freeze", "dur": 0.5}, {"at": 5.0, "fx": "slowmo", "dur": 1.0, "rate": 0.5},
                    {"at": 8.0, "fx": "replay", "dur": 1.0, "rate": 0.5}]}
    segs = build_segments(c, fps)
    fmap, seg_out = frame_map(segs)
    play = (9 - 1 - 1) * 60          # kept source frames
    expect = play + 30 + 60 + 120     # + freeze 0.5 s + slowmo extra 1 s + replay 2 s
    ok_len = len(fmap) == expect
    ok_map = src_to_out(int(4.0 * 60), segs, seg_out, len(fmap)) == src_to_out(int(4.0 * 60) + 1, segs, seg_out, len(fmap)) - 1
    ok_cut = src_to_out(int(3.5 * 60), segs, seg_out, len(fmap)) == src_to_out(int(4.0 * 60), segs, seg_out, len(fmap))
    print(f"timeline: {len(fmap)} frames (expected {expect}) {'OK' if ok_len else 'FAIL'}; "
          f"cut snaps forward {'OK' if ok_cut else 'FAIL'}; mapping continuous {'OK' if ok_map else 'FAIL'}")
    shutil.rmtree(root, ignore_errors=True)
    fails = bad + (not ok_len) + (not ok_cut) + (not ok_map)
    print("selftest: " + ("PASS" if not fails else f"{fails} FAILURES"))
    return 1 if fails else 0
