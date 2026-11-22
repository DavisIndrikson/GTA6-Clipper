"""Single entry point: `uv run clip <command> ...`  (see CLAUDE.md for the workflow)."""
from __future__ import annotations

import argparse
import sys

from .util import ClipError


def main(argv=None):
    ap = argparse.ArgumentParser(prog="clip", description="GTA 6 stream clipper")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("doctor", help="detect tools, write config/machine.json")
    p.add_argument("--cookies", help="browser whose YouTube cookies yt-dlp uses (firefox, chrome, edge, brave, opera, "
                   "safari...) - tested before it is saved; 'none' clears it")

    p = sub.add_parser("setup", help="download + verify yt-dlp and the models, index SFX, doctor, self-test")
    p.add_argument("--skip-whisper", action="store_true", help="don't pre-download the speech model")

    p = sub.add_parser("search", help="find long-form GTA 6 streams on YouTube")
    p.add_argument("query", nargs="?", help="search text (default: every streamer in config/streamers.json)")
    p.add_argument("-n", type=int, default=15, help="results per query")
    p.add_argument("--min-minutes", type=float, default=30)
    p.add_argument("--live", action="store_true", help="also list streams that are live right now")
    p.add_argument("--all", action="store_true", help="also list streams already clipped or skipped here")

    p = sub.add_parser("skip", help="never offer this VOD again in `clip search` (looked at, not clip-worthy)")
    p.add_argument("url")
    p.add_argument("why", nargs="?", default="")

    p = sub.add_parser("fetch", help="download a stream (metadata, captions, chat, audio, video)")
    p.add_argument("url")
    p.add_argument("--slug")
    p.add_argument("--no-video", action="store_true")
    p.add_argument("--video-only", action="store_true")
    p.add_argument("--no-chat", action="store_true", help="skip live-chat replay (slow on very long streams)")

    p = sub.add_parser("scan", help="audio features + candidate funny moments")
    p.add_argument("slug")
    p.add_argument("--force", action="store_true")
    p.add_argument("-k", type=int, help="number of candidates")
    p.add_argument("--rank-only", action="store_true", help="re-rank from existing features")

    p = sub.add_parser("asr", help="transcribe candidate windows")
    p.add_argument("slug")
    p.add_argument("--ids", help="comma list of candidate ids")
    p.add_argument("--model")

    p = sub.add_parser("moments", help="compact view of candidates with transcript")
    p.add_argument("slug")
    p.add_argument("--ids")
    p.add_argument("--top", type=int)
    p.add_argument("--rank", action="store_true", help="order by rank instead of time")

    p = sub.add_parser("words", help="transcript of a source time range")
    p.add_argument("slug")
    p.add_argument("start")
    p.add_argument("end")

    p = sub.add_parser("sheet", help="contact sheet of candidates or clips")
    p.add_argument("slug")
    p.add_argument("--ids", help="candidate ids (K..) or clip ids (C..)")
    p.add_argument("--cols", type=int, default=6)

    p = sub.add_parser("proxy", help="sparse fast-seeking video of the chosen moments (finished live streams)")
    p.add_argument("slug")
    p.add_argument("--ids", help="candidate ids whose scan windows to include, e.g. K01,K07")
    p.add_argument("--add", action="append", help="extra source range a-b (m:ss or h:mm:ss), repeatable")
    p.add_argument("--no-plan", action="store_true", help="don't include plan/clips.json in/out ranges")

    p = sub.add_parser("layout", help="detect the facecam and gameplay areas")
    p.add_argument("slug")
    p.add_argument("--samples", type=int, default=48)

    sub.add_parser("sfx", help="synthesise the sound-effect library and index user SFX")
    sub.add_parser("selftest", help="frame-exact decode + timeline maths on synthetic media")

    p = sub.add_parser("render", help="render planned clips")
    p.add_argument("slug")
    p.add_argument("--ids", help="comma list of clip ids (default: all)")
    p.add_argument("--draft", action="store_true", help="fast preview: half size, no Resolve layers")
    p.add_argument("--no-layers", action="store_true", help="skip separate layer files for Resolve")
    p.add_argument("--force", action="store_true", help="re-render even if the plan entry is unchanged")

    p = sub.add_parser("qc", help="check rendered clips")
    p.add_argument("slug")
    p.add_argument("--ids")

    p = sub.add_parser("review", help="contact sheet of rendered clips")
    p.add_argument("slug")
    p.add_argument("--ids")

    p = sub.add_parser("ig", help="Instagram Reels: whoami | config | add | autoqueue | schedule | show | approve | "
                       "post | comment | tick | loop | service | refresh")
    p.add_argument("action", choices=["whoami", "config", "add", "autoqueue", "schedule", "show", "approve", "post",
                                      "comment", "tick", "loop", "service", "refresh"])
    p.add_argument("target", nargs="*", help="add/autoqueue: <slug>; schedule: slugs/keys to time (default all); "
                   "approve/post/comment: queue keys like slug/C01, slugs, or 'all'; config: key=value ...; "
                   "service: install | uninstall | status")
    p.add_argument("--ids", help="add: comma list of clip ids")
    p.add_argument("--start", help="schedule: first slot, local time 'YYYY-MM-DD HH:MM'")
    p.add_argument("--per-day", type=int, default=3)
    p.add_argument("--every", type=int, help="schedule: one post every N minutes instead of the daily slots; "
                   "loop: minutes between passes (default 5)")
    p.add_argument("--no-purge", action="store_true", help="loop: don't purge finished projects (purge.json)")
    p.add_argument("--order", help="schedule/autoqueue: comma list of queue keys (or clip ids) in posting order")
    p.add_argument("--after-last", action="store_true", help="schedule: start one interval after the last post "
                   "already in the queue (with --every, default 60 min)")
    p.add_argument("--due", action="store_true", help="post: only approved posts whose time has come")
    p.add_argument("--dry-run", action="store_true", help="post: prepare and check files, don't upload; "
                   "service install: print what would be installed")
    p.add_argument("--account", help="whoami: Instagram account ID (for system-user tokens without pages_show_list)")

    p = sub.add_parser("slim", help="delete a queued project's downloaded stream to free disk (keeps the clips)")
    p.add_argument("slug")

    p = sub.add_parser("purge", help="delete a project's folder and its Resolve timelines/bin (asks first)")
    p.add_argument("slug")
    p.add_argument("--yes", action="store_true", help="don't ask")

    p = sub.add_parser("status", help="one-screen summary of a project")
    p.add_argument("slug", nargs="?")

    a = ap.parse_args(argv)
    try:
        return _dispatch(a) or 0
    except ClipError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


