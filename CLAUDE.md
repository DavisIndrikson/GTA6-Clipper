# GTA 6 Clipper (operating manual)

Turns long GTA 6 streams (IShowSpeed, Kai Cenat and other popular streamers) into funny vertical clips with captions,
meme sound effects and punch-in edits, posts them as Instagram Reels on a schedule, and (optionally) builds them as
editable timelines in DaVinci Resolve. Spec: `docs/CLIPPER_SPEC.md`. Ledger: `BUILD_STATUS.md`.
First run on a new computer: follow `SETUP_PROMPT.md` (setup questions, install, checks, first batch).

## Hard rules (short form)
- Plans are data, scripts are deterministic. Every creative choice (which moments, in/out, cuts, zooms, SFX,
  text) lives in `projects/<slug>/plan/clips.json` with a `why`. The engine never guesses.
- Downloaded source files are read-only. Never overwrite media Resolve may have imported: every render writes new
  `_vN` files; Resolve builders pick the newest.
- Resolve: work only in project "GTA6 Clipper"; only create bins/timelines (prefix-free names, no ":"); never delete or
  alter the user's own items or open any of their other projects.
- Nothing is "done" until `clip qc` passes and you have viewed `clip review` sheets.
- Footage, audio and transcripts stay on this computer. Only yt-dlp talks to YouTube; only `clip ig` talks to Meta.
- Captions never invent words: they come from the local transcript; slurs are bleeped and masked (config/censor.json).
  Joke text on freezes is fine, but never state made-up facts about real people.
- Publishing other people's streams is the user's call. Remind them once (during setup, and once per batch when
  autopilot is off) that they need the streamer's permission or their own fair-use judgement.
- Instagram token: the USER saves it in `config/instagram_token.txt`. Never print it, never ask for it in chat, never
  type it anywhere, never put it in a command line.
- Never post, comment or schedule anything the user has not allowed: autopilot and the comment mode are the user's
  own choices, recorded by `clip ig config` during setup (see "Instagram").

## Running commands
- Everything is `uv run clip <cmd>` from this folder. Windows: run it in PowerShell (uv started from Git Bash
  background jobs has failed with exit 127). If `uv` is not found (the PATH change from the installer only reaches
  new shells), call it by the absolute path recorded in `config/machine.json` -> tools.uv. macOS: zsh/bash.
- Long jobs (fetch, scan, asr, render of > 3 clips) run in the background; check their progress files
  (`analysis/scan_progress.txt`, `analysis/asr_progress.txt`, `source/fetch_video.log`) instead of polling quickly.
- Machine facts (CPU, RAM, GPU, hardware encoder, tool paths, cookies browser) are in `config/machine.json`
  (written by `clip doctor`). Read it instead of guessing. Intel laptops render with Quick Sync (~5x faster);
  every other machine renders the same picture with x264 (slower). Speech runs on the CPU (int8) everywhere.

## Commands
setup [--skip-whisper] | doctor [--cookies <browser|none>] | search ["query"] [--live] [--all] | skip <url> ["why"] |
fetch <url> [--no-video|--video-only|--no-chat] [--slug] | scan <slug> [--rank-only -k N] | asr <slug> [--ids K01,..] |
moments <slug> [--top N --rank --ids] | words <slug> <from> <to> | sheet <slug> [--ids] | layout <slug> | sfx |
proxy <slug> [--ids K01,..] [--add a-b] [--no-plan] | render <slug> [--ids C01,..] [--draft] [--force] | qc <slug> |
review <slug> | status [slug] | slim <slug> | purge <slug> |
ig whoami [--account ID] | ig config [key=value ...] | ig add <slug> | ig autoqueue <slug> [--order C03,C01,..] |
ig schedule [--start "YYYY-MM-DD HH:MM" | --after-last] [--every MIN] | ig show | ig approve <keys|slug|all> |
ig post <keys|all> | ig post --due | ig post --dry-run | ig comment <keys|all> | ig tick | ig loop |
ig service install|uninstall|status [--dry-run] | ig refresh

