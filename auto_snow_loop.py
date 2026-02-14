import time
import json
import os
import random
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

# Defaults (overridable via config.json -> automation)
DEFAULT_START_KEY = "F7"
DEFAULT_CANCEL_KEY = "F8"

DEFAULT_STATE_PAUSE_RANGE = (1.0, 1.5)
DEFAULT_P_INTERVAL_RANGE = (0.9, 1.3)
DEFAULT_P_DURATION_SECONDS = 19.5
DEFAULT_STATE_TIMEOUT_SECONDS = 5.0

MIN_SCAN_INTERVAL = 0.08

# Matching / validation defaults
DEFAULT_WHITE_CUTOFF = 255   # with black backgrounds, don't ignore white
DEFAULT_ICON_THR_CLICK = {"put": 0.75, "start": 0.75, "collect": 0.75}

DEFAULT_CLICK_RETRIES = 3
DEFAULT_VERIFY_DELAY_RANGE = (0.12, 0.22)
DEFAULT_ICON_COOLDOWN = 0.45

# Candidate sanity check
DEFAULT_BRIGHT_THR = 215
DEFAULT_DARK_THR = 80
DEFAULT_BRIGHT_TOL = 0.18
DEFAULT_DARK_TOL = 0.18
DEFAULT_BG_BORDER = 4
DEFAULT_BG_MAX_MEAN = 70
DEFAULT_TOPK_TRIES = 6


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
    raise RuntimeError(f"Unsupported key: {key} (use A-Z, 0-9, F1-F12, ESC)")


def is_key_toggled(vk: int) -> bool:
    import ctypes
    return bool(ctypes.windll.user32.GetAsyncKeyState(vk) & 0x1)


def draw_text_with_bg(img, text, x, y, font, scale, color, thickness):
    (tw, th), _ = cv2.getTextSize(text, font, scale, thickness)
    cv2.rectangle(img, (x - 2, y - th - 6), (x + tw + 4, y + 4), (0, 0, 0), -1)
    cv2.putText(img, text, (x, y), font, scale, color, thickness, cv2.LINE_AA)


def sendinput_click(x, y):
    import ctypes
    PUL = ctypes.POINTER(ctypes.c_ulong)

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [
            ("dx", ctypes.c_long), ("dy", ctypes.c_long),
            ("mouseData", ctypes.c_ulong), ("dwFlags", ctypes.c_ulong),
            ("time", ctypes.c_ulong), ("dwExtraInfo", PUL)
        ]

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
    _send(MOUSEINPUT(0, 0, 0, 0x0002, 0, None))            # down
    _send(MOUSEINPUT(0, 0, 0, 0x0004, 0, None))            # up


def jitter_sleep(base, jitter):
    time.sleep(max(0.0, random.uniform(base - jitter, base + jitter)))


def short_pause(rng):
    time.sleep(random.uniform(rng[0], rng[1]))


def pick_monitor_rect(sct, monitor_index):
    if monitor_index >= len(sct.monitors):
        raise RuntimeError(
            f"Invalid monitor index: {monitor_index} (monitors={len(sct.monitors)-1})"
        )
    mon = sct.monitors[monitor_index]
    return int(mon["left"]), int(mon["top"]), int(mon["width"]), int(mon["height"])


def move_debug_window(window_name, sct, window_monitor_index):
    if window_monitor_index < len(sct.monitors):
        mon = sct.monitors[window_monitor_index]
        cv2.moveWindow(window_name, int(mon["left"]) + 50, int(mon["top"]) + 50)
    else:
        cv2.moveWindow(window_name, 50, 50)


