# GTA 6 Clipper: specification v1.0

This tool edits *other people's long streams* into short funny clips and posts them as Instagram Reels. Three core
ideas:

1. **Plans are data, scripts are deterministic.** Claude decides (moments, cuts, effects, text) in
   `plan/clips.json`; the engine executes it identically every time, on every machine.
2. **One engine renders everything; the editor receives it.** ffmpeg + Python + Pillow produce the finished MP4
   and every layer; DaVinci Resolve (optional) receives a timeline built from those layers.
3. **Nothing counts until a program has checked it** (`clip qc`) and Claude has looked at it (`clip review`).

## Pipeline
| Stage | Command | Output |
|---|---|---|
| Find | `clip search` | ranked long-form VODs for streamers in `config/streamers.json` |
| Download | `clip fetch` | `source/`: info.json (heatmap, chapters), YouTube auto-captions (json3), live-chat replay, audio.m4a, video.mp4 (H.264 <=1080p60) |
| Scan | `clip scan` | `analysis/features.npz` (per 0.5 s: loudness, PANNs laugh/scream/cry/game-fx/speech/music), `candidates.json` |
| Transcribe | `clip asr` | `analysis/words.json` for the candidate windows only (faster-whisper large-v3-turbo, CPU int8) |
| Decide | Claude | `plan/layout.json` (facecam box, checked visually) and `plan/clips.json` |
| Render | `clip render` | `clips/<id>/final_vN.mp4` + Resolve layers, `exports/<id> - <title>.mp4` |
| Check | `clip qc`, `clip review` | qc.json per clip, contact sheets with the safe zone |
| Editor | `resolve_build.*` via connector | "<channel> Selects" + one vertical timeline per clip |
| Publish | `clip ig autoqueue`, poster service (`clip ig tick`) | Reels at one per hour, optional first comment |

## Funny-moment detection
Score per hop = 2.2 laugh + 1.1 scream + 0.6 loudness-jump + 1.6 chat + 1.0 heatmap + 0.4 game chaos, each
normalised to the stream itself (median..99.5th percentile), smoothed over 3 s. Peaks at least 40 s apart become
candidates (3 per 10 minutes, 12..70). Chat replay is shifted back by 8 s. The window is 50 s before to 25 s after the
peak, because the setup comes before the laugh. Signals only nominate; Claude reads the transcript, views frames and
decides. A moment that needs long context, is only loud, or is someone else's content (e.g. the trailer itself)
is not a clip.

## The edit kit
- Layouts on 1080x1920: `stack` (facecam 40 % top, gameplay bottom), `full`, `game` (fit + blur), `face`.
- Camera: zoom punch/push/snap per panel (always inside the frame, anchor fixed), shake, flash.
- Time: freeze (desaturated push-in), slow-mo (pitched-down audio), instant replay (inserted, slowed).
  A clip ends on the live reaction, never on a freeze (render refuses one with < 1 s of picture after it).
- Bass boost (low shelf + saturation) with a deep-fried picture for real yelling.
- Captions: word-by-word, 2-3 words, Montserrat Black 88 px, active word yellow with a pop, lexicon fixes,
  masked profanity, bleeped slurs. Hidden during freezes/replays.
- Hook: one line, first 3 s, dark rounded box, `*accent*` words. Text pops and emoji pops (Noto Color Emoji);
  emoji are rare: at most one per clip including the hook (`style.rules.max_emoji`), never a literal picture of a word.
- SFX: 24 synthesised meme effects (no licences needed) + the user's own files in `assets/sfx/user/`.
  Each effect is set ~3 LU under the program around it (range -1..12 LU), so it lands loud but not clipping.
- Audio master: compressor 2.5:1, make-up gain by measurement, true-peak limiter at 192 kHz;
  target -14 LUFS +-0.5, <= -1 dBTP measured on the final AAC.

## Outputs per clip
`final_vN.mp4` (H.264 high CRF 18, AAC 320k, bt709, faststart), `base_vN.mp4` (picture, no text),
`captions_vN.mov` + `gfx/*_vN.mov` (qtrle alpha, full canvas), `program_vN.wav`, `sfx/*_vN.wav`, `render.json`
(every layer's frame position), `qc.json`.

## Gates
- G-setup `clip setup` exits 0: pinned yt-dlp and models verified by SHA-256, SFX indexed, doctor and self-test pass.
- G0 doctor exits 0, machine.json complete.
- G1 search returns finished VODs; fetch downloads an age-restricted VOD with cookies; probe ok.
- G2 scan speed recorded; candidates include the transcript-confirmed funny peaks (checked by reading).
- G3 one clip renders with every effect type; qc passes; contact sheet viewed.
- G4 Resolve (only with the connector): Selects durations match the plan; each clip timeline has all layers, picture duration = rendered frames.
- G5 end to end on a real stream: >= 5 clips, all pass qc.
- G6 long stream (>= 4 h) scanned and transcribed with recorded times.
- G7 Instagram: `clip ig whoami` ok; first Reel posted by the poster at its slot (and its first comment, if a comment mode is on).
