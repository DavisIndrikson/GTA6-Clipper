---
name: clip-stream
description: Find a long GTA 6 stream (IShowSpeed, Kai Cenat or another popular streamer) on YouTube, download it with yt-dlp, find the funniest moments, render vertical clips with captions and meme sound effects, build DaVinci Resolve timelines when the connector is present, and queue the clips for Instagram. Use for "clip <streamer>'s stream", "make funny clips from <url>", "find new GTA 6 streams".
---

# Clip one stream (checklist; details in CLAUDE.md and docs/CLIPPER_SPEC.md)

Run every command as `uv run clip ...` from the tool folder (Windows: PowerShell; if `uv` is not found, use the
absolute path in config/machine.json -> tools.uv).

1. Find: `uv run clip search` (all streamers in config/streamers.json) or `uv run clip search "kai cenat gta 6"`.
   Pick a finished VOD: G (GTA 6) + F (Speed / Kai Cenat featured) first, then G + O (own channel); 1-8 h; high views.
   Already-clipped streams are hidden. Looked at one and it isn't clip-worthy? `uv run clip skip <url> "why"`.
2. Download (background): `uv run clip fetch <url>`. Note the slug it prints.
3. When project.json has files.audio: `uv run clip scan <slug>`, then `uv run clip asr <slug>` (background).
4. `uv run clip moments <slug> --rank` (compact). Shortlist the genuinely funny ones.
5. Once the video is in (or after `clip proxy <slug> --ids K..` for a fragmented live-stream VOD):
   `uv run clip layout <slug>` -> view `review/layout.jpg`; `uv run clip sheet <slug> --ids K..` -> view.
6. `uv run clip words <slug> <from> <to>` for exact word times.
7. Write `plan/clips.json` (format in CLAUDE.md), every clip and event with a `why`. Clip ids: 2 letters for the
   stream + number (SP01, KC01...). End on the reaction (`out` ~0.4 s after the last word, or when the payoff on
   screen is over): no end freeze, no end emoji. Emoji: none unless one genuinely adds something (max 1 per clip
   incl. hook; render refuses more).
8. `uv run clip render <slug>` (background if > 3 clips), `uv run clip qc <slug>`, `uv run clip review <slug>` ->
   VIEW every sheet; fix, `--force` re-render. Nothing is done before qc passes and the sheets were viewed.
9. Resolve, only if its connector tools are in this session (run_script_unsafe, timeout 60): `rb.ensure`,
   `rb.build_selects`, then `rb.build_clip` one clip per call; check reports.
10. Write `plan/instagram.json` (house caption style in CLAUDE.md; per-clip comments only when comment_mode is
    "claude"), then `uv run clip ig autoqueue <slug>` (best clip first or `--order`). Autopilot off: show the user the
    list, captions, comments and times, and approve only after their yes.
11. Report: exports folder, queue times, Resolve timelines, QC numbers, anything skipped and why. When autopilot is
    off, remind once about the streamer's permission / fair use.
