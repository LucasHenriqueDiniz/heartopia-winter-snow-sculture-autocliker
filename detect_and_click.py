import time
import numpy as np
import cv2
from mss import mss
import json
import os
import random

# --- DPI adjustment (Windows) so coordinates match the screen ---
try:
    import ctypes
    ctypes.windll.user32.SetProcessDPIAware()
except Exception:
    pass

CONFIG_FILE = "config.json"


def load_config():
    """Loads settings from the config.json file."""
    if not os.path.exists(CONFIG_FILE):
        raise RuntimeError(f"Configuration file not found: {CONFIG_FILE}")
    
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def draw_text_with_bg(img, text, x, y, font, scale, color, thickness):
    (tw, th), _ = cv2.getTextSize(text, font, scale, thickness)
    cv2.rectangle(img, (x - 2, y - th - 6), (x + tw + 4, y + 4), (0, 0, 0), -1)
    cv2.putText(img, text, (x, y), font, scale, color, thickness, cv2.LINE_AA)


def _has_separated_points(pts, min_count, min_sep):
    if len(pts) < min_count:
        return False
    chosen = []
    for p in pts:
        ok = True
        for c in chosen:
            dx = p[0] - c[0]
            dy = p[1] - c[1]
            if dx * dx + dy * dy < min_sep * min_sep:
                ok = False
                break
        if ok:
            chosen.append(p)
            if len(chosen) >= min_count:
                return True
    return False


def color_hit(crop_bgr, config):
    """Detects target colors in the cropped region."""
    det = config["detection"]
    color_1 = det["color_1"]
    color_2 = det["color_2"]
    
    # convert target colors to BGR
    c1 = np.array([color_1[2], color_1[1], color_1[0]], dtype=np.int16)
    c2 = np.array([color_2[2], color_2[1], color_2[0]], dtype=np.int16)
    img = crop_bgr.astype(np.int16)

    d1 = np.abs(img - c1).sum(axis=2)
    d2 = np.abs(img - c2).sum(axis=2)
    thresh = det["color_tol"] * 3
    mask1 = d1 <= thresh
    mask2 = d2 <= thresh

    pts1 = np.column_stack(np.where(mask1))
    pts2 = np.column_stack(np.where(mask2))

    ok1 = _has_separated_points(pts1, det["color_min_count"], det["color_min_sep"])
    ok2 = _has_separated_points(pts2, det["color_min_count"], det["color_min_sep"])

    c1_count = len(pts1)
    c2_count = len(pts2)
    ratio = c2_count / max(1, c1_count)
    ok_ratio = det["color_ratio_min"] <= ratio <= det["color_ratio_max"]

    return (ok1 and ok2 and ok_ratio), (c1_count, c2_count)


def sendinput_click(x, y):
    # move + click via SendInput (Windows)
    import ctypes

    PUL = ctypes.POINTER(ctypes.c_ulong)

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [("dx", ctypes.c_long), ("dy", ctypes.c_long),
                    ("mouseData", ctypes.c_ulong), ("dwFlags", ctypes.c_ulong),
                    ("time", ctypes.c_ulong), ("dwExtraInfo", PUL)]

    class INPUT(ctypes.Structure):
        _fields_ = [("type", ctypes.c_ulong), ("mi", MOUSEINPUT)]

    def _send(mi):
        inp = INPUT(type=0, mi=mi)
        ctypes.windll.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp))

    screen_w = ctypes.windll.user32.GetSystemMetrics(0)
    screen_h = ctypes.windll.user32.GetSystemMetrics(1)
    nx = int(x * 65535 / max(1, screen_w - 1))
    ny = int(y * 65535 / max(1, screen_h - 1))

    _send(MOUSEINPUT(nx, ny, 0, 0x8000 | 0x0001, 0, None))  # move
    _send(MOUSEINPUT(0, 0, 0, 0x0002, 0, None))  # down
    _send(MOUSEINPUT(0, 0, 0, 0x0004, 0, None))  # up


def pick_monitor_rect(sct, monitor_index):
    """Returns the rectangle of the specified monitor."""
    if monitor_index >= len(sct.monitors):
        raise RuntimeError(
            f"Invalid MONITOR_INDEX: {monitor_index} (monitors={len(sct.monitors)-1})"
        )
    mon = sct.monitors[monitor_index]
    return mon["left"], mon["top"], mon["width"], mon["height"]


def move_debug_window(window_name, sct, window_monitor_index):
    """Moves the debug window to the specified monitor."""
    if window_monitor_index < len(sct.monitors):
        mon = sct.monitors[window_monitor_index]
        cv2.moveWindow(window_name, mon["left"] + 50, mon["top"] + 50)
    else:
        cv2.moveWindow(window_name, 50, 50)


