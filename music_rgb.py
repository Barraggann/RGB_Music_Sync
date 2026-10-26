"""Sync PC RGB lighting with Apple Music.

When the full-screen player (lyrics view) is open, the lights mirror its
animated blurred background live: colors are sampled in a ring around the
window edges (skipping artwork, lyrics and buttons) and that ring is laid
around each fan/strip, so the LEDs move as the background moves. The window
can be behind other windows, just not minimized.

Otherwise it falls back to the album art's main colors as a slowly flowing
gradient. Everything is smoothed, so switching songs or modes cross-fades.

The keyboard (VIA firmware, see KEYBOARD_USB_ID) can only show one color at
a time, so it gets the dominant color of the same ring.

Requirements: OpenRGB running with the SDK server enabled (default port 6742).
Usage:  py music_rgb.py            (Ctrl+C to stop)
        py music_rgb.py --dry-run  (print colors only, no OpenRGB needed)
"""
import asyncio
import colorsys
import ctypes
import ctypes.wintypes as wt
import io
import math
import sys
import threading
import time

import numpy as np
from PIL import Image
from winrt.windows.media.control import (
    GlobalSystemMediaTransportControlsSessionManager as SessionManager,
)
from winrt.windows.storage.streams import Buffer, InputStreamOptions

APP_ID = "AppleMusic"   # matches AppleMusic.exe; iTunes users can change this
POLL_SECONDS = 1.0
FPS = 30                   # lighting updates per second
RING_SIZE = 24             # color samples around the window / gradient
PALETTE_SIZE = 2           # album colors used by the fallback gradient
PATTERN_LEDS = 12          # LEDs per full trip around the ring (≈ one fan)
FLOW_SECONDS = 8.0         # fallback gradient travel time per cycle (0 = static)
LIVE_SMOOTHING = 0.3       # seconds; how quickly lights follow the live background
FADE_SMOOTHING = 1.0       # seconds; song changes / mode switches
LIVE_SATURATION = 1.35     # boost for the (muted) background colors on LEDs
LIVE_MIN_BRIGHTNESS = 0.45
IDLE_COLOR = (40, 40, 60)  # dim color while paused / nothing playing
ARGB_HEADER_LEDS = 60      # LEDs per fan/strip header (raise if some LEDs stay dark)
# OpenRGB zones to leave alone, per device name. OpenRGB 1.1 can't drive the fan
# headers of the Gigabyte B850M EAGLE (IT5711 chip): they keep their rainbow and
# only flicker when sent colors. Add your own device here if it does the same.
SKIP_ZONES = {"B850M EAGLE WIFI6E ICE": {"ARGB_V2_1", "ARGB_V2_2", "ARGB_V2_3"}}
# (vendor id, product id) of a VIA keyboard with RGB Matrix, or None to skip.
# Default: Royal Kludge R65. Find yours in Device Manager > Details > Hardware Ids.
KEYBOARD_USB_ID = (0x342D, 0xE508)
KEYBOARD_MIN_BRIGHTNESS = 0.6
DRY_RUN = "--dry-run" in sys.argv

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
kernel32 = ctypes.windll.kernel32
user32.SetProcessDPIAware()  # so window and UI Automation coordinates agree
# 64-bit handles/pointers: without these, ctypes truncates them to 32-bit ints
user32.GetDC.restype = wt.HDC
user32.GetDC.argtypes = [wt.HWND]
user32.ReleaseDC.argtypes = [wt.HWND, wt.HDC]
user32.PrintWindow.argtypes = [wt.HWND, wt.HDC, wt.UINT]
gdi32.CreateCompatibleDC.restype = wt.HDC
gdi32.CreateCompatibleDC.argtypes = [wt.HDC]
gdi32.CreateCompatibleBitmap.restype = wt.HBITMAP
gdi32.CreateCompatibleBitmap.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int]
gdi32.SelectObject.restype = wt.HGDIOBJ
gdi32.SelectObject.argtypes = [wt.HDC, wt.HGDIOBJ]
gdi32.GetDIBits.argtypes = [wt.HDC, wt.HBITMAP, wt.UINT, wt.UINT, ctypes.c_void_p,
                            ctypes.c_void_p, wt.UINT]
