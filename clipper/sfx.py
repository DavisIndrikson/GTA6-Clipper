"""Meme sound-effect library, synthesised in code (no licensing questions, identical everywhere).

`clip sfx` writes assets/sfx/<name>.wav and assets/sfx/index.json. Drop your own
licensed effects (wav/mp3/ogg/m4a/flac) into assets/sfx/user/ and they become
available to plans as "user:<file stem>" (e.g. user:vine_boom).
"""
from __future__ import annotations

import re
from pathlib import Path

import numpy as np
from scipy import signal

from . import audio as A
from .util import ASSETS, save_json, tool, run

SR = A.SR
SFX = ASSETS / "sfx"


def _t(d):
    return np.arange(int(d * SR)) / SR


def _rng(seed):
    return np.random.default_rng(seed)


def _sine(freq, d=None):
    f = np.broadcast_to(np.asarray(freq, np.float64), (int(d * SR),)) if np.ndim(freq) == 0 else np.asarray(freq)
    return np.sin(2 * np.pi * np.cumsum(f) / SR)


def _saw(freq, d=None):
    f = np.full(int(d * SR), float(freq)) if np.ndim(freq) == 0 else np.asarray(freq, np.float64)
    ph = np.cumsum(f) / SR
    return 2 * (ph % 1.0) - 1


def _square(freq, d):
    return np.sign(_sine(freq, d))


def _filt(x, kind, f, order=2):
    sos = signal.butter(order, f, btype=kind, fs=SR, output="sos")
    return signal.sosfilt(sos, x, axis=0)


def _env(n, a=0.005, d=0.3, hold=0.0):
    t = np.arange(n) / SR
    e = np.minimum(1, t / max(a, 1e-4))
    rel = np.clip(t - a - hold, 0, None)
    return e * np.exp(-rel / max(d, 1e-4))


def _verb(x, length=1.2, decay=3.5, mix=0.25, seed=7):
    n = int(length * SR)
    ir = _rng(seed).standard_normal(n) * np.exp(-np.arange(n) / SR * decay)
    ir = _filt(ir, "lowpass", 5000)
    ir /= np.sqrt(np.sum(ir ** 2))
    wet = signal.fftconvolve(x, ir)
    dry = np.concatenate([x, np.zeros(len(wet) - len(x))])
    return (1 - mix) * dry + mix * wet


def _pad(x, total):
    return np.concatenate([x, np.zeros(max(0, int(total * SR) - len(x)))])


def _place(buf, x, at):
    i = int(at * SR)
    j = min(len(buf), i + len(x))
    buf[i:j] += x[: j - i]
    return buf


def _stereo(x, width=0.0, seed=1):
    if width <= 0:
        return np.stack([x, x], axis=1)
    d = int(0.012 * SR * width)
    r = np.concatenate([np.zeros(d), x])[: len(x)]
    return np.stack([x, 0.6 * x + 0.4 * r], axis=1)


# ------------------------------------------------------------------ effects
def boom():
    d = 1.9
    t = _t(d)
    f = 48 + 110 * np.exp(-t * 18)
    body = _sine(f) * _env(len(t), 0.002, 0.55) + 0.35 * _sine(2 * f) * _env(len(t), 0.002, 0.25)
    click = _filt(_rng(1).standard_normal(int(0.012 * SR)), "lowpass", 2500) * np.linspace(1, 0, int(0.012 * SR))
    x = body
    x[: len(click)] += 0.6 * click
    x = np.tanh(2.2 * x)
    x = _verb(x, 1.4, 2.8, 0.3)
    return _stereo(_filt(x, "lowpass", 6000), 0.4)


def bass_drop():
    d = 2.6
    t = _t(d)
    x = _sine(34 + 46 * np.exp(-t * 2.2)) * _env(len(t), 0.004, 1.2)
    return _stereo(np.tanh(1.8 * x))


