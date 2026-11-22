"""`clip doctor`: detect this machine's tools and write config/machine.json (Windows 10/11 and macOS)."""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

from .util import ASSETS, CONFIG, ROOT, ClipError, load_json, save_json

IS_MAC = sys.platform == "darwin"
IS_WIN = os.name == "nt"
WINGET = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Packages"
MAC_DIRS = [Path("/opt/homebrew/bin"), Path("/usr/local/bin"), Path.home() / ".local" / "bin",
            Path.home() / ".deno" / "bin", Path.home() / ".cargo" / "bin"]
YTDLP_LOCAL = ROOT / ("yt-dlp.exe" if IS_WIN else "yt-dlp")     # pinned copy written by `clip setup`
BROWSERS = ["firefox", "chrome", "edge", "brave", "opera", "vivaldi", "chromium", "safari"]


def _find(name, extra_globs=()):
    p = shutil.which(name)
    if p:
        return str(Path(p).resolve())
    if IS_WIN:
        # winget installs land here; the PATH change only reaches new shells
        for g in extra_globs:
            hits = sorted(WINGET.glob(g)) if WINGET.exists() else []
            if hits:
                return str(hits[-1])
        for d in (Path(os.environ.get("USERPROFILE", "")) / ".local" / "bin",
                  Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Links"):
            if (d / f"{name}.exe").exists():
                return str(d / f"{name}.exe")
    else:
        for d in MAC_DIRS:
            if (d / name).exists():
                return str(d / name)
    return None


def _ffmpeg_features(ffmpeg):
    out = {}
    enc = subprocess.run([ffmpeg, "-hide_banner", "-encoders"], capture_output=True, text=True, errors="replace").stdout
    flt = subprocess.run([ffmpeg, "-hide_banner", "-filters"], capture_output=True, text=True, errors="replace").stdout
    for e in ["libx264", "prores_ks", "qtrle", "aac", "pcm_s24le"]:
        out[e] = f" {e} " in enc
    for f in ["ebur128", "alimiter", "acompressor", "loudnorm", "aresample", "atempo", "rubberband"]:
        out[f] = f" {f} " in flt
    return out


def _hw_h264(ffmpeg):
    """First hardware H.264 encoder that really produces a decodable file here. Render uses Intel Quick Sync
    when it is found (~5x faster); every other machine renders with x264 (same look, slower)."""
    import tempfile
    cands = [("h264_qsv", ["-global_quality", "22"]), ("h264_nvenc", ["-cq", "22"]), ("h264_amf", ["-qp_i", "22"])]
    if IS_MAC:
        cands = [("h264_videotoolbox", ["-q:v", "60"])]
    for enc, extra in cands:
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "t.mp4"
            r = subprocess.run([ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=1080x1920:r=60:d=0.5",
                                "-vf", "format=nv12", "-c:v", enc] + extra + [str(out)], capture_output=True)
            if r.returncode == 0 and out.exists() and out.stat().st_size > 1000:
                p = subprocess.run([ffmpeg, "-v", "error", "-i", str(out), "-f", "null", "-"], capture_output=True)
                if p.returncode == 0:
                    return enc
    return None


def _gpu():
    try:
        if IS_WIN:
            r = subprocess.run(["powershell", "-NoProfile", "-Command", "(Get-CimInstance Win32_VideoController).Name"],
                               capture_output=True, text=True, timeout=20)
            return [x.strip() for x in r.stdout.splitlines() if x.strip()]
        if IS_MAC:
            r = subprocess.run(["system_profiler", "SPDisplaysDataType"], capture_output=True, text=True, timeout=30)
            return [x.split(":", 1)[1].strip() for x in r.stdout.splitlines() if "Chipset Model" in x]
    except Exception:
        pass
    return []


def _ram_gb():
    try:
        if IS_WIN:
            import ctypes

            class MS(ctypes.Structure):
                _fields_ = [("l", ctypes.c_ulong), ("load", ctypes.c_ulong), ("total", ctypes.c_ulonglong),
                            ("avail", ctypes.c_ulonglong), ("tp", ctypes.c_ulonglong), ("ap", ctypes.c_ulonglong),
                            ("tv", ctypes.c_ulonglong), ("av", ctypes.c_ulonglong), ("ae", ctypes.c_ulonglong)]
            m = MS()
            m.l = ctypes.sizeof(MS)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
            return round(m.total / 2**30, 1)
        if IS_MAC:
            r = subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True)
            return round(int(r.stdout.strip()) / 2**30, 1)
    except Exception:
        pass
    return None


def _resolve_installed():
    if IS_MAC:
        return Path("/Applications/DaVinci Resolve/DaVinci Resolve.app").exists()
    exe = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Blackmagic Design" / "DaVinci Resolve" / "Resolve.exe"
    return exe.exists()


def test_cookies(ytdlp, browser):
    """True if yt-dlp can read `browser`'s cookies here (checked against a public video, nothing downloaded)."""
    r = subprocess.run([ytdlp, "--no-warnings", "--cookies-from-browser", browser, "--skip-download",
                        "--print", "id", "https://www.youtube.com/watch?v=jNQXAC9IVRw"],
                       capture_output=True, text=True, errors="replace", timeout=180)
    return r.returncode == 0 and "jNQXAC9IVRw" in r.stdout, (r.stderr or "").strip().splitlines()[-1:] or [""]


