# Auto Snow Loop (Windows) — ROI + Template Click Automation

This repo contains two scripts:

1) **`capture_points.py`** — fast point-capture tool to record the center of UI slots and generate `points.json` (LEFT side only + mirrored RIGHT side).
2) **`auto_snow_loop.py`** — main automation script. Detects UI icons (template matching) + runs a color ROI routine over the recorded points.

> ⚠️ Disclaimer: Use responsibly. Provided as-is.  
> Windows is required due to DPI handling and `SendInput` (mouse injection).

---

## Demo

### Point capture (20s window)
- Video: `media/save_points.mp4`

### Running automation
- Video: `media/running.mp4`

### Preview example
- Image: `media/preview_example.png`

> Tip: You can also add a banner at the top: `media/banner.png` (optional).

---

## Repo layout

```
.
├─ auto_snow_loop.py
├─ capture_points.py
├─ config.json
├─ points.json                  # generated
├─ points-1920x1080.json        # optional fallback
├─ images/
│  ├─ put-snow.png
│  ├─ start-snow.png
│  └─ collect-sculture.png
└─ media/
   ├─ save_points.mp4
   ├─ running.mp4
   └─ preview_example.png
```

---

## Requirements

- Windows 10/11
- Python 3.10+
- Packages:
  - `numpy`
  - `opencv-python`
  - `mss`

Optional (only if your `capture_points.py` uses it):
- `pyautogui`

Install:

```bash
pip install -r requirements.txt
```

or:

```bash
pip install numpy opencv-python mss pyautogui
```

---

## Quickstart

### 1) Configure `config.json`

Minimum fields:

- `monitor.index` — MSS monitor index (usually `1` for primary).
- `files.points_file` — path to `points.json` (default: `points.json`)
- `files.images_dir` — folder containing icon templates (default: `images`)

Example:

```json
{
  "monitor": {
    "index": 1,
    "width": 1920,
    "height": 1080
  },
  "debug": {
    "show_window": true,
    "window_monitor_index": 2,
    "show_scores": true
  },
  "detection": {
    "scan_interval": 0.05,
    "color_box": 70,
    "color_tol": 18,
    "color_min_count": 6,
    "color_min_sep": 6,
    "color_ratio_min": 0.2,
    "color_ratio_max": 5.0,
    "color_1": [255, 252, 255],
    "color_2": [74, 203, 242]
  },
  "click": {
    "delay": 0.03,
    "jitter": 0.0
  },
  "files": {
    "points_file": "points.json",
    "images_dir": "images"
  },
  "automation": {
    "start_key": "F7",
    "cancel_key": "F8",
    "state_pause_range": [1.0, 1.5],
    "p_interval_range": [0.9, 1.3],
    "p_duration_seconds": 19.5,
    "state_timeout_seconds": 5.0
  }
}
```

> **Important:** `config.json` must be valid JSON (no trailing commas).

---

### 2) Capture points (for your resolution)

Run:

```bash
python capture_points.py
```

How it works:

- Press the **Start** hotkey.
- You have **~20 seconds** to capture points (LEFT side only).
- The script will automatically mirror them to generate RIGHT-side points.
- At the end it writes:
  - `points.json`
  - `points_preview.png` (screenshot + numbered markers for quick verification)

> If you change monitor, resolution, or Windows scaling, you must re-capture points.

---

### 3) Run the automation

Run:

```bash
python auto_snow_loop.py
```

Controls:

- **F7** → start the loop
- **F8** → stop/cancel (returns to idle)
- **q** → exit (only if `debug.show_window: true`)
- If `debug.show_window: false`, exit with **Ctrl+C**

---

## Points fallback behavior

`auto_snow_loop.py` tries points in this order:

1. `config.json -> files.points_file` (default: `points.json`)
2. `points-<WIDTH>x<HEIGHT>.json` (example: `points-1920x1080.json`)
3. If current resolution is **1920x1080** only: `points-1920x1080.json`

If none exists (or the file is invalid), the script will print an error telling you to record points and to check this README.

---

## Monitor index (MSS)

MSS exposes monitors like:

- `monitors[0]` = full virtual desktop
- `monitors[1]` = primary
- `monitors[2]` = secondary
- ...

Print them:

```bash
python -c "from mss import mss; s=mss(); print(s.monitors)"
```

Then set `config.json -> monitor.index`.

---

## Troubleshooting

### Coordinates don’t match (DPI / scaling)
Windows scaling (125%, 150%, etc.) can desync screen coordinates.
This repo calls `SetProcessDPIAware()`, but if you still see drift:

- Set Windows Display Scale to **100%**
- Ensure the game/app is on the same monitor configured in `monitor.index`
- Re-capture `points.json`

### `q` doesn’t quit
`q` uses `cv2.waitKey()` and only works if the debug window is enabled (`debug.show_window: true`).
If disabled, exit with **Ctrl+C**.

### Templates matching wrong place
If the icon has a lot of white/flat regions, template matching can lock onto large bright areas.
Fixes that help:
- Add a **dark background** behind the button/icon (recommended)
- Use higher thresholds (`automation.thr_put`, `thr_start`, `thr_collect`) if configured
- Ensure templates are cropped tightly (no extra transparent margin)

---

## License

MIT
