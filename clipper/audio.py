"""Audio helpers: decode, write, BS.1770 loudness in numpy, small filters, mastering via ffmpeg."""
from __future__ import annotations

import math
import subprocess
from pathlib import Path

import numpy as np
from scipy import signal

from .util import ClipError, loudness, run, tool, tool_env

SR = 48000

# BS.1770 K-weighting at 48 kHz
_K1 = (np.array([1.53512485958697, -2.69169618940638, 1.19839281085285]),
       np.array([1.0, -1.69065929318241, 0.73248077421585]))
_K2 = (np.array([1.0, -2.0, 1.0]), np.array([1.0, -1.99004745483398, 0.99007225036621]))


def read(src, start=None, dur=None, sr=SR, ch=2):
    cmd = [tool("ffmpeg"), "-v", "error"]
    if start is not None:
        cmd += ["-ss", f"{start:.6f}"]
    if dur is not None:
        cmd += ["-t", f"{dur:.6f}"]
    cmd += ["-i", str(src), "-map", "0:a:0", "-ac", str(ch), "-ar", str(sr), "-f", "f32le", "-"]
    p = subprocess.run(cmd, capture_output=True, env=tool_env())
    if p.returncode != 0:
        raise ClipError(f"audio decode failed: {p.stderr.decode('utf-8', 'replace')[-300:]}")
    x = np.frombuffer(p.stdout, np.float32).reshape(-1, ch).copy()
    if dur is not None:
        n = int(round(dur * sr))
        if len(x) < n:
            x = np.vstack([x, np.zeros((n - len(x), ch), np.float32)])
        x = x[:n]
    return x


def write(path, x, sr=SR, subtype="PCM_24"):
    import soundfile as sf
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), np.asarray(x, np.float32), sr, subtype=subtype)


def load_wav(path):
    import soundfile as sf
    x, sr = sf.read(str(path), dtype="float32", always_2d=True)
    if sr != SR:
        x = signal.resample_poly(x, SR, sr, axis=0).astype(np.float32)
    if x.shape[1] == 1:
        x = np.repeat(x, 2, axis=1)
    return x[:, :2]


def kweight(x):
    y = signal.lfilter(*_K1, x, axis=0)
    return signal.lfilter(*_K2, y, axis=0)


def _ms_blocks(x, win, hop):
    y = kweight(x)
    p = np.sum(y.astype(np.float64) ** 2, axis=1)
    c = np.concatenate([[0.0], np.cumsum(p)])
    n = len(p)
    starts = np.arange(0, max(1, n - win + 1), hop)
    return (c[np.minimum(starts + win, n)] - c[starts]) / win, starts


def momentary(x):
    """400 ms momentary loudness every 100 ms (LUFS)."""
    ms, st = _ms_blocks(x, int(0.4 * SR), int(0.1 * SR))
    return -0.691 + 10 * np.log10(ms + 1e-12), st


def momentary_max(x):
    m, _ = momentary(x)
    return float(np.max(m)) if len(m) else -70.0


def window_loudness(x):
    """Ungated mean-square loudness of a span (used for the program around an SFX)."""
    y = kweight(x)
    return float(-0.691 + 10 * np.log10(np.mean(np.sum(y.astype(np.float64) ** 2, axis=1)) + 1e-12))


def integrated(x):
    ms, _ = _ms_blocks(x, int(0.4 * SR), int(0.1 * SR))
    lk = -0.691 + 10 * np.log10(ms + 1e-12)
    g = ms[lk > -70]
    if not len(g):
        return -70.0
    rel = -0.691 + 10 * np.log10(np.mean(g)) - 10
    g2 = ms[(lk > -70) & (lk > rel)]
    return float(-0.691 + 10 * np.log10(np.mean(g2) + 1e-12))


def db(x):
    return 10 ** (x / 20.0)


def fade(x, n_in, n_out):
    x = x.copy()
    if n_in > 0:
        n_in = min(n_in, len(x))
        x[:n_in] *= np.linspace(0, 1, n_in, dtype=np.float32)[:, None]
    if n_out > 0:
        n_out = min(n_out, len(x))
        x[len(x) - n_out:] *= np.linspace(1, 0, n_out, dtype=np.float32)[:, None]
    return x


def lowshelf(x, f0, gain_db, q=0.707):
    A = 10 ** (gain_db / 40)
    w0 = 2 * math.pi * f0 / SR
    alpha = math.sin(w0) / (2 * q)
    cw = math.cos(w0)
    b = np.array([A * ((A + 1) - (A - 1) * cw + 2 * math.sqrt(A) * alpha), 2 * A * ((A - 1) - (A + 1) * cw),
                  A * ((A + 1) - (A - 1) * cw - 2 * math.sqrt(A) * alpha)])
    a = np.array([(A + 1) + (A - 1) * cw + 2 * math.sqrt(A) * alpha, -2 * ((A - 1) + (A + 1) * cw),
                  (A + 1) + (A - 1) * cw - 2 * math.sqrt(A) * alpha])
    return signal.lfilter(b / a[0], a / a[0], x, axis=0).astype(np.float32)


def bass_boost(x, amount=1.0):
    """The 'bass boosted' meme treatment: heavy low shelf + saturation, level-matched-ish."""
    y = lowshelf(x, 110, 14 * amount)
    drive = 1 + 3.5 * amount
    y = np.tanh(y * drive) / np.tanh(drive)
    return (0.85 * y + 0.15 * x).astype(np.float32)


def master(mix_wav, out_wav, target, ceiling_db, comp, passes=3, tol=0.3):
    """Compressor + make-up gain + true-peak limiter (192 kHz). Gain found by measuring."""
    i0, _ = loudness(mix_wav)
    if not math.isfinite(i0):
        raise ClipError("mix is silent")
    g = target - i0 + 2.0   # the compressor takes a little level away
    for _ in range(passes):
        af = (f"acompressor=threshold={comp['threshold_db']}dB:ratio={comp['ratio']}:attack={comp['attack_ms']}"
              f":release={comp['release_ms']}:knee=4,volume={g:.2f}dB,aresample=192000,"
              f"alimiter=limit={db(ceiling_db):.4f}:attack=2:release=60:level=disabled:latency=1,aresample=48000")
        run([tool("ffmpeg"), "-y", "-v", "error", "-i", mix_wav, "-af", af, "-c:a", "pcm_s24le", out_wav])
        i, tp = loudness(out_wav)
        if abs(i - target) <= tol:
            break
        g += target - i
    return g, i, tp


def apply_chain(in_wav, out_wav, gain_db, ceiling_db, comp):
    af = (f"acompressor=threshold={comp['threshold_db']}dB:ratio={comp['ratio']}:attack={comp['attack_ms']}"
          f":release={comp['release_ms']}:knee=4,volume={gain_db:.2f}dB,aresample=192000,"
          f"alimiter=limit={db(ceiling_db):.4f}:attack=2:release=60:level=disabled:latency=1,aresample=48000")
    run([tool("ffmpeg"), "-y", "-v", "error", "-i", in_wav, "-af", af, "-c:a", "pcm_s24le", out_wav])