## Workflow: one stream (skill: .claude/skills/clip-stream)
1. `clip search` (or the user's URL). Only finished VODs: live streams must end first (fetch refuses `is_live`).
   Flags: G = GTA 6 in the title, F = Speed / Kai Cenat featured, O = streamer's own channel, D = already clipped
   here (hidden unless --all). Prefer G + F, then G + O, high views, 1-8 h long. Skip re-uploads/compilations.
2. `clip fetch <url>` in the background (meta, captions, chat replay, audio, then video). 1080p60 is ~2.4 GB/hour.
3. `clip scan` once project.json has files.audio (fetch writes it when the audio is complete; the video can still be
   downloading) -> candidates (laugh, scream, loudness, chat replay, heatmap). Then `clip asr` (background) and
   `clip moments --rank`. Read; never load words.json into context.
4. `clip layout` and VIEW review/layout.jpg. Fix plan/layout.json if the facecam box is wrong. Per clip choose layout:
   `stack` (facecam top 40 %, gameplay bottom), `full` (streamer camera fills the frame; set layout_opts cx/cy),
   `game` (fitted gameplay over blur), `face` (facecam only).
5. `clip sheet --ids K..` to see the moments, `clip words` around them for exact word times. Write plan/clips.json
   (format below). As many clips as are genuinely funny; skip moments that need context to land.
6. `clip render <slug>` (background for many clips), `clip qc`, `clip review` -> VIEW the sheets, fix, re-render
   (--force). Read each clip's ending strip: last caption, last word, nothing leaking in after the reaction.
7. Resolve (only when the DaVinci Resolve connector tools are in this session): ensure -> build_selects ->
   build_clip per clip. Report what was built. Without the connector, skip this step: the MP4s are complete.
8. Write `plan/instagram.json` (captions, and comments when comment_mode is "claude"), then `clip ig autoqueue <slug>`.
9. Tell the user where the MP4s are (`projects/<slug>/exports/`), what was queued and when it posts, anything held back.

## Autopilot loop (skill: .claude/skills/clip-autopilot)
The user's goal: find long popular GTA 6 streams (ideally with Speed or Kai Cenat in them), cut as many engaging
clips as the stream genuinely has, post one Reel per hour, and repeat until the Claude usage limit stops the
session. The background poster keeps posting the queue hourly while no session runs. The skill has the loop;
its rules: one stream per iteration, keep `projects/_autopilot/log.md` current (what was searched, picked,
skipped, queued, and the next step) so a resumed session continues instead of starting over, respect the queue cap
(`max_queue_hours`) and free disk space, never retry YouTube faster than the rate-limit lesson allows.

## plan/clips.json entry
`{"id","title","hook" (ONE line, *word* = accent colour, no emoji by default, shows first 3 s only),"why","in","out" (m:ss.ss source),
"cut": [[a,b],..] (dead air removed), "layout", "layout_opts", "captions": true, "events": [...]}`
Clip ids: two letters for the stream + number (e.g. "SP01" for a Speed stream), unique across projects, because queue
keys are `<slug>/<id>`.
Optional per clip: `"captions_y"`, `"caption_drop"`, `"caption_fix": [["m:ss.ss", "word"]]` (a clear mishearing, e.g.
"Chad" for "chat"), `"caption_src": "scan"` (use the stream's window transcript when the precise pass merges
sentences or captions game NPCs), `"blur"`, `"limiter_db"` (lower ceiling when qc reports a true-peak overshoot),
`layout_opts`: `face_rect`, `cx`/`cy` (full), `game_cx`/`game_cy`.
Events (`at` = source time; any event may add `"sfx": name`, `"sfx_offset": s`, `"sfx_gain": dB` and `"why"`):
- `{"fx":"zoom","target":"face|game|all","z":1.3,"ease":"punch|push|snap","dur":s}`
- `{"fx":"shake","amp":px,"dur":s}` | `{"fx":"flash"}` | `{"fx":"bassboost","dur":s}` (bass-boosted audio + deep-fried look)
- `{"fx":"freeze","dur":s,"text":"..."}` (inserted pause, desaturated push-in, captions hidden; a beat INSIDE the clip,
  never the ending: render refuses a freeze with < 1 s of picture after it)
- `{"fx":"slowmo","dur":s,"rate":0.5}` (pitched-down audio) | `{"fx":"replay","dur":s,"rate":0.5,"text":"REPLAY"}` (inserted)
- `{"text":"...","pos":"top|center|bottom","dur":s}` | `{"sfx":"boom"}`
- `{"emoji":"...","pos":"face|top|center","dur":s,"why":"..."}` rare (see Editing taste); `why` required
SFX names: `clip sfx` lists them (boom, record_scratch, airhorn, sad_trombone, slide_down/up, boing, bonk, punch,
rimshot, dun_dun, crickets, ding, whoosh, glitch, buzzer, clown_horn, riser, metal_clang, tada, heartbeat, shutter,
tape_stop, bass_drop) plus `user:<name>` for files the user drops in assets/sfx/user/ (run `clip sfx` after adding).

## Editing taste (what makes these clips funny)
- Start on the setup, not before it; end right after the reaction. 12-45 s. Cut dead air inside (`cut`).
- One big hit per punchline: zoom punch on the face + boom/shake. Don't put an effect on every line.
- End the clip when there is nothing else to show: `out` ~0.3-0.5 s after the last word of the reaction (check
  the next word's start so it doesn't leak in), or when the on-screen payoff is over (crash, takedown, laugh).
  No freeze-frame, end card, emoji or "button" SFX after it. Freezes are fine mid-clip.
- Emoji: none by default. At most ONE per clip, hook included (render enforces `style.rules.max_emoji`), and only
  when it is funny or edgy in itself and adds something the footage doesn't. Never a literal picture of a word
  (bacon for "bacon", turtle for "slow", plane, controller, praying hands), never a 💀/😭 tacked onto a hook or line.
- Bass boost only on real yelling.
- Hook = the situation in plain words, not a spoiler of the punchline. ONE hook per clip, first ~3 s only, no tag
  pills; captions stay on.
- Skip: the streamer watching someone else's video/TikTok (the funny audio isn't theirs; double captions);
  strip-club / lap-dance scenes (platform strikes); story cutscenes in launch week (spoilers); moments that only
  land with 10 minutes of context; moments that are only loud. Always VIEW frames of a moment before planning it
  (`clip sheet`, frame grabs).
