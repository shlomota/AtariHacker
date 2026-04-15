import sys
import time
import random
import threading
import numpy as np
import mss
import pyautogui
from Quartz import (
    CGWindowListCopyWindowInfo,
    kCGWindowListOptionOnScreenOnly,
    kCGNullWindowID,
)
from AppKit import NSWorkspace

# ── startup splash ──────────────────────────────────────────────────────────

LOGO = r"""
  ███╗   ███╗ █████╗ ███╗   ███╗██╗
  ████╗ ████║██╔══██╗████╗ ████║██║
  ██╔████╔██║███████║██╔████╔██║██║
  ██║╚██╔╝██║██╔══██║██║╚██╔╝██║██║
  ██║ ╚═╝ ██║██║  ██║██║ ╚═╝ ██║██║
  ╚═╝     ╚═╝╚═╝  ╚═╝╚═╝     ╚═╝╚═╝
       🎁  T O W E R  B O T  🎁
"""

MATRIX_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789@#$%&*+-="


def matrix_rain(duration=1.0, cols=80, rows=18):
    """Quick Matrix-style rain effect in the terminal."""
    drops = [random.randint(-rows, 0) for _ in range(cols)]
    start = time.time()
    GREEN  = "\033[32m"
    BRIGHT = "\033[92m"
    RESET  = "\033[0m"
    HIDE   = "\033[?25l"   # hide cursor
    SHOW   = "\033[?25h"   # show cursor

    print(HIDE, end="", flush=True)
    try:
        while time.time() - start < duration:
            lines = []
            for row in range(rows):
                line = ""
                for col in range(cols):
                    d = drops[col]
                    if row == d:                        # head of drop
                        line += BRIGHT + random.choice(MATRIX_CHARS) + RESET
                    elif 0 <= row < d and (d - row) < rows // 2:
                        line += GREEN + random.choice(MATRIX_CHARS) + RESET
                    else:
                        line += " "
                lines.append(line)
            print("\033[H" + "\n".join(lines), end="", flush=True)
            for col in range(cols):
                drops[col] += 1
                if drops[col] > rows + random.randint(0, rows):
                    drops[col] = random.randint(-rows, 0)
            time.sleep(0.05)
    finally:
        print(SHOW, end="", flush=True)


def show_intro():
    GREEN  = "\033[32m"
    YELLOW = "\033[33m"
    CYAN   = "\033[96m"
    RESET  = "\033[0m"
    CLEAR  = "\033[2J\033[H"

    print(CLEAR, end="")
    # Matrix rain
    matrix_rain(duration=1.0)

    # Clear and show logo
    print(CLEAR, end="")
    print(GREEN + LOGO + RESET)
    time.sleep(0.3)

    lines = [
        (CYAN,   "  [ SYSTEM BOOT ]"),
        (GREEN,  "  > loading vision module ............. OK"),
        (GREEN,  "  > calibrating pendulum tracker ...... OK"),
        (GREEN,  "  > red-ribbon detector online ........ OK"),
        (YELLOW, "  > let's win this game 😈"),
        ("",     ""),
        (GREEN,  "  Press Ctrl+C to stop.\n"),
    ]
    for color, text in lines:
        print(color + text + RESET)
        time.sleep(0.18)


# === CONFIG ===
GAME_WINDOW_KEYWORD  = "Wiply Games"
CHROME_APP_NAMES     = ("google chrome", "chrome")

SWING_Y_RATIO        = 0.32   # scan line for the swinging block (frame diff)
TOWER_SEARCH_START   = 0.38   # start scanning for tower top here (below swing)
TOWER_SEARCH_END     = 0.85   # give up looking below this point
SCAN_HEIGHT          = 8      # px height of the swing scan strip

OVERLAP_THRESHOLD    = 0.55   # fraction of block width that must overlap to click
MIN_INTERVAL         = 0.4    # min seconds between clicks
GAME_OVER_UNI_FRAMES = 60     # consecutive uniform frames → game over
MAX_MISSED           = 8      # consecutive None detections before resetting prev_x
MIN_SWING_RESET      = 120    # px from center before re-engaging after a click

last_press = 0


# ── window helpers ──────────────────────────────────────────────────────────

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
        "left":   int(bounds.get("X", 0)),
        "top":    int(bounds.get("Y", 0)),
        "width":  int(bounds.get("Width", 0)),
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
        if "chrome" in owner.lower():
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
    name = (active_app.localizedName() if active_app else "").lower()
    return "chrome" in name or GAME_WINDOW_KEYWORD.lower() in name