def record_scratch():
    d = 0.62
    t = _t(d)
    n = _rng(2).standard_normal(len(t))
    pos = np.sin(np.pi * np.clip(t / 0.28, 0, 1)) * (t < 0.28) + np.sin(np.pi * np.clip((t - 0.3) / 0.3, 0, 1)) * (t >= 0.3)
    out = np.zeros_like(t)
    hop = 256
    for i in range(0, len(t), hop):
        c = 350 + 3200 * pos[i]
        sos = signal.butter(2, [c * 0.7, c * 1.4], btype="bandpass", fs=SR, output="sos")
        m = min(hop, len(t) - i)
        out[i: i + m] = signal.sosfilt(sos, n[max(0, i - 512): i + m])[-m:]
    tone = _saw(160 + 900 * pos) * 0.25
    x = (out * 3 + _filt(tone, "lowpass", 3000)) * (0.3 + pos) * _env(len(t), 0.003, 1.0)
    return _stereo(x, 0.3)


def airhorn():
    buf = np.zeros(int(1.75 * SR))
    for at, dur in [(0.0, 0.17), (0.22, 0.09), (0.35, 0.09), (0.5, 1.05)]:
        t = _t(dur)
        f0 = 452 * (1 - 0.03 * (t / dur) ** 3)
        x = sum(_saw(f0 * m * (1 + det), dur) for m, det in [(1, 0), (1, 0.006), (1.5, 0.003), (2, -0.004)])
        x = _filt(x, "lowpass", 4200)
        x = np.clip(x * 2.0, -1, 1) * np.minimum(1, t / 0.01) * np.minimum(1, (dur - t) / 0.03)
        _place(buf, x, at)
    return _stereo(_verb(buf, 0.6, 6, 0.15), 0.5)


def sad_trombone():
    notes = [(293.66, 0.38), (277.18, 0.38), (261.63, 0.38), (246.94, 1.25)]
    buf = np.zeros(int(2.6 * SR))
    at = 0.0
    for k, (f, dur) in enumerate(notes):
        t = _t(dur)
        vib = 1 + (0.025 * np.sin(2 * np.pi * 5.2 * t) * np.clip((t - 0.25) / 0.3, 0, 1) if k == 3
                   else np.zeros_like(t))
        x = _saw(f * vib)
        wah = 500 + 1600 * np.exp(-t * 5) + (900 * (0.5 + 0.5 * np.sin(2 * np.pi * 5.2 * t)) if k == 3 else 0)
        y = np.zeros_like(x)
        for i in range(0, len(x), 512):
            sos = signal.butter(2, min(wah[i], 8000), btype="lowpass", fs=SR, output="sos")
            y[i: i + 512] = signal.sosfilt(sos, x[max(0, i - 1024): i + 512])[-len(x[i: i + 512]):]
        amp = np.minimum(1, t / 0.04) * np.minimum(1, (dur - t) / 0.06) * (0.85 if k < 3 else 1.0)
        _place(buf, np.tanh(1.5 * y) * amp, at)
        at += dur + 0.04
    return _stereo(_verb(buf, 0.8, 4, 0.18), 0.3)


def _slide(f0, f1, d=0.95):
    t = _t(d)
    f = f0 * (f1 / f0) ** (t / d) * (1 + 0.012 * np.sin(2 * np.pi * 6 * t))
    breath = _filt(_rng(3).standard_normal(len(t)), "bandpass", [1500, 6000]) * 0.04
    return _stereo((_sine(f) + breath) * np.minimum(1, t / 0.03) * np.minimum(1, (d - t) / 0.08))


def slide_down():
    return _slide(2300, 420)


def slide_up():
    return _slide(420, 2300, 0.7)


def boing():
    d = 0.85
    t = _t(d)
    f = 240 * (1 + 0.55 * np.exp(-t * 4.5) * np.sin(2 * np.pi * 13 * t))
    x = (_sine(f) + 0.3 * _sine(2.71 * f)) * _env(len(t), 0.003, 0.32)
    return _stereo(x)


