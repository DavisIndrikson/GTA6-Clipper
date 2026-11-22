# Third-party components

Shipped in this folder:
- Montserrat (Black, ExtraBold), SIL Open Font License 1.1: `assets/fonts/Montserrat-OFL.txt`.
- Noto Color Emoji (CBDT build), SIL Open Font License 1.1: `assets/fonts/NotoColorEmoji-OFL.txt`.
- Sound effects in `assets/sfx/`: synthesised by `clipper/sfx.py` (no samples, no licences needed).

Downloaded by `clip setup` / `uv sync` (pinned, checksum-verified), under their own licences:
- yt-dlp 2026.10.26 (Unlicense), https://github.com/yt-dlp/yt-dlp
- PANNs Cnn14_DecisionLevelMax weights (Kong et al., Zenodo record 3987831) and panns-inference (MIT)
- YuNet face detector (OpenCV Zoo, MIT)
- AudioSet class labels (Google, CC BY 4.0)
- faster-whisper (MIT) with the Whisper large-v3-turbo model (MIT)
- Python packages listed in `uv.lock` (numpy, scipy, OpenCV, Pillow, soundfile, PyTorch CPU, ...)
- ffmpeg, uv, deno, cloudflared: installed with winget / Homebrew from their publishers.
