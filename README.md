# Color ROI Detector + Clicker (Windows)

This repo contains two small scripts:

1) **`capture_points.py`** — interactive tool to capture the center of UI “squares” and save them into `points.json`.
2) **`detect_and_click.py`** — **main script**. Captures the screen, checks small ROIs around each saved point, and **clicks all matches when you press `P`**.

> ⚠️ Disclaimer: Use responsibly. Provided as-is.  
> Windows is required due to DPI handling and `SendInput` (mouse injection).

---

## Files

- **`detect_and_click.py`** (main)  
  Screen capture + color detection + debug overlay + click on keypress.

- **`capture_points.py`**  
  Captures **LEFT-side** points only; generates RIGHT-side points by mirroring (so you don’t need to manually capture both sides).

- **`config.json`**  
  Monitor selection, debug options, detection thresholds, click settings, and the `points.json` path.

- **`points.json`**  
  Saved points (`left_points` + generated `right_points`).

---

## Requirements

- Windows 10/11
- Python 3.10+
- Packages:
  - `numpy`
  - `opencv-python`
  - `mss`
  - `pyautogui`

Install:

```bash
pip install pyautogui numpy opencv-python mss
```

(Optional) Create a `requirements.txt`:

```txt
pyautogui
numpy
opencv-python
mss
```

---

## Quickstart

### 1) Configure `config.json`

Minimum:

- `monitor.index` — monitor index used by MSS (usually `1` for primary).
- `files.points_file` — path to `points.json` (default is `points.json`).

**Important:** `config.json` must be valid JSON (no trailing commas).

Example (valid JSON):

```json
{
  "monitor": {
    "index": 1,
    "width": 1920,
    "height": 1080,
    "comment": "index: 1 = primary monitor in MSS. width/height are used by capture_points.py to mirror points."
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
    "trigger_key": "P",
    "delay": 0.05,
    "jitter": 0.03
  },
  "files": {
    "points_file": "points.json"
  }
}
```

### 2) Capture points

Run:

```bash
python capture_points.py
```

How to capture correctly:

- You will capture **10 points on the LEFT side** (top-left square first).
- For each capture:
  1. Move the mouse to the **center** of the current square
  2. Focus the terminal window (so ENTER goes to the script)
  3. Press **ENTER** to capture
  4. Move to the next square and repeat
- After 10 captures, the script mirrors them into `right_points` and writes `points.json`.

Output:

- `points.json` containing:
  - `left_points`: captured points
  - `right_points`: auto-generated mirrored points

### 3) Run the main detector/clicker

Run:

```bash
python detect_and_click.py
```

Controls:

- Press **`P`** → clicks every point that matches **in the current scan/frame**
- Press **`q`** → exits (only works when `debug.show_window: true`)
- If you run without a window (`debug.show_window: false`), exit with **Ctrl+C**

---

## Monitor index (MSS) explained

MSS exposes monitors like this:

- `monitors[0]` = the full “virtual desktop”
- `monitors[1]` = primary monitor
- `monitors[2]` = secondary monitor
- etc.

You can print them:

```bash
python -c "from mss import mss; s=mss(); print(s.monitors)"
```

Then set `config.json -> monitor.index` accordingly.

---

## How detection works (high-level)

For each saved point:

1. The script extracts a square ROI (`detection.color_box`) centered on the point.
2. It checks two target colors (`color_1` and `color_2`, **RGB in config**, converted to BGR internally).
3. It requires:
   - enough pixels close to each color (`color_min_count`)
   - those pixels are not all clumped together (`color_min_sep`)
   - the ratio between color2 and color1 pixels is within `[color_ratio_min, color_ratio_max]`
4. If a point “hits”, it’s highlighted in the debug window and can be clicked when you press `P`.

---

## Troubleshooting

### DPI / scaling issues (coordinates don’t match)
Windows scaling (125%, 150%, etc.) can desync pixel coordinates.
This repo calls `SetProcessDPIAware()`, but if you still see drift:

- Temporarily set Windows Display Scale to **100%**
- Ensure the game/app runs on the same monitor you configured (`monitor.index`)
- Re-capture `points.json` after changing scaling

### `config.json` parsing errors
If you copied the config by hand:
- Remove trailing commas (JSON does **not** allow them)
- Make sure strings use double quotes `"`

### `q` doesn’t quit
`q` is handled via `cv2.waitKey()` only when the debug window is enabled.
If `debug.show_window` is `false`, use **Ctrl+C** in the terminal.

---

## License

MIT