def bonk():
    d = 0.45
    t = _t(d)
    x = (_sine(820 + 0 * t) * 0.7 + _sine(1250 + 0 * t) * 0.4) * _env(len(t), 0.001, 0.06)
    x += _sine(130 * np.exp(-t * 6)) * _env(len(t), 0.001, 0.09) * 0.9
    x[: int(0.004 * SR)] += _rng(4).standard_normal(int(0.004 * SR)) * 0.6
    return _stereo(_verb(x, 0.4, 9, 0.12))


def punch():
    d = 0.4
    t = _t(d)
    x = _sine(95 * np.exp(-t * 4)) * _env(len(t), 0.001, 0.1)
    crack = _filt(_rng(5).standard_normal(len(t)), "bandpass", [900, 4000]) * _env(len(t), 0.0005, 0.025)
    return _stereo(np.tanh(1.5 * (x + 0.8 * crack)))


def rimshot():
    buf = np.zeros(int(1.6 * SR))
    for at, f in [(0.0, 170), (0.17, 125)]:
        t = _t(0.25)
        _place(buf, _sine(f * (1 + 0.5 * np.exp(-t * 30))) * _env(len(t), 0.001, 0.09), at)
    t = _t(1.1)
    cym = _filt(_rng(6).standard_normal(len(t)), "highpass", 5500) * _env(len(t), 0.001, 0.35) * 0.55
    snare = (_filt(_rng(7).standard_normal(len(t)), "bandpass", [1500, 7000]) * _env(len(t), 0.001, 0.08)
             + _sine(200 + 0 * t) * _env(len(t), 0.001, 0.05) * 0.6)
    _place(buf, cym + snare, 0.4)
    return _stereo(buf, 0.6)


def dun_dun():
    buf = np.zeros(int(3.0 * SR))
    chord = [65.41, 98.0, 130.81, 155.56]
    for at, dur in [(0.0, 0.36), (0.42, 0.36), (0.84, 1.9)]:
        t = _t(dur)
        x = sum(_saw(f, dur) for f in chord)
        x = _filt(x, "lowpass", 1100) * np.minimum(1, t / 0.02) * np.exp(-t / (0.9 if dur > 1 else 0.6))
        timp = _sine(55 * (1 + 0.3 * np.exp(-t * 25))) * _env(len(t), 0.002, 0.35)
        _place(buf, 0.35 * np.tanh(x) + 0.8 * timp, at)
    return _stereo(_verb(buf, 1.6, 2.5, 0.3), 0.6)


def crickets():
    d = 2.6
    t = _t(d)
    x = np.zeros_like(t)
    for start in np.arange(0.1, d - 0.4, 0.62):
        for k in range(3):
            s = start + k * 0.055
            n = int(0.035 * SR)
            i = int(s * SR)
            seg = _sine(4600 + 0 * np.arange(n)) * np.sin(np.pi * np.arange(n) / n) ** 2
            x[i: i + n] += seg[: len(x[i: i + n])]
    l = x
    r = np.roll(x, int(0.21 * SR)) * 0.7
    return np.stack([l, r], axis=1)


def ding():
    d = 1.6
    t = _t(d)
    f0 = 1318.5
    x = sum(a * _sine(f0 * m + 0 * t) * np.exp(-t * dk) for m, a, dk in [(1, 1, 2.2), (2.76, 0.35, 4), (5.4, 0.15, 7)])
    return _stereo(x * np.minimum(1, t / 0.002), 0.3)


def whoosh():
    d = 0.6
    t = _t(d)
    n = _rng(8).standard_normal(len(t))
    out = np.zeros_like(n)
    for i in range(0, len(n), 512):
        c = 300 * (12 ** (i / len(n)))
        sos = signal.butter(2, [c * 0.6, min(c * 1.8, 20000)], btype="bandpass", fs=SR, output="sos")
        out[i: i + 512] = signal.sosfilt(sos, n[max(0, i - 1024): i + 512])[-len(n[i: i + 512]):]
    env = np.sin(np.pi * t / d) ** 2
    x = out * env * 2
    pan = t / d
    return np.stack([x * (1 - 0.6 * pan), x * (0.4 + 0.6 * pan)], axis=1)


