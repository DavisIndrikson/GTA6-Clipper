# GTA 6 Clipper

Turns long GTA 6 streams (IShowSpeed, Kai Cenat and other big streamers) into funny vertical clips with word-by-word
captions, punch-in zooms and meme sound effects, and posts them to your Instagram as Reels, one per hour, on
autopilot. Claude Code does the choosing and editing; a fixed engine renders every clip the same way on every
computer.

## What you need
- Windows 10/11 (64-bit) or a Mac with Apple Silicon on macOS 14 or newer.
- 16 GB RAM recommended, 60 GB+ free disk, a good internet connection.
- Claude Code (the Code tab of the Claude desktop app, or the terminal version) on a paid Claude plan.
- An Instagram professional account (Creator or Business) and a free Meta developer app for its token.
  Claude explains every step during setup.
- Optional: DaVinci Resolve with its Claude connector, for editable timelines.

## Start
1. Download `GTA6-Clipper.zip` from the latest release (Releases, on the right of this page) and unzip it, or use
   Code -> Download ZIP, or `git clone` this repository. On a Mac, put the folder in your home folder
   (e.g. `~/GTA6-Clipper`), not in Desktop, Documents or Downloads.
2. Open the folder in Claude Code and choose the most capable model (Opus).
3. Paste the prompt from `SETUP_PROMPT.md` (everything below its line), or simply type:
   `Set up the GTA 6 Clipper by following SETUP_PROMPT.md`
4. Answer Claude's setup questions (cookies browser, Instagram, comments, autopilot, background poster, clean-up,
   Resolve). Claude installs the rest, makes and shows you the first batch, then starts the autopilot.

Later, to continue: open the folder in Claude Code and say `continue the autopilot`.
To stop posting: `uv run clip ig config autopilot=false` (no new approvals) and `uv run clip ig service uninstall`.

## Good to know
- Reposting other people's streams is your responsibility: get the streamer's permission or make your own fair-use
  judgement. Many big streamers welcome clippers; check their rules.
- Instagram's API cannot pin comments. If you choose a first comment and want it pinned, pin it in the Instagram app.
- The background poster only posts while the computer is on, awake and signed in.
- Everything stays on your computer except the YouTube downloads and the Reels uploaded to your own account.
