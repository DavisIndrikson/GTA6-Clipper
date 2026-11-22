"""`clip ig ...`: post finished clips as Instagram Reels through Meta's official API.

Two login types, picked from the token the user saved in config/instagram_token.txt (gitignored,
never printed, never sent anywhere except Meta's API):
  * Instagram Login token ("IG..."): graph.instagram.com. Instagram fetches each video from a public
    URL, so `post` serves the one file from this computer through a temporary Cloudflare quick tunnel
    (random unguessable path, only while that clip uploads), then shuts it down.
  * Facebook Login for Business token ("EAA..."): graph.facebook.com, resumable upload of the local
    file straight to rupload.facebook.com. No tunnel needed. The Instagram account must be linked to a
    Facebook Page.
Posting is public and needs the user's OK: queue entries start as "draft" and are only posted once
marked "approved" - either after the user approved the list, captions and times in chat, or by
`clip ig autoqueue` when the user switched autopilot on themselves during setup (config/instagram.json
"autopilot": true, recorded with the date in "autopilot_since").

First comment under each Reel ("comment_mode" in config/instagram.json, chosen by the user during setup;
nothing is ever commented until they chose): "none" | "fixed" (the same "first_comment" text on every Reel)
| "claude" (Claude writes one per clip in plan/instagram.json). Meta's API cannot pin comments.

`clip ig tick` is one posting pass (due posts + purge of finished projects). The background poster service
(`clip ig service install`: Windows Task Scheduler / macOS launchd) runs it every 10 minutes, so Reels keep
going out on schedule while no Claude session is running.
"""
from __future__ import annotations

import contextlib
import datetime as dt
import json
import os
import secrets
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .util import CONFIG, PROJECTS, ROOT, ClipError, hidden_kw, load_json, run, save_json, tool, video_info

TOKEN_FILE = CONFIG / "instagram_token.txt"
SETTINGS = CONFIG / "instagram.json"            # api version, account id/username (gitignored)
QUEUE = PROJECTS / "_instagram" / "queue.json"
DEFAULTS = {"api_version": "v27.0", "share_to_feed": True, "times": ["12:00", "17:00", "21:00"],
            "comment_mode": "none", "first_comment": None, "comment_guidance": None,
            "autopilot": False, "every_min": 60, "max_queue_hours": 48, "purge_after_post": False}
# keys `clip ig config` may set (the rest come from `clip ig whoami`)
CONFIG_KEYS = {"comment_mode": ("none", "fixed", "claude"), "first_comment": str, "comment_guidance": str,
               "autopilot": bool, "every_min": int, "max_queue_hours": int, "purge_after_post": bool,
               "share_to_feed": bool, "api_version": str}


# ------------------------------------------------------------------ token / http
def _token():
    if not TOKEN_FILE.exists() or not TOKEN_FILE.read_text(encoding="utf-8").strip():
        raise ClipError(f"no Instagram token yet: save it (one line) in {TOKEN_FILE} - see CLAUDE.md 'Instagram'")
    return TOKEN_FILE.read_text(encoding="utf-8").strip()


def _kind(tok):
    if tok.startswith("IG"):
        return "instagram"
    if tok.startswith("EAA"):
        return "facebook"
    raise ClipError("the saved token is neither an Instagram Login (IG...) nor a Facebook Login (EAA...) token")


def _settings():
    s = dict(DEFAULTS)
    s.update(load_json(SETTINGS, {}))
    return s


def _host(kind):
    return "https://graph.instagram.com" if kind == "instagram" else "https://graph.facebook.com"


def _scrub(txt, tok):
    return txt.replace(tok, "<token>") if tok else txt