- Privacy: if an email, phone number, address or private DM shows on stream, pixelate it with
  `"blur": [[x,y,w,h], ...]` (source pixels) or crop it out with `layout_opts.game_cx`.
- If on-screen text sits where captions go, move them with `"captions_y": <y on the 1920 canvas>`.
- Whisper sometimes invents a word over silence at the end ("so", "you"): check the last caption on the review
  strip and drop it with `"caption_drop": ["m:ss.ss", ...]` (word start, source time, +-0.12 s).
- Vertical 9:16 only.

## Instagram (clip ig ...)
- Official API only, the user's own Instagram professional (Business or Creator) account. Token: the USER saves it
  in `config/instagram_token.txt` (one line). Windows Notepad may save it as `instagram_token.txt.txt`: check the real
  file name and rename it. `clip ig whoami` checks it and records @username + id in `config/instagram.json`.
- Two token kinds: Instagram Login ("IG...", graph.instagram.com, 60 days; the poster refreshes it weekly) can only
  take a public `video_url`, so `post` serves the one file through a temporary Cloudflare quick tunnel
  (`cloudflared`, random path, closed after the pass). Facebook Login for Business ("EAA...", IG linked to a Page)
  uploads the local file directly (resumable); no tunnel. System-user tokens without pages_show_list: give the
  account id once with `clip ig whoami --account <id>`.
- Permissions needed: Instagram Login: instagram_business_basic, instagram_business_content_publish (+
  instagram_business_manage_comments for comments). Facebook Login: instagram_basic, instagram_content_publish,
  pages_show_list (+ instagram_manage_comments for comments).