# ── input ───────────────────────────────────────────────────────────────────

def press_space():
    global last_press
    now = time.time()
    if now - last_press < MIN_INTERVAL:
        return
    last_press = now
    pyautogui.press('space')


# ── detection ───────────────────────────────────────────────────────────────

def _largest_segment(xs):
    """Given a sorted list of x indices, return (left, right) of the longest run."""
    if not xs:
        return None
    best_l = best_r = start = prev = xs[0]
    for x in xs[1:]:
        if x == prev + 1:
            prev = x
        else:
            if prev - start > best_r - best_l:
                best_l, best_r = start, prev
            start = prev = x
    if prev - start > best_r - best_l:
        best_l, best_r = start, prev
    return best_l, best_r


def _all_segments(xs):
    """Given a sorted list of x indices, return all contiguous (left, right) runs."""
    if not xs:
        return []
    segments = []
    start = prev = xs[0]
    for x in xs[1:]:
        if x == prev + 1:
            prev = x
        else:
            segments.append((start, prev))
            start = prev = x
    segments.append((start, prev))
    return segments


def find_swing_range(frame, prev_frame):
    """
    Frame-diff detection for the MOVING block.
    Returns (left, right) of the largest changing segment, or None.
    """
    if prev_frame is None:
        return None
    diff = np.abs(frame.astype(np.int16) - prev_frame.astype(np.int16))
    col_diff = diff.sum(axis=(0, 2))              # (W,)
    peak = col_diff.max()
    if peak < 30:
        return None
    threshold = peak * 0.15
    xs = list(np.where(col_diff > threshold)[0])
    return _largest_segment(xs)