def _call(method, url, params=None, headers=None, data=None, timeout=120):
    tok = _token()
    params = dict(params or {})
    if "access_token" not in params and data is None:
        params["access_token"] = tok
    q = urllib.parse.urlencode(params)
    body = None
    if method == "POST" and data is None:
        body = q.encode()
        full = url
        headers = {"Content-Type": "application/x-www-form-urlencoded", **(headers or {})}
    else:
        full = url + ("?" + q if q else "")
        body = data
    req = urllib.request.Request(full, data=body, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        msg = e.read().decode("utf-8", "replace")
        try:
            err = json.loads(msg).get("error", {})
            msg = f"{err.get('type', '')} {err.get('code', '')}: {err.get('message', msg)}"
        except Exception:
            pass
        raise ClipError(f"Instagram API {e.code}: {_scrub(msg, tok)[:400]}") from None
    except urllib.error.URLError as e:
        raise ClipError(f"network error talking to Meta: {e.reason}") from None


# ------------------------------------------------------------------ account
def whoami(account=None):
    tok = _token()
    kind = _kind(tok)
    s = _settings()
    v = s["api_version"]
    if kind == "instagram":
        me = _call("GET", f"{_host(kind)}/{v}/me", {"fields": "user_id,username,account_type"})
        ig_id, user, acct = me.get("user_id") or me.get("id"), me.get("username"), me.get("account_type")
    else:
        ig_id = account or (s.get("ig_user_id") if s.get("kind") == "facebook" else None)
        if ig_id:
            # system-user tokens often lack pages_show_list: check the given Instagram account id directly
            ig = _call("GET", f"{_host(kind)}/{v}/{ig_id}", {"fields": "id,username,name"})
            user, acct = ig.get("username"), "Facebook Login / system user"
        else:
            pages = _call("GET", f"{_host(kind)}/{v}/me/accounts",
                          {"fields": "name,instagram_business_account{id,username}"}).get("data", [])
            linked = [p for p in pages if p.get("instagram_business_account")]
            if not linked:
                raise ClipError("this Facebook token can't list Pages (no pages_show_list). Give the Instagram "
                                "account ID: `clip ig whoami --account <id>` (Business settings > Accounts > "
                                "Instagram accounts), or regenerate the token with pages_show_list.")
            ig = linked[0]["instagram_business_account"]
            ig_id, user, acct = ig["id"], ig.get("username"), f"linked to Page '{linked[0].get('name')}'"
    lim = _call("GET", f"{_host(kind)}/{v}/{ig_id}/content_publishing_limit", {"fields": "quota_usage,config"})
    d = (lim.get("data") or [{}])[0]
    s.update({"kind": kind, "ig_user_id": str(ig_id), "username": user})
    save_json(SETTINGS, s)
    print(f"Instagram: @{user} ({acct}), login type {kind}, posts used in the last 24 h: "
          f"{d.get('quota_usage', '?')}/{(d.get('config') or {}).get('quota_total', '?')}")
    return s


def refresh():
    tok = _token()
    if _kind(tok) != "instagram":
        raise ClipError("Facebook tokens are refreshed in Meta's Access Token Debugger (Extend Access Token)")
    r = _call("GET", "https://graph.instagram.com/refresh_access_token", {"grant_type": "ig_refresh_token"})
    if not r.get("access_token"):
        raise ClipError("refresh returned no token")
    TOKEN_FILE.write_text(r["access_token"], encoding="utf-8")
    print(f"token refreshed, valid for another {int(r.get('expires_in', 0)) // 86400} days")


# ------------------------------------------------------------------ files
IG_MAX_BYTES = 30e6      # Meta's resumable upload failed every file over ~32 MB with ProcessingFailedError
IG_TARGET_BYTES = 22e6   # 12-22 MB x264 "veryfast" uploads were processed reliably; ~28 MB copies sometimes stalled
IG_MAX_KBPS = 8000       # short clips don't need more: Instagram re-encodes every Reel anyway
IG_RECIPE = 3            # bump when the upload-copy recipe changes: every copy rebuilds


def _x264(src: Path, dst: Path, target: float = IG_TARGET_BYTES, audio_copy: bool = True) -> Path:
    """Software x264 'veryfast' re-encode at ~target bytes (src may be dst). Meta's processing stalled on Quick Sync
    and x264 "slow" copies of some clips (a container stuck IN_PROGRESS for over an hour), but took veryfast copies
    of the same clips. x264 also overshoots the bitrate on short clips, so aim 8 % lower."""
    dur = video_info(src)["duration"]
    vk = min(IG_MAX_KBPS, max(1500, int((target * 0.92 * 8 / max(dur, 0.1)) / 1000) - 160))
    tmp = dst.with_name(dst.stem + "_tmp.mp4")
    acodec = ["-c:a", "copy"] if audio_copy else ["-c:a", "aac", "-b:a", "128k", "-ar", "48000"]
    run([tool("ffmpeg"), "-y", "-v", "error", "-i", src, "-map", "0:v:0", "-map", "0:a:0", "-c:v", "libx264",
         "-preset", "veryfast", "-b:v", f"{vk}k", "-maxrate", f"{int(vk * 1.3)}k", "-bufsize", f"{vk * 2}k",
         "-profile:v", "high", "-pix_fmt", "yuv420p"] + acodec + ["-movflags", "+faststart", tmp])
    tmp.replace(dst)
    return dst


def _is_x264(f: Path) -> bool:
    with open(f, "rb") as fh:   # x264 writes its version into an SEI right after the (faststart) moov
        return b"x264 - core" in fh.read(4_000_000)


def ig_copy(final: Path, out: Path):
    """Instagram copy of a finished clip: x264 veryfast at ~IG_TARGET_BYTES, AAC 128 kbps 48 kHz, moov at the front;
    checks the Reels specs. The exported clip itself is untouched. A stamp next to the copy records what it was built
    from, so a re-rendered clip rebuilds it."""
    out.parent.mkdir(parents=True, exist_ok=True)
    fs = final.stat()
    stamp = {"recipe": IG_RECIPE, "final": [final.name, fs.st_size, int(fs.st_mtime)]}
    stamp_f = out.with_suffix(".src.json")
    if not out.exists() or load_json(stamp_f, {}) != stamp:
        _x264(final, out, audio_copy=False)
        if out.stat().st_size > IG_MAX_BYTES:
            _x264(out, out)
        save_json(stamp_f, stamp)
    vi = video_info(out)
    size = out.stat().st_size
    kbps = size * 8 / max(vi["duration"], 0.1) / 1000
    probs = []
    if not 3 <= vi["duration"] <= 900:
        probs.append(f"duration {vi['duration']:.1f} s")
    if size > 300e6:
        probs.append(f"{size / 1e6:.0f} MB > 300 MB")
    if vi["width"] > 1920:
        probs.append(f"width {vi['width']}")
    if not 23 <= float(vi["fps"]) <= 60.01:
        probs.append(f"{float(vi['fps']):.2f} fps")
    if kbps > 25500:
        probs.append(f"{kbps / 1000:.1f} Mbps > 25")
    if probs:
        raise ClipError(f"{out.name} breaks Instagram's Reels specs: {', '.join(probs)}")
    return out, {"seconds": round(vi["duration"], 1), "mb": round(size / 1e6, 1), "mbps": round(kbps / 1000, 1)}


class _OneFileServer:
    """Serve registered files at random paths on 127.0.0.1 (GET/HEAD with Range), nothing else."""

    def __init__(self):
        self.files = {}
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _serve(self, head):
                f = outer.files.get(self.path)
                if f is None:
                    self.send_error(404)
                    return
                size = f.stat().st_size
                start, end = 0, size - 1
                rng = self.headers.get("Range")
                if rng and rng.startswith("bytes="):
                    a, _, b = rng[6:].partition("-")
                    start = int(a) if a else max(0, size - int(b))
                    end = int(b) if (a and b) else end
                    self.send_response(206)
                    self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                else:
                    self.send_response(200)
                self.send_header("Content-Type", "video/mp4")
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Length", str(end - start + 1))
                self.end_headers()
                if head:
                    return
                with open(f, "rb") as fh:
                    fh.seek(start)
                    left = end - start + 1
                    while left > 0:
                        chunk = fh.read(min(1 << 20, left))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        left -= len(chunk)

            def do_GET(self):
                self._serve(False)

            def do_HEAD(self):
                self._serve(True)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def add(self, f: Path):
        path = f"/{secrets.token_hex(16)}/{f.name.replace(' ', '_')}"
        self.files[path] = f
        return path

    def close(self):
        self.httpd.shutdown()


class _Tunnel:
    """Cloudflare quick tunnel (no account) to the local one-file server."""

    def __init__(self, port):
        exe = shutil.which("cloudflared") or load_json(CONFIG / "machine.json", {}).get("tools", {}).get("cloudflared")
        if not exe:
            how = "`brew install cloudflared`" if sys.platform == "darwin" else "`winget install Cloudflare.cloudflared`"
            raise ClipError(f"Instagram Login posting needs cloudflared (temporary public link). Install it with "
                            f"{how}, then run `clip doctor`.")
        self.p = subprocess.Popen([exe, "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{port}"],
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace",
                                  **hidden_kw())
        self.url = None
        t0 = time.time()
        while time.time() - t0 < 60:
            line = self.p.stdout.readline()
            if not line:
                break
            if "trycloudflare.com" in line:
                import re
                m = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", line)
                if m:
                    self.url = m.group(0)
                    break
        if not self.url:
            self.close()
            raise ClipError("cloudflared did not give a public URL within 60 s")
        threading.Thread(target=lambda: [None for _ in self.p.stdout], daemon=True).start()

    def close(self):
        if self.p and self.p.poll() is None:
            self.p.terminate()
            try:
                self.p.wait(10)
            except subprocess.TimeoutExpired:
                self.p.kill()


def _wait_public(url, timeout=60):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            req = urllib.request.Request(url, method="HEAD")
            with urllib.request.urlopen(req, timeout=10) as r:
                if r.status in (200, 206):
                    return True
        except Exception:
            time.sleep(2)
    raise ClipError("the temporary public link never became reachable")


# ------------------------------------------------------------------ queue
def _load_queue():
    return load_json(QUEUE, {"posts": []})


def _save_queue(q):
    save_json(QUEUE, q)


LOCK = QUEUE.with_suffix(".lock")


@contextlib.contextmanager
def _queue_lock(timeout=120):
    """Exclusive lock around one read-modify-write of queue.json. Several sessions and posting loops share
    the queue (2026-11-19): without it a long `post` run saved its stale copy over entries added meanwhile."""
    QUEUE.parent.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    while True:
        try:
            fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            break
        except FileExistsError:
            try:
                if time.time() - LOCK.stat().st_mtime > 60:   # only ever held for a quick edit: stale
                    LOCK.unlink(missing_ok=True)
                    continue
            except FileNotFoundError:
                continue
            if time.time() - t0 > timeout:
                raise ClipError(f"the queue stays locked ({LOCK}); delete that file if no `clip ig` runs")
            time.sleep(0.2)
    try:
        yield
    finally:
        LOCK.unlink(missing_ok=True)


def _edit_queue(fn):
    """Load, change (fn(q)), save under the lock. Returns fn's result."""
    with _queue_lock():
        q = _load_queue()
        r = fn(q)
        _save_queue(q)
        return r


def _claim(key):
    """Mark one approved entry 'posting' so a second posting loop can't upload it too. True if we got it."""
    def f(q):
        for e in q["posts"]:
            if e["key"] == key and e["status"] == "approved":
                e.update({"status": "posting", "posting_since": dt.datetime.now().isoformat(timespec="seconds"),
                          "posting_pid": os.getpid()})
                return True
        return False
    return _edit_queue(f)


def _store(p):
    """Write one entry's result back by key (other entries stay as they are on disk)."""
    def f(q):
        for e in q["posts"]:
            if e["key"] == p["key"]:
                e.update(p)
                for k in ("posting_since", "posting_pid"):
                    e.pop(k, None)
                if p.get("status") == "posted":
                    e.pop("error", None)
    _edit_queue(f)


def show():
    q = _load_queue()
    if not q["posts"]:
        print("queue is empty")
        return
    for p in q["posts"]:
        when = p.get("post_at") or "-"
        extra = p.get("permalink") or p.get("error") or ""
        print(f"{p['key']:28s} {p['status']:9s} {when:16s} {p['caption'].splitlines()[0][:60]}  {extra}")
    h, n = queue_hours()
    s = _settings()
    print(f"-- {n} posts ahead, the last one in {h:.1f} h (queue cap {s.get('max_queue_hours')} h); "
          f"autopilot {'ON' if s.get('autopilot') else 'off'}, one post every {s.get('every_min')} min, "
          f"comments: {s.get('comment_mode')}")


COOLDOWN = QUEUE.with_name("cooldown.json")   # {"until": iso}: Meta's app request limit was hit


def _rate_limited(err: str) -> bool:
    """Meta's throttling errors: (#4) app limit, (#17) user limit, (#32) page limit, (#613) calls per time."""
    return any(s in err for s in ("request limit reached", "OAuthException 4:", "OAuthException 17:",
                                  "OAuthException 32:", "OAuthException 613:"))


def _due(p, now):
    return p.get("post_at") and dt.datetime.fromisoformat(p["post_at"]) <= now


def post(keys=None, due=False, dry_run=False):
    q = _load_queue()
    now = dt.datetime.now()
    if dry_run:   # check/prepare the Instagram copies of any queued clip; no token, no upload
        for p in q["posts"]:
            if keys and keys != ["all"] and p["key"] not in keys:
                continue
            if p["status"] == "posted":
                continue
            f, spec = ig_copy(Path(p["file"]), Path(p["file"]).parent.parent / "instagram" / (p["key"].split("/")[-1] + ".mp4"))
            print(f"{p['key']}: OK for Reels - {spec['seconds']} s, {spec['mb']} MB, {spec['mbps']} Mbps -> {f.name}")
        return 0
    todo = [p for p in q["posts"] if p["status"] == "approved" and
            ((keys and (keys == ["all"] or p["key"] in keys)) or (due and _due(p, now)))]
    if not todo:
        print("nothing approved to post (use `clip ig post all`, a key, or --due)")
        return 0
    cool = load_json(COOLDOWN, {}).get("until")
    if cool and dt.datetime.fromisoformat(cool) > now:
        print(f"Meta API request limit: waiting until {cool[11:16]} before posting {', '.join(p['key'] for p in todo)}")
        return 0
    s = _settings()
    if not s.get("ig_user_id"):
        s = whoami()
    tok = _token()
    kind = _kind(tok)
    v = s["api_version"]
    host = _host(kind)
    link_state = {}
    fails = 0
    rate_limited = False
    try:
        for p in todo:
            if rate_limited:   # the rest stay approved and go after the cool-down
                break
            if not _claim(p["key"]):
                print(f"{p['key']}: skipped (another run is posting it or it changed)")
                continue
            p["status"] = "posting"
            try:
                f, spec = ig_copy(Path(p["file"]), Path(p["file"]).parent.parent / "instagram" / (p["key"].split("/")[-1] + ".mp4"))
                print(f"{p['key']}: uploading {spec['seconds']} s, {spec['mb']} MB")
                for attempt in (1, 2):   # Meta sometimes fails an upload with a one-off ProcessingFailedError
                    if _post_one(p, f, s, kind, host, v, tok, link_state):
                        break
                    err = p.get("error", "")
                    if _rate_limited(err) and not p.get("media_id"):
                        # nothing was published: keep the entry approved and pause all posting for an hour
                        until = (dt.datetime.now() + dt.timedelta(minutes=60)).isoformat(timespec="seconds")
                        save_json(COOLDOWN, {"until": until, "error": err})
                        p.update({"status": "approved", "error": f"rate-limited, retry after {until[11:16]}: {err}"})
                        print(f"{p['key']}: Meta API request limit reached; posting paused until {until[11:16]}")
                        rate_limited = True
                        break
                    if attempt == 2 or not ("ProcessingFailedError" in err or "API 50" in err):
                        fails += 1
                        break
                    print(f"{p['key']}: one-off Meta error, retrying once in 20 s")
                    time.sleep(20)
            except ClipError as e:
                p.update({"status": "failed", "error": str(e)[:300]})
                fails += 1
            finally:
                if p["status"] == "posting":   # interrupted mid-upload: never re-post blindly
                    p.update({"status": "failed", "error": "interrupted while posting: check the profile "
                                                           "before approving it again"})
                _store(p)
    finally:
        if link_state.get("tunnel"):
            link_state["tunnel"].close()
        if link_state.get("server"):
            link_state["server"].close()
    return 1 if fails else 0


def _post_one(p, f, s, kind, host, v, tok, link_state):
    """One upload + publish attempt; updates p in place. Returns True when posted.
    link_state holds the lazily started local server + tunnel (Instagram Login only)."""
    try:
        # a container from an earlier attempt that Meta was still processing: publish it instead of re-uploading
        # (containers live 24 h; one took > 10 min to process and the attempt gave up)
        cid = None
        if p.get("container_id") and p.get("container_at") and (
                dt.datetime.now() - dt.datetime.fromisoformat(p["container_at"]) < dt.timedelta(hours=20)):
            st = _call("GET", f"{host}/{v}/{p['container_id']}", {"fields": "status_code,status"})
            # a container still IN_PROGRESS after 8 h was seen: a stalled one never finishes, so only
            # a young IN_PROGRESS container is worth waiting on
            young = dt.datetime.now() - dt.datetime.fromisoformat(p["container_at"]) < dt.timedelta(minutes=30)
            if st.get("status_code") == "FINISHED" or (st.get("status_code") == "IN_PROGRESS" and young):
                cid = p["container_id"]
                print(f"{p['key']}: reusing container {cid} ({st.get('status_code')})")
        if cid is None:
            cid = _upload(p, f, s, kind, host, v, tok, link_state)
        # some containers never leave IN_PROGRESS (> 1 h) while the next upload of a file with
        # the same spec finished in 2 min. After 10 min, upload a second container and publish whichever
        # finishes first (only ever one publish; the unused container expires unpublished).
        cids, status, st, t0 = [cid], "IN_PROGRESS", {}, time.time()
        # slow containers can take ~25 min and still publish; 50 min still ends before the next hourly post
        while time.time() - t0 < 3000:
            states = {}
            for c in cids:
                st = _call("GET", f"{host}/{v}/{c}", {"fields": "status_code,status"})
                states[c] = st
            done = [c for c in cids if states[c].get("status_code") == "FINISHED"]
            if done:
                cid, status = done[0], "FINISHED"
                break
            if all(states[c].get("status_code") not in (None, "IN_PROGRESS") for c in cids):
                status = states[cids[-1]].get("status_code")
                st = states[cids[-1]]
                break
            if len(cids) < 3 and time.time() - t0 > 600 * len(cids):
                # the same bytes tend to stall again: send a fresh, smaller x264 veryfast re-encode instead
                # (same-size second copies stalled too, while 12-22 MB uploads never got a
                # ProcessingFailedError): the second copy is ~55 % of the target size, a third at 20 min ~35 %.
                f2 = _x264(f, f.with_name(f.stem + ("_x264.mp4", "_x264s.mp4")[len(cids) - 1]),
                           IG_TARGET_BYTES * (0.55, 0.35)[len(cids) - 1])
                print(f"{p['key']}: still processing after {10 * len(cids)} min, uploading copy {len(cids) + 1} "
                      f"({f2.name}, {f2.stat().st_size / 1e6:.1f} MB)")
                cids.append(_upload(p, f2, s, kind, host, v, tok, link_state))
            # polling 3 containers every 15 s (~700 calls/h) hit "(#4) Application request limit reached";
            # once a minute is plenty (good uploads finish in 1-2 min)
            time.sleep(60)
        if status != "FINISHED":
            raise ClipError(f"container {status}: {st.get('status', '')}")
        mid = _call("POST", f"{host}/{v}/{s['ig_user_id']}/media_publish", {"creation_id": cid})["id"]
        p["media_id"] = mid   # published: from here on an error must never lead to a re-post
        p.pop("container_id", None)
        p.pop("container_at", None)
        link = _call("GET", f"{host}/{v}/{mid}", {"fields": "permalink"}).get("permalink")
        p.update({"status": "posted", "media_id": mid, "permalink": link,
                  "posted_at": dt.datetime.now().isoformat(timespec="seconds")})
        p.pop("error", None)
        print(f"{p['key']}: POSTED {link}")
        _first_comment(p, host, v)
        return True
    except ClipError as e:
        if p.get("media_id") and p.get("status") != "posted":
            # the Reel is already live (only the permalink lookup failed): never upload it again
            p.update({"status": "posted", "posted_at": dt.datetime.now().isoformat(timespec="seconds"),
                      "error": f"posted, but the follow-up call failed: {str(e)[:200]}"})
            print(f"{p['key']}: POSTED (media {p['media_id']}), follow-up failed: {e}")
            return True
        p.update({"status": "failed", "error": str(e)[:300]})
        print(f"{p['key']}: FAILED {e}")
        return False


def _upload(p, f, s, kind, host, v, tok, link_state):
    """Create the Reels container and send the file; returns the container id (also kept on the entry)."""
    params = {"media_type": "REELS", "caption": p["caption"],
              "share_to_feed": "true" if s.get("share_to_feed", True) else "false"}
    if p.get("thumb_offset_ms") is not None:
        params["thumb_offset"] = str(int(p["thumb_offset_ms"]))
    if kind == "facebook":
        params["upload_type"] = "resumable"
        cid = _call("POST", f"{host}/{v}/{s['ig_user_id']}/media", params)["id"]
        p.update({"container_id": cid, "container_at": dt.datetime.now().isoformat(timespec="seconds")})
        data = f.read_bytes()
        try:
            _call("POST", f"https://rupload.facebook.com/ig-api-upload/{v}/{cid}",
                  headers={"Authorization": f"OAuth {tok}", "offset": "0", "file_size": str(len(data))},
                  data=data, timeout=600)
        except ClipError as e:
            # 2026-11-19: rupload sometimes answers ProcessingFailedError although the container still
            # finishes; let the container status below decide.
            if "ProcessingFailedError" not in str(e):
                raise
            print(f"{p['key']}: upload answered ProcessingFailedError, waiting on the container anyway")
    else:
        if link_state.get("server") is None:
            link_state["server"] = _OneFileServer()
            link_state["tunnel"] = _Tunnel(link_state["server"].port)
        url = link_state["tunnel"].url + link_state["server"].add(f)
        _wait_public(url)
        params["video_url"] = url
        cid = _call("POST", f"{host}/{v}/{s['ig_user_id']}/media", params)["id"]
    p.update({"container_id": cid, "container_at": dt.datetime.now().isoformat(timespec="seconds")})
    return cid


def _first_comment(p, host, v):
    """Post the entry's 'comment' from the account itself, right after publishing - only when the user chose a
    comment mode other than "none" during setup. Needs instagram_business_manage_comments (Instagram Login) or
    instagram_manage_comments (Facebook Login). Meta's API cannot pin comments: the user pins it in the app."""
    if not p.get("comment") or p.get("comment_id") or not p.get("media_id"):
        return
    if _settings().get("comment_mode", "none") == "none":
        return
    try:
        r = _call("POST", f"{host}/{v}/{p['media_id']}/comments", {"message": p["comment"]})
        p["comment_id"] = r.get("id")
        p.pop("comment_error", None)
        print(f"{p['key']}: first comment posted")
    except ClipError as e:
        p["comment_error"] = str(e)[:300]
        print(f"{p['key']}: comment FAILED {e}")


def comment(keys):
    """Post the first comment on posted entries that don't have it yet (keys or 'all')."""
    tok = _token()
    kind = _kind(tok)
    s = _settings()
    if s.get("comment_mode", "none") == "none":
        print("comment_mode is 'none': no comments are posted (change it with `clip ig config comment_mode=...`)")
        return
    q = _load_queue()
    for p in q["posts"]:
        if p["status"] == "posted" and p.get("comment") and not p.get("comment_id") and \
                (keys == ["all"] or _mine(p, keys)):
            _first_comment(p, _host(kind), s["api_version"])
            _store({k: p[k] for k in ("key", "comment_id", "comment_error") if k in p})


def _caption_and_comment(entry, default_caption):
    """plan/instagram.json value: a caption string, or {"caption": ..., "comment": ...}."""
    if isinstance(entry, dict):
        return entry.get("caption") or default_caption, entry.get("comment")
    return entry or default_caption, None


def add(slug, ids=None):
    """Queue QC-passed clips of a project as drafts. Caption from plan/instagram.json (written by Claude) or a
    default. First comment by config/instagram.json "comment_mode": none -> no comment; fixed -> "first_comment";
    claude -> the clip's "comment" in plan/instagram.json (Claude writes one per clip)."""
    from .util import project_dir, safe_name
    pdir = project_dir(slug)
    pj = load_json(pdir / "project.json")
    plan = load_json(pdir / "plan" / "clips.json")["clips"]
    caps = load_json(pdir / "plan" / "instagram.json", {})
    s = _settings()
    mode = s.get("comment_mode", "none")
    entries = []
    for c in plan:
        if ids and c["id"] not in ids:
            continue
        key = f"{slug}/{c['id']}"
        qc = load_json(pdir / "clips" / c["id"] / "qc.json", {})
        if not qc.get("ok"):
            print(f"{key}: skipped (QC not passed)")
            continue
        f = pdir / "exports" / f"{c['id']} - {safe_name(c.get('title') or c['id'], 60)}.mp4"
        if not f.exists():
            print(f"{key}: skipped (no export)")
            continue
        default_cap = ((c.get("hook") or c.get("title") or "").replace("*", "") +
                       f"\n\n🎥 {pj.get('channel')} live stream\n#gta6 #gtavi #gaming #streamer #funny")
        cap, own_comment = _caption_and_comment(caps.get(c["id"]), default_cap)
        e = {"key": key, "file": str(f), "caption": cap, "status": "draft", "post_at": None}
        if mode == "fixed" and s.get("first_comment"):
            e["comment"] = s["first_comment"]
        elif mode == "claude":
            if own_comment:
                e["comment"] = own_comment
            else:
                print(f"{key}: no comment written in plan/instagram.json (comment_mode is 'claude')")
        entries.append(e)

    def f(q):
        have = {p["key"]: p for p in q["posts"]}
        n = 0
        for e in entries:
            old = have.get(e["key"])
            if old is None:
                q["posts"].append(e)
                n += 1
            elif old["status"] in ("draft", "failed"):
                old.update({k: e[k] for k in ("file", "caption") if k in e})
                if "comment" in e:
                    old["comment"] = e["comment"]
                else:
                    old.pop("comment", None)
        return n, len(q["posts"])
    n, total = _edit_queue(f)
    print(f"{n} clips added as drafts ({total} in the queue)")
    return [e["key"] for e in entries]


def _mine(p, targets):
    """Entry matches a list of slugs or queue keys (None = every entry)."""
    return not targets or p["key"] in targets or p["key"].split("/")[0] in targets


def _next_slot(q, every, exclude=()):
    """First free time for a new post: one interval after the latest post time already in the queue (scheduled,
    posting or posted), and never before the next full quarter hour from now."""
    now = dt.datetime.now().replace(second=0, microsecond=0)
    t = now + dt.timedelta(minutes=(15 - now.minute % 15) or 15)
    last = [dt.datetime.fromisoformat(p["post_at"]) for p in q["posts"]
            if p.get("post_at") and p["key"] not in exclude
            and p["status"] in ("draft", "approved", "posting", "posted")]
    last += [dt.datetime.fromisoformat(p["posted_at"]).replace(second=0) for p in q["posts"]
             if p.get("posted_at") and p["key"] not in exclude]
    if last:
        t = max(t, max(last) + dt.timedelta(minutes=every))
    return t


def schedule(start, per_day=3, order=None, targets=None, every=None, after_last=False):
    """Assign post times to unposted entries of `targets` (slugs/keys; default all): either per_day slots from
    the config times, or one every `every` minutes, starting at `start` (local) - or, with after_last, one
    interval after the latest post already in the queue (keeps a steady 1-per-interval rhythm across batches)."""
    times = _settings()["times"][:per_day]
    if not start and not after_last:
        raise ClipError("give --start 'YYYY-MM-DD HH:MM' or --after-last")

    def f(q):
        posts = [p for p in q["posts"] if p["status"] in ("draft", "approved", "failed") and _mine(p, targets)]
        if order:
            rank = {k: i for i, k in enumerate(order)}
            posts.sort(key=lambda p: rank.get(p["key"], 999))
        step = every or 60
        if after_last:
            t0 = _next_slot(q, step, exclude={p["key"] for p in posts})
        else:
            t0 = dt.datetime.fromisoformat(start)
        if every or after_last:
            slots = [t0 + dt.timedelta(minutes=step * i) for i in range(len(posts))]
        else:
            slots, day = [], t0.date()
            while len(slots) < len(posts):
                for hm in times:
                    h, m = map(int, hm.split(":"))
                    s = dt.datetime.combine(day, dt.time(h, m))
                    if s >= t0 and len(slots) < len(posts):
                        slots.append(s)
                day += dt.timedelta(days=1)
        for p, s in zip(posts, slots):
            p["post_at"] = s.isoformat(timespec="minutes")
        q["posts"].sort(key=lambda p: p.get("post_at") or "9999")
    _edit_queue(f)
    show()


def approve(keys):
    every = int(_settings().get("every_min") or 60)

    def f(q):
        n, late = 0, []
        now = dt.datetime.now()
        for p in q["posts"]:
            if p["status"] in ("draft", "failed") and (keys == ["all"] or _mine(p, keys)):
                p["status"] = "approved"
                n += 1
                if not p.get("post_at") or dt.datetime.fromisoformat(p["post_at"]) < now - dt.timedelta(minutes=5):
                    late.append(p)
        # approved after their slot passed (or never scheduled): give them the next free slots instead of letting
        # the poster publish them all at once
        if late:
            t = _next_slot(q, every, exclude={p["key"] for p in late})
            for i, p in enumerate(late):
                p["post_at"] = (t + dt.timedelta(minutes=every * i)).isoformat(timespec="minutes")
            q["posts"].sort(key=lambda p: p.get("post_at") or "9999")
        return n, len(late)
    n, late = _edit_queue(f)
    print(f"{n} posts approved" + (f" ({late} rescheduled to the next free slots)" if late else ""))


def queue_hours():
    """(hours until the last queued post, number of posts still ahead) - for the autopilot's queue cap."""
    q = _load_queue()
    now = dt.datetime.now()
    ahead = [dt.datetime.fromisoformat(p["post_at"]) for p in q["posts"]
             if p.get("post_at") and p["status"] in ("draft", "approved", "posting")]
    if not ahead:
        return 0.0, 0
    return max(0.0, (max(ahead) - now).total_seconds() / 3600), len(ahead)


def autoqueue(slug, ids=None, order=None):
    """Autopilot hand-off for one finished project: queue its QC-passed clips, give them the next free slots
    (one every `every_min` minutes after the last post already queued), approve them when the user switched
    autopilot on, and register the project for clean-up after its last post when purge_after_post is on."""
    s = _settings()
    keys = add(slug, ids=ids)
    if not keys:
        print("nothing to queue")
        return 0
    if order:
        order = [k if "/" in k else f"{slug}/{k}" for k in order]
    schedule(None, order=order, targets=keys, every=int(s.get("every_min") or 60), after_last=True)
    if s.get("autopilot"):
        approve(keys)
    else:
        print("autopilot is OFF: these posts stay drafts until the user approves them in chat "
              "(then `clip ig approve <keys>`)")
    if s.get("purge_after_post"):
        m = load_json(PURGE, {"slugs": {}, "closed": False})
        m["slugs"].setdefault(slug, {"added": dt.datetime.now().isoformat(timespec="seconds")})
        m["closed"] = False
        save_json(PURGE, m)
        print(f"{slug}: will be deleted (folder + Resolve timelines) once all its clips are posted")
    h, n = queue_hours()
    print(f"queue: {n} posts ahead, the last one in {h:.1f} h (cap {s.get('max_queue_hours')} h)")
    return 0


def set_config(pairs):
    """`clip ig config key=value ...`: record the user's setup choices in config/instagram.json."""
    s = load_json(SETTINGS, {})
    if not pairs:
        cur = _settings()
        for k in CONFIG_KEYS:
            print(f"  {k} = {cur.get(k)!r}")
        print(f"  autopilot_since = {cur.get('autopilot_since')!r}")
        return
    for pair in pairs:
        if "=" not in pair:
            raise ClipError(f"expected key=value, got '{pair}'")
        k, val = pair.split("=", 1)
        k = k.strip()
        kind = CONFIG_KEYS.get(k)
        if kind is None:
            raise ClipError(f"unknown setting '{k}' (known: {', '.join(CONFIG_KEYS)})")
        if isinstance(kind, tuple):
            if val not in kind:
                raise ClipError(f"{k} must be one of {', '.join(kind)}")
        elif kind is bool:
            if val.lower() not in ("true", "false", "on", "off", "yes", "no", "1", "0"):
                raise ClipError(f"{k} must be true or false")
            val = val.lower() in ("true", "on", "yes", "1")
        elif kind is int:
            val = int(val)
        elif val.strip() == "":
            val = None
        s[k] = val
        if k == "autopilot":
            s["autopilot_since"] = dt.datetime.now().isoformat(timespec="seconds") if val else None
        print(f"  {k} = {val!r}")
    save_json(SETTINGS, s)


PURGE = PROJECTS / "_instagram" / "purge.json"   # {"slugs": {slug: {...}}, "closed": bool}


def _purge_ready(slug, q):
    mine = [p for p in q["posts"] if p["key"].split("/")[0] == slug]
    return bool(mine) and all(p["status"] == "posted" for p in mine)


def purge_done(log=print):
    """Purge every project listed in purge.json whose queue entries are all posted. Resolve items whose cleanup
    was skipped (Resolve closed / another project open) are retried on the next call."""
    from .purge import purge, resolve_items
    m = load_json(PURGE, {"slugs": {}, "closed": False})
    q = _load_queue()
    changed = False
    for slug, st in m["slugs"].items():
        if st.get("files") != "done" and _purge_ready(slug, q):
            r = purge(slug)
            st.update({"files": r["files"], "resolve": r["resolve"], "at": dt.datetime.now().isoformat(timespec="seconds")})
            if r["resolve"].startswith("skipped"):
                st["resolve_json"] = r["resolve_json"]
            log(f"purge {slug}: files {r['files']}; Resolve {r['resolve']}")
            changed = True
        elif st.get("files") == "done" and str(st.get("resolve", "")).startswith("skipped") and st.get("resolve_json"):
            try:
                res = resolve_items(slug, st["resolve_json"])
            except Exception as e:
                res = f"skipped: {e}"
            if not res.startswith("skipped"):
                st["resolve"] = res
                st.pop("resolve_json", None)
                log(f"purge {slug}: Resolve {res}")
                changed = True
    if changed:
        save_json(PURGE, m)
    return m


def loop(every=5, purge=True):
    """Post due approved entries every `every` minutes; after each pass purge the projects in purge.json whose
    clips are all posted. Exits when purge.json is closed and nothing is left to post or purge."""
    def log(msg):
        line = f"{dt.datetime.now():%Y-%m-%d %H:%M} {msg}"
        print(line, flush=True)
        with open(PROJECTS / "_instagram" / "loop.log", "a", encoding="utf-8") as f:
            f.write(line + "\n")
    import sys
    sys.stdout.reconfigure(line_buffering=True)   # post() prints show up in the redirected log right away
    log(f"loop started (every {every} min, purge {'on' if purge else 'off'})")
    while True:
        try:
            post(due=True)
        except ClipError as e:
            log(f"post pass failed: {e}")
        m = purge_done(log) if purge else load_json(PURGE, {"slugs": {}, "closed": False})
        q = _load_queue()
        slugs = set(m["slugs"])
        waiting = [p for p in q["posts"] if p["key"].split("/")[0] in slugs and p["status"] in ("approved", "posting")]
        unpurged = [s for s, st in m["slugs"].items() if st.get("files") != "done"
                    or str(st.get("resolve", "")).startswith("skipped")]
        failed = [p["key"] for p in q["posts"] if p["key"].split("/")[0] in slugs and p["status"] == "failed"]
        if m.get("closed") and not waiting:
            log(f"loop done: nothing left to post; not purged: {unpurged or 'none'}; failed posts: {failed or 'none'}")
            return 1 if (failed or unpurged) else 0
        time.sleep(every * 60)


# ------------------------------------------------------------------ background poster
IGDIR = PROJECTS / "_instagram"
POSTER_LOG = IGDIR / "poster.log"
HEARTBEAT = IGDIR / "poster_heartbeat.txt"
TICK_LOCK = IGDIR / "tick.lock"
TASK_NAME = "GTA6 Clipper Poster"            # Windows Task Scheduler
AGENT_LABEL = "com.gta6clipper.poster"       # macOS launchd
TICK_MINUTES = 10


def _plog(text):
    IGDIR.mkdir(parents=True, exist_ok=True)
    if POSTER_LOG.exists() and POSTER_LOG.stat().st_size > 5e6:
        POSTER_LOG.replace(POSTER_LOG.with_suffix(".log.1"))
    stamp = f"{dt.datetime.now():%Y-%m-%d %H:%M}"
    with open(POSTER_LOG, "a", encoding="utf-8") as f:
        for line in text.rstrip().splitlines():
            f.write(f"{stamp} {line}\n")


def _auto_refresh():
    """Instagram Login tokens live 60 days: refresh once a week so a long-running autopilot never loses access.
    (Facebook Login / system-user tokens are refreshed in Meta's tools, not here.)"""
    try:
        tok = _token()
    except ClipError:
        return
    if not tok.startswith("IG"):
        return
    if time.time() - TOKEN_FILE.stat().st_mtime < 7 * 86400:
        return
    try:
        refresh()
    except ClipError as e:
        print(f"token refresh failed: {e}")


def tick():
    """One background pass: post every approved entry whose time has come, then purge finished projects.
    Run by the poster service every 10 minutes; safe to run by hand. Output goes to projects/_instagram/poster.log."""
    import io
    IGDIR.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(TICK_LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        try:
            if time.time() - TICK_LOCK.stat().st_mtime < 3 * 3600:   # a long upload is still running
                return 0
            TICK_LOCK.unlink(missing_ok=True)
            fd = os.open(TICK_LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except (FileExistsError, FileNotFoundError):
            return 0
    os.write(fd, str(os.getpid()).encode())
    os.close(fd)
    buf = io.StringIO()
    rc = 0
    try:
        with contextlib.redirect_stdout(buf):
            _auto_refresh()
            try:
                rc = post(due=True) or 0
            except ClipError as e:
                print(f"post pass failed: {e}")
                rc = 1
            try:
                purge_done(print)
            except Exception as e:   # clean-up problems must never stop posting
                print(f"purge pass failed: {e}")
    finally:
        TICK_LOCK.unlink(missing_ok=True)
        HEARTBEAT.write_text(dt.datetime.now().isoformat(timespec="seconds"), encoding="utf-8")
        out = "\n".join(l for l in buf.getvalue().splitlines() if not l.startswith("nothing approved to post"))
        if out.strip():
            _plog(out)
            if sys.stdout is not None:
                print(out)
    return rc


def _venv_python(windowless=False):
    exe = Path(sys.executable)
    if os.name == "nt" and windowless:
        w = exe.with_name("pythonw.exe")
        if w.exists():
            return w
    return exe


def _ps(script):
    """Run a PowerShell script (Windows PowerShell 5.1 ships with every Windows 10/11)."""
    import base64
    enc = base64.b64encode(script.encode("utf-16-le")).decode()
    return subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                           "-EncodedCommand", enc], capture_output=True, text=True, errors="replace")


def _win_script():
    q = lambda p: str(p).replace("'", "''")
    return f"""$ErrorActionPreference = 'Stop'
$a = New-ScheduledTaskAction -Execute '{q(_venv_python(windowless=True))}' -Argument '-m clipper.cli ig tick' -WorkingDirectory '{q(ROOT)}'
$t = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes {TICK_MINUTES})
$s = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 3)
Register-ScheduledTask -TaskName '{TASK_NAME}' -Description 'Posts scheduled GTA 6 Clipper Reels (clip ig tick) every {TICK_MINUTES} minutes.' -Action $a -Trigger $t -Settings $s -Force | Out-Null
'installed'
"""


def _mac_plist():
    import plistlib
    path = Path.home() / "Library" / "LaunchAgents" / f"{AGENT_LABEL}.plist"
    data = {
        "Label": AGENT_LABEL,
        "ProgramArguments": [str(_venv_python()), "-m", "clipper.cli", "ig", "tick"],
        "WorkingDirectory": str(ROOT),
        "StartInterval": TICK_MINUTES * 60,
        "RunAtLoad": True,
        "StandardOutPath": str(IGDIR / "poster_stdout.log"),
        "StandardErrorPath": str(IGDIR / "poster_stdout.log"),
        "EnvironmentVariables": {"PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"},
    }
    return path, plistlib.dumps(data).decode()


def service(action="status", dry_run=False):
    """`clip ig service install|uninstall|status`: the background poster that runs `clip ig tick` every 10 minutes
    (Windows Task Scheduler task for this user / macOS launchd agent). Install only after the user said yes."""
    IGDIR.mkdir(parents=True, exist_ok=True)
    if sys.platform == "darwin":
        path, plist = _mac_plist()
        dom = f"gui/{os.getuid()}"
        if action == "install":
            if dry_run:
                print(f"would write {path}:\n{plist}")
                return 0
            if any(str(ROOT).startswith(str(Path.home() / d)) for d in ("Desktop", "Documents", "Downloads")):
                print("WARNING: this folder is inside Desktop/Documents/Downloads. macOS blocks background jobs "
                      "from those folders unless Python has Full Disk Access; move the folder to your home folder "
                      "(e.g. ~/GTA6-Clipper) for a reliable poster.")
            path.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(["launchctl", "bootout", f"{dom}/{AGENT_LABEL}"], capture_output=True)
            path.write_text(plist, encoding="utf-8")
            r = subprocess.run(["launchctl", "bootstrap", dom, str(path)], capture_output=True, text=True)
            if r.returncode != 0:
                raise ClipError(f"launchctl bootstrap failed: {(r.stderr or r.stdout).strip()}")
            print(f"poster installed: launchd agent {AGENT_LABEL}, every {TICK_MINUTES} min (log: {POSTER_LOG})")
        elif action == "uninstall":
            subprocess.run(["launchctl", "bootout", f"{dom}/{AGENT_LABEL}"], capture_output=True)
            path.unlink(missing_ok=True)
            print("poster removed")
        else:
            r = subprocess.run(["launchctl", "print", f"{dom}/{AGENT_LABEL}"], capture_output=True, text=True)
            print(f"poster: {'installed' if r.returncode == 0 else 'NOT installed'} (launchd {AGENT_LABEL})")
    elif os.name == "nt":
        if action == "install":
            script = _win_script()
            if dry_run:
                print(script)
                return 0
            r = _ps(script)
            if r.returncode != 0 or "installed" not in r.stdout:
                raise ClipError(f"could not create the scheduled task: {(r.stderr or r.stdout).strip()[-600:]}")
            print(f"poster installed: Task Scheduler task '{TASK_NAME}', every {TICK_MINUTES} min while you are "
                  f"signed in, also on battery (log: {POSTER_LOG})")
        elif action == "uninstall":
            r = _ps(f"Unregister-ScheduledTask -TaskName '{TASK_NAME}' -Confirm:$false; 'removed'")
            print("poster removed" if "removed" in r.stdout else f"nothing removed: {(r.stderr or '').strip()[-300:]}")
        else:
            r = _ps(f"$i = Get-ScheduledTask -TaskName '{TASK_NAME}' -ErrorAction Stop | Get-ScheduledTaskInfo; "
                    f"'last run: ' + $i.LastRunTime + ' result ' + $i.LastTaskResult + ' | next run: ' + $i.NextRunTime")
            print(f"poster: installed, {r.stdout.strip()}" if r.returncode == 0 else
                  f"poster: NOT installed (task '{TASK_NAME}')")
    else:
        raise ClipError("the poster service supports Windows and macOS; elsewhere run `clip ig loop` in a terminal")
    if action == "status":
        hb = HEARTBEAT.read_text(encoding="utf-8") if HEARTBEAT.exists() else "never"
        print(f"last pass: {hb}")
        if POSTER_LOG.exists():
            tail = POSTER_LOG.read_text(encoding="utf-8", errors="replace").splitlines()[-8:]
            print("recent log:\n  " + "\n  ".join(tail))
    return 0
