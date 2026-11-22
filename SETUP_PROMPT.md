# GTA 6 Clipper: setup prompt

> **For the person installing:** unzip the download (`GTA6-Clipper.zip` from the latest release, or the repository's
> Code -> Download ZIP), open the unzipped folder in Claude Code (the Code
> tab of the Claude desktop app, or `claude` in a terminal inside the folder), pick the most capable model your plan
> offers (Opus), and paste everything below the line. Setup takes 30-60 minutes, mostly downloads.
> On a Mac, put the folder in your home folder (for example `~/GTA6-Clipper`), not in Desktop, Documents or
> Downloads: macOS blocks background jobs there.

---

You are setting up the GTA 6 Clipper in this folder on my computer and then running it for me. The tool is complete
and tested: it finds long GTA 6 streams on YouTube (ideally with IShowSpeed or Kai Cenat in them), downloads them with
a pinned yt-dlp, finds the funniest moments, renders vertical clips with captions, punch-in zooms and meme sound
effects, optionally builds editable DaVinci Resolve timelines, and posts the clips as Instagram Reels, one per hour,
in a loop. Start by reading `CLAUDE.md`, `docs/CLIPPER_SPEC.md`, `.claude/skills/clip-stream/SKILL.md` and
`.claude/skills/clip-autopilot/SKILL.md`. They are your operating manual from now on.

## Ground rules
- Do not rewrite or "improve" the engine (`clipper/*.py`, `config/*.json`, `pyproject.toml`, `uv.lock`). Identical
  code and pinned versions are what make my clips look like everyone else's. Only fix a real failure on this machine;
  if you do, keep the change minimal and add a dated line to the Lessons in `CLAUDE.md`.
- Ask me before installing software (show the list and wait for one yes), before creating the background poster,
  and before anything public. Never ask for, print or type my Instagram token or any password: I save the token in
  the file myself, and I type passwords myself.
- Footage, audio and transcripts stay on this computer.
- Record every check in `BUILD_STATUS.md` (gate, command, result, date).