def roi_from_centers(centers):
    xs = [c[0] for c in centers]
    ys = [c[1] for c in centers]
    min_x = min(xs) - 5
    max_x = max(xs) + 5
    min_y = min(ys) - 5
    max_y = max(ys) + 5
    return (min_x, min_y, max_x - min_x, max_y - min_y)


def load_points_file(points_file):
    """Loads detection points from a JSON file."""
    if not os.path.exists(points_file):
        return None, None
    with open(points_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    left_points = [tuple(p) for p in data.get("left_points", [])]
    right_points = [tuple(p) for p in data.get("right_points", [])]
    if not left_points or not right_points:
        return None, None
    return left_points, right_points


def main():
    config = load_config()
    
    monitor_index = config["monitor"]["index"]
    debug_config = config["debug"]
    show_window = debug_config["show_window"]
    window_monitor_index = debug_config["window_monitor_index"]
    show_scores = debug_config["show_scores"]
    det_config = config["detection"]
    click_config = config["click"]
    points_file = config["files"]["points_file"]
    
    trigger_key = click_config["trigger_key"]
    click_delay = click_config["delay"]
    click_jitter = click_config["jitter"]
    scan_interval = det_config["scan_interval"]
    color_box = det_config["color_box"]
    
    print("Press 'q' to exit.")
    
    window_name = "Detect and mark"
    if show_window:
        cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
        cv2.resizeWindow(window_name, 960, 540)
    
    with mss() as sct:
        if show_window:
            move_debug_window(window_name, sct, window_monitor_index)
        
        pending_fire = False
        last_trigger = 0.0
        
        while True:
            # Press trigger_key to fire 1 click on all points detected in this frame
            try:
                import ctypes
                if ctypes.windll.user32.GetAsyncKeyState(ord(trigger_key)) & 0x1:
                    now = time.time()
                    if now - last_trigger > 0.3:
                        pending_fire = True
                        last_trigger = now
            except Exception:
                pass
            
            left, top, width, height = pick_monitor_rect(sct, monitor_index)
            monitor = {"left": left, "top": top, "width": width, "height": height}
            img = np.array(sct.grab(monitor))  # BGRA
            frame = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)

            left_points, right_points = load_points_file(points_file)
            if not left_points or not right_points:
                raise RuntimeError(f"{points_file} not found or has no valid points.")
            all_points = left_points + right_points

            # Draw ROIs based on points (only if showing window)
            if show_window:
                left_roi = roi_from_centers(left_points)
                right_roi = roi_from_centers(right_points)
                for (rx, ry, rw, rh) in [left_roi, right_roi]:
                    cv2.rectangle(
                        frame,
                        (rx - left, ry - top),
                        (rx - left + rw, ry - top + rh),
                        (255, 255, 0),
                        2,
                    )

            matches = 0
            for (cx, cy) in all_points:
                if show_window:
                    # base point (always red)
                    cv2.circle(
                        frame,
                        (cx - monitor["left"], cy - monitor["top"]),
                        6,
                        (0, 0, 255),
                        2,
                    )

                half = color_box // 2
                rx1 = int(cx - monitor["left"] - half)
                ry1 = int(cy - monitor["top"] - half)
                rx2 = int(cx - monitor["left"] + half)
                ry2 = int(cy - monitor["top"] + half)
                if rx1 < 0 or ry1 < 0 or rx2 > frame.shape[1] or ry2 > frame.shape[0]:
                    continue
                crop = frame[ry1:ry2, rx1:rx2]
                hit, counts = color_hit(crop, config)
                if hit:
                    matches += 1
                    if show_window:
                        cv2.rectangle(frame, (rx1, ry1), (rx2, ry2), (255, 0, 0), 2)
                    if pending_fire:
                        sendinput_click(cx, cy)
                        time.sleep(max(0.0, random.uniform(click_delay - click_jitter, click_delay + click_jitter)))
                
                if show_window and show_scores:
                    txt = f"{counts[0]}/{counts[1]}"
                    draw_text_with_bg(
                        frame,
                        txt,
                        cx - monitor["left"] + 6,
                        cy - monitor["top"] - 6,
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.5,
                        (255, 255, 255),
                        1,
                    )

            if show_window:
                label = f"Slots: {len(all_points)}  Matches: {matches}  Fire: {trigger_key if pending_fire else '-'}"
                draw_text_with_bg(
                    frame,
                    label,
                    12,
                    6 + 18,
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.7,
                    (0, 255, 255),
                    2,
                )
                cv2.imshow(window_name, frame)
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break

            time.sleep(scan_interval)
            pending_fire = False

    if show_window:
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
