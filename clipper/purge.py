"""`clip purge <slug>`: delete one project's media and its DaVinci Resolve items once its clips are published.

Removes, in this order:
  1. Resolve (project "GTA6 Clipper" only, and only when it is already the open project: never switches the
     user's project behind their back): the timelines recorded in projects/<slug>/resolve.json and the bin
     Clipper/<slug> with every clip in it. Media is released by Resolve before the files go.
  2. The folder projects/<slug> (downloaded stream, analysis, renders, exports, Instagram copies).
The Instagram queue entries stay: they are the record of what was posted (permalinks) and stop re-posting.
Returns a dict {"resolve": "done|skipped: why", "files": "done|partial: why"}.
"""
from __future__ import annotations

import os
import shutil
import sys
import time
from pathlib import Path

from .util import PROJECTS, ClipError, load_json, machine

if sys.platform == "darwin":
    RESOLVE_API = "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting"
    RESOLVE_LIB = "/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/fusionscript.so"
else:
    RESOLVE_API = os.path.join(os.environ.get("PROGRAMDATA", r"C:\ProgramData"),
                               r"Blackmagic Design\DaVinci Resolve\Support\Developer\Scripting")
    RESOLVE_LIB = os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"),
                               r"Blackmagic Design\DaVinci Resolve\fusionscript.dll")


def _resolve():
    os.environ.setdefault("RESOLVE_SCRIPT_API", RESOLVE_API)
    os.environ.setdefault("RESOLVE_SCRIPT_LIB", RESOLVE_LIB)
    mod = os.path.join(os.environ["RESOLVE_SCRIPT_API"], "Modules")
    if mod not in sys.path:
        sys.path.append(mod)
    try:
        import DaVinciResolveScript as d   # noqa: N813
        return d.scriptapp("Resolve")
    except Exception:
        return None


def resolve_items(slug, rj):
    """Delete the Resolve timelines + bin recorded in a project's resolve.json dict."""
    if not rj:
        return "done (nothing was built in Resolve)"
    want = rj.get("project") or (machine().get("editor") or {}).get("project", "GTA6 Clipper")
    r = _resolve()
    if r is None:
        return "skipped: Resolve is not running"
    proj = r.GetProjectManager().GetCurrentProject()
    if not proj or proj.GetName() != want:
        return f"skipped: open project is not '{want}'"
    mp = proj.GetMediaPool()
    names = set((rj.get("timelines") or {}).values())
    tls = []
    for i in range(1, (proj.GetTimelineCount() or 0) + 1):
        tl = proj.GetTimelineByIndex(i)
        if tl and tl.GetName() in names:
            tls.append(tl)
    if tls and not mp.DeleteTimelines(tls):
        return "skipped: Resolve refused to delete the timelines"
    top = next((f for f in mp.GetRootFolder().GetSubFolderList() or [] if f.GetName() == "Clipper"), None)
    sb = next((f for f in (top.GetSubFolderList() if top else None) or [] if f.GetName() == slug[:40]), None)
    if sb and not mp.DeleteFolders([sb]):
        return "skipped: Resolve refused to delete the bin"
    r.GetProjectManager().SaveProject()
    return f"done ({len(tls)} timelines, bin {'removed' if sb else 'not found'})"


def purge(slug, resolve=True):
    """Returns {"resolve": ..., "files": ..., "resolve_json": <dict, kept so a skipped Resolve step can be
    retried with resolve_items() after the folder is gone>}."""
    if not slug or slug.startswith("_") or "/" in slug or "\\" in slug or slug in (".", ".."):
        raise ClipError(f"refusing to purge '{slug}'")
    pdir = (PROJECTS / slug).resolve()
    if pdir.parent != PROJECTS.resolve():
        raise ClipError(f"refusing to purge outside projects/: {pdir}")
    out = {"resolve": "skipped: not asked", "files": "done (already gone)", "resolve_json": {}}
    if not pdir.exists():
        return out
    out["resolve_json"] = load_json(pdir / "resolve.json", {})
    if resolve:
        try:
            out["resolve"] = resolve_items(slug, out["resolve_json"])
        except Exception as e:   # Resolve scripting errors must not stop the file cleanup
            out["resolve"] = f"skipped: {e}"
    errs = []
    for attempt in range(3):
        errs = []
        shutil.rmtree(pdir, onerror=lambda fn, p, ei: errs.append(f"{Path(p).name}: {ei[0].__name__}"))
        if not pdir.exists():
            break
        time.sleep(2)
    out["files"] = "done" if not pdir.exists() else f"partial: {len(errs)} files still in use ({', '.join(errs[:3])})"
    return out


SLIM_GLOBS = ["source/video_raw.*", "source/video_proxy*.mp4", "source/video.*", "source/audio.*",
              "analysis/features.npz"]


def slim(slug):
    """Free most of a project's disk space once its clips are rendered, QC-passed and queued: deletes the downloaded
    stream (video + audio) and the scan features, keeps plan, analysis text, renders, exports and Instagram copies.
    Re-rendering after this needs `clip fetch` again. Files Resolve holds open are left for `purge`."""
    pdir = (PROJECTS / slug).resolve()
    if not slug or slug.startswith("_") or pdir.parent != PROJECTS.resolve() or not pdir.exists():
        raise ClipError(f"no project '{slug}'")
    freed, kept = 0, []
    for g in SLIM_GLOBS:
        for f in pdir.glob(g):
            if not f.is_file():
                continue
            size = f.stat().st_size
            try:
                f.unlink()
                freed += size
            except OSError:
                kept.append(f.name)
    msg = f"{slug}: freed {freed / 1e9:.1f} GB"
    if kept:
        msg += f"; still in use (Resolve?): {', '.join(kept[:4])}"
    print(msg)
    return freed