## Step 0: ask me these questions in one message, then wait for my answers
1. **YouTube browser:** which browser am I signed in to YouTube with? (Needed for age-restricted VODs such as
   Speed's. Firefox works best on Windows; Chrome, Edge, Brave and Opera must be fully closed while a download runs.)
2. **Instagram account:** do I have an Instagram professional account (Creator or Business) for the clips? Is it
   linked to a Facebook Page? (This decides which token I get in step 6.)
3. **First comment under each Reel:** pick one: (a) no comments; (b) the same comment on every Reel (then ask me for
   the exact text); (c) Claude writes a fitting comment for each Reel (then ask me if I have wishes for them, e.g.
   "ask viewers a question" or "invite them to follow"). Tell me that Instagram's API cannot pin comments: if I want
   one pinned, I pin it myself in the Instagram app.
4. **Autopilot:** should every clip that passes the quality checks be posted automatically at its slot, without
   asking me per batch? Or do I want to approve each batch in chat? Remind me here, once, that reposting other
   people's streams needs the streamer's permission or my own fair-use judgement.
5. **Posting rhythm:** one Reel every 60 minutes, and stop making new clips while 48 hours of posts are already
   queued. Keep those defaults or change them?
6. **Background poster:** may you install a small background job (Windows Task Scheduler / macOS launchd) that posts
   the queued Reels every 10 minutes while this computer is on and signed in, even when Claude isn't running?
7. **Disk clean-up:** may the tool delete a stream's downloaded files and renders after all its clips are posted?
   (A long 1080p60 stream is 10-20 GB.)
8. **DaVinci Resolve:** do I want editable timelines in DaVinci Resolve (needs Resolve installed and its Claude
   connector enabled in this app)? This is optional: the finished clips are identical without it.

## Step 1: check this computer
- Supported: Windows 10/11 (64-bit) or macOS 14+ on Apple Silicon (Intel Macs can't run the pinned PyTorch build).
  16 GB RAM recommended; at least 60 GB free disk (more is better).
- Tell me if the folder path is inside Desktop/Documents/Downloads on a Mac (ask me to move it first).

## Step 2: install the prerequisites (after my one yes for the list)
- **Windows** (PowerShell; if `winget` is missing, ask me to install "App Installer" from the Microsoft Store):
  `winget install --id astral-sh.uv -e --accept-source-agreements --accept-package-agreements`
  `winget install --id Gyan.FFmpeg -e --accept-source-agreements --accept-package-agreements`
  `winget install --id DenoLand.Deno -e --accept-source-agreements --accept-package-agreements`
  and, only if I will use an Instagram Login token ("IG..."): `winget install --id Cloudflare.cloudflared -e`
  New PATH entries only reach new shells: if `uv` is not found, use the uv.exe under
  `%LOCALAPPDATA%\Microsoft\WinGet\Packages\astral-sh.uv_*` (or `%USERPROFILE%\.local\bin`). Run the clip commands
  in PowerShell, not Git Bash.
- **macOS:** Homebrew is required; if `brew` is missing, ask me to install it from https://brew.sh myself (it needs
  my password). Then: `brew install uv ffmpeg deno` (+ `cloudflared` only for an Instagram Login token).

## Step 3: Python environment, pinned downloads, self-test
1. `uv sync` (installs Python 3.11 and the exact package versions from `uv.lock`).
2. `uv run clip setup`: downloads yt-dlp 2026.10.26 from its official GitHub release, the PANNs and YuNet detector
   models and the AudioSet labels, each verified by SHA-256 (a mismatch stops setup: never work around it), loads the
   Whisper large-v3-turbo speech model (~1.6 GB), builds the sound-effect library, runs `clip doctor` and
   `clip selftest`. Gate: it ends with `setup: OK`. Fix what the doctor lists and run it again until it does.
3. Show me the machine summary (CPU, RAM, hardware encoder or x264, free disk).

## Step 4: YouTube cookies
`uv run clip doctor --cookies <my browser>` (ask me to close that browser fully first if it is Chrome, Edge, Brave,
Opera or Vivaldi). It tests the cookies against a public video before saving them. If my browser won't work, try
Firefox (ask me to sign in to YouTube there) or continue without cookies (`--cookies none`; age-restricted VODs will
then fail and must be skipped).

## Step 5: DaVinci Resolve (only if I said yes in step 0)
If the DaVinci Resolve connector tools (run_script / run_script_unsafe) are available in this session, check Resolve
is running and that `rb.ensure` can open/create the project "GTA6 Clipper" (it saves my current project first; tell
me). If the tools are not available in this session, tell me so: I can enable DaVinci Resolve's connector in the
Claude app's connector settings and start a new session later. Continue the setup without it; nothing else depends
on Resolve.

## Step 6: Instagram
1. Explain how I get my token, one step at a time, waiting for me after each step (I do everything in my browser):
   - **No Facebook Page (simplest): Instagram API with Instagram Login.** developers.facebook.com -> My Apps ->
     Create app -> use case for Instagram ("Manage messaging & content on Instagram") -> in the app: Instagram ->
     API setup with Instagram login -> Generate access tokens -> Add account -> sign in with the clips account ->
     allow the permissions -> copy the token (it starts with "IG"). Permissions: instagram_business_basic,
     instagram_business_content_publish, and instagram_business_manage_comments if I chose comments. If Meta asks,
     add the account under App roles -> Instagram testers and accept the invite in the Instagram app.
   - **Account linked to a Facebook Page: Facebook Login for Business** (no tunnel needed, uploads the file directly):
     a token starting with "EAA" with instagram_basic, instagram_content_publish, pages_show_list,
     pages_read_engagement (+ instagram_manage_comments for comments), ideally a long-lived or System User token.
2. I save the token as ONE line in `config/instagram_token.txt` (check the real file name: Notepad likes to add
   `.txt` twice). Never open, print or copy that file's content anywhere.
3. `uv run clip ig whoami` (if it says the token can't list Pages, ask me for the Instagram account ID and run
   `uv run clip ig whoami --account <id>`). Show me the @username it found and the posting quota.
4. Record my step-0 answers exactly (only what I answered):
   `uv run clip ig config comment_mode=<none|fixed|claude> autopilot=<true|false> every_min=<60> max_queue_hours=<48> purge_after_post=<true|false>`
   plus `"first_comment=<my exact text>"` for (b) or `"comment_guidance=<my wishes>"` for (c) (quote the whole
   key=value pair). Then `uv run clip ig config` and show me the result.
5. If I allowed the background poster: `uv run clip ig service install`, then `uv run clip ig service status`.

## Step 7: first batch (end-to-end check)
1. Run the clip-stream skill on one stream: `uv run clip search`, pick the best finished VOD (GTA 6 + Speed/Kai Cenat
   featured first), fetch, scan, transcribe, choose the moments, write the plan, render, `clip qc`, view every
   `clip review` sheet, fix and re-render until each clip is right.
2. Resolve timelines if step 5 is on.
3. Write `plan/instagram.json` in the house caption style from CLAUDE.md (and the comments, if I chose (c)), then
   `uv run clip ig autoqueue <slug>`.
4. Show me: the clips (paths in `projects/<slug>/exports/`), one review sheet, the queue (`uv run clip ig show`) and
   the first post time. With autopilot off, wait for my yes before `uv run clip ig approve`.
5. When the first post time has passed, check `uv run clip ig show` for its permalink (and `comment_id` if comments
   are on) and `uv run clip ig service status`. Record the gates in `BUILD_STATUS.md`.

## Step 8: autopilot
Start the clip-autopilot skill and keep going stream after stream until my usage limit stops you or the queue cap
pauses you. Before you start, tell me in three lines: how to resume later (open this folder in Claude Code and say
"continue the autopilot", or `/loop /clip-autopilot` to let it pause and resume on its own), where the log is
(`projects/_autopilot/log.md`, posts in `projects/_instagram/poster.log`), and how to stop
(`uv run clip ig config autopilot=false` stops approving new clips, `uv run clip ig service uninstall` stops posting).
For long unattended runs I can switch this session to auto mode so permission prompts don't stall the loop.