def run_doctor(cookies=None):
    prev = load_json(CONFIG / "machine.json", {})
    tools = {
        "ffmpeg": _find("ffmpeg", ["Gyan.FFmpeg*/*/bin/ffmpeg.exe"]),
        "ffprobe": _find("ffprobe", ["Gyan.FFmpeg*/*/bin/ffprobe.exe"]),
        "ytdlp": str(YTDLP_LOCAL) if YTDLP_LOCAL.exists() else _find("yt-dlp"),
        "deno": _find("deno", ["DenoLand.Deno*/deno.exe"]),
        "uv": _find("uv", ["astral-sh.uv*/uv.exe"]),
        "cloudflared": _find("cloudflared", ["Cloudflare.cloudflared*/cloudflared.exe"]),   # Instagram Login posting
    }
    missing = [k for k in ("ffmpeg", "ffprobe", "ytdlp", "deno") if not tools[k]]
    feats = _ffmpeg_features(tools["ffmpeg"]) if tools["ffmpeg"] else {}
    du = shutil.disk_usage(ROOT)
    ytv = None
    if tools["ytdlp"]:
        ytv = subprocess.run([tools["ytdlp"], "--version"], capture_output=True, text=True).stdout.strip()

    cookie_browser = prev.get("cookies_from_browser")
    if cookies:
        if cookies.lower() == "none":
            cookie_browser = None
        else:
            if not tools["ytdlp"]:
                raise ClipError("yt-dlp missing: run `clip setup` first")
            ok, err = test_cookies(tools["ytdlp"], cookies.lower())
            if not ok:
                raise ClipError(f"yt-dlp can't read {cookies} cookies: {err[0]}  (close that browser fully and try "
                                f"again, or pick another browser you are signed in to YouTube with)")
            cookie_browser = cookies.lower()
            print(f"cookies: {cookie_browser} works")

    resolve = _resolve_installed()
    models = {
        "yunet": str(ASSETS / "models" / "yunet_2023mar.onnx"),
        "panns_sed": str(ASSETS / "models" / "Cnn14_DecisionLevelMax_mAP=0.385.pth"),
        "panns_labels": str(Path.home() / "panns_data" / "class_labels_indices.csv"),
        "whisper": prev.get("models", {}).get("whisper", "large-v3-turbo"),
        "whisper_clip": prev.get("models", {}).get("whisper_clip", "large-v3-turbo"),
    }
    fonts = sorted(p.name for p in (ASSETS / "fonts").glob("*.ttf"))
    m = {
        "os": f"{platform.system()} {platform.release()} ({platform.version()})",
        "platform": "mac" if IS_MAC else ("windows" if IS_WIN else sys.platform),
        "arch": platform.machine(),
        "cpu_threads": os.cpu_count(),
        "ram_gb": _ram_gb(),
        "gpu": _gpu(),
        "disk_free_gb": round(du.free / 2**30, 1),
        "tools": tools,
        "ytdlp_version": ytv,
        "ffmpeg_features": feats,
        "models": models,
        "fonts": fonts,
        "asr_device": "cpu",   # faster-whisper int8 on CPU everywhere: same transcripts on every machine
        "editor": prev.get("editor") or {
            "app": "DaVinci Resolve" if resolve else None,
            "level": 3 if resolve else 1,
            "connector": "official MCP (run_script / run_script_unsafe) if present in the Claude session",
            "project": "GTA6 Clipper",
            "render_codec": ["mov", "ProRes422HQ"],
            "notes": "Finished MP4s are always encoded by ffmpeg (`clip render`); Resolve receives editable timelines.",
        },
        "cookies_from_browser": cookie_browser,
        "hw_h264": _hw_h264(tools["ffmpeg"]) if tools["ffmpeg"] else None,
    }
    save_json(CONFIG / "machine.json", m)

    problems = list(f"{k} missing" for k in missing)
    for k in ("libx264", "qtrle", "aac", "ebur128", "alimiter", "acompressor"):
        if feats and not feats.get(k):
            problems.append(f"ffmpeg lacks {k}")
    for k in ("yunet", "panns_sed", "panns_labels"):
        if not Path(models[k]).exists():
            problems.append(f"model missing: {k} (run `clip setup`)")
    for f in ("Montserrat-Black.ttf", "NotoColorEmoji.ttf"):
        if f not in fonts:
            problems.append(f"font missing: {f}")
    if not (ASSETS / "sfx" / "index.json").exists():
        problems.append("sfx library not indexed (run `clip sfx`)")
    if m["disk_free_gb"] < 40:
        print(f"WARNING: only {m['disk_free_gb']} GB free. A 1080p60 stream needs ~2.4 GB per hour plus renders: "
              f"prefer streams under 4 h and turn on purge_after_post.")
    print(f"machine: {m['os']} | {m['cpu_threads']} threads | {m['ram_gb'] or '?'} GB RAM | "
          f"GPU {', '.join(m['gpu']) or '?'} | {m['disk_free_gb']} GB free")
    for k, v in tools.items():
        print(f"  {k:11s} {v or 'MISSING'}")
    print(f"  yt-dlp {ytv} | cookies from: {m['cookies_from_browser'] or 'none'} | hw encoder: {m['hw_h264'] or 'none (x264)'}")
    print(f"  editor: {m['editor']['app'] or 'none (clips still render; no Resolve timelines)'} "
          f"-> project '{m['editor']['project']}'")
    if problems:
        print("PROBLEMS: " + "; ".join(problems))
        return 1
    print("doctor: OK (config/machine.json written)")
    return 0
