"""Shared helpers: paths, config, subprocess, probing, loudness, time formatting.

Rules: no hard-coded machine paths (they come from config/machine.json), every
path is quoted by passing argument lists (never shell strings), times inside a
clip are integer frames with half-open ranges [in, out).
"""
from __future__ import annotations

import json
import math
import os
import re
import subprocess
import sys
from fractions import Fraction
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config"
ASSETS = ROOT / "assets"
PROJECTS = ROOT / "projects"

os.environ.setdefault("PYTHONUTF8", "1")
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS", "1")
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


class ClipError(RuntimeError):
    """A failure the CLI reports in one line and exits non-zero."""


# ---------------------------------------------------------------- json / config
def load_json(path, default=None):
    path = Path(path)
    if not path.exists():
        if default is not None:
            return default
        raise ClipError(f"missing file: {path}")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_json(path, obj, indent=1):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=indent)
    os.replace(tmp, path)


def machine():
    p = CONFIG / "machine.json"
    if not p.exists():
        raise ClipError("config/machine.json missing: run `uv run clip doctor` first")
    return load_json(p)


def tool(name):
    m = machine()
    t = m.get("tools", {}).get(name)
    if not t:
        raise ClipError(f"tool '{name}' not recorded in machine.json: run `uv run clip doctor`")
    return t


def deep_merge(a, b):
    out = dict(a)
    for k, v in b.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def style():
    from .defaults import STYLE
    user = load_json(CONFIG / "style.json", {})
    return deep_merge(STYLE, user)


# ---------------------------------------------------------------- subprocess
def tool_env():
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    try:
        extra = [str(Path(p).parent) for p in machine().get("tools", {}).values() if p and Path(p).exists()]
        env["PATH"] = os.pathsep.join(dict.fromkeys(extra + [env.get("PATH", "")]))
    except ClipError:
        pass
    return env


def hidden_kw():
    """Windows: when running without a console (the background poster runs under pythonw), start child
    processes without a console window, so nothing flashes on the user's screen every few minutes."""
    if os.name == "nt" and (sys.__stdout__ is None or os.environ.get("CLIP_HIDDEN")):
        return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)}
    return {}


def run(cmd, check=True, capture=True, input=None, timeout=None):
    cmd = [str(c) for c in cmd]
    r = subprocess.run(cmd, input=input, capture_output=capture, text=True, encoding="utf-8",
                       errors="replace", env=tool_env(), timeout=timeout, **hidden_kw())
    if check and r.returncode != 0:
        tail = (r.stderr or r.stdout or "")[-1500:]
        raise ClipError(f"command failed ({r.returncode}): {Path(cmd[0]).name} ...\n{tail}")
    return r


def popen(cmd, **kw):
    return subprocess.Popen([str(c) for c in cmd], env=tool_env(), **{**hidden_kw(), **kw})


# ---------------------------------------------------------------- media probing
def ffprobe(path):
    r = run([tool("ffprobe"), "-v", "error", "-show_format", "-show_streams", "-of", "json", path])
    return json.loads(r.stdout)


def video_info(path):
    """fps as Fraction, width, height, duration (s), frame count estimate, codec."""
    d = ffprobe(path)
    v = next((s for s in d["streams"] if s.get("codec_type") == "video"), None)
    a = next((s for s in d["streams"] if s.get("codec_type") == "audio"), None)
    if v is None:
        raise ClipError(f"no video stream in {path}")
    fps = Fraction(v.get("avg_frame_rate") or v.get("r_frame_rate"))
    rfps = Fraction(v.get("r_frame_rate"))
    dur = float(d["format"].get("duration") or v.get("duration") or 0)
    rot = 0
    for sd in v.get("side_data_list", []) or []:
        if "rotation" in sd:
            rot = int(sd["rotation"])
    return {
        "fps": fps, "r_fps": rfps, "width": int(v["width"]), "height": int(v["height"]),
        "duration": dur, "codec": v.get("codec_name"), "pix_fmt": v.get("pix_fmt"),
        "frames": int(round(dur * float(fps))), "rotation": rot,
        "color_transfer": v.get("color_transfer"),
        "audio": None if a is None else {"codec": a.get("codec_name"), "rate": int(a.get("sample_rate", 0)),
                                          "channels": int(a.get("channels", 0))},
    }


