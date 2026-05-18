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
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

# src/launcher.py

import threading
import time
import ctypes
import tkinter as tk
from tkinter import messagebox

import pygame
import os
import logging
from ctypes import wintypes

from src.scrcpy_manager import ScrcpyManager, TOP_SCREEN_WINDOW_TITLE, BOTTOM_SCREEN_WINDOW_TITLE
from src.win32_dock import Win32Dock, apply_docked_style, apply_undocked_style
from src.presets import PresetStore
from src.config import ConfigManager
from src.ui_pygame import show_loading_screen
from src.win32_darkmode import enable_dark_titlebar
from src.wireless_dialog import show_wireless_dialog
from src.chassis import ChassisRenderer, surface_to_hbitmap
from src.input_listener import InputListener

logger = logging.getLogger(__name__)

# Win32 window message constants
WM_CLOSE = 0x0010
WM_DESTROY = 0x0002
WM_ERASEBKGND = 0x0014
WM_PAINT = 0x000F

# Win32 window style constants
WS_OVERLAPPEDWINDOW = 0x00CF0000
WS_VISIBLE = 0x10000000
WS_CLIPCHILDREN = 0x02000000
WS_CLIPSIBLINGS = 0x04000000
WS_EX_CONTROLPARENT = 0x00010000

WM_MOUSEACTIVATE = 0x0021
MA_ACTIVATE = 1

# Window show/hide constants
SW_HIDE = 0
SW_SHOW = 5

# GDI constants
BLACK_BRUSH = 4

# Process creation flags
CREATE_NO_WINDOW = 0x08000000

# Default layout positioning
TOP_SCREEN_DEFAULT_X = 0
TOP_SCREEN_DEFAULT_Y = 0
BOTTOM_SCREEN_DEFAULT_X = 0
BOTTOM_SCREEN_DEFAULT_Y = 0
DEFAULT_GLOBAL_SCALE = 0.6

# Allowed scrcpy FPS values exposed in the control panel.
ALLOWED_FPS_VALUES = (30, 60, 90, 120)
DEFAULT_MAX_FPS = 60

# Virtual chassis (Phase 1: static button strip art on each side
# of the bottom screen). The chassis fills the natural gap that
# already exists between the wider top screen and the narrower
# bottom screen, so we don't need to widen the container at all -
# we just paint the system buttons in the empty cream space on
# each side of the bottom screen.
DEFAULT_CHASSIS_ENABLED = True
# Minimum side strip width before the chassis collapses gracefully.
# Below this, the procedural buttons get too cramped to read.
CHASSIS_MIN_SIDE_WIDTH = 80

# Container window initial position
DEFAULT_CONTAINER_X = 100
DEFAULT_CONTAINER_Y = 100

# Timing constants
SCRCPY_POLL_INTERVAL = 0.1
DOCKING_MONITOR_TIME_DELAY = 0.5
UI_FPS = 60

# Math constants
HALF = 0.5

# Default config
DEFAULT_LAYOUT = {"tx": TOP_SCREEN_DEFAULT_X, "ty": TOP_SCREEN_DEFAULT_Y,
                  "bx": BOTTOM_SCREEN_DEFAULT_X, "by": BOTTOM_SCREEN_DEFAULT_Y,
                  "global_scale": DEFAULT_GLOBAL_SCALE}