def load_points_file(points_file):
    if not os.path.exists(points_file):
        return None, None, None

    with open(points_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    left_points = [tuple(p) for p in data.get("left_points", [])]
    right_points = [tuple(p) for p in data.get("right_points", [])]
    mon_rect = data.get("monitor_rect")  # optional metadata

    if not left_points or not right_points:
        return None, None, None

    return left_points, right_points, mon_rect


def resolve_points(config, monitor_w, monitor_h):
    """
    Resolution-aware points resolution:
      1) config files.points_file (e.g. points.json)
      2) points-{W}x{H}.json (if exists)
      3) points-1920x1080.json only if current resolution == 1920x1080
      else: print warning and exit
    """
    base_dir = os.path.dirname(os.path.abspath(__file__))
    files_cfg = config.get("files", {})

    points_file_rel = files_cfg.get("points_file", "points.json")
    points_file = os.path.join(base_dir, points_file_rel)

    # 1) configured file
    lp, rp, mon_rect = load_points_file(points_file)
    if lp and rp:
        return points_file, lp, rp, mon_rect

    # 2) resolution-specific file (best)
    points_res_file = os.path.join(base_dir, f"points-{monitor_w}x{monitor_h}.json")
    lp, rp, mon_rect = load_points_file(points_res_file)
    if lp and rp:
        print(f"[points] Using resolution-specific points: {os.path.basename(points_res_file)}")
        return points_res_file, lp, rp, mon_rect

    # 3) legacy fallback only for 1920x1080
    points_1920 = os.path.join(base_dir, "points-1920x1080.json")
    if monitor_w == 1920 and monitor_h == 1080 and os.path.exists(points_1920):
        lp, rp, mon_rect = load_points_file(points_1920)
        if lp and rp:
            print("[points] points.json missing/invalid, using fallback: points-1920x1080.json")
            return points_1920, lp, rp, mon_rect

    # fail with actionable message
    msg = (
        f"\n[points] No valid points file found.\n"
        f"  Tried: {points_file_rel}, points-{monitor_w}x{monitor_h}.json"
        + (", points-1920x1080.json" if os.path.exists(points_1920) else "")
        + "\n\n"
        f"Your current monitor resolution is {monitor_w}x{monitor_h}.\n"
        f"You need to record points for this resolution.\n"
        f"Check the README for how to capture points (run capture_points.py).\n"
    )
    raise RuntimeError(msg)


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
    det = config["detection"]
    color_1 = det["color_1"]
    color_2 = det["color_2"]

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


def grab_frame(sct, monitor):
    img = np.array(sct.grab(monitor))
    frame_bgr = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
    frame_gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
    return frame_bgr, frame_gray


def load_icon_template(path, mask_mode="auto", white_cutoff=255,
                       bright_thr=DEFAULT_BRIGHT_THR, dark_thr=DEFAULT_DARK_THR):
    if not os.path.exists(path):
        raise RuntimeError(f"Icon template not found: {path}")

    rgba = cv2.imread(path, cv2.IMREAD_UNCHANGED)
    if rgba is None:
        raise RuntimeError(f"Failed to read template: {path}")

    if rgba.ndim == 2:
        gray = rgba
        alpha = np.full_like(gray, 255, dtype=np.uint8)
    elif rgba.shape[2] == 3:
        bgr = rgba
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        alpha = np.full_like(gray, 255, dtype=np.uint8)
    else:
        bgr = rgba[:, :, :3]
        alpha = rgba[:, :, 3]
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)

    alpha_bool = alpha > 25
    coverage = float(np.mean(alpha_bool))

    use_mask = True
    method = "sqdiff"
    mask = None

    if mask_mode == "none":
        use_mask = False
        method = "ccoeff"
        mask = None
    elif mask_mode == "alpha":
        mask = (alpha_bool.astype(np.uint8) * 255)
        use_mask = True
        method = "sqdiff"
    elif mask_mode == "detail":
        detail = alpha_bool & (gray < white_cutoff)
        if int(detail.sum()) < 200:
            mask = (alpha_bool.astype(np.uint8) * 255)
        else:
            mask = (detail.astype(np.uint8) * 255)
        use_mask = True
        method = "sqdiff"
    else:
        # auto
        if coverage >= 0.98:
            use_mask = False
            method = "ccoeff"
            mask = None
        else:
            mask = (alpha_bool.astype(np.uint8) * 255)
            use_mask = True
            method = "sqdiff"

    pix = gray[alpha_bool] if np.any(alpha_bool) else gray.reshape(-1)
    bright_frac = float(np.mean(pix >= bright_thr))
    dark_frac = float(np.mean(pix <= dark_thr))
    has_black_bg = dark_frac >= 0.30

    h, w = gray.shape[:2]
    return {
        "path": path,
        "gray": gray,
        "mask": mask,
        "use_mask": use_mask,
        "method": method,
        "w": int(w),
        "h": int(h),
        "alpha_bool": alpha_bool if coverage < 0.999 else np.ones((h, w), dtype=bool),
        "bright_frac": bright_frac,
        "dark_frac": dark_frac,
        "has_black_bg": has_black_bg,
        "bright_thr": bright_thr,
        "dark_thr": dark_thr,
    }


