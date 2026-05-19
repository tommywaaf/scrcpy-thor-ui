# ThorCPY - Dual-screen scrcpy docking and control UI for Windows
# Copyright (C) 2026 the_swest
# Contact: Github issues
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.

# src/chassis.py
#
# Procedural renderer for the AYN Thor virtual button overlay. We
# DON'T draw a chassis body or any plastic art - the existing layout
# already centers the bottom scrcpy window with empty side strips on
# each side, and we paint only the controller buttons into those gaps:
#
#   Left  (top->bottom):  SELECT  -  Left Joystick  -  D-Pad  -  HOME
#   Right (top->bottom):  START   -  X/Y/B/A        -  Right Joystick - BACK
#
# The renderer outputs a pygame Surface filled with black, with the
# buttons drawn into the two side strip rectangles. The launcher
# blits the whole surface; the scrcpy children paint over the top
# and bottom rows so only the side strips remain visible.

import logging

import pygame

logger = logging.getLogger(__name__)


# Background fill (matches the previous behaviour of the container).
COLOR_BG = (0, 0, 0)

# Inputs / buttons palette
COLOR_BUTTON_GREY = (62, 62, 68)
COLOR_BUTTON_GREY_PRESSED = (38, 38, 44)
COLOR_BUTTON_GREY_HIGHLIGHT = (96, 96, 104)
COLOR_PILL_FACE = (54, 54, 60)
COLOR_PILL_FACE_PRESSED = (32, 32, 38)
COLOR_PILL_RIM = (28, 28, 32)
COLOR_PILL_LABEL = (220, 220, 224)
COLOR_JOYSTICK_WELL = (40, 40, 46)
COLOR_JOYSTICK_WELL_RIM = (24, 24, 28)

# Face button colors (matches the photograph: Y green, X blue, A red, B yellow)
COLOR_BTN_Y = (108, 192, 102)
COLOR_BTN_X = (62, 134, 232)
COLOR_BTN_A = (216, 70, 76)
COLOR_BTN_B = (242, 192, 76)
COLOR_BTN_LABEL = (250, 250, 250)
COLOR_BTN_INSET = (28, 28, 32)

# Trigger LED indicator colors (dim red idle, bright red pressed).
COLOR_LED_OFF = (60, 18, 22)
COLOR_LED_ON = (240, 56, 56)
COLOR_LED_RIM = (28, 28, 32)
COLOR_LED_LABEL = (180, 180, 184)


def _darken(color, factor):
    return (
        max(0, min(255, int(color[0] * factor))),
        max(0, min(255, int(color[1] * factor))),
        max(0, min(255, int(color[2] * factor))),
    )


def _rounded_rect(surface, color, rect, radius):
    pygame.draw.rect(surface, color, rect, border_radius=int(radius))