def find_tower_range(search_strip, expected_cx=None):
    """
    Find the topmost placed block in the tower by detecting the red ribbon
    on gift boxes.  Scans the strip top-to-bottom; the first row with a
    meaningful cluster of red pixels is the top of the tower.

    Returns (left, right) of the block at that row, or None.
    """
    H, W, _ = search_strip.shape

    r = search_strip[:, :, 0].astype(np.int16)
    g = search_strip[:, :, 1].astype(np.int16)
    b = search_strip[:, :, 2].astype(np.int16)

    # Red ribbon: dominant red channel (more robust to shading/AA than hard RGB caps)
    red_mask = (r > 105) & ((r - g) > 35) & ((r - b) > 35)  # shape (H, W)

    # Find topmost row with enough red pixels
    row_red_count = red_mask.sum(axis=1)             # (H,)
    red_rows = np.where(row_red_count >= 2)[0]

    if len(red_rows) == 0:
        return None

    candidates = []
    for top_row in red_rows[:30]:
        # Scan a band from this row downward to capture the box body
        band_end = min(H, top_row + 48)
        band_red = red_mask[top_row:band_end]              # (band_h, W)

        # White/light box body in same band
        r_b = r[top_row:band_end]
        g_b = g[top_row:band_end]
        b_b = b[top_row:band_end]
        white_mask = (r_b > 180) & (g_b > 180) & (b_b > 180)

        combined = band_red | white_mask                   # red OR white columns
        col_active = combined.any(axis=0)                  # (W,)
        xs = list(np.where(col_active)[0])
        segments = _all_segments(xs)

        for seg_l, seg_r in segments:
            width = seg_r - seg_l + 1
            if width < 12:
                continue
            seg_red = int(band_red[:, seg_l:seg_r + 1].sum())
            seg_white = int(white_mask[:, seg_l:seg_r + 1].sum())

            # Reject noisy red blobs (e.g., hearts/HUD elements)
            if seg_red < max(10, width // 2):
                continue
            if seg_white < width * 2:
                continue

            cx = (seg_l + seg_r) // 2
            dist = abs(cx - expected_cx) if expected_cx is not None else 0
            score = seg_red + 0.20 * seg_white - 1.5 * dist - 0.6 * top_row
            candidates.append((score, dist, (seg_l, seg_r)))

    if not candidates:
        return None

    candidates.sort(key=lambda item: (-item[0], item[1]))
    return candidates[0][2]


def calc_overlap_ratio(r1, r2):
    """Overlap as fraction of the smaller segment's width."""
    l1, r1 = r1
    l2, r2 = r2
    overlap = max(0, min(r1, r2) - max(l1, l2))
    min_width = min(r1 - l1, r2 - l2)
    if min_width <= 0:
        return 0.0
    return overlap / min_width


def is_frame_uniform(frame, threshold=8):
    return int(frame.max()) - int(frame.min()) < threshold


# ── main loop ────────────────────────────────────────────────────────────────

def main():
    show_intro()
    with mss.mss() as sct:

        uniform_count    = 0
        prev_swing_img   = None
        prev_swing_range = None
        missed_frames    = 0
        prev_overlap     = 0.0
        waiting_for_swing = False
        frame_time       = time.time()

        while True:
            win = get_game_window()
            if not win:
                print("Game window not found, waiting...")
                time.sleep(1)
                prev_swing_img = prev_swing_range = None
                uniform_count = missed_frames = 0
                waiting_for_swing = False
                prev_overlap = 0.0
                continue

            if not is_game_focused():
                time.sleep(0.05)
                continue

            now = time.time()
            dt  = now - frame_time
            frame_time = now

            # ── grab scan strips ──
            swing_mon = {
                "top":    win["top"] + int(win["height"] * SWING_Y_RATIO),
                "left":   win["left"],
                "width":  win["width"],
                "height": SCAN_HEIGHT,
            }
            tower_search_top = win["top"] + int(win["height"] * TOWER_SEARCH_START)
            tower_search_bot = win["top"] + int(win["height"] * TOWER_SEARCH_END)
            tower_mon = {
                "top":    tower_search_top,
                "left":   win["left"],
                "width":  win["width"],
                "height": max(1, tower_search_bot - tower_search_top),
            }

            swing_img = np.array(sct.grab(swing_mon))[:, :, :3]
            tower_img = np.array(sct.grab(tower_mon))[:, :, :3]

            # ── game-over detection ──
            if is_frame_uniform(swing_img) and is_frame_uniform(tower_img):
                uniform_count += 1
                if uniform_count == GAME_OVER_UNI_FRAMES:
                    print("Canvas appears gone (game over?). Waiting for restart...")
                if uniform_count >= GAME_OVER_UNI_FRAMES:
                    prev_swing_img = prev_swing_range = None
                    waiting_for_swing = False
                    prev_overlap = 0.0
                prev_swing_img = None
                time.sleep(0.01)
                continue
            else:
                uniform_count = 0

            # ── detect swinging block ──
            swing_range = find_swing_range(swing_img, prev_swing_img)
            prev_swing_img = swing_img

            if swing_range is None:
                missed_frames += 1
                if missed_frames >= MAX_MISSED:
                    prev_swing_range = None
                    missed_frames = 0
                time.sleep(0.01)
                continue

            missed_frames = 0
            swing_cx = (swing_range[0] + swing_range[1]) // 2
            center   = win["width"] // 2
            offset   = swing_cx - center
            velocity = ((swing_cx - ((prev_swing_range[0] + prev_swing_range[1]) // 2)) / dt
                        if (prev_swing_range is not None and dt > 0) else 0)

            # ── detect tower top block (biased toward swing x to avoid HUD false positives) ──
            tower_range = find_tower_range(tower_img, expected_cx=swing_cx)

            # ── post-click: wait for block to swing away from center ──
            if waiting_for_swing:
                if abs(offset) >= MIN_SWING_RESET:
                    waiting_for_swing = False
                    print(f"  [ready] offset={offset:+d}, re-engaging")
                else:
                    prev_swing_range = swing_range
                    time.sleep(0.01)
                    continue

            # ── overlap with tower top ──
            overlap = 0.0
            if tower_range is not None:
                overlap = calc_overlap_ratio(swing_range, tower_range)
                tower_cx = (tower_range[0] + tower_range[1]) // 2
                print(f"swing=({swing_range[0]:4d},{swing_range[1]:4d}) cx={swing_cx:4d}  "
                      f"tower=({tower_range[0]:4d},{tower_range[1]:4d}) cx={tower_cx:4d}  "
                      f"overlap={overlap:.2f}  vel={velocity:+7.1f}")
            else:
                print(f"swing=({swing_range[0]:4d},{swing_range[1]:4d}) cx={swing_cx:4d}  "
                      f"tower=None  vel={velocity:+7.1f}")

            # ── click on rising edge above threshold ──
            if overlap >= OVERLAP_THRESHOLD and prev_overlap < OVERLAP_THRESHOLD:
                print(f"  >>> CLICK (overlap={overlap:.2f}, vel={velocity:+.1f})")
                press_space()
                waiting_for_swing = True
                prev_overlap = 0.0
            else:
                prev_overlap = overlap

            prev_swing_range = swing_range
            time.sleep(0.01)


if __name__ == "__main__":
    if "--list" in sys.argv:
        list_windows()
    else:
        main()
