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

# Moving block scan settings
SCAN_Y_RATIO = 0.32
SCAN_HEIGHT = 10

# Highest-box center detection settings (relative to game window)
BOX_SEARCH_Y_MIN_RATIO = 0.18
BOX_SEARCH_Y_MAX_RATIO = 0.76
BOX_SEARCH_X_MIN_RATIO = 0.15
BOX_SEARCH_X_MAX_RATIO = 0.85
BOX_MIN_ROW_PIXELS = 12
BOX_MIN_COL_PIXELS = 3
BOX_MAX_MISSED = 30

CENTER_TOLERANCE = 2
MIN_INTERVAL = 0.4
GAME_OVER_UNIFORM_FRAMES = 60
MIN_SWING_RESET = 150
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


def _largest_segment(xs):
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
    return best_start, best_end


def find_object_x(frame, prev_frame=None):
    """Return x-center of largest moving segment, or None."""
    if prev_frame is None:
        return None

    diff = np.abs(frame.astype(np.int16) - prev_frame.astype(np.int16))
    col_diff = diff.sum(axis=(0, 2))
    threshold = max(30, col_diff.max() * 0.15)
    xs = list(np.where(col_diff > threshold)[0])

    segment = _largest_segment(xs)
    if segment is None:
        return None

    left, right = segment
    return (left + right) // 2


def find_highest_box_center_x(frame):
    """
    Find center-x of the highest gift box based on white/off-white pixels.

    This avoids relying on ribbon color (red/gold/etc.).
    """
    h, w, _ = frame.shape

    y0 = int(h * BOX_SEARCH_Y_MIN_RATIO)
    y1 = int(h * BOX_SEARCH_Y_MAX_RATIO)
    x0 = int(w * BOX_SEARCH_X_MIN_RATIO)
    x1 = int(w * BOX_SEARCH_X_MAX_RATIO)

    roi = frame[y0:y1, x0:x1]
    if roi.size == 0:
        return None

    # BGR channels from mss; detect neutral bright tones (white/off-white box faces)
    c_min = roi.min(axis=2)
    c_max = roi.max(axis=2)
    mask = (c_min >= 150) & ((c_max - c_min) <= 45)

    row_counts = mask.sum(axis=1)
    candidate_rows = np.where(row_counts >= BOX_MIN_ROW_PIXELS)[0]
    if len(candidate_rows) == 0:
        return None

    # Highest matching row in ROI
    top_row = int(candidate_rows[0])

    # Build a small vertical band that follows this object downward
    end_row = top_row
    max_row = mask.shape[0] - 1
    gap = 0
    while end_row < max_row:
        next_row = end_row + 1
        if row_counts[next_row] >= max(6, BOX_MIN_ROW_PIXELS // 2):
            end_row = next_row
            gap = 0
        else:
            gap += 1
            end_row = next_row
            if gap >= 3:
                break

    band = mask[top_row:end_row + 1, :]
    col_counts = band.sum(axis=0)
    cols = list(np.where(col_counts >= BOX_MIN_COL_PIXELS)[0])
    segment = _largest_segment(cols)
    if segment is None:
        return None

    seg_left, seg_right = segment
    center_roi_x = (seg_left + seg_right) // 2
    return x0 + center_roi_x


def is_frame_uniform(frame, threshold=8):
    return int(frame.max()) - int(frame.min()) < threshold


def main():
    print("Starting script #4 (highest-box-center bot)... Press Ctrl+C to stop.")
    with mss.mss() as sct:
        uniform_count = 0
        prev_img = None
        prev_x = None
        waiting_for_swing = False
        missed_frames = 0
        was_in_zone = False

        target_center_x = None
        target_missed = 0

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
                target_center_x = None
                target_missed = 0
                continue

            if not is_game_focused():
                time.sleep(0.05)
                continue

            full_monitor = {
                "top": win["top"],
                "left": win["left"],
                "width": win["width"],
                "height": win["height"],
            }
            strip_monitor = {
                "top": win["top"] + int(win["height"] * SCAN_Y_RATIO),
                "left": win["left"],
                "width": win["width"],
                "height": SCAN_HEIGHT,
            }

            full_img = np.array(sct.grab(full_monitor))[:, :, :3]
            img = np.array(sct.grab(strip_monitor))[:, :, :3]

            if is_frame_uniform(img):
                uniform_count += 1
                if uniform_count >= GAME_OVER_UNIFORM_FRAMES:
                    if uniform_count == GAME_OVER_UNIFORM_FRAMES:
                        print("Canvas appears gone (game over?). Waiting for restart...")
                    prev_img = None
                    prev_x = None
                    waiting_for_swing = False
                    was_in_zone = False
                    target_center_x = None
                    target_missed = 0
                time.sleep(0.001)
                continue
            else:
                uniform_count = 0

            # Update "true center" from highest detected white/off-white block
            detected_center = find_highest_box_center_x(full_img)
            if detected_center is not None:
                target_center_x = detected_center
                target_missed = 0
            else:
                target_missed += 1
                if target_missed >= BOX_MAX_MISSED:
                    target_center_x = None

            x = find_object_x(img, prev_img)
            prev_img = img

            if x is None:
                missed_frames += 1
                if missed_frames >= MAX_MISSED:
                    prev_x = None
                    was_in_zone = False
                    missed_frames = 0
                time.sleep(0.001)
                continue

            missed_frames = 0
            center = target_center_x if target_center_x is not None else (strip_monitor["width"] // 2)
            offset = x - center

            if waiting_for_swing:
                if abs(offset) >= MIN_SWING_RESET:
                    waiting_for_swing = False
                    was_in_zone = False
                    print(f"  [ready] block swung to offset={offset:+d}, re-engaging")
                else:
                    print(f"  [wait]  x={x:4d}  offset={offset:+4d}  center={center:4d} (waiting for swing)")
                prev_x = x
                time.sleep(0.01)
                continue

            in_zone = abs(offset) <= CENTER_TOLERANCE
            center_src = "box" if target_center_x is not None else "screen"
            print(
                f"x={x:4d}  center={center:4d}({center_src})  offset={offset:+4d}  "
                f"zone={'Y' if in_zone else 'N'}"
            )

            if in_zone and not was_in_zone:
                if press_space():
                    print(f"  >>> CLICK (offset={offset:+d}, tol={CENTER_TOLERANCE}, center={center_src})")
                    waiting_for_swing = True

            was_in_zone = in_zone
            prev_x = x
            time.sleep(0.01)


if __name__ == "__main__":
    if "--list" in sys.argv:
        list_windows()
    else:
        main()