def candidate_ok(frame_gray, x, y, templ, bright_tol, dark_tol, bg_border, bg_max_mean):
    h, w = templ["h"], templ["w"]
    H, W = frame_gray.shape[:2]
    if x < 0 or y < 0 or x + w > W or y + h > H:
        return False, "oob"

    crop = frame_gray[y:y+h, x:x+w]
    m = templ["alpha_bool"]
    pix = crop[m]
    if pix.size < 50:
        return False, "pix_low"

    b = float(np.mean(pix >= templ["bright_thr"]))
    d = float(np.mean(pix <= templ["dark_thr"]))

    if abs(b - templ["bright_frac"]) > bright_tol:
        return False, f"bright_off({b:.2f}/{templ['bright_frac']:.2f})"
    if abs(d - templ["dark_frac"]) > dark_tol:
        return False, f"dark_off({d:.2f}/{templ['dark_frac']:.2f})"

    if templ["has_black_bg"]:
        xb1 = max(0, x - bg_border)
        yb1 = max(0, y - bg_border)
        xb2 = min(W, x + w + bg_border)
        yb2 = min(H, y + h + bg_border)

        region = frame_gray[yb1:yb2, xb1:xb2]
        mask = np.ones(region.shape, dtype=bool)
        ix1 = y - yb1
        iy1 = x - xb1
        ix2 = ix1 + h
        iy2 = iy1 + w
        mask[ix1:ix2, iy1:iy2] = False
        outside = region[mask]
        if outside.size > 0 and float(outside.mean()) > bg_max_mean:
            return False, f"bg_mean({outside.mean():.1f})"

    return True, "ok"


def match_best_valid(frame_gray, templ, topk, bright_tol, dark_tol, bg_border, bg_max_mean):
    H, W = frame_gray.shape[:2]
    h, w = templ["h"], templ["w"]
    if h >= H or w >= W:
        return 0.0, (0, 0), False, "templ_gt_frame"

    if templ["method"] == "ccoeff":
        res = cv2.matchTemplate(frame_gray, templ["gray"], cv2.TM_CCOEFF_NORMED)
        fill = -1.0
        best_score, best_loc, best_reason = 0.0, (0, 0), "no_try"
        for _ in range(topk):
            _min, max_val, _min_loc, max_loc = cv2.minMaxLoc(res)
            score = float(max_val)
            x, y = max_loc
            if score > best_score:
                best_score, best_loc = score, (x, y)
            ok, reason = candidate_ok(frame_gray, x, y, templ, bright_tol, dark_tol, bg_border, bg_max_mean)
            if ok:
                return score, (x, y), True, "ok"
            best_reason = reason
            x2 = min(res.shape[1], x + w)
            y2 = min(res.shape[0], y + h)
            res[y:y2, x:x2] = fill
        return best_score, best_loc, False, best_reason

    # SQDIFF (masked when available)
    if templ["use_mask"] and templ["mask"] is not None:
        res = cv2.matchTemplate(frame_gray, templ["gray"], cv2.TM_SQDIFF_NORMED, mask=templ["mask"])
    else:
        res = cv2.matchTemplate(frame_gray, templ["gray"], cv2.TM_SQDIFF_NORMED)

    fill = 1.0
    best_score, best_loc, best_reason = 0.0, (0, 0), "no_try"
    for _ in range(topk):
        min_val, _max, min_loc, _max_loc = cv2.minMaxLoc(res)
        score = 1.0 - float(min_val)
        x, y = min_loc
        if score > best_score:
            best_score, best_loc = score, (x, y)
        ok, reason = candidate_ok(frame_gray, x, y, templ, bright_tol, dark_tol, bg_border, bg_max_mean)
        if ok:
            return score, (x, y), True, "ok"
        best_reason = reason
        x2 = min(res.shape[1], x + w)
        y2 = min(res.shape[0], y + h)
        res[y:y2, x:x2] = fill

    return best_score, best_loc, False, best_reason