def std_fps(fps: Fraction) -> Fraction:
    """Snap a measured rate to the nearest standard rate."""
    cands = [Fraction(24000, 1001), Fraction(24), Fraction(25), Fraction(30000, 1001), Fraction(30),
             Fraction(50), Fraction(60000, 1001), Fraction(60)]
    return min(cands, key=lambda c: abs(float(c) - float(fps)))


def fps_str(fps: Fraction) -> str:
    return f"{fps.numerator}/{fps.denominator}"


# ---------------------------------------------------------------- loudness
_I_RE = re.compile(r"I:\s+(-?[\d.]+|-inf)\s+LUFS")
_TP_RE = re.compile(r"Peak:\s+(-?[\d.]+|-inf)\s+dBFS")


def loudness(path, start=None, dur=None):
    """Integrated loudness (LUFS) and true peak (dBTP) via ffmpeg ebur128."""
    cmd = [tool("ffmpeg"), "-hide_banner", "-nostats"]
    if start is not None:
        cmd += ["-ss", f"{start:.3f}"]
    if dur is not None:
        cmd += ["-t", f"{dur:.3f}"]
    cmd += ["-i", path, "-map", "0:a:0", "-af", "ebur128=peak=true:framelog=quiet", "-f", "null", "-"]
    r = run(cmd)
    txt = r.stderr
    summ = txt[txt.rfind("Summary:"):]
    i = _I_RE.search(summ)
    tp = _TP_RE.search(summ)
    f = lambda m: (-math.inf if m.group(1) == "-inf" else float(m.group(1))) if m else math.nan
    return f(i), f(tp)


# ---------------------------------------------------------------- time
def fmt_t(sec: float, decimals=1) -> str:
    sign = "-" if sec < 0 else ""
    sec = abs(sec)
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = sec - h * 3600 - m * 60
    s_txt = f"{s:0{3 + decimals if decimals else 2}.{decimals}f}"
    return f"{sign}{h}:{m:02d}:{s_txt}" if h else f"{sign}{m}:{s_txt}"


def parse_t(txt) -> float:
    if isinstance(txt, (int, float)):
        return float(txt)
    parts = [float(p) for p in str(txt).strip().split(":")]
    v = 0.0
    for p in parts:
        v = v * 60 + p
    return v


def slugify(txt: str, maxlen=40) -> str:
    txt = re.sub(r"[^A-Za-z0-9]+", "_", txt).strip("_").lower()
    return txt[:maxlen].strip("_") or "video"


def safe_name(txt: str, maxlen=80) -> str:
    """For file and Resolve timeline names: no : / \\ ? * " < > | and no emoji."""
    txt = "".join(ch for ch in txt if ord(ch) < 0x2000 or 0x2010 <= ord(ch) <= 0x206F)
    txt = re.sub(r'[:/\\?*"<>|]+', " ", txt)
    txt = re.sub(r"\s+", " ", txt).strip()
    return txt[:maxlen].strip()


# ---------------------------------------------------------------- projects
SUBDIRS = ["source", "analysis", "analysis/asr", "plan", "clips", "exports", "review"]


def project_dir(slug, create=False) -> Path:
    p = PROJECTS / slug
    if create:
        for s in SUBDIRS:
            (p / s).mkdir(parents=True, exist_ok=True)
    elif not p.exists():
        known = ", ".join(sorted(x.name for x in PROJECTS.glob("*") if x.is_dir())) or "none"
        raise ClipError(f"no project '{slug}' (known: {known})")
    return p


def project(slug):
    return load_json(project_dir(slug) / "project.json")


def update_project(slug, section, values):
    """Merge values into project.json[section], re-reading the file first (other commands may run in parallel)."""
    p = project_dir(slug) / "project.json"
    pj = load_json(p, {})
    pj.setdefault(section, {}).update(values)
    save_json(p, pj)
    return pj


def latest_version(folder: Path, stem: str, ext: str):
    """Newest existing <stem>_vN<ext>, or None."""
    best, bn = None, 0
    for f in folder.glob(f"{stem}_v*{ext}"):
        m = re.match(rf"{re.escape(stem)}_v(\d+){re.escape(ext)}$", f.name)
        if m and int(m.group(1)) > bn:
            best, bn = f, int(m.group(1))
    return best, bn


def next_version(folder: Path, stem: str, ext: str) -> Path:
    _, n = latest_version(folder, stem, ext)
    return folder / f"{stem}_v{n + 1}{ext}"