class Launcher:
    """
    Main window controller for ThorCPY
    Manages scrcpy instances, docking and undocking behabiour,
    UI rendering and event handling and configuration persistance

    The launcher makes a container window that holds 2 scrcpy instances and controls positioning
    """

    def __init__(self):
        """
        Sets up the launcher with default layouts and configurations
        Sets up scrcpy instance with saved scale
        forces the default layout on boot
        Manages windows docking
        Sorts out Win32 API function signatures
        """
        logger.info("Initializing Launcher with Forced Default Layout")
        self.user32 = ctypes.windll.user32
        self.kernel32 = ctypes.windll.kernel32

        # Load config managers
        self.store = PresetStore("config/layout.json")
        self.config = ConfigManager("config/config.json")

        # Load scale or use the default
        self.global_scale = self.config.get(
            "global_scale", DEFAULT_LAYOUT["global_scale"]
        )
        self.launch_scale = self.global_scale

        # Chassis toggle (default on). When enabled, the existing empty
        # gaps to the left and right of the centered bottom screen are
        # used to paint a vertical column of system buttons. We do NOT
        # widen the container or move the screens - we just repurpose
        # the gap that's naturally there.
        self.chassis_enabled = bool(
            self.config.get("chassis_enabled", DEFAULT_CHASSIS_ENABLED)
        )

        # Configurable FPS cap for the scrcpy stream (30/60/120).
        # Saved to config so it survives restarts; user-controllable
        # from the control panel.
        try:
            cfg_fps = int(self.config.get("max_fps", DEFAULT_MAX_FPS))
        except (TypeError, ValueError):
            cfg_fps = DEFAULT_MAX_FPS
        self.max_fps = cfg_fps if cfg_fps in ALLOWED_FPS_VALUES else DEFAULT_MAX_FPS

        # Initialize Scrcpy with the saved scale and FPS
        self.scrcpy = ScrcpyManager(scale=self.launch_scale, max_fps=self.max_fps)

        # Calculate the forced layout (Top at 0,0 - bottom centred underneath) with scaled dimensions
        w1, h1 = self.scrcpy.f_w1, self.scrcpy.f_h1
        w2, _ = self.scrcpy.f_w2, self.scrcpy.f_h2

        self.tx = TOP_SCREEN_DEFAULT_X
        self.ty = TOP_SCREEN_DEFAULT_Y
        self.by = int(h1)
        self.bx = int(w1 * HALF - w2 * HALF)

        logger.info(
            f"Layout Reset: Top(0,0), Bottom({self.bx}, {self.by}) "
            f"at Scale {self.global_scale} chassis={self.chassis_enabled}"
        )

        # Chassis bitmap state (lazily created on first paint, recreated
        # whenever chassis_enabled toggles or the scale/dimensions change).
        self._chassis_hbitmap = None
        self._chassis_w = 0
        self._chassis_h = 0
        self._chassis_renderer = None
        self._chassis_button_state = {}

        # Live input plumbing (Phase 2)
        self._input_listener = None
        self._input_dirty = threading.Event()
        # Last time we rebuilt the chassis bitmap, used for throttling.
        self._last_chassis_redraw = 0.0

        # When chassis is on AND the user has ticked "Separate screens",
        # we float ONLY the top scrcpy window and keep the bottom one
        # docked inside a shrunk container that still shows the chassis
        # buttons on each side. This flag tracks that intermediate
        # state so all the layout maths can adjust to it.
        self._top_only_separated = False
        self._original_container_w = 0
        self._original_container_h = 0
        # Min interval between chassis redraws while inputs are streaming.
        # 33 ms ~= 30 fps which is plenty smooth for joystick movement
        # without melting CPU when an axis fires every few ms.
        self._chassis_redraw_min_interval = 1.0 / 30.0

        # Single persistent tkinter root so all dialogs use it as their parent without corrupting control window's state
        self._tk_root = tk.Tk()
        self._tk_root.withdraw()

        # Initialise window management
        self.dock = Win32Dock()
        self.running = False
        self.docked = True
        self.hwnd_container = None
        self._wndproc = None
        self.dock_lock = threading.Lock()

        # Define Win32 API signatures for type safety
        self.LRESULT = ctypes.c_longlong
        self.WPARAM = ctypes.c_ulonglong
        self.LPARAM = ctypes.c_longlong

        try:
            self.user32.DefWindowProcW.argtypes = [
                wintypes.HWND,
                wintypes.UINT,
                self.WPARAM,
                self.LPARAM,
            ]
            self.user32.DefWindowProcW.restype = self.LRESULT
        except Exception as ArgtypeError:
            logger.error(f"Error when defining window argtypes: {ArgtypeError}")
            pass

        # Make sure GDI signatures are wide enough for 64-bit handles.
        try:
            self._setup_gdi_signatures()
        except Exception as GdiArgtypeError:
            logger.error(f"Error when defining GDI argtypes: {GdiArgtypeError}")

    def save_layout(self):
        """
        Saves current state and scale to config file in a single write
        Called during shutdown to keep settings.
        """
        try:
            cfg = self.config.load()
            cfg["tx"] = self.tx
            cfg["ty"] = self.ty
            cfg["bx"] = self.bx
            cfg["by"] = self.by
            cfg["global_scale"] = self.global_scale
            self.config.save(cfg)
            logger.info(f"Saved configuration (Scale: {self.global_scale})")
        except Exception as SaveConfigError:
            logger.error(f"Failed to save configuration: {SaveConfigError}")

    def save_scale(self):
        """Save only the global scale to config in a single write"""
        try:
            cfg = self.config.load()
            cfg["global_scale"] = self.global_scale
            self.config.save(cfg)
        except Exception as SaveScaleError:
            logger.error(f"Failed to save scale: {SaveScaleError}")

    def _create_wnd_proc(self):
        # We only need these two for the stable "double-click style" logic
        WM_LBUTTONDOWN = 0x0201
        WM_PARENTNOTIFY = 0x0210

        WNDPROC = ctypes.WINFUNCTYPE(
            self.LRESULT, wintypes.HWND, wintypes.UINT, self.WPARAM, self.LPARAM
        )

        def py_wndproc(hwnd, msg, wp, lp):
            if msg in (WM_CLOSE, WM_DESTROY):
                self.stop()
                return 0

            # Paint chassis art behind the embedded scrcpy windows.
            # WM_ERASEBKGND is sent BEFORE child windows paint, so any
            # scrcpy frames we draw will end up on top of our bitmap.
            # Because the container has WS_CLIPCHILDREN set, we don't
            # waste pixels drawing under the screens themselves.
            if msg == WM_ERASEBKGND:
                if self._paint_chassis_background(wp):
                    return 1  # we handled the erase
                # Fall through to default if disabled / not ready

            # New: Handle activation when the mouse enters/clicks the container
            if msg == WM_MOUSEACTIVATE:
                # Get mouse position relative to container
                pt = wintypes.POINT()
                self.user32.GetCursorPos(ctypes.byref(pt))
                self.user32.ScreenToClient(hwnd, ctypes.byref(pt))

                # Check if mouse is over top or bottom screen and force focus
                if (self.tx <= pt.x <= self.tx + self.scrcpy.f_w1 and
                        self.ty <= pt.y <= self.ty + self.scrcpy.f_h1):
                    self.dock.force_focus(self.dock.hwnd_top)
                elif (self.bx <= pt.x <= self.bx + self.scrcpy.f_w2 and
                      self.by <= pt.y <= self.by + self.scrcpy.f_h2):
                    self.dock.force_focus(self.dock.hwnd_bottom)
                return MA_ACTIVATE

            # This is the ONLY place focus should be handled
            if msg == WM_PARENTNOTIFY:
                if (wp & 0xFFFF) == WM_LBUTTONDOWN:
                    # lp contains coordinates relative to the ThorCPY container
                    mx = lp & 0xFFFF
                    my = (lp >> 16) & 0xFFFF

                    if (self.tx <= mx <= self.tx + self.scrcpy.f_w1 and
                            self.ty <= my <= self.ty + self.scrcpy.f_h1):
                        self.dock.force_focus(self.dock.hwnd_top)

                    elif (self.bx <= mx <= self.bx + self.scrcpy.f_w2 and
                          self.by <= my <= self.by + self.scrcpy.f_h2):
                        self.dock.force_focus(self.dock.hwnd_bottom)

            return self.user32.DefWindowProcW(hwnd, msg, wp, lp)

        return WNDPROC(py_wndproc)

    def _setup_gdi_signatures(self):
        """
        Tell ctypes the proper signatures for the GDI functions we
        invoke. Without this, ctypes defaults arguments to c_int and
        returns int, which truncates 64-bit Windows HANDLE/HBITMAP/HDC
        values once the OS hands us addresses above 2^31. That bites
        especially during the Phase 2 live-input loop where we
        recreate the chassis bitmap many times and Windows hands out
        higher and higher handle values.

        Safe to call multiple times - argtypes assignment is idempotent.
        """
        gdi32 = ctypes.windll.gdi32
        gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
        gdi32.SelectObject.restype = wintypes.HGDIOBJ
        gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
        gdi32.CreateCompatibleDC.restype = wintypes.HDC
        gdi32.DeleteDC.argtypes = [wintypes.HDC]
        gdi32.DeleteDC.restype = wintypes.BOOL
        gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
        gdi32.DeleteObject.restype = wintypes.BOOL
        gdi32.BitBlt.argtypes = [
            wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            wintypes.HDC, ctypes.c_int, ctypes.c_int, wintypes.DWORD,
        ]
        gdi32.BitBlt.restype = wintypes.BOOL

    def _build_chassis_bitmap(self):
        """
        Render the chassis surface and convert to a Win32 HBITMAP.

        IMPORTANT: pygame is not thread-safe. This method must run on
        the same thread that owns the rest of pygame (the main thread
        that runs launch()'s event loop). It is NEVER safe to call
        from inside the wndproc, because the wndproc runs on the
        container's worker thread and would race with the control
        panel's pygame draws.
        """
        if not self.chassis_enabled:
            return False
        if not self.hwnd_container:
            return False

        rect = wintypes.RECT()
        if not self.user32.GetClientRect(self.hwnd_container, ctypes.byref(rect)):
            return False
        cw = rect.right - rect.left
        ch = rect.bottom - rect.top
        if cw <= 0 or ch <= 0:
            return False

        if (self._chassis_hbitmap is not None
                and self._chassis_w == cw
                and self._chassis_h == ch):
            return True

        try:
            bottom_h = self.scrcpy.f_h2
            # When the top screen has been floated out, the container
            # has been shrunk to just the bottom row, so the bottom
            # screen now sits at y=0 inside it.
            bottom_y = 0 if self._top_only_separated else self.by
            # Side strip rectangles cover the empty space to the left
            # and right of the centered bottom screen.
            left_strip = (0, bottom_y, self.bx, bottom_h)
            right_strip = (
                self.bx + self.scrcpy.f_w2,
                bottom_y,
                cw - (self.bx + self.scrcpy.f_w2),
                bottom_h,
            )
            logger.info(
                f"Building chassis bitmap container=({cw}x{ch}) "
                f"left_strip={left_strip} right_strip={right_strip}"
            )
            self._chassis_renderer = ChassisRenderer(cw, ch, left_strip, right_strip)
            surf = self._chassis_renderer.render(self._chassis_button_state)

            # Drop previous bitmap if any
            if self._chassis_hbitmap:
                ctypes.windll.gdi32.DeleteObject(self._chassis_hbitmap)
                self._chassis_hbitmap = None

            hbitmap, w, h = surface_to_hbitmap(surf)
            self._chassis_hbitmap = hbitmap
            self._chassis_w = w
            self._chassis_h = h
            return True
        except Exception as ChassisRenderError:
            logger.error(f"Failed to render chassis: {ChassisRenderError}", exc_info=True)
            return False

    def _paint_chassis_background(self, hdc_param):
        """
        Paint the container's background:
          - if the chassis is enabled and we have a cached bitmap,
            BitBlt it,
          - otherwise fill with black so we never display garbage
            (the window class's hbrBackground is NULL).

        Pure GDI calls - safe to invoke from the wndproc thread.
        Always returns True so the caller knows we handled the erase.
        """
        if not self.hwnd_container:
            return False

        gdi32 = ctypes.windll.gdi32
        user32 = ctypes.windll.user32
        hdc_dst = wintypes.HDC(hdc_param)

        if self.chassis_enabled and self._chassis_hbitmap is not None:
            hdc_mem = gdi32.CreateCompatibleDC(hdc_dst)
            if not hdc_mem:
                return False
            try:
                old = gdi32.SelectObject(hdc_mem, self._chassis_hbitmap)
                try:
                    # SRCCOPY = 0x00CC0020
                    gdi32.BitBlt(hdc_dst, 0, 0, self._chassis_w, self._chassis_h,
                                 hdc_mem, 0, 0, 0x00CC0020)
                finally:
                    gdi32.SelectObject(hdc_mem, old)
            finally:
                gdi32.DeleteDC(hdc_mem)
            return True

        # Fallback: chassis disabled (or bitmap not yet built) - paint
        # the entire client area black so the screen gaps don't reveal
        # whatever was previously on-screen.
        rect = wintypes.RECT()
        if user32.GetClientRect(self.hwnd_container, ctypes.byref(rect)):
            BLACK_BRUSH_ID = 4
            black_brush = gdi32.GetStockObject(BLACK_BRUSH_ID)
            user32.FillRect(hdc_param, ctypes.byref(rect), black_brush)
        return True

    def _invalidate_chassis(self):
        """Force a full background repaint of the container."""
        if self.hwnd_container:
            self.user32.InvalidateRect(self.hwnd_container, None, True)

    def cycle_max_fps(self):
        """
        Cycle through the allowed FPS presets (30 -> 60 -> 120 -> 30).
        Persists to config. Takes effect on the next scrcpy restart;
        the user is expected to click RESTART afterwards.
        """
        try:
            idx = ALLOWED_FPS_VALUES.index(self.max_fps)
        except ValueError:
            idx = ALLOWED_FPS_VALUES.index(DEFAULT_MAX_FPS)
        new_fps = ALLOWED_FPS_VALUES[(idx + 1) % len(ALLOWED_FPS_VALUES)]
        self.set_max_fps(new_fps)
        return new_fps

    def set_max_fps(self, fps):
        """Set the FPS cap and persist; restart required to take effect."""
        if fps not in ALLOWED_FPS_VALUES:
            logger.warning(f"Ignoring out-of-range FPS request: {fps}")
            return
        logger.info(f"FPS preference changed: {self.max_fps} -> {fps}")
        self.max_fps = fps
        try:
            self.config.set("max_fps", fps)
        except Exception as FpsSaveError:
            logger.warning(f"Failed to persist max_fps: {FpsSaveError}")
        # Update the live ScrcpyManager so a restart picks it up.
        if hasattr(self, "scrcpy"):
            self.scrcpy.max_fps = fps

    def restart_app(self):
        """
        Restart the entire application. Spawns a fresh main.py (or
        the bundled exe under PyInstaller) and exits this process.
        Used by the control panel after global-scale or FPS changes.
        """
        import subprocess
        import sys
        try:
            if getattr(sys, "frozen", False):
                cmd = [sys.executable]
            else:
                cmd = [sys.executable, "main.py"]
            logger.info(f"Restarting application: {' '.join(cmd)}")
            subprocess.Popen(cmd, cwd=os.getcwd())
        except Exception as RestartSpawnError:
            logger.error(f"Failed to spawn restart process: {RestartSpawnError}",
                         exc_info=True)
            return
        # Now tear ourselves down (this calls os._exit(0) at the end).
        self.stop()

    def toggle_chassis(self):
        """
        Flip the on-screen button overlay on or off.
        Persists the new state to config, starts/stops the live input
        listener as appropriate, and forces an immediate repaint of
        the container so the change is visible right away.
        """
        new_state = not self.chassis_enabled
        logger.info(f"Toggling chassis overlay: {self.chassis_enabled} -> {new_state}")
        self.chassis_enabled = new_state
        try:
            self.config.set("chassis_enabled", new_state)
        except Exception as ChassisToggleSaveError:
            logger.warning(f"Failed to persist chassis_enabled: {ChassisToggleSaveError}")

        if new_state:
            # Turning on: rebuild bitmap and ensure listener is running.
            self._build_chassis_bitmap()
            if self._input_listener is None and self.scrcpy.serial:
                self._input_listener = InputListener(
                    self.scrcpy.adb_bin, self.scrcpy.serial,
                    on_change=self._on_input_changed,
                )
                self._input_listener.start()
        else:
            # Turning off: stop listener and drop the bitmap so the
            # paint handler falls back to plain black.
            if self._input_listener:
                try:
                    self._input_listener.stop()
                except Exception:
                    pass
                self._input_listener = None
            if self._chassis_hbitmap:
                try:
                    ctypes.windll.gdi32.DeleteObject(self._chassis_hbitmap)
                except Exception:
                    pass
                self._chassis_hbitmap = None
                self._chassis_w = 0
                self._chassis_h = 0

        self._invalidate_chassis()

    def _on_input_changed(self):
        """
        Listener-thread callback. Just flips a thread-safe flag so the
        main loop knows to redraw - we never touch pygame from here.
        """
        self._input_dirty.set()

    def _maybe_redraw_chassis_for_input(self):
        """
        Main-thread tick: if the input state has changed since the
        last repaint AND the throttle window has elapsed, regenerate
        the chassis bitmap from the latest button state and force the
        container to repaint.
        """
        if not (self.chassis_enabled and self._input_listener and self.hwnd_container):
            return
        if not self._input_dirty.is_set():
            return
        now = time.time()
        if now - self._last_chassis_redraw < self._chassis_redraw_min_interval:
            return

        # Consume the dirty flag and capture the latest snapshot. If
        # more events arrive while we're rendering, the flag will be
        # re-set and we'll redraw on the next tick.
        self._input_dirty.clear()
        self._chassis_button_state = self._input_listener.snapshot()
        # Force a fresh build at the same dimensions.
        self._chassis_w = 0
        self._chassis_h = 0
        if self._build_chassis_bitmap():
            self._invalidate_chassis()
            self._last_chassis_redraw = now

    def _create_container_window(self):
        """
        Creates the main container window in a background thread
        Handles both scrcpy windows as children
        Waits for scrcpy dimensions to be available before creating window.
        """

        def loop():
            # Wait for the window dimensions
            while self.scrcpy.f_w1 == 0:
                time.sleep(SCRCPY_POLL_INTERVAL)
                if not self.running:
                    return

            # Define window class structure
            class WNDCLASSEX(ctypes.Structure):
                _fields_ = [
                    ("cbSize", wintypes.UINT),
                    ("style", wintypes.UINT),
                    ("lpfnWndProc", ctypes.c_void_p),
                    ("cbClsExtra", ctypes.c_int),
                    ("cbWndExtra", ctypes.c_int),
                    ("hInstance", wintypes.HINSTANCE),
                    ("hIcon", wintypes.HANDLE),
                    ("hCursor", wintypes.HANDLE),
                    ("hbrBackground", wintypes.HANDLE),
                    ("lpszMenuName", wintypes.LPCWSTR),
                    ("lpszClassName", wintypes.LPCWSTR),
                    ("hIconSm", wintypes.HANDLE),
                ]

            # Register the class
            wc = WNDCLASSEX()
            wc.cbSize = ctypes.sizeof(WNDCLASSEX)
            wc.lpfnWndProc = ctypes.cast(self._wndproc, ctypes.c_void_p).value
            wc.lpszClassName = "ThorFinalBridge"
            hinst = self.kernel32.GetModuleHandleW(None)
            wc.hInstance = hinst
            # We paint the background ourselves in WM_ERASEBKGND so we
            # can blit the button strip overlay there. Setting the
            # class brush to NULL avoids a default fill flash before
            # our paint runs.
            wc.hbrBackground = 0

            self.user32.RegisterClassExW(ctypes.byref(wc))

            # Original geometry - top screen above bottom screen
            # centered horizontally underneath. The empty space on
            # each side of the bottom screen is where the chassis
            # buttons render.
            client_w = max(self.scrcpy.f_w1, self.scrcpy.f_w2 + abs(self.bx))
            client_h = self.scrcpy.f_h1 + self.scrcpy.f_h2
            # Stash the full size so we can shrink and restore later
            # when the user toggles "Separate screens".
            self._original_container_w = client_w
            self._original_container_h = client_h

            # Adjustments for window decorations
            rect = wintypes.RECT(0, 0, int(client_w), int(client_h))
            style = WS_OVERLAPPEDWINDOW | WS_VISIBLE | WS_CLIPCHILDREN | WS_CLIPSIBLINGS
            self.user32.AdjustWindowRectEx(
                ctypes.byref(rect), style, False, WS_EX_CONTROLPARENT
            )

            # Create the container window
            hwnd = self.user32.CreateWindowExW(
                WS_EX_CONTROLPARENT,
                "ThorFinalBridge",
                "scrcpy-thor-ui",
                style,
                DEFAULT_CONTAINER_X,
                DEFAULT_CONTAINER_Y,
                rect.right - rect.left,
                rect.bottom - rect.top,
                None,
                0,
                ctypes.c_void_p(hinst),
                None,
            )

            if hwnd:
                self.hwnd_container = hwnd
                self.dock.hwnd_container = hwnd
                self.user32.ShowWindow(hwnd, SW_SHOW)

                # Enable the dark titlebar
                enable_dark_titlebar(hwnd)

            # Run the message loop for the container window
            msg = wintypes.MSG()
            while self.running and self.user32.GetMessageW(
                    ctypes.byref(msg), None, 0, 0
            ):
                self.user32.TranslateMessage(ctypes.byref(msg))
                self.user32.DispatchMessageW(ctypes.byref(msg))

        threading.Thread(target=loop, daemon=True).start()

    def _docking_monitor(self):
        """
        Background thread to continuously montor and dock windows.
        Searches for titles and automatically sets their parent to the container window and applies styling
        """
        while self.running:
            with self.dock_lock:
                if self.hwnd_container and self.docked:
                    # Find scrcpy windows by their titles
                    topScr = self.user32.FindWindowW(None, TOP_SCREEN_WINDOW_TITLE)
                    bottomScr = self.user32.FindWindowW(None, BOTTOM_SCREEN_WINDOW_TITLE)

                    # Dock top screen if found and not already docked
                    if topScr and self.user32.GetParent(topScr) != self.hwnd_container:
                        self.user32.SetParent(topScr, self.hwnd_container)
                        apply_docked_style(topScr)
                        self.dock.hwnd_top = topScr

                    # Dock bottom screen if found and not already docked
                    if bottomScr and self.user32.GetParent(bottomScr) != self.hwnd_container:
                        self.user32.SetParent(bottomScr, self.hwnd_container)
                        apply_docked_style(bottomScr)
                        self.dock.hwnd_bottom = bottomScr
            time.sleep(DOCKING_MONITOR_TIME_DELAY)

    def toggle_dock(self):
        """
        Toggle the "Separate screens" mode.

        Three states are possible:

        1. DOCKED        - both screens inside the container, chassis
                           painted around the bottom screen.
        2. TOP_FLOATING  - chosen automatically when the user enables
                           "Separate screens" while the chassis overlay
                           is ON. The container shrinks down to just
                           the bottom row (bottom scrcpy window plus
                           chassis on each side) and the top scrcpy
                           window pops out as an independent draggable
                           top-level window.
        3. BOTH_FLOATING - chosen when the user enables "Separate
                           screens" while the chassis overlay is OFF.
                           The container hides entirely and both
                           scrcpy windows float free.
        """
        if not self.dock.hwnd_top or not self.dock.hwnd_bottom:
            logger.warning("Cannot toggle dock: windows not available")
            return

        with self.dock_lock:
            if self.docked:
                # === DOCKED -> SEPARATED ===
                logger.info("Undocking windows (chassis_enabled=%s)", self.chassis_enabled)
                self.docked = False

                if self.chassis_enabled:
                    # Top floats free, bottom stays in a shrunk
                    # container that still shows the chassis art.
                    self._top_only_separated = True
                    apply_undocked_style(self.dock.hwnd_top)
                    self._resize_container_for_separation()
                    self._build_chassis_bitmap()
                    self._invalidate_chassis()
                else:
                    # Classic full-separation behaviour - both windows
                    # become independent and the container disappears.
                    self._top_only_separated = False
                    apply_undocked_style(self.dock.hwnd_top)
                    apply_undocked_style(self.dock.hwnd_bottom)
                    self.user32.ShowWindow(self.hwnd_container, SW_HIDE)

                self.dock.invalidate_geom_cache()
                logger.info("Windows undocked successfully")

            else:
                # === SEPARATED -> DOCKED ===
                logger.info("Docking windows back (top_only=%s)", self._top_only_separated)
                was_top_only = self._top_only_separated
                self._top_only_separated = False

                if was_top_only:
                    # Only the TOP was floating - the bottom never
                    # left the container, so its cached child hwnd is
                    # still valid (FindWindowW can't see child
                    # windows so we mustn't try to re-look it up).
                    # We DO need to re-find the top because it has
                    # been a top-level window and may have been moved
                    # or otherwise re-validated.
                    topScr = self.user32.FindWindowW(None, TOP_SCREEN_WINDOW_TITLE)
                    if not topScr:
                        logger.error("Failed to find top scrcpy window for re-docking")
                        self._top_only_separated = was_top_only
                        return
                    self.dock.hwnd_top = topScr
                    # Container is currently shrunk - restore full size
                    # before re-parenting the top.
                    self._resize_container_to_full()
                    self.user32.SetParent(self.dock.hwnd_top, self.hwnd_container)
                    apply_docked_style(self.dock.hwnd_top)
                    self._build_chassis_bitmap()
                    self._invalidate_chassis()
                else:
                    # Both were floating top-level windows so we can
                    # safely re-find both by title.
                    topScr = self.user32.FindWindowW(None, TOP_SCREEN_WINDOW_TITLE)
                    bottomScr = self.user32.FindWindowW(None, BOTTOM_SCREEN_WINDOW_TITLE)
                    if not topScr or not bottomScr:
                        logger.error("Failed to find scrcpy windows for re-docking")
                        return
                    self.dock.hwnd_top = topScr
                    self.dock.hwnd_bottom = bottomScr
                    self.user32.ShowWindow(self.hwnd_container, SW_SHOW)
                    self.user32.SetParent(self.dock.hwnd_top, self.hwnd_container)
                    self.user32.SetParent(self.dock.hwnd_bottom, self.hwnd_container)
                    apply_docked_style(self.dock.hwnd_top)
                    apply_docked_style(self.dock.hwnd_bottom)

                self.dock.invalidate_geom_cache()
                self.docked = True
                logger.info("Windows docked successfully")

    def _resize_container_for_separation(self):
        """
        Shrink the container window down to just the bottom row
        (bottom screen + chassis side strips). Used when entering
        TOP_FLOATING mode so the chassis stays visible around the
        bottom screen but the wasted top-row area disappears.
        """
        if not self.hwnd_container:
            return
        cw = self._original_container_w or self.scrcpy.f_w1
        new_h = self.scrcpy.f_h2
        # Adjust outer window size to keep the desired client size
        rect = wintypes.RECT(0, 0, int(cw), int(new_h))
        WS_OVERLAPPEDWINDOW = 0x00CF0000
        WS_EX_CONTROLPARENT = 0x00010000
        self.user32.AdjustWindowRectEx(ctypes.byref(rect), WS_OVERLAPPEDWINDOW, False,
                                       WS_EX_CONTROLPARENT)
        SWP_NOMOVE = 0x0002
        SWP_NOZORDER = 0x0004
        self.user32.SetWindowPos(
            self.hwnd_container, 0, 0, 0,
            rect.right - rect.left, rect.bottom - rect.top,
            SWP_NOMOVE | SWP_NOZORDER,
        )

    def _resize_container_to_full(self):
        """Restore the container to its original full size."""
        if not self.hwnd_container:
            return
        cw = self._original_container_w or self.scrcpy.f_w1
        ch = self._original_container_h or (self.scrcpy.f_h1 + self.scrcpy.f_h2)
        rect = wintypes.RECT(0, 0, int(cw), int(ch))
        WS_OVERLAPPEDWINDOW = 0x00CF0000
        WS_EX_CONTROLPARENT = 0x00010000
        self.user32.AdjustWindowRectEx(ctypes.byref(rect), WS_OVERLAPPEDWINDOW, False,
                                       WS_EX_CONTROLPARENT)
        SWP_NOMOVE = 0x0002
        SWP_NOZORDER = 0x0004
        self.user32.SetWindowPos(
            self.hwnd_container, 0, 0, 0,
            rect.right - rect.left, rect.bottom - rect.top,
            SWP_NOMOVE | SWP_NOZORDER,
        )

    def show_connection_dialog(self):
        """
        Shows the wireless connection dialog
        Hides the scrcpy container and pygame control panel first so their
        Win32 handles don't conflict with the tkinter dialog grab, then
        restores them when the dialog closes
        """
        logger.info("Opening wireless connection dialog — hiding scrcpy windows")

        # Hide pygame control panel
        try:
            info = pygame.display.get_wm_info()
            hwnd_pygame = info.get("window")
            if hwnd_pygame:
                self.user32.ShowWindow(hwnd_pygame, SW_HIDE)
        except Exception as HidePygameError:
            logger.warning(f"Could not hide pygame window: {HidePygameError}")

        # Hide scrcpy container and child windows
        if self.hwnd_container:
            self.user32.ShowWindow(self.hwnd_container, SW_HIDE)

        try:
            result = show_wireless_dialog(self._tk_root, self.scrcpy, config=self.config)

            if result == 'connected':
                logger.info("Wireless connection established via dialog")
                return True
            elif result == 'disconnected':
                logger.info("Device disconnected via dialog")
                return False
            else:
                logger.info("Dialog closed without action")
                return None

        except Exception as DialogError:
            logger.error(f"Error showing wireless dialog: {DialogError}")
            messagebox.showerror(
                "Dialog Error",
                f"Failed to show wireless connection dialog:\n{DialogError}"
            )
            return None

        finally:
            logger.info("Wireless dialog closed — restoring scrcpy windows")
            # Restore scrcpy container
            if self.hwnd_container:
                self.user32.ShowWindow(self.hwnd_container, SW_SHOW)

            # Restore pygame control panel
            try:
                info = pygame.display.get_wm_info()
                hwnd_pygame = info.get("window")
                if hwnd_pygame:
                    self.user32.ShowWindow(hwnd_pygame, SW_SHOW)
                    self.user32.SetForegroundWindow(hwnd_pygame)
            except Exception as ShowPygameError:
                logger.warning(f"Could not restore pygame window: {ShowPygameError}")

    def launch(self):
        """
        Main application entry point.
        Starts all components in the following order:
        1) Shows the loading screen
        2) Detects the android device via ADB or shows wireless window
        3) Start the scrcpy instances for both screens
        4) Creates the container window
        5) Starts the docking monitor
        6) Initialises the Pygame UI
        7) Enter the main event loop which handles:
         - Pygame events
         - Window position syncing
         - UI rendering

        It exits if no device is attached
        """
        self.running = True
        self._wndproc = self._create_wnd_proc()
        show_loading_screen()

        # Detect device
        serial = self.scrcpy.detect_device()

        # If no device found, suggest wireless connection
        if not serial:
            logger.info("No USB device found, offering wireless connection")

            response = messagebox.askyesno(
                "No Device Found",
                "No USB device detected.\n\n"
                "Would you like to connect wirelessly?\n\n"
                "Click Yes to open the wireless connection dialog,\n"
                "or No to exit."
            )

            if response:
                result = show_wireless_dialog(self._tk_root, self.scrcpy, config=self.config)

                if result == 'connected':
                    serial = self.scrcpy.serial
                    logger.info(f"Connected wirelessly to {serial}")
                else:
                    logger.info("No wireless connection established")
                    self.stop()
                    return
            else:
                logger.info("User chose to exit")
                self.stop()
                return

        # Start scrcpy if no device is connected
        if serial:
            logger.info(f"Starting scrcpy with device: {serial} (mode: {self.scrcpy.connection_mode})")
            self.scrcpy.start_scrcpy(serial)
        else:
            logger.error("No device available to start scrcpy")
            self.stop()
            return

        # Start background threads
        self._create_container_window()
        threading.Thread(target=self._docking_monitor, daemon=True).start()

        # Init UI and event loop
        from src.ui_pygame import PygameUI

        pygame.init()
        self.ui = PygameUI(self)
        clock = pygame.time.Clock()

        # Build the chassis bitmap on the main thread (pygame is not
        # thread-safe). The container window has already been created
        # in a worker thread; wait briefly for its handle to appear.
        chassis_wait_deadline = time.time() + 3.0
        while self.running and not self.hwnd_container and time.time() < chassis_wait_deadline:
            time.sleep(0.05)
        if self.hwnd_container and self.chassis_enabled:
            if self._build_chassis_bitmap():
                self._invalidate_chassis()
                logger.info("Chassis bitmap ready, container repainted")

        # Phase 2: spin up the live-input listener so the chassis art
        # animates with whatever is happening on the device. The
        # listener's on_change callback runs on its OWN thread and
        # only flips a thread-safe Event flag; the main loop below
        # consumes it and rebuilds the bitmap in the safe thread.
        if self.chassis_enabled and serial:
            self._input_listener = InputListener(
                self.scrcpy.adb_bin, serial,
                on_change=self._on_input_changed,
            )
            self._input_listener.start()

        while self.running:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    self.stop()
                self.ui.handle_event(event)

            # Sync window positions if they exist. When only the top
            # is separated, the bottom screen needs to sit at y=0
            # inside the shrunk container, and we must NOT touch the
            # top window any more (it's draggable now and keeps its
            # own coords).
            if self.dock.hwnd_top or self.dock.hwnd_bottom:
                if self._top_only_separated:
                    self.dock.sync(
                        self.tx, self.ty,
                        self.bx, 0,
                        self.scrcpy.f_w1, self.scrcpy.f_h1,
                        self.scrcpy.f_w2, self.scrcpy.f_h2,
                        is_docked=True,
                        sync_top=False,
                    )
                else:
                    self.dock.sync(
                        self.tx, self.ty,
                        self.bx, self.by,
                        self.scrcpy.f_w1, self.scrcpy.f_h1,
                        self.scrcpy.f_w2, self.scrcpy.f_h2,
                        is_docked=self.docked,
                    )

            # Repaint the chassis bitmap if the input state changed
            # (throttled so axis storms don't churn the GPU/CPU).
            self._maybe_redraw_chassis_for_input()

            self.ui.render()
            clock.tick(UI_FPS)

    def stop(self):
        """
        Cleanly shuts down the application
        Performs the following actions:
        1) Saves current layout config
        2) Stops all scrcpy processes
        3) Quits pygame
        4) Close the container window
        5) Force exit
        """
        if not self.running:
            return
        self.running = False
        self.save_layout()

        # Stop the live-input listener if it was running
        if self._input_listener:
            try:
                self._input_listener.stop()
            except Exception:
                pass
            self._input_listener = None

        # Free chassis bitmap if we created one
        if self._chassis_hbitmap:
            try:
                ctypes.windll.gdi32.DeleteObject(self._chassis_hbitmap)
            except Exception:
                pass
            self._chassis_hbitmap = None

        # Taskkill the scrcpy
        import subprocess
        subprocess.run(
            ["taskkill", "/F", "/IM", "scrcpy.exe", "/T"],
            capture_output=True,
            creationflags=CREATE_NO_WINDOW,
        )
        # Shutdown Pygame UI and close container window
        pygame.quit()
        if self.hwnd_container:
            self.user32.PostMessageW(self.hwnd_container, WM_CLOSE, 0, 0)
        os._exit(0)