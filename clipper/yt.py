"""YouTube search and download through the pinned yt-dlp (`clip setup` downloads it).

`clip search`   finds long-form (VOD) streams for the configured streamers.
`clip fetch`    downloads metadata, captions, live-chat replay, audio and video
                into projects/<slug>/source/ and writes project.json.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from .util import (CONFIG, ClipError, fmt_t, load_json, machine, project_dir, run, popen, save_json,
                   slugify, tool, update_project, video_info)

STREAMERS_DEFAULT = {
    "streamers": ["IShowSpeed", "Kai Cenat", "N3ON", "xQc", "Adin Ross", "Jynxzi", "Kwebbelkop", "CaseOh",
                  "Duke Dennis", "Agent 00"],
    "game_terms": ["gta 6", "gta vi", "gta6", "grand theft auto vi", "grand theft auto 6"],
    "query_suffix": "GTA 6 stream",
    "featured": ["IShowSpeed", "Speed", "Kai Cenat"],
    "extra_queries": ["IShowSpeed Kai Cenat GTA 6", "Kai Cenat GTA 6 stream with Speed"],
}

VIDEO_FORMAT = "bv*[vcodec^=avc1][height<=1080][fps<=60]+ba[ext=m4a]/bv*[height<=1080]+ba/b"
AUDIO_FORMAT = "bestaudio[ext=m4a]/bestaudio"
VIDEO_ONLY_FORMAT = "bv*[vcodec^=avc1][height<=1080][fps<=60][ext=mp4]/bv*[height<=1080][ext=mp4]/bv*[height<=1080]"


def _base_args():
    m = machine()
    args = [tool("ytdlp"), "--no-warnings", "--ffmpeg-location", str(Path(tool("ffmpeg")).parent)]
    # CLIP_NO_COOKIES=1 skips browser cookies (Chromium browsers lock their cookie DB while they are open;
    # fine for VODs that are not age-restricted).
    if m.get("cookies_from_browser") and not os.environ.get("CLIP_NO_COOKIES"):
        args += ["--cookies-from-browser", m["cookies_from_browser"]]
    return args


def _video_id(url_or_id: str) -> str:
    m = re.search(r"(?:v=|youtu\.be/|/live/|/shorts/)([A-Za-z0-9_-]{11})", url_or_id)
    if m:
        return m.group(1)
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", url_or_id):
        return url_or_id
    raise ClipError(f"not a YouTube video URL or id: {url_or_id}")


# ---------------------------------------------------------------- search
def done_ids():
    """YouTube ids already clipped here: project folders (<channel>_<id>) and Instagram queue keys (they outlive
    purged projects), so the autopilot never clips the same stream twice."""
    from .util import PROJECTS
    ids = set()
    for p in PROJECTS.glob("*_*"):
        if p.is_dir() and not p.name.startswith("_"):
            ids.add(p.name[-11:])
    q = load_json(PROJECTS / "_instagram" / "queue.json", {"posts": []})
    for e in q.get("posts", []):
        ids.add(e["key"].split("/")[0][-11:])
    ids |= set(load_json(PROJECTS / "_instagram" / "skipped.json", {}).get("ids", []))
    return ids


def skip(video_id, why=""):
    """Remember a VOD the autopilot looked at and rejected (nothing funny, wrong game, re-upload...)."""
    from .util import PROJECTS
    p = PROJECTS / "_instagram" / "skipped.json"
    d = load_json(p, {"ids": [], "why": {}})
    vid = _video_id(video_id)
    if vid not in d["ids"]:
        d["ids"].append(vid)
    d["why"][vid] = why
    save_json(p, d)
    print(f"{vid}: skipped from now on ({why or 'no reason given'})")


def search(query=None, n=20, min_minutes=30, include_live=False, include_done=False):
    cfg = {**STREAMERS_DEFAULT, **load_json(CONFIG / "streamers.json", {})}
    queries = [query] if query else ([f"{s} {cfg['query_suffix']}" for s in cfg["streamers"]] +
                                     list(cfg.get("extra_queries") or []))
    terms = cfg["game_terms"]
    streamers = [s.lower().replace(" ", "") for s in cfg["streamers"]]
    featured = [re.compile(r"\b" + re.escape(s.lower()) + r"\b") for s in cfg.get("featured") or []]
    done = done_ids()
    rows = {}
    for q in queries:
        r = run([tool("ytdlp"), "--flat-playlist", "--dump-json", "--no-warnings", f"ytsearch{n}:{q}"], check=False,
                timeout=180)
        for line in r.stdout.splitlines():
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            vid = e.get("id")
            if not vid or vid in rows:
                continue
            title = e.get("title") or ""
            chan = e.get("channel") or e.get("uploader") or ""
            dur = e.get("duration")
            live = e.get("live_status")
            game = any(t in title.lower() for t in terms)
            official = any(s and s in chan.lower().replace(" ", "") for s in streamers)
            both = (title + " " + chan).lower()
            feat = any(rx.search(both) for rx in featured)
            rows[vid] = {"id": vid, "url": f"https://www.youtube.com/watch?v={vid}", "title": title,
                         "channel": chan, "duration": dur, "views": e.get("view_count"), "live_status": live,
                         "game_match": game, "official": official, "featured": feat, "done": vid in done,
                         "query": q}
    out = []
    for r_ in rows.values():
        if r_["done"] and not include_done:
            continue
        if r_["live_status"] in ("is_live", "is_upcoming"):
            if include_live:
                out.append(r_)
            continue
        if not r_["duration"] or r_["duration"] < min_minutes * 60:
            continue
        out.append(r_)
    # GTA 6 first, then streams with a featured streamer (Speed / Kai Cenat) in them, then the streamer's own
    # channel, then views
    out.sort(key=lambda r_: (not r_["game_match"], not r_["featured"], not r_["official"], -(r_["views"] or 0)))
    save_json(CONFIG / "last_search.json", {"query": query, "results": out})
    live_now = [r_ for r_ in rows.values() if r_["live_status"] == "is_live" and r_["game_match"]]
    print(f"{len(out)} long-form results (>= {min_minutes} min)" + ("" if query else f" over {len(queries)} streamer queries"))
    for i, r_ in enumerate(out[:40], 1):
        flag = (("G" if r_["game_match"] else "-") + ("F" if r_["featured"] else "-") +
                ("O" if r_["official"] else "-") + ("D" if r_["done"] else ""))
        dur = fmt_t(r_["duration"], 0) if r_["duration"] else "LIVE"
        views = f"{r_['views']:,}" if r_["views"] else "?"
        print(f"{i:2d}. [{flag}] {dur:>8s} {views:>11s}  {r_['channel'][:22]:22s} {r_['title'][:70]}  {r_['id']}")
    if live_now and not include_live:
        print("still live (VOD not ready yet): " + "; ".join(f"{r_['channel']} ({r_['id']})" for r_ in live_now[:6]))
    hidden = sum(1 for r_ in rows.values() if r_["done"]) if not include_done else 0
    print(f"flags: G = GTA 6 in title, F = Speed/Kai Cenat featured, O = streamer's own channel, D = already clipped"
          f"{f' ({hidden} already-clipped hidden, --all shows them)' if hidden else ''}. Saved config/last_search.json")
    return out


# ---------------------------------------------------------------- fetch
def fetch(url_or_id, slug=None, meta=True, audio=True, subs=True, video=True, chat=True):
    vid = _video_id(url_or_id)
    url = f"https://www.youtube.com/watch?v={vid}"
    info = None
    if meta or slug is None:
        r = run(_base_args() + ["-J", "--no-playlist", url], check=False, timeout=300)
        if r.returncode != 0:
            err = (r.stderr or "").strip().splitlines()[-1:] or ["?"]
            raise ClipError(f"yt-dlp could not read {vid}: {err[0]}")
        info = json.loads(r.stdout)
        if info.get("live_status") in ("is_live", "is_upcoming"):
            raise ClipError(f"'{info.get('title')}' is still live. Run fetch again once the stream has ended.")
        slug = slug or f"{slugify(info.get('channel') or 'yt', 20)}_{vid}"
    pdir = project_dir(slug, create=True)
    src = pdir / "source"
    if info is not None:
        slim = {k: info.get(k) for k in ("id", "title", "channel", "channel_id", "uploader", "duration",
                                         "upload_date", "release_timestamp", "live_status", "was_live", "view_count",
                                         "age_limit", "heatmap", "chapters", "description", "webpage_url")}
        save_json(src / "info.json", slim)
        pj = load_json(pdir / "project.json", {})
        pj.update({"slug": slug, "id": vid, "url": url, "title": info.get("title"), "channel": info.get("channel"),
                   "duration": info.get("duration"), "live_status": info.get("live_status"),
                   "age_limit": info.get("age_limit")})
        pj.setdefault("files", {})
        save_json(pdir / "project.json", pj)
        hm = info.get("heatmap") or []
        print(f"{slug}: '{info.get('title')}' by {info.get('channel')}, {fmt_t(info.get('duration') or 0, 0)}, "
              f"heatmap {'yes' if hm else 'no'}, live_status {info.get('live_status')}")
        if info.get("live_status") == "post_live":
            print("note: YouTube is still processing this VOD (post_live); chat replay and captions may appear later.")
    pj = load_json(pdir / "project.json")

    if subs:
        langs = "en,en-orig,en.*" + (",live_chat" if chat else "")
        r = run(_base_args() + ["--skip-download", "--write-subs", "--write-auto-subs", "--sub-langs", langs,
                                "--sub-format", "json3/json/best", "-o", str(src / "subs.%(ext)s"), url],
                check=False, timeout=3600)
        found = sorted(p.name for p in src.glob("subs.*"))
        update_project(slug, "files", {
            "captions": next((str(src / f) for f in found if f.endswith(".json3") and ".en" in f), None),
            "chat": next((str(src / f) for f in found if "live_chat" in f), None)})
        print(f"subtitles: {', '.join(found) or 'none'}")

    if audio:
        r = run(_base_args() + ["-f", AUDIO_FORMAT, "--no-playlist", "-N", "16", "-o", str(src / "audio.%(ext)s"), url],
                check=False, timeout=7200)
        a = next(iter(sorted(src.glob("audio.*"))), None)
        if r.returncode != 0 or a is None:
            raise ClipError(f"audio download failed: {(r.stderr or '')[-400:]}")
        update_project(slug, "files", {"audio": str(a)})
        print(f"audio: {a.name} ({a.stat().st_size / 1e6:.0f} MB)")

    if video:
        # Video stream only (no merge): the audio is already in source/audio.*, and merging a finished live
        # stream's per-frame DASH fragments takes ffmpeg hours. Such a file gets a `clip proxy` instead.
        from .proxy import is_fragmented
        log = src / "fetch_video.log"
        # yt-dlp resumes video_raw.*.part blindly: pin the format of the interrupted download so a retry
        # can't append a different stream to it.
        fmt = VIDEO_ONLY_FORMAT
        if pj.get("files", {}).get("video_format") and any(src.glob("video_raw.*.part")):
            fmt = pj["files"]["video_format"]
            print(f"video: resuming {next(src.glob('video_raw.*.part')).name} with format {fmt}")
        with open(log, "w", encoding="utf-8") as lf:
            p = popen(_base_args() + ["-f", fmt, "--no-playlist", "-N", "16", "--newline",
                                      "-o", str(src / "video_raw.%(ext)s"), url], stdout=lf, stderr=lf)
            rc = p.wait()
        m = re.search(r"Downloading 1 format\(s\): (\S+)", log.read_text(encoding="utf-8", errors="replace"))
        if m:
            update_project(slug, "files", {"video_format": m.group(1)})
        v = next((x for x in sorted(src.glob("video_raw.*")) if not x.name.endswith((".part", ".ytdl"))), None)
        if rc != 0 or v is None:
            tail = log.read_text(encoding="utf-8", errors="replace")[-600:]
            if "rate-limited" in tail:
                raise ClipError(f"YouTube rate-limited this account: wait at least an hour, don't retry sooner (see {log})")
            raise ClipError(f"video download failed (see {log}); re-running fetch resumes the .part")
        update_project(slug, "files", {"video_raw": str(v)})
        if is_fragmented(v):
            print(f"video: {v.name} ({v.stat().st_size / 1e9:.2f} GB) is per-frame fragmented DASH (finished live "
                  f"stream): ffmpeg can't seek it fast. After `clip moments`, run `clip proxy {slug} --ids K..`")
            return slug
        cur = Path((load_json(project_dir(slug) / "project.json").get("files") or {}).get("video") or "")
        if cur.name.startswith("video_proxy") and cur.exists():
            # clips were already cut from a partial download through `clip proxy`: keep that proxy (2026-11-20: the
            # finishing fetch switched files.video to the slow raw file under two projects mid-edit)
            print(f"video: {v.name} done ({v.stat().st_size / 1e9:.2f} GB); files.video stays {cur.name} "
                  f"(re-run `clip proxy {slug}` for clips outside its windows)")
            return slug
        vi = video_info(v)
        update_project(slug, "files", {"video": str(v)})
        update_project(slug, "video", {"fps": [vi["fps"].numerator, vi["fps"].denominator], "width": vi["width"],
                                       "height": vi["height"], "duration": vi["duration"], "codec": vi["codec"],
                                       "frames": vi["frames"], "has_audio": False})
        print(f"video: {v.name} {vi['width']}x{vi['height']} {float(vi['fps']):.3f} fps {vi['codec']} "
              f"{fmt_t(vi['duration'], 0)} ({v.stat().st_size / 1e9:.2f} GB)")
    return slug