def click_with_verification(
    sct,
    monitor,
    frame_gray,
    monitor_left,
    monitor_top,
    templ_current,
    thr_click,
    click_delay,
    click_jitter,
    last_click_ts,
    cooldown,
    retries,
    verify_delay_range,
    confirm_mode,
    templ_next,
    thr_next,
    topk,
    bright_tol,
    dark_tol,
    bg_border,
    bg_max_mean,
):
    now = time.time()
    score0, loc0, valid0, reason0 = match_best_valid(
        frame_gray, templ_current, topk, bright_tol, dark_tol, bg_border, bg_max_mean
    )

    if now - last_click_ts < cooldown:
        return False, score0, loc0, last_click_ts, "cooldown"

    if (not valid0) or score0 < thr_click:
        return False, score0, loc0, last_click_ts, f"no_match({reason0})"

    x0, y0 = loc0
    w, h = templ_current["w"], templ_current["h"]

    click_points = [
        (0.50, 0.55),
        (0.50, 0.50),
        (0.48, 0.55),
        (0.52, 0.55),
        (0.50, 0.60),
    ]

    for i in range(retries):
        fx, fy = click_points[i % len(click_points)]
        cx = monitor_left + x0 + int(w * fx) + random.randint(-2, 2)
        cy = monitor_top + y0 + int(h * fy) + random.randint(-2, 2)

        sendinput_click(cx, cy)
        jitter_sleep(click_delay, click_jitter)
        last_click_ts = time.time()

        time.sleep(random.uniform(*verify_delay_range))
        _fbgr, g2 = grab_frame(sct, monitor)

        if confirm_mode == "gone":
            s1, _l1, v1, _r1 = match_best_valid(g2, templ_current, topk, bright_tol, dark_tol, bg_border, bg_max_mean)
            if (not v1) or (s1 < (thr_click * 0.55)):
                return True, score0, loc0, last_click_ts, f"gone(v={v1},s1={s1:.3f})"

        elif confirm_mode == "next":
            if templ_next is None:
                raise RuntimeError("confirm_mode='next' requires templ_next")
            sN, _lN, vN, _rN = match_best_valid(g2, templ_next, topk, bright_tol, dark_tol, bg_border, bg_max_mean)
            if vN and sN >= thr_next:
                return True, score0, loc0, last_click_ts, f"next(sN={sN:.3f})"
        else:
            raise RuntimeError(f"Unknown confirm_mode: {confirm_mode}")

    return False, score0, loc0, last_click_ts, "retries_exhausted"


def run_p_routine_once(frame_bgr, monitor_left, monitor_top, all_points, config, click_delay, click_jitter):
    det_config = config["detection"]
    color_box = det_config["color_box"]

    matches = 0
    for (cx, cy) in all_points:
        half = color_box // 2
        rx1 = int(cx - monitor_left - half)
        ry1 = int(cy - monitor_top - half)
        rx2 = int(cx - monitor_left + half)
        ry2 = int(cy - monitor_top + half)

        if rx1 < 0 or ry1 < 0 or rx2 > frame_bgr.shape[1] or ry2 > frame_bgr.shape[0]:
            continue

        crop = frame_bgr[ry1:ry2, rx1:rx2]
        hit, _ = color_hit(crop, config)
        if hit:
            matches += 1
            sendinput_click(cx, cy)
            jitter_sleep(click_delay, click_jitter)

    return matches


