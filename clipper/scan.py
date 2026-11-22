"""`clip scan`: find candidate funny moments in a long stream without watching it.

Signals, all per 0.5 s hop over the whole stream:
  laugh   PANNs sound-event detector: Laughter, Giggle, Belly laugh, Chuckle, Snicker
  scream  Screaming, Shout, Yell, Whoop, Battle cry, Children shouting
  loud    loudness above the rolling 2-minute median (sudden yelling)
  fx      game chaos: explosions, gunfire, sirens, crashes, tyre squeal
  chat    live-chat replay message rate + laugh tokens (LMAO, KEKW, skull emoji...), shifted back by the chat lag
  heat    YouTube "most replayed" heatmap, when the video has one
The weighted sum gives candidates; Claude then reads their transcripts and decides.
"""
from __future__ import annotations

import csv
import json
import math
import re
import time
from pathlib import Path

import numpy as np

from .util import (ClipError, fmt_t, load_json, machine, popen, project_dir, save_json, style, tool,
                   update_project)

LAUGH = ["Laughter", "Baby laughter", "Giggle", "Snicker", "Belly laugh", "Chuckle, chortle"]
SCREAM = ["Screaming", "Shout", "Yell", "Children shouting", "Battle cry", "Whoop", "Bellow"]
CRY = ["Crying, sobbing", "Whimper", "Wail, moan"]
FX = ["Explosion", "Gunshot, gunfire", "Machine gun", "Siren", "Police car (siren)", "Skidding", "Tire squeal",
      "Smash, crash", "Car alarm", "Vehicle horn, car horn, honking", "Boom", "Burst, pop"]
OTHER = ["Speech", "Music"]
GROUPS = {"laugh": LAUGH, "scream": SCREAM, "cry": CRY, "fx": FX, "speech": ["Speech"], "music": ["Music"]}

SR = 32000

LAUGH_WORDS = re.compile(r"\b(l+m+f*a+o+|lo+l+|lu+l+w?|kekw|omegalul|icant|ha(ha)+h?|xd+|dead+|crying|"
                         r"aint no way|no shot|skull)\b", re.I)
LAUGH_EMOJI = set("\U0001F480\U0001F602\U0001F923\U0001F62D☠")   # skull, tears of joy, rofl, sob, skull+bones