gdi32.DeleteObject.argtypes = [wt.HGDIOBJ]
gdi32.DeleteDC.argtypes = [wt.HDC]
kernel32.OpenProcess.restype = wt.HANDLE
kernel32.CloseHandle.argtypes = [wt.HANDLE]
kernel32.QueryFullProcessImageNameW.argtypes = [wt.HANDLE, wt.DWORD, wt.LPWSTR,
                                                ctypes.POINTER(wt.DWORD)]


async def find_session(manager):
    for s in manager.get_sessions():
        if APP_ID.lower() in (s.source_app_user_model_id or "").lower():
            return s
    return None


async def read_thumbnail(props):
    if props.thumbnail is None:
        return None
    stream = await props.thumbnail.open_read_async()
    buf = Buffer(stream.size)
    await stream.read_async(buf, buf.capacity, InputStreamOptions.READ_AHEAD)
    return Image.open(io.BytesIO(bytes(buf))).convert("RGB")


# --- OKLab: a color space where blends look natural (no muddy grey midpoints) ---

def _to_linear(c):
    c /= 255
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _to_srgb(c):
    c = 12.92 * c if c <= 0.0031308 else 1.055 * max(c, 0) ** (1 / 2.4) - 0.055
    return max(0, min(255, round(c * 255)))


def to_oklab(rgb):
    r, g, b = (_to_linear(c) for c in rgb)
    l = (0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b) ** (1 / 3)
    m = (0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b) ** (1 / 3)
    s = (0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b) ** (1 / 3)
    return (0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s,
            1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s,
            0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s)


def from_oklab(lab):
    L, a, b = lab
    l = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3
    m = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3
    s = (L - 0.0894841775 * a - 1.2914855480 * b) ** 3
    return (_to_srgb(4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s),
            _to_srgb(-1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s),
            _to_srgb(-0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s))


def lerp(a, b, t):
    return tuple(x + (y - x) * t for x, y in zip(a, b))


def ease(t):
    return t * t * (3 - 2 * t)  # smoothstep: gentle start and finish


def ring_at(ring, pos):
    """Color at pos (0..1, wrapping) along a looping list of OKLab colors."""
    n = len(ring)
    x = (pos % 1.0) * n
    i = int(x)
    return lerp(ring[i % n], ring[(i + 1) % n], ease(x - i))


# --- album palette (fallback) ---

def _led_friendly(rgb):
    """Boost saturation/brightness so the color reads well on LEDs."""
    h, s, v = colorsys.rgb_to_hsv(*(c / 255 for c in rgb))
    if s < 0.15:  # greyscale -> soft white
        return (200, 200, 200)
    s = min(1.0, s * 1.3 + 0.15)
    v = max(v, 0.85)  # LEDs show dark colors poorly
    return tuple(round(c * 255) for c in colorsys.hsv_to_rgb(h, s, v))


def album_palette(img, n=PALETTE_SIZE):
    """The n most prominent vivid colors of the cover, each clearly different."""
    small = img.resize((64, 64))
    pal = small.quantize(colors=16, method=Image.Quantize.MEDIANCUT)
    palette = pal.getpalette()
    scored = []
    for count, idx in pal.getcolors():
        rgb = tuple(palette[idx * 3: idx * 3 + 3])
        h, s, v = colorsys.rgb_to_hsv(*(c / 255 for c in rgb))
        # favour saturated, bright colors, weighted by how much of the cover they fill
        score = (s * 0.7 + 0.3) * (v * 0.6 + 0.4) * (count ** 0.5)
        if v < 0.12:
            score *= 0.1  # near-black is a bad LED color
        scored.append((score, _led_friendly(rgb)))
    scored.sort(reverse=True)

    chosen = []
    for _, rgb in scored:
        lab = to_oklab(rgb)
        if all(math.dist(lab, to_oklab(c)) > 0.12 for c in chosen):
            chosen.append(rgb)
        if len(chosen) == n:
            break
    if not chosen:
        chosen = [(200, 200, 200)]
    while len(chosen) < n:  # cover has fewer distinct colors: use lighter/darker shades
        h, s, v = colorsys.rgb_to_hsv(*(c / 255 for c in chosen[0]))
        shade = colorsys.hsv_to_rgb((h + 0.04 * len(chosen)) % 1, s * 0.75, v)
        chosen.append(tuple(round(c * 255) for c in shade))
    return chosen


# --- live capture of the full-screen player background ---

