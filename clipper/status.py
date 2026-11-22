"""`clip status`: one-screen summary of one or all projects."""
from __future__ import annotations

from .util import PROJECTS, fmt_t, load_json


def status(slug=None):
    dirs = [PROJECTS / slug] if slug else sorted(p for p in PROJECTS.glob("*") if (p / "project.json").exists())
    if not dirs:
        print("no projects yet: `clip search`, then `clip fetch <url>`")
    for d in dirs:
        pj = load_json(d / "project.json", {})
        f = pj.get("files", {})
        cands = load_json(d / "analysis" / "candidates.json", {}).get("candidates", [])
        words = load_json(d / "analysis" / "words.json", {}).get("words", [])
        plan = load_json(d / "plan" / "clips.json", {}).get("clips", [])
        rendered = [c for c in plan if (d / "clips" / c["id"] / "render.json").exists()]
        qc_ok = [c for c in plan if load_json(d / "clips" / c["id"] / "qc.json", {}).get("ok")]
        rv = load_json(d / "resolve.json", {})
        print(f"{d.name}: '{pj.get('title')}' ({pj.get('channel')}, {fmt_t(pj.get('duration') or 0, 0)})")
        print(f"  files: audio {'yes' if f.get('audio') else 'no'}, video {'yes' if f.get('video') else 'no'}, "
              f"chat {'yes' if f.get('chat') else 'no'}, captions {'yes' if f.get('captions') else 'no'}")
        print(f"  scan: {len(cands)} candidates, {len(words)} words transcribed | plan: {len(plan)} clips, "
              f"{len(rendered)} rendered, {len(qc_ok)} passed QC | Resolve: {len(rv.get('timelines', {}))} timelines")
