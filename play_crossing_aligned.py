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
SCAN_Y_RATIO = 0.2                      # moving block scan location
SCAN_HEIGHT = 50
TOWER_SCAN_Y_RATIO = 0.5               # "highest placed box" probe row
TOWER_SCAN_HEIGHT = 18
MAX_CENTER_SHIFT = 30                   # clip adaptive center shift to [-50, 50]
TOLERANCE = 2                          # pixels from adaptive center to trigger
MIN_INTERVAL = 0.3                     # seconds between drops
GAME_OVER_UNIFORM_FRAMES = 60           # consecutive uniform frames before assuming game over
PREDICT_LEAD_TIME = 0.08                # click this many seconds before center at current speed
MIN_LEAD_PX = 1                         # minimum pre-center trigger distance
MAX_LEAD_PX = 20                        # cap for high-speed swings
MIN_SWING_RESET = 150                   # px from adaptive center to re-arm after click

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
        "left": int(bounds.get("X", 0)),
        "top": int(bounds.get("Y", 0)),
        "width": int(bounds.get("Width", 0)),
        "height": int(bounds.get("Height", 0)),
        "title": w.get("kCGWindowName", "") or "",
        "owner": w.get("kCGWindowOwnerName", "") or "",
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


def _largest_segment(xs):
    """Return (start, end) for the largest continuous run in sorted xs."""
    if not xs:
        return None

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

    return best_start, best_end


def _non_blue_xs(frame):
    """Return x coords that are not background sky-blue."""
    xs = []
    for x in range(frame.shape[1]):
        column = frame[:, x]
        # Sky is blue-dominant; boxes are usually not.
        mask = ~((column[:, 2] > column[:, 0] + 20) & (column[:, 2] > column[:, 1] + 20))
        if np.sum(mask) > 3:
            xs.append(x)
    return xs


def find_object_x(frame, prev_frame=None):
    """Return x center of largest moving segment, or None.

    If prev_frame is supplied, use frame differencing to isolate motion.
    Fallback to non-blue detection when prev_frame is unavailable.
    """
    if prev_frame is not None:
        diff = np.abs(frame.astype(np.int16) - prev_frame.astype(np.int16))
        col_diff = diff.sum(axis=(0, 2))
        threshold = max(30, col_diff.max() * 0.15)
        xs = list(np.where(col_diff > threshold)[0])
    else:
        xs = _non_blue_xs(frame)

    segment = _largest_segment(xs)
    if segment is None:
        return None

    return (segment[0] + segment[1]) // 2


def find_tower_center_x(frame, real_center):
    """Estimate center of top tower box and clip offset to ±MAX_CENTER_SHIFT."""
    xs = _non_blue_xs(frame)
    if not xs:
        return real_center

    # Prefer segments near center; choose the longest among near-center candidates.
    segments = []
    start = prev = xs[0]
    for x in xs[1:]:
        if x == prev + 1:
            prev = x
        else:
            segments.append((start, prev))
            start = prev = x
    segments.append((start, prev))

    near_center = [
        seg for seg in segments
        if abs(((seg[0] + seg[1]) // 2) - real_center) <= MAX_CENTER_SHIFT + 30
    ]
    if not near_center:
        return real_center

    best = max(near_center, key=lambda seg: (seg[1] - seg[0], -abs(((seg[0] + seg[1]) // 2) - real_center)))
    tower_center = (best[0] + best[1]) // 2
    clipped_shift = int(np.clip(tower_center - real_center, -MAX_CENTER_SHIFT, MAX_CENTER_SHIFT))
    return real_center + clipped_shift


def is_frame_uniform(frame, threshold=8):
    """Return True if frame is near-solid color (game over / blank canvas)."""
    return int(frame.max()) - int(frame.min()) < threshold


def main():
    with mss.mss() as sct:
        print("Starting aligned-crossing bot... Press Ctrl+C to stop.")

        uniform_count = 0
        prev_x = None
        prev_moving_img = None
        missed_frames = 0
        MAX_MISSED = 8
        waiting_for_swing = False
        frame_time = time.time()

        while True:
            win = get_game_window()
            if not win:
                print("Game window not found, waiting...")
                time.sleep(1)
                uniform_count = 0
                prev_x = None
                prev_moving_img = None
                missed_frames = 0
                waiting_for_swing = False
                continue

            if not is_game_focused():
                time.sleep(0.05)
                continue

            moving_monitor = {
                "top": win["top"] + int(win["height"] * SCAN_Y_RATIO),
                "left": win["left"],
                "width": win["width"],
                "height": SCAN_HEIGHT,
            }
            tower_monitor = {
                "top": win["top"] + int(win["height"] * TOWER_SCAN_Y_RATIO),
                "left": win["left"],
                "width": win["width"],
                "height": TOWER_SCAN_HEIGHT,
            }

            now = time.time()
            dt = now - frame_time
            frame_time = now

            moving_img = np.array(sct.grab(moving_monitor))[:, :, :3]
            tower_img = np.array(sct.grab(tower_monitor))[:, :, :3]

            if is_frame_uniform(moving_img):
                uniform_count += 1
                if uniform_count >= GAME_OVER_UNIFORM_FRAMES:
                    if uniform_count == GAME_OVER_UNIFORM_FRAMES:
                        print("Canvas appears gone (game over?). Waiting for restart...")
                    prev_x = None
                    prev_moving_img = None
                    waiting_for_swing = False
                prev_moving_img = None
                time.sleep(0.0003)
                continue
            else:
                uniform_count = 0

            real_center = moving_monitor["width"] // 2
            target_center = find_tower_center_x(tower_img, real_center)
            center_shift = target_center - real_center

            x = find_object_x(moving_img, prev_moving_img)
            prev_moving_img = moving_img

            if x is not None:
                missed_frames = 0
                offset = x - target_center
                velocity = (x - prev_x) / dt if (prev_x is not None and dt > 0) else 0

                if waiting_for_swing:
                    if abs(offset) >= MIN_SWING_RESET:
                        waiting_for_swing = False
                        prev_x = x
                        print(
                            f"  [ready] block swung to offset={offset:+d}, "
                            f"center_shift={center_shift:+d}, re-engaging"
                        )
                    else:
                        print(
                            f"  [wait]  x={x:4d}  offset={offset:+4d}  "
                            f"center_shift={center_shift:+4d} (waiting for swing)"
                        )
                    time.sleep(0.001)
                    continue

                print(
                    f"x={x:4d}  offset={offset:+4d}  vel={velocity:+7.1f} px/s  "
                    f"center_shift={center_shift:+4d}"
                )

                if prev_x is not None:
                    prev_offset = prev_x - target_center
                    crossed_center = (prev_offset < 0 and offset >= 0) or \
                                     (prev_offset > 0 and offset <= 0)

                    moving_toward_center = (offset * velocity) < 0
                    lead_px = int(np.clip(abs(velocity) * PREDICT_LEAD_TIME, MIN_LEAD_PX, MAX_LEAD_PX))
                    pre_center_window = moving_toward_center and abs(offset) <= lead_px

                    if (crossed_center and abs(offset) <= TOLERANCE) or (pre_center_window and abs(offset) < 100):
                        trigger = "crossed aligned center" if crossed_center else f"pre-center (lead={lead_px}px)"
                        print(
                            f"  >>> CLICK ({trigger}, vel={velocity:+.1f}, "
                            f"center_shift={center_shift:+d})"
                        )
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
