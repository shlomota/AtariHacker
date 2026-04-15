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
GAME_WINDOW_KEYWORD = "Wiply Games"   # part of your tab title (adjust if needed)
CHROME_APP_NAMES = ("google chrome", "chrome")
SCAN_Y_RATIO = 0.32            # vertical location to scan (tune if needed)
TOLERANCE = 2                 # pixels from center to trigger
MIN_INTERVAL = 0.4             # seconds between drops
GAME_OVER_UNIFORM_FRAMES = 60  # consecutive uniform frames before assuming game over
PREDICT_LEAD_TIME = 0.018      # click this many seconds before center at current speed
MIN_LEAD_PX = 4                # minimum pre-center trigger distance
MAX_LEAD_PX = 30               # cap for high-speed swings

last_press = 0


def _all_windows():
    return CGWindowListCopyWindowInfo(kCGWindowListOptionOnScreenOnly, kCGNullWindowID)


def list_windows():
    """Print all visible windows for debugging."""
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
        "left":   int(bounds.get("X", 0)),
        "top":    int(bounds.get("Y", 0)),
        "width":  int(bounds.get("Width", 0)),
        "height": int(bounds.get("Height", 0)),
        "title":  w.get("kCGWindowName", "") or "",
        "owner":  w.get("kCGWindowOwnerName", "") or "",
    }


def get_game_window():
    """Return window info dict for the game window, or None.

    Priority:
      1. Any window whose title contains GAME_WINDOW_KEYWORD
      2. The largest Chrome window (fallback when the tab title is foreign-language)
    """
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
            if area > 10000:          # ignore tiny helper windows
                chrome_candidates.append((area, w))

    if chrome_candidates:
        chrome_candidates.sort(key=lambda x: x[0], reverse=True)
        return _make_win(chrome_candidates[0][1])

    return None


def is_game_focused():
    """Return True if Chrome (or the keyword app) is frontmost."""
    active_app = NSWorkspace.sharedWorkspace().frontmostApplication()
    active_name = (active_app.localizedName() if active_app else "").lower()
    return "chrome" in active_name or GAME_WINDOW_KEYWORD.lower() in active_name


def press_space():
    global last_press
    now = time.time()
    if now - last_press < MIN_INTERVAL:
        return
    last_press = now
    pyautogui.press('space')


def find_object_x(frame, prev_frame=None):
    """
    If prev_frame is supplied, use frame differencing to find the MOVING block
    (ignores the static tower entirely).
    Falls back to color-based detection when prev_frame is unavailable.
    Returns x center of the largest moving segment, or None.
    """
    H, W, _ = frame.shape

    if prev_frame is not None:
        # Per-column sum of absolute pixel change
        diff = np.abs(frame.astype(np.int16) - prev_frame.astype(np.int16))
        col_diff = diff.sum(axis=(0, 2))          # shape (W,)
        threshold = max(30, col_diff.max() * 0.15)  # adaptive: 15% of peak change
        xs = list(np.where(col_diff > threshold)[0])
    else:
        # Fallback: non-blue pixel detection
        xs = []
        for x in range(W):
            column = frame[:, x]
            mask = ~((column[:, 2] > column[:, 0] + 20) & (column[:, 2] > column[:, 1] + 20))
            if np.sum(mask) > 3:
                xs.append(x)

    if not xs:
        return None

    # Find largest continuous segment
    best_start = best_end = xs[0]
    start = prev = xs[0]

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
    """Return True if the frame is a near-solid color (game over / blank canvas)."""
    return int(frame.max()) - int(frame.min()) < threshold


def main():
    with mss.mss() as sct:
        print("Starting bot... Press Ctrl+C to stop.")

        # After clicking, wait until the block swings this far from center
        # before re-engaging — filters out the static tower sitting at center
        MIN_SWING_RESET = 150   # px

        uniform_count = 0
        prev_x = None
        prev_img = None
        missed_frames = 0
        MAX_MISSED = 8          # frames before we consider the block gone
        waiting_for_swing = False   # True = post-click, waiting for block to leave center
        frame_time = time.time()

        while True:
            win = get_game_window()
            if not win:
                print("Game window not found, waiting...")
                time.sleep(1)
                uniform_count = 0
                prev_x = None
                prev_img = None
                missed_frames = 0
                waiting_for_swing = False
                continue

            if not is_game_focused():
                time.sleep(0.05)
                continue

            monitor = {
                "top":    win["top"] + int(win["height"] * SCAN_Y_RATIO),
                "left":   win["left"],
                "width":  win["width"],
                "height": 10,
            }

            now = time.time()
            dt = now - frame_time
            frame_time = now

            img = np.array(sct.grab(monitor))[:, :, :3]

            # Game-over detection: stop if canvas has been blank/uniform too long
            if is_frame_uniform(img):
                uniform_count += 1
                if uniform_count >= GAME_OVER_UNIFORM_FRAMES:
                    if uniform_count == GAME_OVER_UNIFORM_FRAMES:
                        print("Canvas appears gone (game over?). Waiting for restart...")
                    prev_x = None
                    prev_img = None
                    waiting_for_swing = False
                prev_img = None
                time.sleep(0.0003)
                continue
            else:
                uniform_count = 0

            x = find_object_x(img, prev_img)
            prev_img = img

            if x is not None:
                missed_frames = 0
                center = monitor["width"] // 2
                offset = x - center

                # velocity in px/s (positive = moving right)
                velocity = (x - prev_x) / dt if (prev_x is not None and dt > 0) else 0

                # Post-click: ignore until the new block has swung far from center
                if waiting_for_swing:
                    if abs(offset) >= MIN_SWING_RESET:
                        waiting_for_swing = False
                        prev_x = x
                        print(f"  [ready] block swung to offset={offset:+d}, re-engaging")
                    else:
                        print(f"  [wait]  x={x:4d}  offset={offset:+4d}  (waiting for swing)")
                    time.sleep(0.01)
                    continue

                print(f"x={x:4d}  offset={offset:+4d}  vel={velocity:+7.1f} px/s")

                # Fire near center crossing:
                # 1) exact crossing (prev and current on opposite sides), or
                # 2) predictive pre-click when fast and still approaching center
                if prev_x is not None:
                    prev_offset = prev_x - center
                    crossed_center = (prev_offset < 0 and offset >= 0) or \
                                     (prev_offset > 0 and offset <= 0)

                    moving_toward_center = (offset * velocity) < 0
                    lead_px = int(np.clip(abs(velocity) * PREDICT_LEAD_TIME, MIN_LEAD_PX, MAX_LEAD_PX))
                    pre_center_window = moving_toward_center and abs(offset) <= lead_px

                    if crossed_center or pre_center_window:
                        trigger = "crossed center" if crossed_center else f"pre-center (lead={lead_px}px)"
                        print(f"  >>> CLICK ({trigger}, vel={velocity:+.1f})")
                        press_space()
                        waiting_for_swing = True
                        prev_x = None

                if not waiting_for_swing:
                    prev_x = x
            else:
                missed_frames += 1
                if missed_frames >= MAX_MISSED:
                    prev_x = None
                    missed_frames = 0

            time.sleep(0.01)


if __name__ == "__main__":
    if "--list" in sys.argv:
        list_windows()
    else:
        main()
