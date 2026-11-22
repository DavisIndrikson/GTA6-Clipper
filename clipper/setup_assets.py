"""`clip setup`: download and verify everything the clipper needs that is not in this folder, then index the
sound effects, run the doctor and the self-test. Safe to re-run: files that are present and verified are kept.

Every download is pinned and checked against a SHA-256, so every machine runs exactly the same binaries and
models (the same yt-dlp release, the same detectors, the same speech model).
"""
from __future__ import annotations

import hashlib
import os
import stat
import sys
import time
import urllib.request
from pathlib import Path

from .util import ASSETS, ROOT, ClipError

YTDLP_VERSION = "2026.10.26"
YTDLP = {
    # official release assets of https://github.com/yt-dlp/yt-dlp/releases/tag/2026.10.26 (SHA2-256SUMS)
    "win": ("yt-dlp.exe", "yt-dlp.exe", "f5389d3698ecde4ef06f4e6b1ce0294fe9ac1396228c8990b8d3416d6d177a7d"),
    "mac": ("yt-dlp_macos", "yt-dlp", "0f922c15ccc0b83eefa1c8bc9104e434318c4bf16e408e1ee93b7745a6efe0aa"),
}
MODELS = [
    # PANNs sound-event detector (laughter / screams / game chaos), Kong et al., Zenodo record 3987831
    ("Cnn14_DecisionLevelMax_mAP=0.385.pth", ASSETS / "models",
     "https://zenodo.org/records/3987831/files/Cnn14_DecisionLevelMax_mAP%3D0.385.pth?download=1",
     "dd3b4043a87d4ec13df8082c0fcfee3fb5084151808e47e060987a95eabdd142"),
    # YuNet face detector (facecam box), OpenCV Zoo
    ("yunet_2023mar.onnx", ASSETS / "models",
     "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx",
     "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4"),
    # AudioSet class labels; panns_inference reads them from ~/panns_data and would try `wget` (absent on Windows)
    ("class_labels_indices.csv", Path.home() / "panns_data",
     "http://storage.googleapis.com/us_audioset/youtube_corpus/v1/csv/class_labels_indices.csv",
     "cdd1049833c4b86127c2773ac0d14a2754b6a6d0d1798002ed5c66e699708429"),
]
WHISPER = "large-v3-turbo"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _download(url: str, dest: Path, sha: str, label: str):
    if dest.exists() and _sha256(dest) == sha:
        print(f"  {label}: ok (already here, checksum verified)")
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".download")
    last_err = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "gta6-clipper-setup"})
            with urllib.request.urlopen(req, timeout=120) as r, open(tmp, "wb") as f:
                total = int(r.headers.get("Content-Length") or 0)
                got, t0, shown = 0, time.time(), 0
                while True:
                    chunk = r.read(1 << 20)
                    if not chunk:
                        break
                    f.write(chunk)
                    got += len(chunk)
                    if total and got - shown > total / 10:
                        shown = got
                        print(f"    {label}: {got / 1e6:.0f}/{total / 1e6:.0f} MB")
            break
        except Exception as e:   # flaky networks: retry twice
            last_err = e
            time.sleep(5 * (attempt + 1))
    else:
        tmp.unlink(missing_ok=True)
        raise ClipError(f"{label}: download failed from {url}: {last_err}")
    got_sha = _sha256(tmp)
    if got_sha != sha:
        tmp.unlink(missing_ok=True)
        raise ClipError(f"{label}: checksum mismatch (got {got_sha[:12]}..., expected {sha[:12]}...). "
                        f"The file at {url} changed; do not use it.")
    os.replace(tmp, dest)
    print(f"  {label}: downloaded and verified ({dest.stat().st_size / 1e6:.1f} MB)")


def ytdlp():
    plat = "mac" if sys.platform == "darwin" else ("win" if os.name == "nt" else None)
    if plat is None:
        raise ClipError("this kit supports Windows and macOS")
    asset, local, sha = YTDLP[plat]
    dest = ROOT / local
    _download(f"https://github.com/yt-dlp/yt-dlp/releases/download/{YTDLP_VERSION}/{asset}", dest, sha,
              f"yt-dlp {YTDLP_VERSION}")
    if plat == "mac":
        dest.chmod(dest.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        os.system(f'xattr -d com.apple.quarantine "{dest}" 2>/dev/null')
    return dest


def models():
    for name, folder, url, sha in MODELS:
        _download(url, folder / name, sha, name)


def whisper():
    """Fetch the speech model into the Hugging Face cache now, so the first transcription doesn't stall."""
    print(f"  whisper {WHISPER}: loading (first time downloads ~1.6 GB)...")
    from faster_whisper import WhisperModel
    WhisperModel(WHISPER, device="cpu", compute_type="int8")
    print(f"  whisper {WHISPER}: ok")


def run_setup(skip_whisper=False):
    print("1/5 yt-dlp (pinned release)")
    ytdlp()
    print("2/5 detector models")
    models()
    print("3/5 speech model")
    if skip_whisper:
        print("  skipped")
    else:
        whisper()
    print("4/5 sound-effect library")
    from .sfx import build_library
    build_library()
    print("5/5 doctor + self-test")
    from .doctor import run_doctor
    rc = run_doctor()
    if rc:
        print("setup: the doctor found problems (see PROBLEMS above); fix them and run `clip setup` again")
        return rc
    from .selftest import run_selftest
    rc = run_selftest()
    print("setup: OK" if rc == 0 else "setup: self-test FAILED")
    return rc
