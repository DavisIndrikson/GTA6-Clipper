---
name: clip-autopilot
description: Run the GTA 6 Clipper on autopilot - find a long popular GTA 6 stream (ideally with IShowSpeed or Kai Cenat), cut every genuinely funny moment into vertical clips, queue them as Instagram Reels (one per hour), and repeat with the next stream until the usage limit or the queue cap stops it. Use for "run the autopilot", "keep clipping", "start the loop", "continue the clipper".
---

# Autopilot loop

Prerequisites (set up once by SETUP_PROMPT.md): `uv run clip doctor` passes, `uv run clip ig whoami` works, and the
user's choices are recorded (`uv run clip ig config` shows autopilot, every_min, comment_mode, max_queue_hours,
purge_after_post). If any is missing, stop and run the matching setup step with the user first.

## Start / resume
1. Read `projects/_autopilot/log.md` (create it if missing). If the last entry is an unfinished stream, continue that
   stream from its next step instead of searching (check `uv run clip status <slug>`).
2. `uv run clip ig service status`: the poster must be installed and its last pass recent (< 30 min while the
   computer was on). If it is not installed and the user agreed to it in setup, install it; otherwise warn once.
3. `uv run clip ig show`: handle failed posts as CLAUDE.md says ("Failed posts").

## One iteration (one stream)
1. Queue cap: `uv run clip ig config` -> max_queue_hours; `uv run clip ig show` -> time of the last scheduled post.
   If the queue already reaches past now + max_queue_hours, pause (see "Pausing").
2. Disk: `uv run clip doctor` prints free space. Below ~40 GB, prefer streams under 4 h; below ~20 GB, `uv run clip
   slim <slug>` the projects whose clips are all queued, and pause if that is not enough.
3. Find: `uv run clip search` and pick per the clip-stream skill (G + F first). Write the pick and why to the log.
   Nothing new and good? Try `uv run clip search "<streamer> gta 6"` for the featured streamers, then others from
   config/streamers.json. Still nothing: pause.
4. Run the clip-stream skill steps 2-10 for that stream. With autopilot on, `clip ig autoqueue` approves the clips
   itself: do not wait for the user. A stream with no genuinely funny moment: `uv run clip skip <url> "why"`, log it,
   next iteration.
5. After autoqueue: `uv run clip slim <slug>` when disk is tight (the exports stay; the Selects timeline goes
   offline). Append to the log: slug, clips queued (ids + times), anything skipped and why, next step.
6. Go straight to the next iteration. Do not end the turn between streams.

## Rules while looping
- Quality over count: as many clips as the stream genuinely has (often 3-10 from a long stream), never filler. Every
  clip passes `clip qc` and you VIEW its review sheet before it is queued.
- Keep context small: never load words.json or large JSON into context; use `clip moments`, `clip words`,
  `clip sheet`, `clip review`.
- YouTube "rate-limited": stop fetching, log the time, work on what is already downloaded, and try again >= 2 h later.
- A fetch, scan or render error: read it, fix what you can (CLAUDE.md lessons), log it, and move on to another stream
  rather than retrying in a tight loop.
- Never change the user's choices (autopilot, comment mode, interval, purge) on your own. Never print the token.

## Pausing
When the queue cap is reached, no good stream is left, or disk is full: write the reason and the time to the log,
then end the turn with a short summary (streams done, clips queued, next post time, why paused). If this session
runs under `/loop` (dynamic), schedule the next wake-up instead: about when the queue drops below the cap
(at least 1 h). The background poster keeps posting the queue either way.

## Summary for the user (when the turn ends)
Streams clipped this session, clips queued with their times, posts that went out (permalinks from `clip ig show`),
failures and what was done, disk left, why the loop stopped.