class ChassisRenderer:
    """
    Renders the AYN Thor virtual button overlay to a pygame Surface.

    Given the FULL container size and the two side-strip rectangles
    flanking the bottom scrcpy window, paints buttons into those
    rectangles on a black background. The launcher blits the whole
    surface; the scrcpy windows paint over the rest.

    A `button_state` dict can be passed to render() to highlight any
    buttons currently being pressed. Keys (all optional, default off):
        - 'select', 'start', 'home', 'back'
        - 'btn_a', 'btn_b', 'btn_x', 'btn_y'
        - 'dpad_up', 'dpad_down', 'dpad_left', 'dpad_right'
        - 'l1', 'r1', 'l2', 'r2', 'l3', 'r3'
        - 'lstick_x', 'lstick_y'  (-1.0 .. 1.0)
        - 'rstick_x', 'rstick_y'  (-1.0 .. 1.0)
    """

    def __init__(self, container_w, container_h, left_strip, right_strip):
        """
        container_w/h:  full container window inner size
        left_strip:     (x, y, w, h) gap left of the bottom screen
        right_strip:    (x, y, w, h) gap right of the bottom screen
        """
        self.container_w = container_w
        self.container_h = container_h
        self.left_strip = pygame.Rect(*left_strip)
        self.right_strip = pygame.Rect(*right_strip)
        self._font_cache = {}

        # Pygame must be initialised for font + surface use; safe to
        # call multiple times - the launcher already calls pygame.init()
        # for the control panel UI.
        if not pygame.get_init():
            pygame.init()
        if not pygame.font.get_init():
            pygame.font.init()

    def _font(self, size):
        if size not in self._font_cache:
            try:
                self._font_cache[size] = pygame.font.SysFont("Segoe UI", size, bold=True)
            except Exception:
                self._font_cache[size] = pygame.font.Font(None, size)
        return self._font_cache[size]

    def render(self, button_state=None):
        """
        Returns a fresh pygame Surface containing the button overlay.
        """
        btn = button_state or {}
        s = pygame.Surface((self.container_w, self.container_h))
        s.fill(COLOR_BG)

        # Skip drawing if a strip is too narrow to be useful.
        if self.left_strip.w >= 60 and self.left_strip.h >= 200:
            self._draw_left_strip(s, self.left_strip, btn)
        if self.right_strip.w >= 60 and self.right_strip.h >= 200:
            self._draw_right_strip(s, self.right_strip, btn)

        return s

    # ------------------------------------------------------------------
    # Side strips
    # ------------------------------------------------------------------

    def _strip_anchor_size(self, rect):
        """
        Pick a visual size for buttons inside this strip.

        The ABXY cluster is the widest element we draw and it spans
        about 3.1 * anchor wide (gap + radius on each side). The
        D-pad spans ~1.55 * anchor. We want the WIDEST element to fit
        comfortably with healthy padding on both sides, so we cap by
        rect.w / 3.5 to give the cluster ~12% padding inside the strip.
        Also cap by available vertical space (we have 4 anchored
        elements stacked vertically).
        """
        return max(28, min(int(rect.w / 3.5), int(rect.h / 7)))

    def _draw_left_strip(self, s, rect, btn):
        # Vertical layout: L1/L2 LEDs, SELECT, joystick, dpad, HOME.
        # HOME shares its y-row exactly with BACK on the right strip
        # so the two system buttons line up across the bottom screen.
        cx = rect.x + rect.w // 2
        anchor = self._strip_anchor_size(rect)
        led_y = rect.y + int(rect.h * 0.025) + 10
        select_y = rect.y + int(rect.h * 0.10)
        joystick_y = rect.y + int(rect.h * 0.32)
        dpad_y = rect.y + int(rect.h * 0.63)
        home_y = rect.y + int(rect.h * 0.92)

        self._draw_trigger_leds(s, cx, led_y, anchor,
                                ("L1", btn.get("l1")), ("L2", btn.get("l2")))
        self._draw_pill(s, cx, select_y, "SELECT", anchor, btn.get("select"))
        self._draw_joystick(s, cx, joystick_y, anchor,
                            btn.get("lstick_x", 0.0), btn.get("lstick_y", 0.0),
                            pressed=btn.get("l3"))
        self._draw_dpad(s, cx, dpad_y, anchor,
                        btn.get("dpad_up"), btn.get("dpad_down"),
                        btn.get("dpad_left"), btn.get("dpad_right"))
        self._draw_pill(s, cx, home_y, "HOME", anchor, btn.get("home"))

    def _draw_right_strip(self, s, rect, btn):
        # Vertical layout: R1/R2 LEDs, START, ABXY, joystick, BACK.
        # The AYN system-menu button (KEY_APPSELECT) is rendered as a
        # small pill positioned slightly below-and-left of BACK, like
        # a satellite to the right strip's main bottom button.
        cx = rect.x + rect.w // 2
        anchor = self._strip_anchor_size(rect)
        led_y = rect.y + int(rect.h * 0.025) + 10
        start_y = rect.y + int(rect.h * 0.10)
        abxy_y = rect.y + int(rect.h * 0.32)
        joystick_y = rect.y + int(rect.h * 0.63)
        # BACK on the same y-row as HOME on the left strip.
        back_y = rect.y + int(rect.h * 0.92)

        self._draw_trigger_leds(s, cx, led_y, anchor,
                                ("R1", btn.get("r1")), ("R2", btn.get("r2")))
        self._draw_pill(s, cx, start_y, "START", anchor, btn.get("start"))
        self._draw_abxy(s, cx, abxy_y, anchor, btn)
        self._draw_joystick(s, cx, joystick_y, anchor,
                            btn.get("rstick_x", 0.0), btn.get("rstick_y", 0.0),
                            pressed=btn.get("r3"))
        self._draw_pill(s, cx, back_y, "BACK", anchor, btn.get("back"))
        # Small AYN pill below + to the left of BACK. Sized and
        # positioned to stay inside the strip (not clip off bottom)
        # and to sit closer to the bottom screen than to the right
        # edge of the strip.
        ayn_anchor = max(18, int(anchor * 0.55))
        ayn_cx = cx - int(anchor * 1.05)
        ayn_cy = back_y + int(anchor * 0.32)
        self._draw_pill(s, ayn_cx, ayn_cy, "AYN", ayn_anchor, btn.get("ayn"))

    def _draw_single_led(self, s, cx, cy, anchor_size, label, pressed):
        """
        One LED with a centered label below it. Same visual language
        as the L1/L2/R1/R2 trigger LEDs - dim red when idle, bright
        red when pressed - used for the AYN system-menu button at
        the bottom of the right strip.
        """
        led_r = max(6, int(anchor_size * 0.20))
        color = COLOR_LED_ON if pressed else COLOR_LED_OFF
        # Inset rim shadow + LED face
        pygame.draw.circle(s, COLOR_LED_RIM, (cx, cy + 1), led_r + 2)
        pygame.draw.circle(s, color, (cx, cy), led_r)
        if pressed:
            pygame.draw.circle(s, _darken(color, 1.4),
                               (cx - led_r // 3, cy - led_r // 3),
                               max(2, led_r // 3))
        font_size = max(9, int(led_r * 1.0))
        font = self._font(font_size)
        text = font.render(label, True, COLOR_LED_LABEL)
        s.blit(text, text.get_rect(center=(cx, cy + led_r + font_size)))

    def _draw_trigger_leds(self, s, cx, cy, anchor_size, left, right):
        """
        Two small LED-style indicators side by side, with a tiny label
        underneath each one. `left` and `right` are (label, pressed)
        tuples - one for the inner trigger (L1/R1) and one for the
        outer trigger (L2/R2).

        The gap is sized so the LED labels sit OUTSIDE the SELECT/START
        pill below them (the pill is ~1.4 * anchor_size wide). We push
        the LEDs to about ~1.1 * anchor_size away from center so the
        labels never collide with the pill text underneath.
        """
        led_r = max(6, int(anchor_size * 0.20))
        gap = max(int(anchor_size * 1.05), led_r * 5)
        positions = [
            (cx - gap, left),
            (cx + gap, right),
        ]
        font_size = max(9, int(led_r * 1.0))
        font = self._font(font_size)
        for px, (label, pressed) in positions:
            color = COLOR_LED_ON if pressed else COLOR_LED_OFF
            # Inset rim for an embedded look
            pygame.draw.circle(s, COLOR_LED_RIM, (px, cy + 1), led_r + 2)
            pygame.draw.circle(s, color, (px, cy), led_r)
            # Subtle highlight when on
            if pressed:
                pygame.draw.circle(s, _darken(color, 1.4),
                                   (px - led_r // 3, cy - led_r // 3),
                                   max(2, led_r // 3))
            text = font.render(label, True, COLOR_LED_LABEL)
            s.blit(text, text.get_rect(center=(px, cy + led_r + font_size)))

    # ------------------------------------------------------------------
    # Individual input drawings
    # ------------------------------------------------------------------

    def _draw_pill(self, s, cx, cy, label, anchor_size, pressed):
        """Capsule-shaped system button (Select/Start/Home/Back)."""
        w = int(anchor_size * 1.4)
        h = max(20, int(anchor_size * 0.40))
        rect = pygame.Rect(cx - w // 2, cy - h // 2, w, h)
        face = COLOR_PILL_FACE_PRESSED if pressed else COLOR_PILL_FACE
        # Rim shadow
        pygame.draw.rect(s, COLOR_PILL_RIM,
                         pygame.Rect(rect.x - 1, rect.y + 1, rect.w + 2, rect.h + 2),
                         border_radius=h // 2)
        _rounded_rect(s, face, rect, h // 2)
        # Label
        font_size = max(11, int(h * 0.55))
        font = self._font(font_size)
        text = font.render(label, True, COLOR_PILL_LABEL)
        s.blit(text, text.get_rect(center=(cx, cy)))

    def _draw_joystick(self, s, cx, cy, anchor_size, axis_x=0.0, axis_y=0.0, pressed=False):
        """Recessed circular well plus an offset stick cap."""
        outer_r = int(anchor_size * 0.95)
        inner_r = int(anchor_size * 0.62)
        # Outer recessed well
        pygame.draw.circle(s, COLOR_JOYSTICK_WELL, (cx, cy), outer_r)
        pygame.draw.circle(s, COLOR_JOYSTICK_WELL_RIM, (cx, cy), outer_r, 2)
        # Stick cap offset by axis
        max_offset = outer_r - inner_r - 2
        ox = int(max(-1.0, min(1.0, axis_x)) * max_offset)
        oy = int(max(-1.0, min(1.0, axis_y)) * max_offset)
        cap_color = COLOR_BUTTON_GREY_PRESSED if pressed else COLOR_BUTTON_GREY
        # Drop shadow
        pygame.draw.circle(s, COLOR_PILL_RIM, (cx + ox + 2, cy + oy + 3), inner_r)
        # Cap face
        pygame.draw.circle(s, cap_color, (cx + ox, cy + oy), inner_r)
        # Top highlight
        pygame.draw.circle(s, COLOR_BUTTON_GREY_HIGHLIGHT,
                           (cx + ox - inner_r // 4, cy + oy - inner_r // 3),
                           max(2, inner_r // 6))

    def _draw_dpad(self, s, cx, cy, anchor_size, up, down, left, right):
        """Plus-shaped D-pad with each arm independently highlightable."""
        arm_w = int(anchor_size * 0.55)
        arm_l = int(anchor_size * 0.78)
        rim = COLOR_PILL_RIM
        face = COLOR_BUTTON_GREY
        face_p = COLOR_BUTTON_GREY_PRESSED

        v_rect = pygame.Rect(cx - arm_w // 2, cy - arm_l, arm_w, arm_l * 2)
        h_rect = pygame.Rect(cx - arm_l, cy - arm_w // 2, arm_l * 2, arm_w)
        # Rim/shadow
        pygame.draw.rect(s, rim,
                         pygame.Rect(v_rect.x - 1, v_rect.y + 1, v_rect.w + 2, v_rect.h + 2),
                         border_radius=6)
        pygame.draw.rect(s, rim,
                         pygame.Rect(h_rect.x - 1, h_rect.y + 1, h_rect.w + 2, h_rect.h + 2),
                         border_radius=6)
        # Faces
        _rounded_rect(s, face, v_rect, 6)
        _rounded_rect(s, face, h_rect, 6)

        # Pressed highlights per arm
        if up:
            _rounded_rect(s, face_p,
                          pygame.Rect(cx - arm_w // 2, cy - arm_l, arm_w, arm_l - arm_w // 2), 6)
        if down:
            _rounded_rect(s, face_p,
                          pygame.Rect(cx - arm_w // 2, cy + arm_w // 2, arm_w, arm_l - arm_w // 2), 6)
        if left:
            _rounded_rect(s, face_p,
                          pygame.Rect(cx - arm_l, cy - arm_w // 2, arm_l - arm_w // 2, arm_w), 6)
        if right:
            _rounded_rect(s, face_p,
                          pygame.Rect(cx + arm_w // 2, cy - arm_w // 2, arm_l - arm_w // 2, arm_w), 6)

        # Center dot accent
        pygame.draw.circle(s, _darken(face, 0.85), (cx, cy), max(3, arm_w // 5))

    def _draw_abxy(self, s, cx, cy, anchor_size, btn):
        """
        Diamond-pattern colored buttons in AYN Thor layout:
        X top, A right, B bottom, Y left.
        """
        r = max(12, int(anchor_size * 0.47))
        gap = int(r * 1.55)

        positions = {
            "btn_x": (cx, cy - gap, COLOR_BTN_X, "X"),
            "btn_a": (cx + gap, cy, COLOR_BTN_A, "A"),
            "btn_b": (cx, cy + gap, COLOR_BTN_B, "B"),
            "btn_y": (cx - gap, cy, COLOR_BTN_Y, "Y"),
        }
        font_size = max(11, int(r * 0.95))
        font = self._font(font_size)
        for key, (px, py, color, label) in positions.items():
            pressed = bool(btn.get(key))
            face = _darken(color, 0.7) if pressed else color
            # Inset rim shadow
            pygame.draw.circle(s, COLOR_BTN_INSET, (px, py + 2), r + 1)
            pygame.draw.circle(s, face, (px, py), r)
            # Subtle highlight crescent
            pygame.draw.circle(s, _darken(face, 1.18) if not pressed else face,
                               (px - r // 4, py - r // 3), max(2, r // 3))
            text = font.render(label, True, COLOR_BTN_LABEL)
            s.blit(text, text.get_rect(center=(px, py)))


# ----------------------------------------------------------------------
# Win32 helper: convert pygame Surface -> HBITMAP for blitting in WM_PAINT
# ----------------------------------------------------------------------

def surface_to_hbitmap(surface):
    """
    Convert a pygame.Surface into a Win32 HBITMAP (a top-down 32-bit DIB).
    Returns (hbitmap, width, height). Caller owns the HBITMAP and must
    DeleteObject() it when done.
    """
    import ctypes
    from ctypes import wintypes

    w, h = surface.get_size()
    raw = pygame.image.tostring(surface, "BGRA", False)

    gdi32 = ctypes.windll.gdi32
    user32 = ctypes.windll.user32

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [
            ("biSize", wintypes.DWORD),
            ("biWidth", wintypes.LONG),
            ("biHeight", wintypes.LONG),
            ("biPlanes", wintypes.WORD),
            ("biBitCount", wintypes.WORD),
            ("biCompression", wintypes.DWORD),
            ("biSizeImage", wintypes.DWORD),
            ("biXPelsPerMeter", wintypes.LONG),
            ("biYPelsPerMeter", wintypes.LONG),
            ("biClrUsed", wintypes.DWORD),
            ("biClrImportant", wintypes.DWORD),
        ]

    class BITMAPINFO(ctypes.Structure):
        _fields_ = [
            ("bmiHeader", BITMAPINFOHEADER),
            ("bmiColors", wintypes.DWORD * 3),
        ]

    bmi = BITMAPINFO()
    bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bmi.bmiHeader.biWidth = w
    bmi.bmiHeader.biHeight = -h
    bmi.bmiHeader.biPlanes = 1
    bmi.bmiHeader.biBitCount = 32
    bmi.bmiHeader.biCompression = 0  # BI_RGB

    bits_ptr = ctypes.c_void_p()

    gdi32.CreateDIBSection.argtypes = [
        wintypes.HDC, ctypes.POINTER(BITMAPINFO), wintypes.UINT,
        ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD,
    ]
    gdi32.CreateDIBSection.restype = wintypes.HBITMAP

    screen_dc = user32.GetDC(0)
    try:
        hbitmap = gdi32.CreateDIBSection(
            screen_dc, ctypes.byref(bmi), 0,
            ctypes.byref(bits_ptr), None, 0,
        )
    finally:
        user32.ReleaseDC(0, screen_dc)

    if not hbitmap or not bits_ptr.value:
        raise RuntimeError("CreateDIBSection failed")

    ctypes.memmove(bits_ptr, raw, len(raw))
    return hbitmap, w, h