def main():
    config = load_config()

    monitor_index = int(config["monitor"]["index"])
    debug_config = config.get("debug", {})
    show_window = bool(debug_config.get("show_window", True))
    window_monitor_index = int(debug_config.get("window_monitor_index", monitor_index))

    det_config = config["detection"]
    click_config = config["click"]

    scan_interval = float(det_config.get("scan_interval", 0.05))
    scan_interval = max(scan_interval, MIN_SCAN_INTERVAL)

    click_delay = float(click_config.get("delay", 0.03))
    click_jitter = float(click_config.get("jitter", 0.0))

    auto = config.get("automation", {})
    start_key = auto.get("start_key", DEFAULT_START_KEY)
    cancel_key = auto.get("cancel_key", DEFAULT_CANCEL_KEY)

    state_pause_range = tuple(auto.get("state_pause_range", list(DEFAULT_STATE_PAUSE_RANGE)))
    p_interval_range = tuple(auto.get("p_interval_range", list(DEFAULT_P_INTERVAL_RANGE)))
    p_duration_seconds = float(auto.get("p_duration_seconds", DEFAULT_P_DURATION_SECONDS))
    state_timeout_seconds = float(auto.get("state_timeout_seconds", DEFAULT_STATE_TIMEOUT_SECONDS))

    white_cutoff = int(auto.get("white_cutoff", DEFAULT_WHITE_CUTOFF))
    mask_mode = auto.get("mask_mode", "auto")

    thr_put = float(auto.get("thr_put", DEFAULT_ICON_THR_CLICK["put"]))
    thr_start = float(auto.get("thr_start", DEFAULT_ICON_THR_CLICK["start"]))
    thr_collect = float(auto.get("thr_collect", DEFAULT_ICON_THR_CLICK["collect"]))

    retries = int(auto.get("click_retries", DEFAULT_CLICK_RETRIES))
    verify_delay_range = tuple(auto.get("verify_delay_range", list(DEFAULT_VERIFY_DELAY_RANGE)))
    cooldown = float(auto.get("icon_cooldown", DEFAULT_ICON_COOLDOWN))

    bright_thr = int(auto.get("bright_thr", DEFAULT_BRIGHT_THR))
    dark_thr = int(auto.get("dark_thr", DEFAULT_DARK_THR))
    bright_tol = float(auto.get("bright_tol", DEFAULT_BRIGHT_TOL))
    dark_tol = float(auto.get("dark_tol", DEFAULT_DARK_TOL))
    bg_border = int(auto.get("bg_border", DEFAULT_BG_BORDER))
    bg_max_mean = int(auto.get("bg_max_mean", DEFAULT_BG_MAX_MEAN))
    topk = int(auto.get("topk_tries", DEFAULT_TOPK_TRIES))

    start_vk = vk_from_key(start_key)
    cancel_vk = vk_from_key(cancel_key)

    base_dir = os.path.dirname(os.path.abspath(__file__))
    images_dir = os.path.join(base_dir, config.get("files", {}).get("images_dir", "images"))

    put_path = os.path.join(images_dir, "put-snow.png")
    start_path = os.path.join(images_dir, "start-snow.png")
    collect_path = os.path.join(images_dir, "collect-sculture.png")

    NEXT_STATE = {"PUT": "START", "START": "P_RUN", "COLLECT": "CENTER", "CENTER": "PUT"}

    window_name = "Auto Snow Loop (verified clicks)"
    if show_window:
        cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
        cv2.resizeWindow(window_name, 1100, 650)

    print(f"Idle. START={start_key} | STOP={cancel_key} | exit=q")

    running = False
    state = "PUT"
    cycle = 0
    state_enter_ts = time.time()

    last_put = 0.0
    last_start = 0.0
    last_collect = 0.0

    with mss() as sct:
        if show_window:
            move_debug_window(window_name, sct, window_monitor_index)

        # Get monitor geometry once (for points resolution + center click)
        mon_left, mon_top, mon_w, mon_h = pick_monitor_rect(sct, monitor_index)
        monitor = {"left": mon_left, "top": mon_top, "width": mon_w, "height": mon_h}

        # Points resolution fallback
        points_used, left_points, right_points, pts_mon_rect = resolve_points(config, mon_w, mon_h)
        all_points = left_points + right_points

        # optional: warn if points metadata disagree
        if isinstance(pts_mon_rect, dict):
            pw = pts_mon_rect.get("width")
            ph = pts_mon_rect.get("height")
            if isinstance(pw, int) and isinstance(ph, int) and (pw, ph) != (mon_w, mon_h):
                print(f"[warn] points were recorded on {pw}x{ph}, but current monitor is {mon_w}x{mon_h}.")
                print("[warn] Expect misalignment. Re-capture points for this resolution (check README).")

        # Load icon templates (from images/)
        put_t = load_icon_template(put_path, mask_mode=mask_mode, white_cutoff=white_cutoff, bright_thr=bright_thr, dark_thr=dark_thr)
        start_t = load_icon_template(start_path, mask_mode=mask_mode, white_cutoff=white_cutoff, bright_thr=bright_thr, dark_thr=dark_thr)
        collect_t = load_icon_template(collect_path, mask_mode=mask_mode, white_cutoff=white_cutoff, bright_thr=bright_thr, dark_thr=dark_thr)

        while True:
            frame_bgr, frame_gray = grab_frame(sct, monitor)

            # hotkeys
            if is_key_toggled(start_vk):
                running = True
                state = "PUT"
                cycle = 0
                state_enter_ts = time.time()
                print("START -> running")

            if is_key_toggled(cancel_vk):
                running = False
                state = "PUT"
                state_enter_ts = time.time()
                print("STOP -> idle")

            action = "idle"
            note = "-"

            # state timeout (except P_RUN): skip to next
            if running and state != "P_RUN":
                if (time.time() - state_enter_ts) >= state_timeout_seconds:
                    prev = state
                    state = NEXT_STATE.get(state, "PUT")
                    state_enter_ts = time.time()
                    action = f"TIMEOUT {prev} -> {state}"
                    note = "state_timeout"

            t0 = time.time()

            if running:
                if state == "PUT":
                    clicked, score0, _loc0, last_put, note = click_with_verification(
                        sct=sct, monitor=monitor, frame_gray=frame_gray,
                        monitor_left=mon_left, monitor_top=mon_top,
                        templ_current=put_t, thr_click=thr_put,
                        click_delay=click_delay, click_jitter=click_jitter,
                        last_click_ts=last_put, cooldown=cooldown,
                        retries=retries, verify_delay_range=verify_delay_range,
                        confirm_mode="next", templ_next=start_t, thr_next=thr_start,
                        topk=topk, bright_tol=bright_tol, dark_tol=dark_tol,
                        bg_border=bg_border, bg_max_mean=bg_max_mean,
                    )
                    action = f"PUT score={score0:.3f} ({note})"
                    if clicked:
                        short_pause(state_pause_range)
                        state = "START"
                        state_enter_ts = time.time()

                elif state == "START":
                    clicked, score0, _loc0, last_start, note = click_with_verification(
                        sct=sct, monitor=monitor, frame_gray=frame_gray,
                        monitor_left=mon_left, monitor_top=mon_top,
                        templ_current=start_t, thr_click=thr_start,
                        click_delay=click_delay, click_jitter=click_jitter,
                        last_click_ts=last_start, cooldown=cooldown,
                        retries=retries, verify_delay_range=verify_delay_range,
                        confirm_mode="gone", templ_next=None, thr_next=0.0,
                        topk=topk, bright_tol=bright_tol, dark_tol=dark_tol,
                        bg_border=bg_border, bg_max_mean=bg_max_mean,
                    )
                    action = f"START score={score0:.3f} ({note})"
                    if clicked:
                        short_pause(state_pause_range)
                        state = "P_RUN"
                        state_enter_ts = time.time()

                elif state == "P_RUN":
                    p_start = time.time()
                    p_deadline = p_start + p_duration_seconds
                    rounds = 0
                    total_matches = 0
                    action = f"P running ({p_duration_seconds:.0f}s)"

                    while time.time() < p_deadline:
                        if is_key_toggled(cancel_vk):
                            running = False
                            state = "PUT"
                            state_enter_ts = time.time()
                            print("STOP (during P_RUN) -> idle")
                            break

                        f2_bgr, _ = grab_frame(sct, monitor)
                        m = run_p_routine_once(f2_bgr, mon_left, mon_top, all_points, config, click_delay, click_jitter)
                        total_matches += m
                        rounds += 1

                        time.sleep(random.uniform(p_interval_range[0], p_interval_range[1]))

                        if show_window:
                            cv2.waitKey(1)

                    if running:
                        elapsed = time.time() - p_start
                        action = f"P done rounds={rounds} matches={total_matches} elapsed={elapsed:.1f}s"
                        short_pause(state_pause_range)
                        state = "COLLECT"
                        state_enter_ts = time.time()

                elif state == "COLLECT":
                    clicked, score0, _loc0, last_collect, note = click_with_verification(
                        sct=sct, monitor=monitor, frame_gray=frame_gray,
                        monitor_left=mon_left, monitor_top=mon_top,
                        templ_current=collect_t, thr_click=thr_collect,
                        click_delay=click_delay, click_jitter=click_jitter,
                        last_click_ts=last_collect, cooldown=cooldown,
                        retries=retries, verify_delay_range=verify_delay_range,
                        confirm_mode="gone", templ_next=None, thr_next=0.0,
                        topk=topk, bright_tol=bright_tol, dark_tol=dark_tol,
                        bg_border=bg_border, bg_max_mean=bg_max_mean,
                    )
                    action = f"COLLECT score={score0:.3f} ({note})"
                    if clicked:
                        short_pause(state_pause_range)
                        state = "CENTER"
                        state_enter_ts = time.time()

                elif state == "CENTER":
                    cx = mon_left + mon_w // 2
                    cy = mon_top + mon_h // 2
                    sendinput_click(cx, cy)
                    jitter_sleep(click_delay, click_jitter)
                    short_pause(state_pause_range)
                    cycle += 1
                    state = "PUT"
                    state_enter_ts = time.time()
                    action = "click center"

            if show_window:
                # debug overlay scores
                p_score, p_loc, p_ok, p_reason = match_best_valid(frame_gray, put_t, topk, bright_tol, dark_tol, bg_border, bg_max_mean)
                s_score, s_loc, s_ok, s_reason = match_best_valid(frame_gray, start_t, topk, bright_tol, dark_tol, bg_border, bg_max_mean)
                c_score, c_loc, c_ok, c_reason = match_best_valid(frame_gray, collect_t, topk, bright_tol, dark_tol, bg_border, bg_max_mean)

                def draw_box(templ, loc, score, name, ok, reason):
                    x, y = loc
                    cv2.rectangle(frame_bgr, (x, y), (x + templ["w"], y + templ["h"]), (0, 0, 255), 2)
                    tag = f"{name} {score:.2f}" + ("" if ok else " !")
                    draw_text_with_bg(frame_bgr, tag, x, max(20, y - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1)
                    if not ok:
                        draw_text_with_bg(frame_bgr, reason[:28], x, min(frame_bgr.shape[0] - 6, y + templ["h"] + 18),
                                          cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1)

                if p_score > 0.30:
                    draw_box(put_t, p_loc, p_score, "put", p_ok, p_reason)
                if s_score > 0.30:
                    draw_box(start_t, s_loc, s_score, "start", s_ok, s_reason)
                if c_score > 0.30:
                    draw_box(collect_t, c_loc, c_score, "collect", c_ok, c_reason)

                dt_ms = (time.time() - t0) * 1000
                state_age = time.time() - state_enter_ts
                hud = [
                    f"{'RUNNING' if running else 'IDLE'} | state={state} | cycle={cycle} | dt={dt_ms:.0f}ms",
                    f"START={start_key} | STOP={cancel_key} | exit=q | state_age={state_age:.1f}s timeout={state_timeout_seconds:.1f}s",
                    f"points={os.path.basename(points_used)} | images_dir={os.path.basename(images_dir)}",
                    f"thr: put={thr_put:.2f} start={thr_start:.2f} collect={thr_collect:.2f}",
                    f"verify: retries={retries} delay={verify_delay_range[0]:.2f}-{verify_delay_range[1]:.2f}s cooldown={cooldown:.2f}s",
                    f"pause={state_pause_range[0]:.1f}-{state_pause_range[1]:.1f}s | P={p_interval_range[0]:.1f}-{p_interval_range[1]:.1f}s | Pdur={p_duration_seconds:.0f}s",
                    f"scores: put={p_score:.3f} start={s_score:.3f} collect={c_score:.3f}",
                    f"action: {action}",
                ]
                y0 = 28
                for i, line in enumerate(hud):
                    draw_text_with_bg(frame_bgr, line, 12, y0 + i * 22, cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)

                cv2.imshow(window_name, frame_bgr)
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break

            time.sleep(scan_interval)

    if show_window:
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
