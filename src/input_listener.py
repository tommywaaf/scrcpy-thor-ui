# ThorCPY - Dual-screen scrcpy docking and control UI for Windows
# Copyright (C) 2026 the_swest
#
# This program is free software under the GPL v3 (see LICENSE).

# src/input_listener.py
#
# Streams the AYN Thor's input events back to the host via
# `adb shell getevent -lq` (no device path = listen to ALL nodes).
# Each parsed event updates a thread-safe ButtonState dict that the
# chassis renderer reads to highlight pressed buttons and offset
# joystick caps in real time.
#
# We listen to all input nodes rather than a single one because the
# AYN system button is a kernel gpio-key (event4), while the gamepad
# controls live on the "Odin Controller" node (event9). The parser
# whitelists only known KEY_/BTN_/ABS_ codes, so noise from touch
# screens, audio jacks, etc. is dropped cheaply.
#
# A standard Linux gamepad reports the four face buttons by physical
# POSITION rather than by silkscreen label, so:
#
#   BTN_NORTH  =  top    button   (silkscreened "X" on Thor)  -> btn_x
#   BTN_EAST   =  right  button   (silkscreened "A" on Thor)  -> btn_a
#   BTN_SOUTH  =  bottom button   (silkscreened "B" on Thor)  -> btn_b
#   BTN_WEST   =  left   button   (silkscreened "Y" on Thor)  -> btn_y
#
# The mapping below preserves Thor's silkscreen labels even though
# the kernel uses the inverted "BTN_X = north" naming convention.

import logging
import re
import subprocess
import threading

logger = logging.getLogger(__name__)


# Input device path on the AYN Thor for the controller surface.
# Autoprobed at startup; this is the fallback if probe fails.
DEFAULT_GAMEPAD_DEVICE = "/dev/input/event9"
DEFAULT_GAMEPAD_NAME = "Odin Controller"

# Process creation flag to suppress console window on Windows.
CREATE_NO_WINDOW = 0x08000000

# Keys we care about; everything else is ignored.
#
# IMPORTANT: AYN Thor fires events by SILKSCREEN LETTER, not by
# physical position. Linux's input headers alias BTN_A=BTN_SOUTH,
# BTN_B=BTN_EAST, BTN_X=BTN_NORTH, BTN_Y=BTN_WEST - so when the
# Thor firmware reports "user pressed A", the kernel sees BTN_SOUTH
# even though A on the Thor is the RIGHT face button. We just map
# straight from the BTN_* code to the same letter on the chassis.
KEY_MAP = {
    "BTN_SOUTH":      "btn_a",   # BTN_A alias - the silkscreen "A" (right on Thor)
    "BTN_GAMEPAD":    "btn_a",   # another alias of BTN_SOUTH
    "BTN_EAST":       "btn_b",   # BTN_B alias - the silkscreen "B" (bottom on Thor)
    "BTN_NORTH":      "btn_x",   # BTN_X alias - the silkscreen "X" (top on Thor)
    "BTN_WEST":       "btn_y",   # BTN_Y alias - the silkscreen "Y" (left on Thor)
    # Shoulders + triggers
    "BTN_TL":         "l1",
    "BTN_TR":         "r1",
    "BTN_TL2":        "l2",
    "BTN_TR2":        "r2",
    # System buttons
    "BTN_SELECT":     "select",
    "BTN_START":      "start",
    "BTN_MODE":       "home",
    "KEY_HOME":       "home",
    "KEY_BACK":       "back",
    "KEY_APPSELECT":  "ayn",     # historical: some Thor builds report APPSELECT
    "KEY_F24":        "ayn",     # actual AYN-button keycode on this Thor unit
    # Stick clicks
    "BTN_THUMBL":     "l3",
    "BTN_THUMBR":     "r3",
    # Digital D-pad (some controllers fire BTN_DPAD_*, others fire ABS_HAT0*)
    "BTN_DPAD_UP":    "dpad_up",
    "BTN_DPAD_DOWN":  "dpad_down",
    "BTN_DPAD_LEFT":  "dpad_left",
    "BTN_DPAD_RIGHT": "dpad_right",
}