def _process_name(hwnd):
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    h = kernel32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return ""
    buf = ctypes.create_unicode_buffer(260)
    size = wt.DWORD(260)
    kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size))
    kernel32.CloseHandle(h)
    return buf.value.rsplit("\\", 1)[-1]


def _apple_music_windows():
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
    def cb(hwnd, _):
        if (user32.IsWindowVisible(hwnd) and not user32.IsIconic(hwnd)
                and APP_ID.lower() in _process_name(hwnd).lower()):
            found.append(hwnd)
        return True

    user32.EnumWindows(cb, 0)
    return found


class _BitmapInfo(ctypes.Structure):
    _fields_ = [("size", wt.DWORD), ("width", wt.LONG), ("height", wt.LONG),
                ("planes", wt.WORD), ("bits", wt.WORD), ("compression", wt.DWORD),
                ("image_size", wt.DWORD), ("xppm", wt.LONG), ("yppm", wt.LONG),
                ("used", wt.DWORD), ("important", wt.DWORD)]


class PlayerWatcher(threading.Thread):
    """Finds the full-screen player window and where its artwork/lyrics/buttons are.

    UI Automation is slow, so this runs in the background every few seconds;
    the render loop only reads `self.state`.
    """
    SKIP_IDS = {"TimeBasedLyrics"}  # don't walk into hundreds of lyric lines

    def __init__(self):
        super().__init__(daemon=True)
        self.state = None  # (hwnd, [exclusion rects in client coords])

    def run(self):
        # imported here, not at the top: importing it sets up COM on the importing
        # thread, which on the main thread blocks the album artwork download
        global auto
        import uiautomation as auto
        with auto.UIAutomationInitializerInThread():
            while True:
                try:
                    self.state = self._scan()
                except Exception:
                    self.state = None
                time.sleep(3)

    def _scan(self):
        for hwnd in _apple_music_windows():
            rects, is_player = [], False

            def walk(ctrl, depth):
                nonlocal is_player
                for ch in ctrl.GetChildren():
                    aid = ch.AutomationId
                    if aid == "ExitFullScreenButton":
                        is_player = True
                    # the lyrics pane spans half the window and its text is filtered out anyway
                    if (ch.Name or aid) and aid not in self.SKIP_IDS:
                        r = ch.BoundingRectangle
                        if r.width() > 0 and r.height() > 0:
                            rects.append((r.left, r.top, r.right, r.bottom))
                    if depth < 3 and aid not in self.SKIP_IDS:  # player controls sit shallow
                        walk(ch, depth + 1)

            walk(auto.ControlFromHandle(hwnd), 0)
            if is_player:
                origin = wt.POINT(0, 0)
                user32.ClientToScreen(hwnd, ctypes.byref(origin))
                pad = 16
                return hwnd, [(l - origin.x - pad, t - origin.y - pad, r - origin.x + pad,
                               b - origin.y + pad) for l, t, r, b in rects]
        return None