def _labels():
    path = machine()["models"]["panns_labels"]
    with open(path, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    return [r["display_name"] for r in rows]


def _load_sed():
    import torch
    from panns_inference.models import Cnn14_DecisionLevelMax
    torch.set_num_threads(max(1, (machine().get("cpu_threads") or 4)))
    model = Cnn14_DecisionLevelMax(sample_rate=SR, window_size=1024, hop_size=320, mel_bins=64, fmin=50, fmax=14000,
                                   classes_num=527, interpolate_mode="nearest")
    ck = torch.load(machine()["models"]["panns_sed"], map_location="cpu", weights_only=False)
    model.load_state_dict(ck["model"])
    model.eval()
    return model


def audio_features(slug, force=False):
    pdir = project_dir(slug)
    out = pdir / "analysis" / "features.npz"
    if out.exists() and not force:
        print(f"features exist: {out.name} (use --force to recompute)")
        return out
    pj = load_json(pdir / "project.json")
    src = pj["files"].get("audio") or pj["files"].get("video")
    if not src:
        raise ClipError("no audio yet: run `clip fetch` first")
    st = style()["scan"]
    hop = st["hop_s"]
    chunk = int(st["chunk_s"] * SR)
    names = _labels()
    idx = {g: [names.index(n) for n in lst if n in names] for g, lst in GROUPS.items()}

    import torch
    model = _load_sed()
    proc = popen([tool("ffmpeg"), "-v", "error", "-i", src, "-map", "0:a:0", "-ac", "1", "-ar", str(SR),
                  "-f", "f32le", "-"], stdout=-1)
    hop_n = int(hop * SR)
    rms, groups = [], {g: [] for g in GROUPS}
    t0 = time.time()
    done_s = 0.0
    total = float(pj.get("duration") or 0)
    prog = pdir / "analysis" / "scan_progress.txt"
    carry = np.zeros(0, np.float32)
    while True:
        buf = proc.stdout.read(chunk * 4)
        if not buf:
            break
        x = np.frombuffer(buf, np.float32)
        x = np.concatenate([carry, x])
        n_hops = len(x) // hop_n
        if n_hops == 0:
            carry = x
            continue
        use = x[: n_hops * hop_n]
        carry = x[n_hops * hop_n:]
        seg = use.reshape(n_hops, hop_n)
        rms.append(10 * np.log10(np.mean(seg.astype(np.float64) ** 2, axis=1) + 1e-10))
        with torch.no_grad():
            fw = model(torch.from_numpy(use[None, :].copy()), None)["framewise_output"][0].numpy()  # (T, 527) @100 Hz
        per = int(round(hop * 100))
        T = (fw.shape[0] // per) * per
        fw = fw[:T].reshape(-1, per, fw.shape[1]).max(axis=1)
        if fw.shape[0] < n_hops:   # pad the tail
            fw = np.vstack([fw, np.repeat(fw[-1:], n_hops - fw.shape[0], axis=0)])
        fw = fw[:n_hops]
        for g, ii in idx.items():
            groups[g].append(fw[:, ii].max(axis=1) if ii else np.zeros(n_hops, np.float32))
        done_s += n_hops * hop
        el = time.time() - t0
        prog.write_text(f"{fmt_t(done_s, 0)} / {fmt_t(total, 0)} audio scanned, {done_s / max(el, 1e-6):.0f}x realtime\n",
                        encoding="utf-8")
    proc.wait()
    rms = np.concatenate(rms).astype(np.float32)
    g = {k: np.concatenate(v).astype(np.float32) for k, v in groups.items()}
    t = (np.arange(len(rms)) + 0.5) * hop
    np.savez_compressed(out, t=t, rms_db=rms, hop=hop, **g)
    el = time.time() - t0
    speed = done_s / max(el, 1e-6)
    update_project(slug, "stats", {"scan_speed_x": round(speed, 1)})
    print(f"features: {len(t)} hops over {fmt_t(done_s, 0)} in {el:.0f} s ({speed:.0f}x realtime)")
    return out


# ---------------------------------------------------------------- chat / captions / heatmap
def chat_series(slug, t):
    pdir = project_dir(slug)
    pj = load_json(pdir / "project.json")
    path = pj["files"].get("chat")
    if not path or not Path(path).exists():
        return None, None, []
    hop = t[1] - t[0]
    msgs = np.zeros(len(t), np.float32)
    laugh = np.zeros(len(t), np.float32)
    texts = []
    seen = {}   # hop -> set of normalised texts: spam ("7 7 7", copypasta) counts once per hop
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            rep = d.get("replayChatItemAction") or {}
            off = rep.get("videoOffsetTimeMsec")
            if off is None:
                continue
            for act in rep.get("actions", []):
                item = (act.get("addChatItemAction") or {}).get("item") or {}
                r = item.get("liveChatTextMessageRenderer")
                if not r:
                    continue
                parts = []
                for run in (r.get("message") or {}).get("runs", []):
                    if "text" in run:
                        parts.append(run["text"])
                    elif "emoji" in run:
                        e = run["emoji"]
                        parts.append(e.get("emojiId") if len(e.get("emojiId", "")) <= 2 else
                                     (e.get("shortcuts") or [""])[0])
                txt = "".join(parts).strip()
                sec = int(off) / 1000.0
                k = int(sec / hop)
                if 0 <= k < len(t):
                    norm = re.sub(r"\s+", " ", txt.lower()).strip()[:40]
                    if sec - seen.get(norm, -99.0) < 10.0:
                        continue    # same text within 10 s = spam / copypasta
                    seen[norm] = sec
                    lw = len(LAUGH_WORDS.findall(txt)) + sum(ch in LAUGH_EMOJI for ch in txt)
                    if len(norm) > 2 or lw:
                        msgs[k] += 1
                    laugh[k] += min(lw, 3)
                    texts.append((sec, txt))
    return msgs, laugh, texts


def captions_words(slug):
    """YouTube auto-captions (json3) -> word list with start/end seconds."""
    pdir = project_dir(slug)
    pj = load_json(pdir / "project.json")
    path = pj["files"].get("captions")
    if not path or not Path(path).exists():
        return []
    d = load_json(path)
    words = []
    for ev in d.get("events", []):
        t0 = ev.get("tStartMs", 0) / 1000.0
        for s in ev.get("segs", []) or []:
            w = (s.get("utf8") or "").strip()
            if w:
                words.append({"w": w, "s": t0 + s.get("tOffsetMs", 0) / 1000.0})
    words.sort(key=lambda w: w["s"])
    for a, b in zip(words, words[1:]):
        a["e"] = min(b["s"], a["s"] + 1.2)
    if words:
        words[-1]["e"] = words[-1]["s"] + 0.5
    return words


def _norm(x, lo_pct=50, hi_pct=99.5, cap=1.5):
    lo, hi = np.percentile(x, lo_pct), np.percentile(x, hi_pct)
    return np.clip((x - lo) / (hi - lo + 1e-9), 0, cap)


def _smooth(x, n):
    if n <= 1:
        return x
    k = np.ones(n) / n
    return np.convolve(x, k, mode="same")


def _rolling_median(x, n):
    """Coarse rolling median (blockwise, then interpolated) to keep it fast on long streams."""
    step = max(1, n // 8)
    centers = np.arange(0, len(x), step)
    med = np.array([np.median(x[max(0, c - n // 2): c + n // 2 + 1]) for c in centers])
    return np.interp(np.arange(len(x)), centers, med)


def _movmax(x, n):
    from scipy.ndimage import maximum_filter1d
    return maximum_filter1d(x, size=max(1, n), mode="nearest")


def candidates(slug, k=None):
    pdir = project_dir(slug)
    f = np.load(pdir / "analysis" / "features.npz")
    t, hop = f["t"], float(f["hop"])
    st = style()["scan"]
    w = st["weights"]
    per_s = lambda s: max(1, int(round(s / hop)))

    comp = {}
    comp["laugh"] = _norm(_smooth(_movmax(f["laugh"], per_s(1.5)), per_s(3)))
    comp["scream"] = _norm(_smooth(_movmax(f["scream"], per_s(1.0)), per_s(2)))
    loud_rel = f["rms_db"] - _rolling_median(f["rms_db"], per_s(120))
    comp["loud"] = _norm(_smooth(_movmax(loud_rel, per_s(1.0)), per_s(2)))
    comp["fx"] = _norm(_smooth(_movmax(f["fx"], per_s(1.0)), per_s(3)))

    msgs, chat_laugh, chat_texts = chat_series(slug, t)
    if msgs is not None and msgs.sum() > 50:
        lag = per_s(st["chat_lag_s"])
        rate = _smooth(msgs, per_s(6))
        rate_rel = rate / (_rolling_median(rate, per_s(300)) + 0.5)
        cl = _smooth(chat_laugh, per_s(6))
        chat = 0.5 * _norm(rate_rel) + 0.8 * _norm(cl)
        comp["chat"] = np.concatenate([chat[lag:], np.zeros(lag)])
    else:
        comp["chat"] = np.zeros_like(t)

    info = load_json(pdir / "source" / "info.json", {})
    hm = info.get("heatmap") or []
    if hm:
        xs = np.array([(h["start_time"] + h["end_time"]) / 2 for h in hm])
        comp["heat"] = _norm(np.interp(t, xs, np.array([h["value"] for h in hm])), 40, 99)
    else:
        comp["heat"] = np.zeros_like(t)

    score = sum(w[c] * comp[c] for c in comp)
    score = _smooth(score, per_s(3))

    dur = float(t[-1]) if len(t) else 0
    if k is None:
        k = int(np.clip(round(st["per_10min"] * dur / 600), st["min_candidates"], st["max_candidates"]))
    order = np.argsort(-score)
    picks = []
    gap = st["min_gap_s"]
    for i in order:
        if len(picks) >= k:
            break
        tt = float(t[i])
        if tt < 20 or tt > dur - 5:
            continue
        if all(abs(tt - p) >= gap for p in picks):
            picks.append(tt)
    picks.sort()
    ytw = captions_words(slug)
    cands = []
    for n, pt in enumerate(picks, 1):
        i = int(pt / hop)
        a, b = max(0.0, pt - st["pre_s"]), min(dur, pt + st["post_s"])
        brk = {c: round(float(comp[c][i]), 2) for c in comp}
        chats = []
        if chat_texts:
            near = [x for x in chat_texts if pt - 2 <= x[0] - st["chat_lag_s"] <= pt + 12]
            seen = set()
            for _, txt in near:
                s_ = txt[:40]
                if s_.lower() not in seen and s_:
                    seen.add(s_.lower())
                    chats.append(s_)
                if len(chats) >= 6:
                    break
        cands.append({"id": f"K{n:02d}", "peak": round(pt, 2), "win": [round(a, 2), round(b, 2)],
                      "score": round(float(score[i]), 2), "signals": brk, "chat": chats,
                      "yt_caption": " ".join(w_["w"] for w_ in ytw if a <= w_["s"] < b)[:600] if ytw else None})
    ranked = sorted(cands, key=lambda c: -c["score"])
    for r, c in enumerate(ranked, 1):
        c["rank"] = r
    save_json(pdir / "analysis" / "candidates.json", {"k": k, "hop": hop, "weights": w,
                                                      "has_chat": bool(msgs is not None and msgs.sum() > 50),
                                                      "has_heatmap": bool(hm), "candidates": cands})
    # keep a compact per-second score series for later inspection
    step = per_s(1.0)
    np.savez_compressed(pdir / "analysis" / "score.npz", t=t[::step], score=score[::step],
                        **{c: comp[c][::step] for c in comp})
    print(f"{len(cands)} candidates (chat {'yes' if msgs is not None and msgs.sum() > 50 else 'no'}, "
          f"heatmap {'yes' if hm else 'no'}, captions {'yes' if ytw else 'no'})")
    for c in ranked[:15]:
        s = c["signals"]
        print(f"  #{c['rank']:2d} {c['id']} peak {fmt_t(c['peak'])} score {c['score']:.2f}  laugh {s['laugh']:.2f} "
              f"scream {s['scream']:.2f} loud {s['loud']:.2f} chat {s['chat']:.2f} heat {s['heat']:.2f}")
    return cands
