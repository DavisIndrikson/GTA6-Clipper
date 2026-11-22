# Build status (GTA 6 Clipper, spec docs/CLIPPER_SPEC.md v1.0)

Fill in on this computer during setup (SETUP_PROMPT.md). One row per gate: status PASS / FAIL / SKIPPED (why).

| Gate | Status | Command | Evidence | Date |
|---|---|---|---|---|
| G-setup install | not run | `uv sync`, `uv run clip setup` | | |
| G0 doctor | not run | `clip doctor` | | |
| selftest | not run | `clip selftest` | | |
| cookies | not run | `clip doctor --cookies <browser>` | | |
| G1 search/fetch | not run | `clip search`, `clip fetch` | | |
| G2 scan | not run | `clip scan`, `clip asr` | | |
| G3 render + QC | not run | `clip render`, `clip qc`, `clip review` | | |
| G4 Resolve (optional) | not run | `rb.ensure`, `rb.build_selects`, `rb.build_clip` | | |
| G7 Instagram | not run | `clip ig whoami`, `clip ig autoqueue`, poster service | | |

Reference numbers from a 16-thread laptop with Intel Quick Sync: scan 48-78x
realtime, window ASR 4.6x realtime (batched), render 15-26 fps of 60 fps output, 7 h 1080p60 VOD = 11.4 GB in 24 min.
Expect slower renders on machines without Intel Quick Sync (x264).
