"""Compact text views for planning. Never dump whole JSON files into the conversation."""
from __future__ import annotations

import numpy as np

from .util import fmt_t, load_json, parse_t, project_dir


def _marks(pdir):
    try:
        f = np.load(pdir / "analysis" / "features.npz")
    except FileNotFoundError:
        return None
    # thresholds relative to this stream (raw detector probabilities are small in mixed audio)
    lt = max(float(np.percentile(f["laugh"], 98.5)), 0.003)
    yt = max(float(np.percentile(f["scream"], 98.5)), 0.003)
    return f["t"], float(f["hop"]), f["laugh"] / lt, f["scream"] / yt


def lines(words, marks=None, per_line=14):
    out, cur = [], []
    for w in words:
        cur.append(w)
        end_sent = w["w"][-1:] in ".?!" and len(cur) >= 6
        if len(cur) >= per_line or end_sent:
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    txt = []
    for ln in out:
        toks = []
        for w in ln:
            tag = ""
            if marks is not None:
                t, hop, laugh, scream = marks
                k = min(len(t) - 1, max(0, int(w["s"] / hop)))
                lo, hi = max(0, k - 2), k + 3
                if laugh[lo:hi].max() > 1.0:
                    tag = "(LAUGH)"
                elif scream[lo:hi].max() > 1.0:
                    tag = "(YELL)"
            toks.append(w["w"] + tag)
        txt.append(f"  {fmt_t(ln[0]['s'])} [{ln[0].get('i', '')}] " + " ".join(toks))
    return txt


def moments(slug, ids=None, top=None, order="time"):
    pdir = project_dir(slug)
    cands = load_json(pdir / "analysis" / "candidates.json")["candidates"]
    words = load_json(pdir / "analysis" / "words.json", {"words": []})["words"]
    marks = _marks(pdir)
    if ids:
        cands = [c for c in cands if c["id"] in ids]
    if top:
        cands = sorted(cands, key=lambda c: c["rank"])[:top]
    if order == "time":
        cands = sorted(cands, key=lambda c: c["peak"])
    for c in cands:
        s = c["signals"]
        sig = " ".join(f"{k} {v:.1f}" for k, v in s.items() if v >= 0.15)
        print(f"{c['id']} #{c['rank']} {fmt_t(c['win'][0])}-{fmt_t(c['win'][1])} peak {fmt_t(c['peak'])} "
              f"score {c['score']:.2f} [{sig}]")
        if c.get("chat"):
            print("  chat: " + " | ".join(c["chat"]))
        ws = [w for w in words if c["win"][0] <= w["s"] < c["win"][1]]
        if ws:
            print("\n".join(lines(ws, marks)))
        elif c.get("yt_caption"):
            print("  (yt captions) " + c["yt_caption"][:400])
        else:
            print("  (no transcript yet: run `clip asr`)")


def words_range(slug, a, b):
    pdir = project_dir(slug)
    words = load_json(pdir / "analysis" / "words.json", {"words": []})["words"]
    a, b = parse_t(a), parse_t(b)
    ws = [w for w in words if a <= w["s"] < b]
    print("\n".join(lines(ws, _marks(pdir), per_line=12)) or "(no words in range)")
