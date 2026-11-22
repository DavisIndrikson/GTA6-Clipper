"""DaVinci Resolve builder. Standard library only: it runs INSIDE Resolve's Python through the
official connector (run_script_unsafe), where `resolve` is injected. Usage from a connector script:

    import sys; sys.path.insert(0, r"<tool folder>")
    from clipper import resolve_build as rb
    result = rb.ensure(resolve, r"<tool folder>", "<slug>")          # project + bins + source import
    result = rb.build_selects(resolve, r"<tool folder>", "<slug>")
    result = rb.build_clip(resolve, r"<tool folder>", "<slug>", "C01")  # one clip per call (10 s timeout!)

Rules: work only in the tool's own Resolve project, only create (never delete or alter) user items,
reuse media already in the bin, names never contain ':'.
"""
import json
import math
import os
import re

TOOL_TAG = "clipper"


def _j(path, default=None):
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _w(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def _safe(txt, n=60):
    txt = "".join(ch for ch in (txt or "") if ord(ch) < 0x2000)
    txt = re.sub(r'[:/\\?*"<>|]+', " ", txt)
    return re.sub(r"\s+", " ", txt).strip()[:n].strip()


def _fps_str(fps):
    v = fps[0] / fps[1]
    for s in ("23.976", "24", "25", "29.97", "30", "47.952", "48", "50", "59.94", "60"):
        if abs(float(s) - v) < 0.01:
            return s
    return str(round(v, 3))


def _project(resolve, root):
    m = _j(os.path.join(root, "config", "machine.json"), {})
    name = (m.get("editor") or {}).get("project", "GTA6 Clipper")
    pm = resolve.GetProjectManager()
    cur = pm.GetCurrentProject()
    if cur and cur.GetName() == name:
        return cur, name, False, None
    prev = cur.GetName() if cur else None
    if cur:
        pm.SaveProject()
    p = pm.LoadProject(name)
    created = False
    if not p:
        p = pm.CreateProject(name)
        created = True
        if not p:
            raise RuntimeError("could not create Resolve project " + name)
    return p, name, created, prev


def _folder(mp, parent, name):
    for f in parent.GetSubFolderList() or []:
        if f.GetName() == name:
            return f
    return mp.AddSubFolder(parent, name)


def _find_or_import(mp, folder, path):
    path_n = os.path.normcase(os.path.abspath(path))
    for it in folder.GetClipList() or []:
        fp = it.GetClipProperty("File Path") or ""
        if fp and os.path.normcase(os.path.abspath(fp)) == path_n:
            return it, False
    mp.SetCurrentFolder(folder)
    items = mp.ImportMedia([path]) or []
    if not items:
        raise RuntimeError("Resolve could not import " + path)
    return items[0], True


def _timeline_by_name(project, name):
    for i in range(1, project.GetTimelineCount() + 1):
        t = project.GetTimelineByIndex(i)
        if t and t.GetName() == name:
            return t
    return None


def _unique_name(project, base):
    if not _timeline_by_name(project, base):
        return base
    k = 2
    while _timeline_by_name(project, f"{base} v{k}"):
        k += 1
    return f"{base} v{k}"


def ensure(resolve, root, slug):
    """Open/create the tool's project, make bins, import the source stream."""
    pdir = os.path.join(root, "projects", slug)
    pj = _j(os.path.join(pdir, "project.json"))
    p, name, created, prev = _project(resolve, root)
    if created:
        p.SetSetting("timelineFrameRate", _fps_str(pj["video"]["fps"]))
        p.SetSetting("timelineResolutionWidth", "1920")
        p.SetSetting("timelineResolutionHeight", "1080")
    mp = p.GetMediaPool()
    top = _folder(mp, mp.GetRootFolder(), "Clipper")
    sb = _folder(mp, top, _safe(slug, 40))
    src_bin = _folder(mp, sb, "Source")
    item, imported = _find_or_import(mp, src_bin, pj["files"]["video"])
    st = _j(os.path.join(pdir, "resolve.json"), {}) or {}
    st.update({"project": name, "source_bin": f"Clipper/{_safe(slug, 40)}/Source"})
    st.setdefault("timelines", {})
    _w(os.path.join(pdir, "resolve.json"), st)
    return {"project": name, "created_project": created, "switched_from": prev, "source_imported": imported,
            "source_fps": item.GetClipProperty("FPS"), "source_frames": item.GetClipProperty("Frames")}


def build_selects(resolve, root, slug):
    """16:9 timeline with every planned clip's source range in order, with a marker per clip."""
    pdir = os.path.join(root, "projects", slug)
    pj = _j(os.path.join(pdir, "project.json"))
    plan = _j(os.path.join(pdir, "plan", "clips.json"))["clips"]
    p, *_ = _project(resolve, root)
    mp = p.GetMediaPool()
    sb = _folder(mp, _folder(mp, mp.GetRootFolder(), "Clipper"), _safe(slug, 40))
    item, _ = _find_or_import(mp, _folder(mp, sb, "Source"), pj["files"]["video"])
    fps = pj["video"]["fps"]
    fr = fps[0] / fps[1]
    st0 = _j(os.path.join(pdir, "resolve.json"), {}) or {}
    old_name = (st0.get("timelines") or {}).get("selects")
    replaced = kept = None
    if old_name:
        old = _timeline_by_name(p, old_name)
        old_sig = (st0.get("signatures") or {}).get("selects")
        if old is not None:
            if old_sig is not None and _signature(old) == old_sig and mp.DeleteTimelines([old]):
                replaced = old_name
            else:
                kept = old_name
    mp.SetCurrentFolder(sb)
    name = _unique_name(p, _safe(f"{pj.get('channel') or slug} Selects", 60))
    tl = mp.CreateEmptyTimeline(name)
    if not tl:
        raise RuntimeError("CreateEmptyTimeline failed for " + name)
    p.SetCurrentTimeline(tl)
    tl.SetSettings({"useCustomSettings": "1", "timelineFrameRate": _fps_str(fps),
                    "timelineResolutionWidth": "1920", "timelineResolutionHeight": "1080"})
    start = tl.GetStartFrame()
    rec = 0
    infos, marks = [], []
    for c in sorted(plan, key=lambda c: _t(c["in"])):
        a, b = int(round(_t(c["in"]) * fr)), int(round(_t(c["out"]) * fr))
        infos.append({"mediaPoolItem": item, "startFrame": a, "endFrame": b, "recordFrame": start + rec})
        marks.append((rec, c, b - a))
        rec += b - a
    items = mp.AppendToTimeline(infos) or []
    colors = ["Yellow", "Cyan", "Pink", "Green", "Lavender", "Sky"]
    for i, (off, c, n) in enumerate(marks):
        tl.AddMarker(off, colors[i % len(colors)], _safe(f"{c['id']} {c.get('title', '')}", 80),
                     (c.get("hook") or "") + " | " + (c.get("why") or ""), n, f"{TOOL_TAG}:{c['id']}")
    durs = [it.GetDuration() for it in items if it.GetTrackTypeAndIndex()[0] == "video"]
    want = [n for _, _, n in marks]
    st = _j(os.path.join(pdir, "resolve.json"), {}) or {}
    st.setdefault("timelines", {})["selects"] = name
    st.setdefault("signatures", {})["selects"] = _signature(tl)
    _w(os.path.join(pdir, "resolve.json"), st)
    return {"timeline": name, "clips": len(marks), "video_items": len(durs),
            "durations_match": durs == want, "end_frame": tl.GetEndFrame() - start, "expected_end": rec,
            "replaced_unedited": replaced, "kept_user_edited": kept}


def _t(v):
    if isinstance(v, (int, float)):
        return float(v)
    x = 0.0
    for part in str(v).split(":"):
        x = x * 60 + float(part)
    return x


def _signature(tl):
    st = tl.GetStartFrame()
    sig = []
    for kind in ("video", "audio"):
        for t in range(1, tl.GetTrackCount(kind) + 1):
            for it in tl.GetItemListInTrack(kind, t) or []:
                sig.append([kind, t, it.GetName(), it.GetStart() - st, it.GetDuration()])
    return sorted(sig)


def build_clip(resolve, root, slug, cid, replace=True):
    """Vertical timeline for one rendered clip: V1 picture, V2 captions, V3+ hook/pops, A1 audio, A2+ SFX.
    replace=True deletes this tool's previous timeline for the clip ONLY if a read-back shows it is exactly as
    the tool built it (the user has not edited it); otherwise the old one is kept and a 'vN' name is used."""
    pdir = os.path.join(root, "projects", slug)
    cdir = os.path.join(pdir, "clips", cid)
    man = _j(os.path.join(cdir, "render.json"))
    if not man or man.get("draft"):
        raise RuntimeError(cid + " is not rendered (run clip render)")
    p, *_ = _project(resolve, root)
    mp = p.GetMediaPool()
    st0 = _j(os.path.join(pdir, "resolve.json"), {}) or {}
    replaced, kept_edited = None, None
    old_name = (st0.get("timelines") or {}).get(cid)
    old_sig = (st0.get("signatures") or {}).get(cid)
    if replace and old_name:
        old = _timeline_by_name(p, old_name)
        if old is not None:
            if old_sig is not None and _signature(old) == old_sig:
                if mp.DeleteTimelines([old]):
                    replaced = old_name
            else:
                kept_edited = old_name
    sb = _folder(mp, _folder(mp, mp.GetRootFolder(), "Clipper"), _safe(slug, 40))
    cb = _folder(mp, sb, _safe(f"{cid} {man.get('title') or ''}", 50))
    f = man["files"]
    imp = lambda rel: _find_or_import(mp, cb, os.path.join(cdir, rel))[0]
    base = imp(f["base"])
    caps = imp(f["captions"]) if f.get("captions") else None
    prog = imp(f["program"])
    gfx = {k: imp(v["file"]) for k, v in man.get("gfx", {}).items()}
    sfx = [(s, imp(s["file"])) for s in man.get("sfx", []) if s.get("file")]
    for it in [caps] + list(gfx.values()):
        if it is not None:
            it.SetClipProperty("Data Level", "Full")
    mp.SetCurrentFolder(cb)
    name = _unique_name(p, _safe(f"{cid} {man.get('title') or ''}", 60))
    tl = mp.CreateEmptyTimeline(name)
    if not tl:
        raise RuntimeError("CreateEmptyTimeline failed for " + name)
    p.SetCurrentTimeline(tl)
    W, H = man["size"]
    tl.SetSettings({"useCustomSettings": "1", "timelineFrameRate": _fps_str(man["fps"]),
                    "timelineResolutionWidth": str(W), "timelineResolutionHeight": str(H),
                    "timelineInputResMismatchBehavior": "scaleToFit"})
    start = tl.GetStartFrame()
    N = man["frames"]
    fr = man["fps"][0] / man["fps"][1]
    # tracks: V1 picture, V2 captions, V3.. graphics ; A1 clip audio, A2.. sfx
    v_end = {}   # track -> last end frame
    a_end = {}

    def vtrack(k0, n, first=3):
        t = first
        while v_end.get(t, -1) > k0:
            t += 1
        while tl.GetTrackCount("video") < t:
            tl.AddTrack("video")
        v_end[t] = k0 + n
        return t

    def atrack(k0, n, first=2):
        t = first
        while a_end.get(t, -1) > k0:
            t += 1
        while tl.GetTrackCount("audio") < t:
            tl.AddTrack("audio", "stereo")
        a_end[t] = k0 + n
        return t

    while tl.GetTrackCount("video") < 2:
        tl.AddTrack("video")
    infos = [{"mediaPoolItem": base, "recordFrame": start, "trackIndex": 1, "mediaType": 1},
             {"mediaPoolItem": prog, "recordFrame": start, "trackIndex": 1, "mediaType": 2}]
    if caps is not None:
        infos.append({"mediaPoolItem": caps, "recordFrame": start, "trackIndex": 2, "mediaType": 1})
    for k, v in man.get("gfx", {}).items():
        infos.append({"mediaPoolItem": gfx[k], "recordFrame": start + v["k0"], "trackIndex": vtrack(v["k0"], v["n"]),
                      "mediaType": 1})
    for s, it in sfx:
        n_fr = int(math.ceil(s["n"] / 48000 * fr))
        infos.append({"mediaPoolItem": it, "recordFrame": start + s["k0"], "trackIndex": atrack(s["k0"], n_fr),
                      "mediaType": 2})
    items = mp.AppendToTimeline(infos) or []
    names = {1: "Picture", 2: "Captions"}
    for t in range(1, tl.GetTrackCount("video") + 1):
        tl.SetTrackName("video", t, names.get(t, "Graphics" if t == 3 else f"Graphics {t - 2}"))
    for t in range(1, tl.GetTrackCount("audio") + 1):
        tl.SetTrackName("audio", t, "Clip Audio" if t == 1 else ("SFX" if t == 2 else f"SFX {t - 1}"))
    tl.AddMarker(0, "Yellow", "HOOK", man.get("hook") or "", min(N, int(3 * fr)), f"{TOOL_TAG}:{cid}:hook")
    report = {"timeline": name, "items_appended": len(items), "items_expected": len(infos),
              "end_frame": tl.GetEndFrame() - start, "expected_frames": N,
              "video_tracks": tl.GetTrackCount("video"), "audio_tracks": tl.GetTrackCount("audio")}
    v1 = tl.GetItemListInTrack("video", 1) or []
    report["picture_duration_ok"] = bool(v1) and v1[0].GetDuration() == N
    report["replaced_unedited"] = replaced
    report["kept_user_edited"] = kept_edited
    st = _j(os.path.join(pdir, "resolve.json"), {}) or {}
    st.setdefault("timelines", {})[cid] = name
    st.setdefault("signatures", {})[cid] = _signature(tl)
    _w(os.path.join(pdir, "resolve.json"), st)
    return report