def _ids(s):
    return [x.strip() for x in s.split(",") if x.strip()] if s else None


def _dispatch(a):
    c = a.cmd
    if c == "doctor":
        from .doctor import run_doctor
        return run_doctor(cookies=a.cookies)
    if c == "setup":
        from .setup_assets import run_setup
        return run_setup(skip_whisper=a.skip_whisper)
    if c == "search":
        from .yt import search
        search(a.query, n=a.n, min_minutes=a.min_minutes, include_live=a.live, include_done=a.all)
    elif c == "skip":
        from .yt import skip
        skip(a.url, a.why)
    elif c == "fetch":
        from .yt import fetch
        if a.video_only:
            fetch(a.url, slug=a.slug, meta=a.slug is None, audio=False, subs=False, video=True)
        else:
            fetch(a.url, slug=a.slug, video=not a.no_video, chat=not a.no_chat)
    elif c == "scan":
        from .scan import audio_features, candidates
        if not a.rank_only:
            audio_features(a.slug, force=a.force)
        candidates(a.slug, k=a.k)
    elif c == "asr":
        from .asr import windows
        windows(a.slug, ids=_ids(a.ids), model_name=a.model)
    elif c == "moments":
        from .views import moments
        moments(a.slug, ids=_ids(a.ids), top=a.top, order="rank" if a.rank else "time")
    elif c == "words":
        from .views import words_range
        words_range(a.slug, a.start, a.end)
    elif c == "sheet":
        from .review import sheet
        sheet(a.slug, ids=_ids(a.ids), cols=a.cols)
    elif c == "layout":
        from .layout import detect
        detect(a.slug, samples=a.samples)
    elif c == "proxy":
        from .proxy import build
        build(a.slug, ids=_ids(a.ids), extra=[x.rsplit("-", 1) for x in a.add or []], use_plan=not a.no_plan)
    elif c == "sfx":
        from .sfx import build_library
        build_library()
    elif c == "selftest":
        from .selftest import run_selftest
        return run_selftest()
    elif c == "render":
        from .render import render_all
        return render_all(a.slug, ids=_ids(a.ids), draft=a.draft, layers=not (a.no_layers or a.draft), force=a.force)
    elif c == "qc":
        from .qc import qc_all
        return qc_all(a.slug, ids=_ids(a.ids))
    elif c == "review":
        from .review import review_clips
        review_clips(a.slug, ids=_ids(a.ids))
    elif c == "ig":
        from . import insta
        act = a.action
        if act == "whoami":
            insta.whoami(account=a.account)
        elif act == "refresh":
            insta.refresh()
        elif act == "show":
            insta.show()
        elif act == "add":
            for slug in a.target:
                insta.add(slug, ids=_ids(a.ids))
        elif act == "schedule":
            insta.schedule(a.start, per_day=a.per_day, order=_ids(a.order), targets=a.target or None,
                           every=a.every, after_last=a.after_last)
        elif act == "autoqueue":
            for slug in a.target:
                insta.autoqueue(slug, ids=_ids(a.ids), order=_ids(a.order))
        elif act == "config":
            insta.set_config(a.target)
        elif act == "tick":
            return insta.tick()
        elif act == "service":
            return insta.service((a.target or ["status"])[0], dry_run=a.dry_run)
        elif act == "approve":
            insta.approve(a.target)
        elif act == "post":
            return insta.post(keys=a.target or None, due=a.due, dry_run=a.dry_run)
        elif act == "comment":
            insta.comment(a.target or ["all"])
        elif act == "loop":
            return insta.loop(every=a.every or 5, purge=not a.no_purge)
    elif c == "slim":
        from .purge import slim
        slim(a.slug)
    elif c == "purge":
        from .purge import purge
        if not a.yes and input(f"delete projects/{a.slug} and its Resolve timelines? [y/N] ").strip().lower() != "y":
            return 1
        print(purge(a.slug))
    elif c == "status":
        from .status import status
        status(a.slug)
    return 0


if __name__ == "__main__":
    sys.exit(main())
