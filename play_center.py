import sys
import time
import numpy as np
import mss
import pyautogui
from Quartz import (
    CGWindowListCopyWindowInfo,
    kCGWindowListOptionOnScreenOnly,
    kCGNullWindowID,
)
from AppKit import NSWorkspace

# === CONFIG ===
GAME_WINDOW_KEYWORD = "Wiply Games"
CHROME_APP_NAMES = ("google chrome", "chrome")
SCAN_Y_RATIO = 0.32
SCAN_HEIGHT = 10
CENTER_TOLERANCE = 3          # click when abs(offset) <= this many pixels
MIN_INTERVAL = 0.4            # min seconds between clicks
GAME_OVER_UNIFORM_FRAMES = 60
MIN_SWING_RESET = 150         # after click, wait until block swings away this far
MAX_MISSED = 8

last_press = 0


def _all_windows():
    return CGWindowListCopyWindowInfo(kCGWindowListOptionOnScreenOnly, kCGNullWindowID)


def list_windows():
    print(f"{'Owner':<30} {'Title'}")
    print("-" * 70)
    for w in _all_windows():
        owner = w.get("kCGWindowOwnerName", "") or ""
        title = w.get("kCGWindowName", "") or ""
        if owner:
            print(f"{owner:<30} {title}")


def _make_win(w):
    bounds = w.get("kCGWindowBounds", {})
    return {
        "left": int(bounds.get("X", 0)),
        "top": int(bounds.get("Y", 0)),
        "width": int(bounds.get("Width", 0)),
        "height": int(bounds.get("Height", 0)),
    }


def get_game_window():
    windows = _all_windows()
    chrome_candidates = []
    for w in windows:
        title = w.get("kCGWindowName", "") or ""
        owner = w.get("kCGWindowOwnerName", "") or ""

        if GAME_WINDOW_KEYWORD.lower() in title.lower():
            return _make_win(w)

        if owner.lower() in CHROME_APP_NAMES or "chrome" in owner.lower():
            bounds = w.get("kCGWindowBounds", {})
            area = bounds.get("Width", 0) * bounds.get("Height", 0)
            if area > 10000:
                chrome_candidates.append((area, w))

    if chrome_candidates:
        chrome_candidates.sort(key=lambda x: x[0], reverse=True)
        return _make_win(chrome_candidates[0][1])

    return None


def is_game_focused():
    active_app = NSWorkspace.sharedWorkspace().frontmostApplication()
    active_name = (active_app.localizedName() if active_app else "").lower()
    return "chrome" in active_name or GAME_WINDOW_KEYWORD.lower() in active_name


def press_space():
    global last_press
    now = time.time()
    if now - last_press < MIN_INTERVAL:
        return False
    last_press = now
    pyautogui.press("space")
    return True


def find_object_x(frame, prev_frame=None):
    """Return x-center of the largest moving segment, or None."""
    _, W, _ = frame.shape

    if prev_frame is None:
        return None

    diff = np.abs(frame.astype(np.int16) - prev_frame.astype(np.int16))
    col_diff = diff.sum(axis=(0, 2))
    threshold = max(30, col_diff.max() * 0.15)
    xs = list(np.where(col_diff > threshold)[0])

    if not xs:
        return None

    best_start = best_end = start = prev = xs[0]
    for x in xs[1:]:
        if x == prev + 1:
            prev = x
        else:
            if prev - start > best_end - best_start:
                best_start, best_end = start, prev
            start = prev = x

    if prev - start > best_end - best_start:
        best_start, best_end = start, prev

    return (best_start + best_end) // 2


def is_frame_uniform(frame, threshold=8):
    return int(frame.max()) - int(frame.min()) < threshold


def main():
    print("Starting center-tolerance bot... Press Ctrl+C to stop.")
    with mss.mss() as sct:
        uniform_count = 0
        prev_img = None
        prev_x = None
        waiting_for_swing = False
        missed_frames = 0
        frame_time = time.time()
        was_in_zone = False

        while True:
            win = get_game_window()
            if not win:
                print("Game window not found, waiting...")
                time.sleep(1)
                uniform_count = 0
                prev_img = None
                prev_x = None
                waiting_for_swing = False
                missed_frames = 0
                was_in_zone = False
                continue

            if not is_game_focused():
                time.sleep(0.05)
                continue

            monitor = {
                "top": win["top"] + int(win["height"] * SCAN_Y_RATIO),
                "left": win["left"],
                "width": win["width"],
                "height": SCAN_HEIGHT,
            }

            now = time.time()
            dt = now - frame_time
            frame_time = now

            img = np.array(sct.grab(monitor))[:, :, :3]

            if is_frame_uniform(img):
                uniform_count += 1
                if uniform_count >= GAME_OVER_UNIFORM_FRAMES:
                    if uniform_count == GAME_OVER_UNIFORM_FRAMES:
                        print("Canvas appears gone (game over?). Waiting for restart...")
                    prev_img = None
                    prev_x = None
                    waiting_for_swing = False
                    was_in_zone = False
                time.sleep(0.01)
                continue
            else:
                uniform_count = 0

            x = find_object_x(img, prev_img)
            prev_img = img

            if x is None:
                missed_frames += 1
                if missed_frames >= MAX_MISSED:
                    prev_x = None
                    was_in_zone = False
                    missed_frames = 0
                time.sleep(0.01)
                continue

            missed_frames = 0
            center = monitor["width"] // 2
            offset = x - center
            velocity = (x - prev_x) / dt if (prev_x is not None and dt > 0) else 0.0

            if waiting_for_swing:
                if abs(offset) >= MIN_SWING_RESET:
                    waiting_for_swing = False
                    was_in_zone = False
                    print(f"  [ready] block swung to offset={offset:+d}, re-engaging")
                else:
                    print(f"  [wait]  x={x:4d}  offset={offset:+4d}  (waiting for swing)")
                prev_x = x
                time.sleep(0.01)
                continue

            in_zone = abs(offset) <= CENTER_TOLERANCE
            print(
                f"x={x:4d}  offset={offset:+4d}  vel={velocity:+7.1f} px/s  "
                f"zone={'Y' if in_zone else 'N'}"
            )

            # Trigger only on entering the center zone (not while staying inside it)
            if in_zone and not was_in_zone:
                if press_space():
                    print(f"  >>> CLICK (offset={offset:+d}, tol={CENTER_TOLERANCE})")
                    waiting_for_swing = True

            was_in_zone = in_zone
            prev_x = x
            time.sleep(0.01)


if __name__ == "__main__":
    if "--list" in sys.argv:
        list_windows()
    else:
        main()