def glitch():
    d = 0.65
    buf = np.zeros(int(d * SR))
    r = _rng(9)
    for k in range(9):
        at = k * 0.07 + r.uniform(0, 0.02)
        n = int(r.uniform(0.02, 0.05) * SR)
        if r.random() < 0.5:
            seg = np.round(r.standard_normal(n) * 3) / 3
        else:
            seg = _square(r.uniform(200, 1800), n / SR)
        _place(buf, seg * 0.6, at)
    return _stereo(_filt(buf, "lowpass", 9000), 0.8)


def buzzer():
    d = 0.75
    t = _t(d)
    x = 0.6 * _square(118, d) + 0.4 * _square(177, d)
    x = _filt(x, "lowpass", 3000) * (0.85 + 0.15 * np.sin(2 * np.pi * 30 * t))
    return _stereo(x * np.minimum(1, t / 0.005) * np.minimum(1, (d - t) / 0.02))


def clown_horn():
    buf = np.zeros(int(0.65 * SR))
    for at in (0.0, 0.3):
        dur = 0.22
        t = _t(dur)
        x = _saw(330 * (1 + 0.22 * t / dur))
        x = _filt(_filt(x, "bandpass", [450, 1400]), "lowpass", 2500)
        _place(buf, np.tanh(3 * x) * np.sin(np.pi * t / dur) ** 0.5, at)
    return _stereo(buf)


def riser():
    d = 2.0
    t = _t(d)
    x = _sine(180 * (9 ** (t / d))) * 0.4 + _filt(_rng(10).standard_normal(len(t)), "highpass", 2000) * 0.3 * (t / d)
    return _stereo(x * (t / d) ** 2, 0.7)


def metal_clang():
    d = 2.2
    t = _t(d)
    f0 = 523
    x = sum(a * _sine(f0 * m + 0 * t) * np.exp(-t * dk)
            for m, a, dk in [(1, 1, 1.6), (2.32, 0.8, 2.0), (4.25, 0.6, 3.0), (6.63, 0.4, 4.5), (9.1, 0.25, 6)])
    x[: int(0.006 * SR)] += _rng(11).standard_normal(int(0.006 * SR)) * 1.2
    return _stereo(_verb(np.tanh(1.3 * x), 1.0, 3.5, 0.2), 0.4)


def tada():
    buf = np.zeros(int(1.8 * SR))
    for k, (at, f) in enumerate([(0.0, 523.25), (0.09, 659.25), (0.18, 783.99)]):
        t = _t(0.18)
        _place(buf, _filt(_saw(f, 0.18), "lowpass", 3500) * _env(len(t), 0.005, 0.2) * 0.6, at)
    t = _t(1.3)
    ch = sum(_saw(f, 1.3) for f in (523.25, 659.25, 783.99, 1046.5))
    _place(buf, _filt(ch, "lowpass", 3000) * np.minimum(1, t / 0.01) * np.exp(-t / 0.9) * 0.45, 0.3)
    return _stereo(_verb(buf, 0.9, 4, 0.2), 0.5)


def heartbeat():
    buf = np.zeros(int(1.8 * SR))
    for at in (0.0, 0.24, 0.9, 1.14):
        t = _t(0.2)
        _place(buf, _sine(55 * (1 + 0.4 * np.exp(-t * 30))) * _env(len(t), 0.003, 0.06), at)
    return _stereo(np.tanh(2 * buf))


def shutter():
    buf = np.zeros(int(0.3 * SR))
    for at in (0.0, 0.08):
        n = int(0.02 * SR)
        _place(buf, _filt(_rng(12).standard_normal(n), "bandpass", [2000, 8000]) * np.linspace(1, 0, n), at)
    return _stereo(buf)


def tape_stop():
    d = 0.7
    t = _t(d)
    x = _saw(320 * np.exp(-t * 4.5), d)
    return _stereo(_filt(x, "lowpass", 2500) * (1 - t / d))