class Capturer:
    SCALE = 8  # work on a 1/8-size image; the background is blurry anyway

    def __init__(self):
        self.cache = None  # (hwnd, size, dc, bitmap)
        self.boxes_for = None
        self.boxes = []

    def _grab(self, hwnd):
        r = wt.RECT()
        user32.GetClientRect(hwnd, ctypes.byref(r))
        w, h = r.right, r.bottom
        if w < 200 or h < 200:
            return None
        if not self.cache or self.cache[:2] != (hwnd, (w, h)):
            self._release()
            wdc = user32.GetDC(hwnd)
            dc = gdi32.CreateCompatibleDC(wdc)
            bmp = gdi32.CreateCompatibleBitmap(wdc, w, h)
            gdi32.SelectObject(dc, bmp)
            user32.ReleaseDC(hwnd, wdc)
            self.cache = (hwnd, (w, h), dc, bmp)
        _, _, dc, bmp = self.cache
        if not user32.PrintWindow(hwnd, dc, 3):  # client area, full DirectComposition content
            return None
        info = _BitmapInfo(ctypes.sizeof(_BitmapInfo), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
        buf = np.empty((h, w, 4), np.uint8)
        gdi32.GetDIBits(dc, bmp, 0, h, buf.ctypes.data, ctypes.byref(info), 0)
        return buf[::self.SCALE, ::self.SCALE, 2::-1]  # BGRA -> RGB

    def _release(self):
        if self.cache:
            gdi32.DeleteObject(self.cache[3])
            gdi32.DeleteDC(self.cache[2])
            self.cache = None

    def _ring_boxes(self, w, h, exclusions):
        """Sample boxes in a loop around the window edges, minus covered areas."""
        inset, size = 0.05, max(2, round(min(w, h) * 0.05))
        x0, y0, x1, y1 = w * inset, h * inset, w * (1 - inset), h * (1 - inset)
        per = 2 * ((x1 - x0) + (y1 - y0))
        boxes = []
        for i in range(RING_SIZE * 4):  # clockwise from top-left
            d = per * i / (RING_SIZE * 4)
            for length, start, step in (((x1 - x0), (x0, y0), (1, 0)), ((y1 - y0), (x1, y0), (0, 1)),
                                        ((x1 - x0), (x1, y1), (-1, 0)), ((y1 - y0), (x0, y1), (0, -1))):
                if d <= length:
                    cx, cy = start[0] + step[0] * d, start[1] + step[1] * d
                    break
                d -= length
            box = (int(cx - size / 2), int(cy - size / 2), int(cx + size / 2), int(cy + size / 2))
            full = tuple(v * self.SCALE for v in box)
            if not any(full[0] < r and l < full[2] and full[1] < b and t < full[3]
                       for l, t, r, b in exclusions):
                boxes.append(box)
        return boxes

    def ring(self, state):
        """RING_SIZE live background colors (OKLab) around the window, or None."""
        if not state:
            return None
        hwnd, exclusions = state
        img = self._grab(hwnd)
        if img is None or img.max() < 8:  # failed / black capture
            return None
        h, w = img.shape[:2]
        if self.boxes_for != (state, w, h):
            self.boxes = self._ring_boxes(w, h, exclusions)
            self.boxes_for = (state, w, h)
        if len(self.boxes) < 6:
            return None
        samples = []
        for l, t, r, b in self.boxes:
            px = img[max(t, 0):b, max(l, 0):r].reshape(-1, 3).astype(np.float32)
            if not len(px):
                continue
            lum = px @ np.array([0.299, 0.587, 0.114], np.float32)
            # darker half only: keeps faint lyric text / highlights out of the color
            samples.append(_live_boost(px[lum <= np.median(lum)].mean(axis=0)))
        n = len(samples)
        return [ring_at(samples, i / RING_SIZE) for i in range(RING_SIZE)] if n else None


def _live_boost(rgb):
    """Background colors are dark and muted; lift them so LEDs show the hue.

    Only colored areas are lifted: the dark grey parts of the background stay
    dark, so the colored blobs read as moving light on the fans.
    """
    h, s, v = colorsys.rgb_to_hsv(*(float(c) / 255 for c in rgb))
    colorfulness = min(1.0, s / 0.3)
    s = min(1.0, s * LIVE_SATURATION)
    v = min(1.0, v * 1.1 + LIVE_MIN_BRIGHTNESS * colorfulness)
    return to_oklab(tuple(c * 255 for c in colorsys.hsv_to_rgb(h, s, v)))


# --- keyboard (Royal Kludge R65, VIA firmware: one color for the whole board) ---

class ViaKeyboard:
    """Sets an RGB Matrix keyboard's color over the VIA raw-HID protocol.

    Changes are not saved to the keyboard; unplugging it, or quitting this
    script with Ctrl+C, brings back its own effect.
    """
    RGB_MATRIX, BRIGHTNESS, EFFECT, COLOR = 3, 1, 2, 4
    SOLID_COLOR = 1

    def __init__(self):
        self.dev = None
        self.original = None
        self.last = None
        self.retry_at = 0.0

    def _cmd(self, *data):
        self.dev.write([0] + list(data) + [0] * (32 - len(data)))
        return self.dev.read(32, 200)

    def connect(self):
        if self.dev or DRY_RUN or not KEYBOARD_USB_ID or time.monotonic() < self.retry_at:
            return
        import hid
        try:
            path = next(d["path"] for d in hid.enumerate(*KEYBOARD_USB_ID)
                        if d["usage_page"] == 0xFF60)  # VIA / QMK raw HID
            self.dev = hid.device()
            self.dev.open_path(path)
            if self.original is None:
                self.original = [self._cmd(0x08, self.RGB_MATRIX, v)[3:5]
                                 for v in (self.BRIGHTNESS, self.EFFECT, self.COLOR)]
            self._cmd(0x07, self.RGB_MATRIX, self.EFFECT, self.SOLID_COLOR)
            self.last = None
            print("Connected to keyboard")
        except Exception:
            self.dev = None
            self.retry_at = time.monotonic() + 5  # unplugged / not found

    def set_color(self, rgb):
        if not self.dev:
            return
        h, s, v = colorsys.rgb_to_hsv(*(c / 255 for c in rgb))
        v = max(v, KEYBOARD_MIN_BRIGHTNESS)  # one color for all keys: never let it go dark
        hsv = (round(h * 255), round(s * 255), round(v * 255))
        if self.last and max(abs(a - b) for a, b in zip(hsv, self.last)) < 2:
            return  # no visible change
        try:
            self._cmd(0x07, self.RGB_MATRIX, self.COLOR, hsv[0], hsv[1])
            self._cmd(0x07, self.RGB_MATRIX, self.BRIGHTNESS, hsv[2])
            self.last = hsv
        except Exception:
            self.dev = None
            print("Lost keyboard connection")

    def restore(self):
        if self.dev and self.original:
            (bright, _), (effect, _), (hue, sat) = self.original
            self._cmd(0x07, self.RGB_MATRIX, self.EFFECT, effect)
            self._cmd(0x07, self.RGB_MATRIX, self.COLOR, hue, sat)
            self._cmd(0x07, self.RGB_MATRIX, self.BRIGHTNESS, bright)


KEYBOARD = ViaKeyboard()


def dominant(ring):
    """One color for the whole ring: an average that favours its colorful parts."""
    weights = [0.0005 + math.hypot(a, b) ** 2 for _, a, b in ring]  # OKLab chroma²
    total = sum(weights)
    return tuple(sum(w * c[i] for w, c in zip(weights, ring)) / total for i in range(3))


# --- lights ---

class Lights:
    def __init__(self):
        self.client = None
        self.layout = []  # per device: list of ("solid", ring pos) / ("flow", ring pos)
        idle = to_oklab(IDLE_COLOR)
        self.palette = [idle] * PALETTE_SIZE
        self.shown = [idle] * RING_SIZE   # two cascaded smoothing stages give
        self.stage = [idle] * RING_SIZE   # an eased (S-curve) response
        self.last_frame = time.monotonic()
        self.live = False
        self.retry_at = 0.0

    def connect(self):
        if DRY_RUN or self.client:
            return
        from openrgb import OpenRGBClient
        from openrgb.utils import ZoneType
        try:
            self.client = OpenRGBClient("127.0.0.1", 6742, "Apple Music RGB")
            self.layout = []
            solid_i = flow_i = 0
            for d in self.client.devices:
                zones = [z for z in d.zones if z.name not in SKIP_ZONES.get(d.name, ())]
                if not zones:
                    continue
                # addressable headers report 0 LEDs until sized; extra LEDs are harmless
                for z in zones:
                    if z.type == ZoneType.LINEAR and len(z.leds) == 0:
                        try:
                            z.resize(ARGB_HEADER_LEDS)
                        except Exception:
                            pass
                try:
                    d.set_mode("direct")
                except Exception:
                    try:
                        d.set_mode("static")
                    except Exception:
                        pass
                for z in zones:
                    if len(z.leds) == 1:
                        # single-LED zones take different parts of the ring in turn
                        leds = [("solid", solid_i / PALETTE_SIZE)]
                        solid_i += 1
                    else:
                        # each header starts at a different point of the ring
                        offset = flow_i / max(1, PALETTE_SIZE)
                        leds = [("flow", offset + k / PATTERN_LEDS) for k in range(len(z.leds))]
                        flow_i += 1
                    self.layout.append([z, leds, None])  # zone, LED positions, last colors sent
            print("Connected to OpenRGB:", ", ".join(d.name or "(unnamed)" for d in self.client.devices))
        except Exception as e:
            self.client = None
            print(f"OpenRGB not reachable ({e}); retrying...")

    def set_palette(self, colors):
        self.palette = [to_oklab(c) for c in colors]

    def update(self, live_ring):
        """Move the shown ring toward the live background, or the album gradient."""
        now = time.monotonic()
        dt, self.last_frame = now - self.last_frame, now
        if (live_ring is not None) != self.live:
            self.live = live_ring is not None
            print("Mode:", "live lyrics background" if self.live else "album colors")
        if live_ring is not None:
            target, tau = live_ring, LIVE_SMOOTHING
        else:
            flow = now / FLOW_SECONDS if FLOW_SECONDS else 0.0
            target = [ring_at(self.palette, i / RING_SIZE - flow) for i in range(RING_SIZE)]
            tau = FADE_SMOOTHING
        a = 1 - math.exp(-dt / (tau / 2))
        self.stage = [lerp(s, t, a) for s, t in zip(self.stage, target)]
        self.shown = [lerp(s, t, a) for s, t in zip(self.shown, self.stage)]

    def render(self):
        if DRY_RUN or not self.client:
            return
        from openrgb.utils import RGBColor
        try:
            for entry in self.layout:
                zone, leds, last = entry
                colors = []
                for kind, pos in leds:
                    if kind == "solid":  # average a stretch of the ring
                        labs = [ring_at(self.shown, pos + k / 16) for k in range(4)]
                        lab = tuple(sum(c) / len(labs) for c in zip(*labs))
                    else:
                        lab = ring_at(self.shown, pos)
                    colors.append(from_oklab(lab))
                if colors == last:
                    continue  # nothing changed: don't keep hammering the controller
                zone.set_colors([RGBColor(*c) for c in colors], fast=True)
                entry[2] = colors
        except Exception as e:
            print(f"Lost OpenRGB connection ({e})")
            self.client = None


class CaptureThread(threading.Thread):
    """Keeps `self.ring` filled with the latest live colors (or None).

    Runs apart from the asyncio loop: window capture there starves the media
    session callbacks, so song changes would go unnoticed.
    """

    def __init__(self, watcher):
        super().__init__(daemon=True)
        self.watcher = watcher
        self.ring = None

    def run(self):
        capturer = Capturer()
        while True:
            try:
                self.ring = capturer.ring(self.watcher.state)
            except Exception:
                self.ring = None
            time.sleep(1 / FPS if self.ring is not None else 0.5)


async def render_loop(lights, keyboard, capture):
    last_tick = time.time()  # wall clock: always jumps across sleep
    while True:
        now = time.time()
        if now - last_tick > 5:
            # the PC slept: controllers come back in their built-in effect, which
            # fights our colors (flashing) until they're put in direct mode again
            print("Resumed from sleep; re-initializing lights")
            if lights.client:
                try:
                    lights.client.disconnect()
                except Exception:
                    pass
            lights.client = None
            lights.retry_at = 0.0
            if keyboard.dev:
                try:
                    keyboard.dev.close()
                except Exception:
                    pass
            keyboard.dev = None
            keyboard.retry_at = 0.0
        last_tick = now
        if time.monotonic() >= lights.retry_at:
            lights.connect()
            if not lights.client:
                lights.retry_at = time.monotonic() + 3
        keyboard.connect()
        lights.update(capture.ring)
        lights.render()
        keyboard.set_color(from_oklab(dominant(lights.shown)))
        await asyncio.sleep(1 / FPS)


async def main():
    manager = await SessionManager.request_async()
    lights = Lights()
    watcher = PlayerWatcher()
    watcher.start()
    capture = CaptureThread(watcher)
    capture.start()
    asyncio.create_task(render_loop(lights, KEYBOARD, capture))
    last_key = None
    print("Watching Apple Music... (Ctrl+C to stop)")
    while True:
        session = await find_session(manager)
        key = None
        if session:
            info = session.get_playback_info()
            playing = info.playback_status == 4  # PLAYING
            props = await session.try_get_media_properties_async()
            key = (props.title, props.artist) if playing else None
        if key != last_key:
            if key:
                img = await read_thumbnail(props)
                colors = album_palette(img) if img else [IDLE_COLOR] * PALETTE_SIZE
                hexes = " ".join("#{:02x}{:02x}{:02x}".format(*c) for c in colors)
                print(f"{time.strftime('%H:%M:%S')}  {props.title} — {props.artist}  ->  {hexes}")
            else:
                colors = [IDLE_COLOR] * PALETTE_SIZE
            lights.set_palette(colors)
            last_key = key
        await asyncio.sleep(POLL_SECONDS)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        KEYBOARD.restore()
