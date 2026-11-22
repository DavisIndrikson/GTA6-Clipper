"""Local speech-to-text with faster-whisper (CPU int8). Nothing leaves this computer.

windows(): transcribe only the candidate windows of a long stream (fast scan pass)
clip_words(): precise pass over one clip's source range, for captions
"""
from __future__ import annotations

import time

import numpy as np

from .util import CONFIG, ClipError, fmt_t, load_json, machine, project_dir, save_json, tool, update_project

_MODEL = {}


def _model(name):
    if name not in _MODEL:
        from faster_whisper import WhisperModel
        _MODEL[name] = WhisperModel(name, device="cpu", compute_type="int8",
                                    cpu_threads=max(1, machine().get("cpu_threads") or 4))
    return _MODEL[name]


def _read16k(src, a, b):
    import subprocess
    from .util import tool_env
    p = subprocess.run([tool("ffmpeg"), "-v", "error", "-ss", f"{a:.3f}", "-t", f"{b - a:.3f}", "-i", src,
                        "-map", "0:a:0", "-ac", "1", "-ar", "16000", "-f", "f32le", "-"],
                       capture_output=True, env=tool_env())
    if p.returncode != 0:
        raise ClipError(p.stderr.decode("utf-8", "replace")[-400:])
    return np.frombuffer(p.stdout, np.float32).copy()


def _prompt():
    lex = load_json(CONFIG / "lexicon.json", {})
    return lex.get("prompt") or None


def transcribe(src, a, b, model_name, beam=1, batched=False):
    audio = _read16k(src, a, b)
    m = _model(model_name)
    if batched:
        from faster_whisper import BatchedInferencePipeline
        bp = BatchedInferencePipeline(model=m)
        segs, info = bp.transcribe(audio, language="en", word_timestamps=True, beam_size=beam, batch_size=8,
                                   initial_prompt=_prompt())
    else:
        segs, info = m.transcribe(audio, language="en", word_timestamps=True, beam_size=beam, vad_filter=False,
                                  condition_on_previous_text=False, initial_prompt=_prompt(),
                                  hallucination_silence_threshold=2.0, no_speech_threshold=0.6)
    words = []
    for s in segs:
        for w in s.words or []:
            txt = w.word.strip()
            if not txt:
                continue
            words.append({"w": txt, "s": round(a + w.start, 3), "e": round(a + w.end, 3), "p": round(w.probability, 3)})
    return words


def _merge(iv, gap=3.0):
    iv = sorted(iv)
    out = []
    for a, b in iv:
        if out and a <= out[-1][1] + gap:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out


def windows(slug, ids=None, model_name=None):
    pdir = project_dir(slug)
    pj = load_json(pdir / "project.json")
    src = pj["files"].get("audio") or pj["files"].get("video")
    cands = load_json(pdir / "analysis" / "candidates.json")["candidates"]
    if ids:
        cands = [c for c in cands if c["id"] in ids]
    model_name = model_name or machine()["models"]["whisper"]
    done = load_json(pdir / "analysis" / "asr" / "index.json", {"intervals": [], "model": model_name})
    covered = done["intervals"]
    todo = []
    for a, b in _merge([c["win"] for c in cands]):
        # skip parts already transcribed
        rest = [[a, b]]
        for ca, cb in covered:
            nxt = []
            for x, y in rest:
                if cb <= x or ca >= y:
                    nxt.append([x, y])
                else:
                    if x < ca:
                        nxt.append([x, ca])
                    if cb < y:
                        nxt.append([cb, y])
            rest = nxt
        todo += [r for r in rest if r[1] - r[0] > 1.0]
    total = sum(b - a for a, b in todo)
    if not todo:
        print("all candidate windows already transcribed")
        return
    allw = load_json(pdir / "analysis" / "words.json", {"words": []})["words"]
    t0 = time.time()
    prog = pdir / "analysis" / "asr_progress.txt"
    done_s = 0.0
    from .util import style
    # CLIP_ASR_UNBATCHED=1: the batched pipeline once hit a native stack overflow (0xC00000FD, no Python
    # traceback) on one stream window; unbatched is slower but avoids it.
    import os
    batched = style()["scan"].get("asr_batched", True) and not os.environ.get("CLIP_ASR_UNBATCHED")
    for a, b in todo:
        allw += transcribe(src, a, b, model_name, beam=1, batched=batched)
        covered.append([a, b])
        done_s += b - a
        el = time.time() - t0
        prog.write_text(f"{fmt_t(done_s, 0)} / {fmt_t(total, 0)} transcribed, {done_s / el:.1f}x realtime\n",
                        encoding="utf-8")
        _save_words(pdir, allw, _merge(covered, 0.0), model_name)
    el = time.time() - t0
    update_project(slug, "stats", {"asr_speed_x": round(total / el, 2)})
    print(f"transcribed {fmt_t(total, 0)} of candidate audio in {el:.0f} s ({total / el:.1f}x realtime), "
          f"{len(allw)} words total")


def _save_words(pdir, words, covered, model_name):
    words = sorted({(w["s"], w["w"]): w for w in words}.values(), key=lambda w: w["s"])
    for i, w in enumerate(words):
        w["i"] = i
    save_json(pdir / "analysis" / "words.json", {"model": model_name, "words": words}, indent=None)
    save_json(pdir / "analysis" / "asr" / "index.json", {"model": model_name, "intervals": covered})


def clip_words(slug, a, b, out_path, model_name=None):
    """Precise transcription of [a, b) source seconds for captions (beam 5)."""
    pdir = project_dir(slug)
    pj = load_json(pdir / "project.json")
    # audio first: a `clip proxy` video has no audio track, and both come from the same VOD timeline
    src = pj["files"].get("audio") or pj["files"].get("video")
    model_name = model_name or machine()["models"]["whisper_clip"]
    pad = 1.0
    words = transcribe(src, max(0.0, a - pad), b + pad, model_name, beam=5)
    words = [w for w in words if w["e"] > a and w["s"] < b]
    save_json(out_path, {"model": model_name, "range": [a, b], "words": words})
    return words
