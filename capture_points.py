import json
import time
import os
import pyautogui

# --- DPI adjustment (Windows) so coordinates match the screen ---
try:
    import ctypes
    ctypes.windll.user32.SetProcessDPIAware()
except Exception:
    pass

CONFIG_FILE = "config.json"
POINTS_FILE = "points.json"
TOTAL_POINTS_LEFT = 10


def load_config():
    """Loads settings from the config.json file."""
    if not os.path.exists(CONFIG_FILE):
        raise RuntimeError(f"Configuration file not found: {CONFIG_FILE}")

    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def main():
    config = load_config()

    # Get monitor resolution from config.json (same config used by the detector script)
    screen_width = int(config["monitor"]["width"])
    screen_height = int(config["monitor"]["height"])

    print("We will capture 10 points on the LEFT side.")
    print("For each point:")
    print("  1) Move the mouse to the CENTER of the top-left square (first square).")
    print("  2) Keep the mouse still.")
    print("  3) Focus the terminal/command prompt window, then press ENTER to capture.")
    print("Then move to the next square and press ENTER again, and so on.")
    print("")
    print("Note:")
    print("- You only need to capture ONE side (LEFT). The RIGHT side is mirrored automatically.")
    print("- Tip: keep the terminal on a second monitor if possible, so you can press ENTER easily.")
    input("Press ENTER to start...")

    left_points = []
    for i in range(TOTAL_POINTS_LEFT):
        input(f"[{i+1}/{TOTAL_POINTS_LEFT}] Move to the center of the next square and press ENTER...")
        x, y = pyautogui.position()
        left_points.append((x, y))
        print(f"  Captured: ({x}, {y})")
        time.sleep(0.05)

    # Mirror points horizontally to generate the right side automatically
    right_points = [(screen_width - 1 - x, y) for (x, y) in left_points]

    data = {
        "screen_width": screen_width,
        "screen_height": screen_height,
        "left_points": left_points,
        "right_points": right_points,
    }

    with open(POINTS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

    print(f"Saved to {POINTS_FILE}.")


if __name__ == "__main__":
    main()