- Settings (`clip ig config key=value`, recorded only from the user's own answers in chat):
  `autopilot` true/false (true = every clip that passes QC is approved and posted at its slot without asking;
  the date is stored in autopilot_since), `every_min` (60 = one Reel per hour), `comment_mode` none | fixed | claude,
  `first_comment` (the text for "fixed"), `comment_guidance` (the user's wishes for "claude"), `max_queue_hours`
  (default 48: stop making new clips while this much is already queued), `purge_after_post` (delete a project's
  folder and Resolve items once all its clips are posted).
- Queue: `projects/_instagram/queue.json`. `clip ig autoqueue <slug>` = add QC-passed clips as drafts, give them the
  next free hourly slots after the last queued post, approve them if autopilot is on, register the purge. With
  autopilot OFF show the user the list (titles, captions, comments, times) and run `clip ig approve` only after
  their yes (slots that passed while waiting move to the next free ones). Order: best clip first, or `--order`.
- Captions: `projects/<slug>/plan/instagram.json` (not in clips.json, so caption edits don't re-render):
  `{"SP01": "caption"}` or, with comment_mode "claude", `{"SP01": {"caption": "...", "comment": "..."}}`.
  House caption style (one or two lines, then credit and tags):
  `<Streamer> <did something> in GTA 6... <then/and the turn> 💀 "<the best quote, verbatim from the transcript>"`
  blank line, `🎥 <Streamer> stream`, then `#gta6 #gtavi #<streamer> #gaming #streamer #funny`. Emoji are fine in
  captions (the no-emoji rule is about the video). No spoilers of story missions, no made-up facts.
- Comments (comment_mode "claude"): one short, natural line per clip that invites replies (a question about the
  moment, a playful take), following `comment_guidance`; no slurs, no made-up facts, no other accounts' handles, no
  links. Meta's API cannot pin comments: if the user wants a comment pinned, they pin it in the Instagram app.
- Background poster: `clip ig service install` (only after the user's yes) runs `clip ig tick` every 10 minutes
  (Windows Task Scheduler task "GTA6 Clipper Poster" for this user, also on battery; macOS launchd agent
  com.gta6clipper.poster). It posts due entries and purges finished projects. Log: `projects/_instagram/poster.log`;
  check with `clip ig service status`. It only runs while the computer is on, awake and signed in.
- Failed posts: read the error in `clip ig show`. "interrupted while posting" -> ask the user to check the profile
  first (it may be live). A clean failure (container ERROR / ProcessingFailed) -> with autopilot on,
  `clip ig approve <key>` (an approved post whose slot has passed moves to the next free slot); otherwise ask the user.
- Limits: Meta counts 50-100 API posts per 24 h (`whoami` shows the quota). Reels spec: MP4, H.264, AAC 48 kHz
  128 kbps, <= 25 Mbps, 3 s-15 min, <= 300 MB, 23-60 fps (`ig post --dry-run` checks the upload copies).
- Posting a batch at once (not hourly): post in REVERSE priority so the best clip ends on top of the profile grid.

## Editor: DaVinci Resolve (optional, level 3 with the official MCP connector)
- Only if the DaVinci Resolve connector's tools (run_script / run_script_unsafe) are in this Claude session.
  Builders live in `clipper/resolve_build.py` (stdlib only). Call them with `run_script_unsafe` (timeout 60):
  `import sys, importlib; sys.path.insert(0, ROOT); import clipper.resolve_build as rb; importlib.reload(rb); result = rb.ensure(resolve, ROOT, slug)`
  then `rb.build_selects(...)`, then `rb.build_clip(resolve, ROOT, slug, "SP01")` ONE clip per call, check each report.
- Timelines: "<channel> Selects" (16:9, source fps, one marker per clip with hook + why) and one vertical timeline per
  clip "SP01 <title>": V1 Picture (base_vN.mp4), V2 Captions (qtrle alpha), V3+ Graphics (hook, each pop),
  A1 Clip Audio (program_vN.wav), A2+ SFX (each effect its own clip). Overlay clips get Data Level = Full.
- Finished MP4s always come from `clip render` (ffmpeg); Resolve's own H.264 render produced no video stream on some
  machines. If the user re-edits in Resolve, render ProRes 422 HQ from Resolve and convert with ffmpeg (libx264, aac).
- A run_script call that times out keeps running inside Resolve: list timelines before retrying.
- Switching projects: `_project()` saves the current project first, then loads/creates "GTA6 Clipper". Tell the user.
- After `clip slim`, the Selects timeline's source media is offline (the clip timelines are unaffected).

## Lessons (from building and running this tool)
- Speed's VODs are age-restricted: yt-dlp needs `--cookies-from-browser <browser>` (set once with
  `clip doctor --cookies <browser>`). Chromium browsers (Chrome, Edge, Opera, Brave) lock their cookie database while
  open ("Could not copy Chrome cookie database"): the user must close that browser fully. Firefox is the most
  reliable on Windows. Without cookies YouTube answers 403 on the media. `CLIP_NO_COOKIES=1` skips cookies.
- Fresh launch-day VODs (post_live) have no heatmap/captions/chat yet: the audio detectors carry the scan.
- PANNs laughter probabilities are tiny in mixed stream audio (max ~0.09): the scan normalises per stream.
- Parallel commands must update project.json with `update_project()` (a scan once wiped files.video).
- Whisper splits "30,000" into "30" ",000" and "y'all" into "y" "'all": `merge_tokens()` re-joins them.
- Chat spam ("7" x3730 in 10 min) faked a hype spike: the same text within 10 s counts once and 1-2 character
  messages don't count toward the chat rate.
- numpy float32 values are not JSON serialisable: wrap with float() before save_json.
- `clip render` skips clips whose plan entry, layout and style are unchanged (use --force after code changes).
- ffmpeg `alimiter` delays audio by its attack unless `latency=1`, and normalises its output back up to 0 dBFS unless
  `level=disabled`. Always pass both.
- Batched faster-whisper can die with a native stack overflow (exit 0xC00000FD, no Python traceback) on a window.
  `CLIP_ASR_UNBATCHED=1` works (slower); asr resumes from analysis/asr/index.json.
- Facecam overlays move during a stream (e.g. y~350 early, y~430 later with chat under it). Check the face panel per
  clip and set `layout_opts.face_rect` when chat shows in it.
- Finished live streams come as fMP4 with ONE moof per frame (millions of fragments on a 24 h VOD): ffmpeg needs minutes
  per seek and yt-dlp's merge crawls. fetch downloads the video stream only (source/video_raw.*, no merge; audio is
  source/audio.*). Then `clip proxy <slug> --ids K..` (after moments) or after writing plan/clips.json: copies only the
  fragments around those windows into source/video_proxy.mp4 (seeks in ~1.5 s, source timestamps kept). Re-run proxy if
  you add a clip outside the existing windows; with an existing proxy it writes video_proxy_vN.mp4 (Resolve may hold
  the old one open).
- A fast retry loop on a failing fetch got the account "rate-limited by YouTube for up to an hour"; retries 30 min apart
  were still limited. On "rate-limited" stop, write it in the autopilot log, and resume once >= 2 h later. Never loop
  yt-dlp faster than every few minutes. An interrupted video fetch leaves source/video_raw.*.part: `clip proxy` cuts
  from its complete fragments, and `clip fetch <url> --slug <slug> --video-only` resumes it.
- Some Reels containers never leave IN_PROGRESS while same-spec files post in 2 min. The poster uploads a smaller x264
  "veryfast" copy after 10 min (and a third after 20) and publishes whichever finishes first (one publish only).
  Uploads of 12-22 MB never hit ProcessingFailedError; ~28 MB ones sometimes did, so upload copies target ~22 MB.
  Slow containers can still publish after ~25 min, so the poster waits up to 50 min, checking once a minute
  (checking every 15 s hit Meta's "(#4) Application request limit reached"). On a Meta request-limit error nothing is
  published: the entry stays approved and `projects/_instagram/cooldown.json` pauses all posting for 60 min. Once
  `media_publish` has returned an id, the Reel is live and is never uploaded again, even if a later call fails.
  A failed post (status failed) needs a look at the profile and a new time before it is approved again.
- A running `clip ig loop` keeps the code it started with: restart it after changing insta.py (never mid-upload).
  The poster service starts fresh every 10 minutes, so it always runs the current code.