# Axis mapping: ABS code -> (state key, raw_min, raw_max, deadzone)
# raw_min/max ranges come from the Thor's `getevent -p` capability dump.
AXIS_MAP = {
    "ABS_X":  ("lstick_x", -32767, 32767, 0.08),
    "ABS_Y":  ("lstick_y", -32767, 32767, 0.08),
    "ABS_Z":  ("rstick_x", -32767, 32767, 0.08),
    "ABS_RZ": ("rstick_y", -32767, 32767, 0.08),
    # Analog triggers (0..1)
    "ABS_GAS":   ("trigger_r", 0, 32767, 0.02),
    "ABS_BRAKE": ("trigger_l", 0, 32767, 0.02),
}


class InputListener:
    """
    Background ADB-driven gamepad event listener.

    Spawns `adb shell getevent -lq <device>` and parses the labelled
    output into a thread-safe state dict. Optionally invokes
    `on_change(state)` whenever a parsed event mutates the state, so
    the renderer can repaint without having to poll continuously.
    """

    def __init__(self, adb_bin, serial, on_change=None,
                 device_path=DEFAULT_GAMEPAD_DEVICE):
        self.adb_bin = adb_bin
        self.serial = serial
        self.on_change = on_change
        self.device_path = device_path
        self._proc = None
        self._thread = None
        self._stop = threading.Event()
        self._state = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self):
        if not self.adb_bin or not self.serial:
            logger.error("InputListener: missing adb binary or device serial")
            return False

        # We intentionally do NOT probe for a specific input device
        # here - the AYN system button and the gamepad live on
        # different nodes, so we run getevent across all nodes and
        # let the parser whitelist what it cares about.
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="ThorInputListener"
        )
        self._thread.start()
        logger.info("InputListener started (listening on all input nodes)")
        return True

    def stop(self):
        self._stop.set()
        if self._proc:
            try:
                self._proc.kill()
            except Exception:
                pass
        if self._thread:
            self._thread.join(timeout=1.0)

    # ------------------------------------------------------------------
    # State accessors
    # ------------------------------------------------------------------

    def snapshot(self):
        """Return an immutable copy of the current state dict."""
        with self._lock:
            return dict(self._state)

    # ------------------------------------------------------------------
    # Internal: device probe
    # ------------------------------------------------------------------

    def _probe_device_path(self):
        """
        Run `getevent -p` and return the /dev/input/eventN path of the
        first device whose name contains 'Controller', preferring
        'Odin Controller' if present. Returns None if probe fails or
        no controller is found - in which case we fall back to the
        constructor's default.
        """
        try:
            res = subprocess.run(
                [self.adb_bin, "-s", self.serial, "shell", "getevent", "-p"],
                capture_output=True, text=True, timeout=5,
                creationflags=CREATE_NO_WINDOW,
            )
            if res.returncode != 0:
                return None
            text = res.stdout
        except Exception as DeviceProbeError:
            logger.warning(f"Could not probe input devices: {DeviceProbeError}")
            return None

        # Scan blocks separated by 'add device N: <path>' followed by 'name: "..."'
        candidates = []
        cur_path = None
        cur_name = None
        for line in text.splitlines():
            m = re.match(r"\s*add device \d+:\s*(/dev/input/event\d+)", line)
            if m:
                cur_path = m.group(1)
                cur_name = None
                continue
            m = re.match(r'\s*name:\s*"([^"]+)"', line)
            if m and cur_path:
                cur_name = m.group(1)
                candidates.append((cur_path, cur_name))
                cur_path = None

        # Prefer Odin Controller, then anything containing "Controller"
        for path, name in candidates:
            if name == DEFAULT_GAMEPAD_NAME:
                return path
        for path, name in candidates:
            if "Controller" in name and "Mouse" not in name:
                return path
        return None

    # ------------------------------------------------------------------
    # Internal: event stream + parser
    # ------------------------------------------------------------------

    def _run(self):
        try:
            # No device path - listen to ALL input nodes. The AYN
            # system button on the Thor lives on /dev/input/event4
            # (gpio-keys), NOT on the Odin Controller gamepad node,
            # so a single-device getevent misses it. Listening
            # broadly is essentially free here because our parser
            # only acts on whitelisted KEY_/BTN_/ABS_ codes; events
            # from touch screens, audio jacks, etc. are dropped.
            cmd = [
                self.adb_bin, "-s", self.serial,
                "shell", "getevent -lq",
            ]
            logger.debug(f"Spawning input listener: {' '.join(cmd)}")
            self._proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                text=True,
                bufsize=1,  # line-buffered
                creationflags=CREATE_NO_WINDOW,
            )
            assert self._proc.stdout is not None
            for line in iter(self._proc.stdout.readline, ""):
                if self._stop.is_set():
                    break
                self._parse_line(line.strip())
        except Exception as InputListenError:
            logger.error(f"InputListener crashed: {InputListenError}", exc_info=True)
        finally:
            if self._proc:
                try:
                    self._proc.kill()
                except Exception:
                    pass
            logger.info("InputListener stopped")

    def _parse_line(self, line):
        """
        Parse a single labelled getevent line. Format examples:
            EV_KEY       BTN_NORTH            DOWN
            EV_KEY       BTN_NORTH            UP
            EV_ABS       ABS_X                ffff8000
            EV_ABS       ABS_HAT0X            00000001
            EV_SYN       SYN_REPORT           00000000

        Some adb shells prefix the device path; we split tokens loosely
        and look for the first three meaningful columns.
        """
        if not line or "SYN_" in line:
            return
        # Drop a possible leading "/dev/input/eventN:" prefix
        if ":" in line.split()[0]:
            line = line.split(":", 1)[1].strip()
        parts = line.split()
        if len(parts) < 3:
            return
        ev_type, code, value = parts[0], parts[1], parts[2]

        changed = False
        if ev_type == "EV_KEY":
            mapped = KEY_MAP.get(code)
            if mapped:
                pressed = (value == "DOWN")
                with self._lock:
                    if self._state.get(mapped) != pressed:
                        self._state[mapped] = pressed
                        changed = True

        elif ev_type == "EV_ABS":
            # Hat axes encode the digital D-pad - handle separately.
            if code == "ABS_HAT0X":
                raw = self._parse_hex_signed(value)
                if raw is None:
                    return
                with self._lock:
                    new_l = (raw < 0)
                    new_r = (raw > 0)
                    if self._state.get("dpad_left") != new_l:
                        self._state["dpad_left"] = new_l
                        changed = True
                    if self._state.get("dpad_right") != new_r:
                        self._state["dpad_right"] = new_r
                        changed = True
            elif code == "ABS_HAT0Y":
                raw = self._parse_hex_signed(value)
                if raw is None:
                    return
                with self._lock:
                    new_u = (raw < 0)
                    new_d = (raw > 0)
                    if self._state.get("dpad_up") != new_u:
                        self._state["dpad_up"] = new_u
                        changed = True
                    if self._state.get("dpad_down") != new_d:
                        self._state["dpad_down"] = new_d
                        changed = True
            elif code in AXIS_MAP:
                key, vmin, vmax, dz = AXIS_MAP[code]
                raw = self._parse_hex_signed(value)
                if raw is None:
                    return
                if vmin < 0:
                    norm = max(-1.0, min(1.0, raw / max(abs(vmin), abs(vmax))))
                else:
                    span = (vmax - vmin) or 1
                    norm = max(0.0, min(1.0, (raw - vmin) / span))
                if abs(norm) < dz:
                    norm = 0.0
                # Quantize stick + trigger values to coarse 0.05 steps.
                # Hall-effect sticks emit constant micro-changes even at
                # rest (raw values shifting by tiny amounts). Without
                # quantization, every tiny shift triggers a chassis
                # rebuild downstream, which dominated host CPU during
                # active gameplay. 0.05 = 41 distinct values across the
                # full -1..1 range, far more than the eye can resolve
                # for a stick cap that's only ~30 px in radius.
                norm = round(norm * 20.0) / 20.0
                with self._lock:
                    if self._state.get(key) != norm:
                        self._state[key] = norm
                        changed = True

        if changed and self.on_change is not None:
            try:
                self.on_change()
            except Exception as OnChangeError:
                logger.debug(f"on_change handler raised: {OnChangeError}")

    @staticmethod
    def _parse_hex_signed(value):
        """Parse 'ffff8000' style hex into a signed 32-bit int."""
        try:
            raw = int(value, 16)
        except ValueError:
            return None
        if raw >= 0x80000000:
            raw -= 0x100000000
        return raw
