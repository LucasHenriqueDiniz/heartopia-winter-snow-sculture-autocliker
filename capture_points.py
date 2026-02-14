import json
import os
import time
import cv2
import numpy as np
from mss import mss

# --- DPI adjustment (Windows) so coordinates match the screen ---
try:
    import ctypes
    ctypes.windll.user32.SetProcessDPIAware()
except Exception:
    pass

CONFIG_FILE = "config.json"
OUT_POINTS_FILE = "points.json"
OUT_PREVIEW_FILE = "points_preview.png"

DEFAULT_DURATION_SECONDS = 20.0
DEFAULT_MAX_POINTS = 10

DEFAULT_START_KEY = "F7"
DEFAULT_CAPTURE_KEY = "F8"
DEFAULT_SAVE_KEY = "F10"
DEFAULT_QUIT_KEY = "ESC"

# capture_input: "keyboard" | "mouse_right" | "mouse_x1" | "mouse_x2"
DEFAULT_CAPTURE_INPUT = "keyboard"

# Preview screenshot timing
DEFAULT_PREVIEW_CAPTURE_AT_N = 4  # take base screenshot when reaching this point count


def load_config():
    if not os.path.exists(CONFIG_FILE):
        raise RuntimeError(f"Configuration file not found: {CONFIG_FILE}")
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def vk_from_key(key: str) -> int:
    k = key.strip().upper()
    if k == "ESC":
        return 0x1B
    if len(k) == 1:
        return ord(k)
    if k.startswith("F"):
        n = int(k[1:])
        if 1 <= n <= 12:
            return 0x70 + (n - 1)
    raise RuntimeError(f"Unsupported key: {key}")


def is_key_toggled(vk: int) -> bool:
    import ctypes
    return bool(ctypes.windll.user32.GetAsyncKeyState(vk) & 0x1)


def is_mouse_button_toggled(vk_mouse: int) -> bool:
    import ctypes
    return bool(ctypes.windll.user32.GetAsyncKeyState(vk_mouse) & 0x1)


def capture_triggered(capture_input: str, capture_vk: int) -> bool:
    # Mouse VK codes
    VK_RBUTTON = 0x02
    VK_XBUTTON1 = 0x05
    VK_XBUTTON2 = 0x06

    if capture_input == "keyboard":
        return is_key_toggled(capture_vk)
    if capture_input == "mouse_right":
        return is_mouse_button_toggled(VK_RBUTTON)
    if capture_input == "mouse_x1":
        return is_mouse_button_toggled(VK_XBUTTON1)
    if capture_input == "mouse_x2":
        return is_mouse_button_toggled(VK_XBUTTON2)
    raise RuntimeError(f"Unknown capture_input: {capture_input}")


def get_cursor_pos():
    import ctypes

    class POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    pt = POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
    return int(pt.x), int(pt.y)


def pick_monitor_rect(sct, monitor_index: int):
    if monitor_index >= len(sct.monitors):
        raise RuntimeError(
            f"Invalid monitor index: {monitor_index} (available: 1..{len(sct.monitors)-1})"
        )
    mon = sct.monitors[monitor_index]
    return int(mon["left"]), int(mon["top"]), int(mon["width"]), int(mon["height"])


def mirror_x_in_monitor(x: int, mon_left: int, mon_width: int) -> int:
    rel = x - mon_left
    mirrored_rel = (mon_width - 1) - rel
    return mon_left + mirrored_rel


def grab_monitor_bgr(sct, monitor):
    img = np.array(sct.grab(monitor))  # BGRA
    return cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)