LIB = {
    "boom": (boom, "deep 'vine boom' style hit: punchline, sudden realisation, cursed moment"),
    "bass_drop": (bass_drop, "808 sub drop under a big moment"),
    "record_scratch": (record_scratch, "record scratch: 'wait, what?' freeze-frame moments"),
    "airhorn": (airhorn, "MLG air horn: hype, W play, insane stunt"),
    "sad_trombone": (sad_trombone, "wah-wah-wah fail sound"),
    "slide_down": (slide_down, "slide whistle down: falling, crashing, plan going wrong"),
    "slide_up": (slide_up, "slide whistle up: jump, launch, flying car"),
    "boing": (boing, "cartoon spring: bouncing, ragdoll"),
    "bonk": (bonk, "cartoon bonk: bumping into things, getting hit"),
    "punch": (punch, "punch impact"),
    "rimshot": (rimshot, "ba-dum-tss after a joke"),
    "dun_dun": (dun_dun, "dramatic dun-dun-dunnn: plot twist, police arrive, betrayal"),
    "crickets": (crickets, "awkward silence"),
    "ding": (ding, "ding: idea, correct, notification"),
    "whoosh": (whoosh, "whoosh: zoom-in or transition"),
    "glitch": (glitch, "digital glitch: game bug, chaos"),
    "buzzer": (buzzer, "wrong-answer buzzer: terrible decision"),
    "clown_horn": (clown_horn, "honk honk: clown behaviour"),
    "riser": (riser, "2 s tension riser before a payoff"),
    "metal_clang": (metal_clang, "metal-pipe clang: absurd impact"),
    "tada": (tada, "fanfare: success, W moment"),
    "heartbeat": (heartbeat, "tense heartbeat: hiding, sneaking, police nearby"),
    "shutter": (shutter, "camera snap: freeze-frame click"),
    "tape_stop": (tape_stop, "tape-stop slowdown into a freeze"),
}

USER_EXT = {".wav", ".mp3", ".ogg", ".m4a", ".flac", ".aac", ".opus"}


def build_library():
    SFX.mkdir(parents=True, exist_ok=True)
    idx = {}
    for name, (fn, desc) in LIB.items():
        x = np.asarray(fn(), np.float64)
        if x.ndim == 1:
            x = np.stack([x, x], axis=1)
        x = x / (np.max(np.abs(x)) + 1e-9) * A.db(-1.0)
        n_tail = int(0.005 * SR)
        x[-n_tail:] *= np.linspace(1, 0, n_tail)[:, None]
        path = SFX / f"{name}.wav"
        A.write(path, x.astype(np.float32))
        idx[name] = {"file": str(path), "dur": round(len(x) / SR, 3), "loud_m": round(A.momentary_max(x), 2),
                     "desc": desc, "kind": "synth"}
    user = SFX / "user"
    cache = SFX / "user_cache"
    cache.mkdir(parents=True, exist_ok=True)
    for f in sorted(user.glob("*")):
        if f.suffix.lower() not in USER_EXT:
            continue
        name = "user:" + re.sub(r"[^a-z0-9]+", "_", f.stem.lower()).strip("_")
        out = cache / (name.split(":", 1)[1] + ".wav")
        run([tool("ffmpeg"), "-y", "-v", "error", "-i", f, "-ac", "2", "-ar", str(SR), "-c:a", "pcm_s24le", out])
        x = A.load_wav(out)
        idx[name] = {"file": str(out), "dur": round(len(x) / SR, 3), "loud_m": round(A.momentary_max(x), 2),
                     "desc": f"your file {f.name}", "kind": "user"}
    save_json(SFX / "index.json", idx)
    print(f"sfx library: {sum(1 for v in idx.values() if v['kind'] == 'synth')} synthesised, "
          f"{sum(1 for v in idx.values() if v['kind'] == 'user')} from assets/sfx/user")
    for k, v in idx.items():
        print(f"  {k:16s} {v['dur']:4.1f}s  {v['loud_m']:6.1f} LUFS-M  {v['desc']}")
    return idx
