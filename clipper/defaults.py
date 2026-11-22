"""Default style. config/style.json overrides any key (deep merge)."""

STYLE = {
    "output": {"width": 1080, "height": 1920, "max_fps": 60, "crf": 18, "audio_kbps": 320, "encoder": "auto"},
    # Vertical layouts. y values are on the 1080x1920 canvas.
    "layout": {
        "stack_split": 0.40,          # facecam panel height as a share of 1920 (stack layout)
        "game_fit_y": 0.50,           # centre of the fitted gameplay band (game layout)
        "blur_sigma": 28,
    },
    "safe": {"x0": 60, "x1": 1020, "y0": 220, "y1": 1500},   # text never leaves this box
    "captions": {
        "font": "Montserrat-Black.ttf", "px": 88, "stroke": 11, "max_words": 3,
        "max_chars": 18, "gap_break_s": 0.32, "color": "#FFFFFF", "active": "#FFE14D",
        "stroke_color": "#000000", "uppercase": True, "pop_frames": 4,
        "y_stack": 1250, "y_game": 1420, "y_face": 1300, "y_full": 1300,
    },
    "hook": {
        "font": "Montserrat-Black.ttf", "px": 70, "dur_s": 3.0, "box": "#000000", "box_alpha": 0.82,
        "color": "#FFFFFF", "accent": "#FFE14D", "max_lines": 3, "radius": 26,
    },
    "pop_text": {"font": "Montserrat-Black.ttf", "px": 110, "stroke": 14, "color": "#FFFFFF"},
    "sfx": {"target_lu_below": 3.0, "min_lu_below": -1.0, "max_lu_below": 12.0},
    "audio": {"lufs": -14.0, "tolerance": 0.5, "true_peak_max": -1.0, "limiter_db": -1.6,
              "comp": {"threshold_db": -20, "ratio": 2.5, "attack_ms": 5, "release_ms": 150},
              "fade_in_ms": 60, "fade_out_ms": 120, "join_fade_ms": 4},
    "scan": {
        "hop_s": 0.5, "chunk_s": 60, "chat_lag_s": 8.0,
        "weights": {"laugh": 2.2, "scream": 1.1, "loud": 0.6, "chat": 1.6, "heat": 1.0, "fx": 0.4},
        "min_gap_s": 40, "per_10min": 3, "min_candidates": 12, "max_candidates": 70,
        "pre_s": 50, "post_s": 25, "asr_batched": True,
    },
    "clips": {"min_s": 12, "max_s": 60, "target_s": [18, 45]},
    # Editing rules the engine enforces (house style): a clip ends on the reaction, not on a frozen
    # frame, and emoji are rare. render refuses a plan that breaks them; qc warns about endings.
    "rules": {
        "end_freeze": False,          # allow a freeze with < end_min_play_s of picture after it (an end card)
        "end_min_play_s": 1.0,
        "max_emoji": 1,               # per clip: hook + text pops + emoji pops together
        "tail_s": [0.2, 1.5],         # qc warns if the clip ends sooner / later than this after the last word
    },
    "censor": {"captions": True, "bleep_slurs": True},
}