def draw_marker(img_bgr, x, y, label: str):
    cv2.circle(img_bgr, (x, y), 8, (0, 0, 255), 2)
    (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 2)
    cv2.rectangle(img_bgr, (x + 10, y - th - 10), (x + 14 + tw, y - 6), (0, 0, 0), -1)
    cv2.putText(img_bgr, label, (x + 12, y - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2, cv2.LINE_AA)


def save_outputs(monitor_index, mon_left, mon_top, mon_w, mon_h, duration_seconds, max_points,
                 capture_input, capture_key, left_points, base_preview_bgr):
    right_points = [(mirror_x_in_monitor(x, mon_left, mon_w), y) for (x, y) in left_points]

    data = {
        "monitor_index": monitor_index,
        "monitor_rect": {"left": mon_left, "top": mon_top, "width": mon_w, "height": mon_h},
        "captured_at_unix": int(time.time()),
        "duration_seconds": duration_seconds,
        "max_points": max_points,
        "capture_input": capture_input,
        "capture_key": capture_key if capture_input == "keyboard" else None,
        "left_points": left_points,
        "right_points": right_points
    }

    with open(OUT_POINTS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

    # Preview image: if base not captured, capture now (last resort)
    if base_preview_bgr is None:
        preview = None
    else:
        preview = base_preview_bgr.copy()
        for i, (x, y) in enumerate(left_points, start=1):
            px = x - mon_left
            py = y - mon_top
            draw_marker(preview, px, py, str(i))
        cv2.imwrite(OUT_PREVIEW_FILE, preview)

    print(f"Saved: {OUT_POINTS_FILE}")
    if preview is not None:
        print(f"Saved preview: {OUT_PREVIEW_FILE}")
    else:
        print("Preview not saved (no base screenshot was captured).")


def main():
    config = load_config()

    monitor_index = int(config["monitor"]["index"])

    cap_cfg = config.get("capture_points", {})
    duration_seconds = float(cap_cfg.get("duration_seconds", DEFAULT_DURATION_SECONDS))
    max_points = int(cap_cfg.get("max_points", DEFAULT_MAX_POINTS))

    start_key = str(cap_cfg.get("start_key", DEFAULT_START_KEY)).upper()
    capture_key = str(cap_cfg.get("capture_key", DEFAULT_CAPTURE_KEY)).upper()
    save_key = str(cap_cfg.get("save_key", DEFAULT_SAVE_KEY)).upper()
    quit_key = str(cap_cfg.get("quit_key", DEFAULT_QUIT_KEY)).upper()

    capture_input = str(cap_cfg.get("capture_input", DEFAULT_CAPTURE_INPUT)).lower().strip()

    preview_capture_at_n = int(cap_cfg.get("preview_capture_at_n", DEFAULT_PREVIEW_CAPTURE_AT_N))
    preview_capture_at_n = max(1, min(preview_capture_at_n, max_points))

    start_vk = vk_from_key(start_key)
    capture_vk = vk_from_key(capture_key)
    save_vk = vk_from_key(save_key)
    quit_vk = vk_from_key(quit_key)

    print("")
    print("=== Fast Point Capture (preview screenshot on Nth point) ===")
    print("")
    print("Behavior:")
    print("- No live window. No screenshot is taken at startup.")
    print(f"- A single base screenshot is captured on point #{preview_capture_at_n}.")
    print("- The preview image is saved only at the end, with markers drawn on top.")
    print("")
    print("Instructions:")
    print(f"- Press {start_key} to START (you have {duration_seconds:.0f}s).")
    if capture_input == "keyboard":
        print(f"- To capture a point: move mouse to the slot center and press {capture_key}.")
    else:
        print(f"- To capture a point: move mouse to the slot center and press {capture_input.replace('_',' ')}.")
    print(f"- Press {save_key} to SAVE early.")
    print(f"- Press {quit_key} to QUIT without saving.")
    print("")
    print(f"Output: {OUT_POINTS_FILE} and {OUT_PREVIEW_FILE}")
    print("")

    with mss() as sct:
        mon_left, mon_top, mon_w, mon_h = pick_monitor_rect(sct, monitor_index)
        monitor = {"left": mon_left, "top": mon_top, "width": mon_w, "height": mon_h}

        capturing = False
        capture_start_ts = 0.0
        left_points = []
        base_preview_bgr = None

        while True:
            if is_key_toggled(quit_vk):
                print("Quit (no save).")
                return

            if (not capturing) and is_key_toggled(start_vk):
                capturing = True
                capture_start_ts = time.time()
                left_points = []
                base_preview_bgr = None
                print(f"Capture started. You have {duration_seconds:.0f}s.")

            if not capturing:
                time.sleep(0.01)
                continue

            elapsed = time.time() - capture_start_ts
            remaining = max(0.0, duration_seconds - elapsed)

            if capture_triggered(capture_input, capture_vk):
                x, y = get_cursor_pos()
                inside = (mon_left <= x < mon_left + mon_w) and (mon_top <= y < mon_top + mon_h)
                if inside:
                    left_points.append((x, y))
                    count = len(left_points)
                    print(f"Captured {count}/{max_points}: ({x}, {y})")

                    # Capture base screenshot on Nth point (exactly once)
                    if (base_preview_bgr is None) and (count >= preview_capture_at_n):
                        base_preview_bgr = grab_monitor_bgr(sct, monitor)
                        print(f"Base preview screenshot captured at point #{count}.")
                else:
                    print(f"Ignored: mouse outside selected monitor: ({x}, {y})")

            save_now = is_key_toggled(save_vk)

            if save_now or remaining <= 0.0 or len(left_points) >= max_points:
                # If user finished but base screenshot not captured yet, capture now as fallback
                if base_preview_bgr is None and len(left_points) > 0:
                    base_preview_bgr = grab_monitor_bgr(sct, monitor)
                    print("Base preview screenshot captured at save-time (fallback).")

                save_outputs(
                    monitor_index, mon_left, mon_top, mon_w, mon_h,
                    duration_seconds, max_points, capture_input, capture_key,
                    left_points, base_preview_bgr
                )
                capturing = False
                print(f"Done. Press {start_key} to capture again, or {quit_key} to quit.")

            time.sleep(0.01)


if __name__ == "__main__":
    main()
