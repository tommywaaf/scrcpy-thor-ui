<p align="center">
  <img src="assets/scrcpy-thor-ui-logo.png" alt="scrcpy-thor-ui" width="220">
</p>

<h1 align="center">scrcpy-thor-ui</h1>

<p align="center">
  <em>A dual-screen scrcpy launcher for the AYN Thor with a live virtual controller overlay.</em>
</p>

<p align="center">
  <a href="https://github.com/tommywaaf/scrcpy-thor-ui/releases/latest">
    <img src="https://img.shields.io/badge/download-latest%20release-5fb1ff?style=for-the-badge&logo=github" alt="Download latest release">
  </a>
  <a href="LICENSE">
    <img src="https://img.shields.io/badge/license-GPL%20v3-blue?style=for-the-badge" alt="GPL v3">
  </a>
  <img src="https://img.shields.io/badge/platform-Windows%2010%2F11-0078d4?style=for-the-badge&logo=windows" alt="Windows 10/11">
</p>

> **Recommended (best performance) — run from source with Python.**
> The packaged `.exe` works, but the from-source build is consistently
> smoother in real use (no PyInstaller bootloader, no Python interpreter
> repacking, no Windows Defender re-scanning binaries on cold launches,
> nothing between you and the scrcpy process). Five steps:
>
> 1. Install **Python 3.10–3.12** from <https://www.python.org/downloads/>
>    (Pygame doesn't ship a wheel for 3.14 yet, and 3.10 is the
>    sweet spot the project is developed against). Tick *"Add python.exe
>    to PATH"* in the installer.
> 2. Open PowerShell and clone the repo somewhere convenient:
>    ```powershell
>    git clone https://github.com/tommywaaf/scrcpy-thor-ui.git
>    cd scrcpy-thor-ui
>    ```
> 3. Install the Python dependencies (one-time):
>    ```powershell
>    py -3.10 -m pip install -r requirements.txt
>    ```
> 4. Plug in the Thor with USB debugging enabled and tap **Allow** when
>    the RSA-key prompt appears on the device.
> 5. Launch:
>    ```powershell
>    py -3.10 main.py
>    ```
>
> *Prefer a one-click .exe?* Grab `scrcpy-thor-ui-v1.0.0.zip` from the
> [latest release](https://github.com/tommywaaf/scrcpy-thor-ui/releases/latest),
> unzip it anywhere on your PC, and double-click `scrcpy-thor-ui.exe`
> inside the unzipped folder. `config/` and `logs/` are created next
> to the .exe on first launch. Performance is a hair behind the
> from-source path but absolutely usable.

scrcpy-thor-ui mirrors both screens of the AYN Thor to your Windows
desktop and draws a real-time virtual controller around the bottom
screen. Every button press, joystick tilt, D-pad direction and trigger
pull on the actual handheld is reflected on screen in the same instant
— so what you see on the desktop genuinely looks like an AYN Thor.

It's tuned for the smoothest possible mirroring over USB: hardware
H.264 encoding on the device, software-friendly H.264 decoding on
the host, Direct3D 11 presentation with vsync disabled to match
high-refresh monitors, a constant-bitrate stream with intra-refresh
slices (no keyframe spikes) and a generous 80 ms display buffer to
absorb any residual decode/render jitter. On-the-fly FPS switching
(30 / 60 / 90 / 120), one-click restart, and a real-time virtual
controller overlay round it out.

| Menu UI                             | Mirror UI                              |
|-------------------------------------|----------------------------------------|
| ![](assets/screenshots/menu_ui.png) | ![](assets/screenshots/mirror_ui.png)  |

> **Designed for Windows 11.** Windows 10 (1809+) should work but bugs may occur.

---

## Highlights

### Live virtual controller overlay
- Procedurally drawn AYN Thor button layout in the empty space on each side of the bottom screen — no screen real estate is wasted.
- Left strip (top → bottom): **L1 / L2 LEDs**, **SELECT**, **left joystick**, **D-pad**, **HOME**.
- Right strip (top → bottom): **R1 / R2 LEDs**, **START**, **X / Y / B / A** cluster, **right joystick**, **BACK**.
- Face buttons follow the AYN Thor silkscreen exactly: **X top, A right, B bottom, Y left**.
- L1 / L2 / R1 / R2 indicators glow from dim red to bright red when their triggers fire.
- Joystick caps offset smoothly within their wells based on real stick position (Hall-effect axes are handled with a deadzone for clean rest behaviour).
- D-pad arms light up independently. Pills (`SELECT` / `START` / `HOME` / `BACK`) and ABXY circles darken when pressed.
- One-click **`BUTTONS ON / OFF`** toggle in the control panel hides or shows the entire overlay; preference persists in `config/config.json`.

### Real-time input streaming from the device
- Pulls events directly from the Thor's `Odin Controller` input node via `adb shell getevent -lq`, so there's no Android-side companion app to install.
- Auto-detects the gamepad device path on startup (probes `getevent -p` and prefers `Odin Controller`, with a sensible fallback).
- Parses the Linux event stream into a thread-safe button-state dict and throttles redraws to ~30 fps so an axis-storm never melts the renderer.

### Performance tuning (the whole pipeline, end to end)

The mirror is essentially as smooth as a USB-tethered scrcpy can get
on Windows. Everything below is on by default.

**Encoder side (on the Thor):**
- **`--video-codec=h264`** — H.264 instead of H.265, because scrcpy's host-side decoder is software (no D3D11VA hwaccel exposed in scrcpy 3.3.4) and software H.264 decode is roughly 2× faster than H.265.
- **Forced Qualcomm hardware encoder** (`c2.qti.avc.encoder`) so the device-side encode never falls back to software.
- **`bitrate-mode=2` (CBR)** — every frame carries roughly the same number of bits regardless of motion, so the USB transport sees a steady byte rate instead of bursts.
- **`intra-refresh-period=60`** — gradual slice-based refresh (~1/60 of the picture per frame) **eliminates keyframe spikes entirely**. The bitstream is perfectly uniform.
- **`i-frame-interval=10`** + **`low-latency=1`** + **`max-bframes=0`** + **`complexity=0`** — minimal encoder buffering, no B-frames, lowest-complexity deterministic encode time.
- **`priority=0`** + **`operating-rate=120`** — MediaCodec scheduling hints that pin the encoder thread to a real-time fast path on the device.
- **Bottom screen capped at 30 fps** — the bottom is a static touchpad, no need for 60; this frees significant device-side GPU for the top encoder.
- Bitrate scaled high enough to never bottleneck the encoder under heavy motion.

**Transport side:**
- USB only (wireless is supported but USB is recommended for best timing).
- **Per-instance scrcpy log files** — every launch writes `logs/scrcpy_top_*.log` and `logs/scrcpy_bottom_*.log` containing scrcpy's `--print-fps` counter so you can verify the actual on-screen rate.

**Host (PC) side:**
- **`--render-driver=direct3d11`** — SDL's modern Direct3D 11 backend. Replacing the upstream `direct3d` (D3D9) was a big win on Windows 11 because D3D9's child-window presentation path is heavily DWM-throttled.
- **`SDL_RENDER_VSYNC=0`** — disables SDL's vsync wait inside scrcpy. On high-refresh monitors (144/165/240 Hz) the source rate (60 Hz) doesn't align with display vsync slots, and waiting for them caused dropped/coalesced frames. Tearing on a 200+ Hz panel is essentially invisible.
- **`--video-buffer=80`** — 5 frames of jitter buffering at 60 Hz. With the encoder emitting a uniform bitstream, this absorbs any residual variance and presents at a perfectly steady cadence.
- **`HIGH_PRIORITY_CLASS`** for both scrcpy children + **`ABOVE_NORMAL_PRIORITY_CLASS`** for the host process so neither gets descheduled when other apps spike.
- **`subprocess.PIPE` deadlock fix** — scrcpy's stdout/stderr now go to real file handles (per-instance log files) instead of unread `PIPE`s. Closes the path that previously caused intermittent multi-second stalls.
- **Audio buffers tuned** to `--audio-buffer=60 --audio-output-buffer=15` so dense music doesn't underrun the SDL audio output.

**Window-sync / overlay rendering:**
- Chassis bitmap is built once into a persistent DIB and updated in place via `memmove` — no `CreateDIBSection` / `DeleteObject` per frame.
- Targeted BitBlt: only the two side-strip rectangles are copied to the screen, not the full container bitmap.
- Targeted `InvalidateRect`: only the strip regions are marked dirty, so the embedded scrcpy children are strictly left alone.
- Hash-dedup before each chassis rebuild: if button state didn't change, the rebuild is skipped entirely.
- Axis values quantized to 0.05 steps so Hall-stick rest noise doesn't trigger redraws.
- Geometry cache + removed `SWP_NOCOPYBITS` so the embedded scrcpy windows aren't repainted 60×/sec when the layout is static.
- 64-bit-safe ctypes signatures for every GDI call we make (`SelectObject`, `BitBlt`, `FillRect`, `GetStockObject`, `CreateCompatibleDC`, `DeleteObject`, `DeleteDC`, `UpdateWindow`).

### Window-sync optimisations
- Geometry cache: `SetWindowPos` is only invoked when the embedded scrcpy windows actually need to move, instead of being hit twice per frame at 60 Hz.
- Removed `SWP_NOCOPYBITS` from the dock sync, so the embedded scrcpy frames aren't trashed by needless full repaints.
- Hardened ctypes signatures for GDI calls (`SelectObject`, `BitBlt`, `CreateCompatibleDC`, `DeleteObject`, `DeleteDC`) so 64-bit Windows handles never get truncated.

### Control panel additions
- **FPS selector** (cycles 30 → 60 → 120) saved to `config.json`.
- **RESTART** button that re-launches the app cleanly so global-scale or FPS changes take effect without you needing to touch a terminal.
- **`BUTTONS ON / OFF`** chassis toggle.
- All while preserving the upstream layout sliders, undock / dock, screenshot, wireless connection dialog and preset save/load/delete.

### Inherited features
- Native Win32 container that hosts both scrcpy windows as embedded children.
- Layout sliders for Top X / Y and Bottom X / Y.
- Layout presets stored in `config/layout.json` (save / load / delete from the UI).
- One-click clipboard screenshot of the entire docked container, including transparency where the screens aren't.
- Wireless ADB connection dialog (Android 11+ pair-with-code or legacy `IP:5555`).
- Comprehensive logging with daily rotation in `logs/`.
- Full PyInstaller bundling support via `build.py`.

---

## Installation

> **Requirement:** USB Debugging must be enabled on your Thor.
>
> 1. **Settings → About device.**
> 2. Tap **Build number** seven times.
> 3. **Settings → Developer options → USB debugging.**
>
> Then connect via USB or use the in-app **Wireless** dialog to pair.

### Option 1 — Run from source ⭐ **Recommended for best performance**

> Pygame doesn't ship a wheel for Python 3.14 yet; use Python 3.10–3.12
> (3.10 is what the project is developed against). Make sure
> *"Add python.exe to PATH"* is ticked when you install Python.

```powershell
git clone https://github.com/tommywaaf/scrcpy-thor-ui.git
cd scrcpy-thor-ui
py -3.10 -m pip install -r requirements.txt
py -3.10 main.py
```

That's the smoothest configuration available. The packaged `.exe`
goes through PyInstaller's bootloader and a separate Python
interpreter, which carries a small but noticeable runtime tax under
heavy gameplay; the source path skips both.

### Option 2 — Build a standalone executable

```powershell
pip install pyinstaller
python build.py
```

Output appears at `dist/scrcpy-thor-ui/` (a folder containing
`scrcpy-thor-ui.exe` next to an `_internal/` folder with all
dependencies). The folder is fully self-contained — you can move it
anywhere on disk. `config/` and `logs/` are created next to the .exe
on first run. Zip the folder if you want to redistribute it.

### Option 3 — Pre-built release

Pre-built executables (when available) live on the
[Releases](https://github.com/tommywaaf/scrcpy-thor-ui/releases) page.

---

## Bundled software

scrcpy-thor-ui ships with the following third-party binaries for
end-user convenience. They are unmodified.

- **scrcpy v3.3.4** — Apache License 2.0. See `bin/LICENSE_scrcpy.txt`. Source: https://github.com/Genymobile/scrcpy
- **ADB (Android Debug Bridge)** — Apache License 2.0.
- **Cal Sans** font — SIL Open Font License 1.1. See `assets/fonts/OFL.txt`.

To rebuild `bin/` from scratch, extract the
[latest scrcpy release](https://github.com/Genymobile/scrcpy/releases/tag/v3.3.4)
into the `bin/` folder.

---

## Requirements

| | |
|-|-|
| **OS** | Windows 11 (Windows 10 1809+ likely works) |
| **Python** | 3.8+ when running from source (3.10–3.12 recommended) |
| **Device** | AYN Thor with USB Debugging enabled |
| **Cable** | Data-capable USB cable; USB 3 port strongly recommended |

---

## Usage

### Connecting the device
- **USB:** Plug in the Thor and launch scrcpy-thor-ui. The first launch will require you to tap **Allow** on the device when the RSA-key prompt appears.
- **Wireless:** Launch without a USB device, click **Wireless** in the control panel, then either:
  - Pair with code (Android 11+ Wireless Debugging), or
  - Connect by `IP:5555` after enabling TCP/IP mode while still wired.

### Control panel walkthrough

| Element | What it does |
|---|---|
| `BUTTONS  ON / OFF` | Show or hide the chassis overlay (top-right of the layout area). |
| **GLOBAL SCALE** | Sets the scrcpy capture scale (0.3 — 1.0). Requires a **RESTART** to apply. |
| **TOP X / Y** | Position of the top scrcpy window inside the container. |
| **BOTTOM X / Y** | Position of the bottom scrcpy window inside the container. |
| **FPS: 30 / 60 / 120** | Cycles the scrcpy `--max-fps` cap. Requires a **RESTART** to apply. |
| **RESTART** | Cleanly relaunches the app. Use after changing **GLOBAL SCALE** or **FPS**. |
| **UNDOCK / DOCK** | Floats the two scrcpy windows free of the container (handy for individual capture in OBS), or pulls them back together. |
| **SCREENSHOT** | Copies a transparent dual-screen capture of the docked container to the clipboard. (Docked only.) |
| **WIRELESS** | Opens the wireless pairing dialog. |
| **Save / Load / Del Preset** | Layout presets stored in `config/layout.json`. |

---

## Configuration

### `config/config.json`

```json
{
  "global_scale": 0.6,
  "tx": 0,
  "ty": 0,
  "bx": 251,
  "by": 648,
  "max_fps": 60,
  "chassis_enabled": true
}
```

### `config/layout.json`

```json
{
  "Default": {
    "tx": 0,
    "ty": 0,
    "bx": 251,
    "by": 648,
    "global_scale": 0.6
  },
  "Streaming": {
    "tx": 100,
    "ty": 50,
    "bx": 300,
    "by": 700,
    "global_scale": 0.3
  }
}
```

### Logs
Daily-rotated logs land in `logs/`:
- `thorcpy_YYYYMMDD.log` — main application log.
- `thorcpy_top_YYYYMMDD_HHMMSS.log` / `thorcpy_bottom_YYYYMMDD_HHMMSS.log` — scrcpy stream logs.

Bump the level in `main.py`:

```python
logging.basicConfig(level=logging.DEBUG, ...)
```

---

## Troubleshooting

### Device not detected
- Confirm USB Debugging is on, tap **Allow** when the RSA prompt appears.
- Try a different (data-capable) cable; charge-only cables won't show as a device.
- `bin\adb.exe devices` should list `e17da8dd  device`. If it shows `unauthorized`, accept the prompt on the Thor.
- Restart the daemon: `bin\adb.exe kill-server` then `bin\adb.exe start-server`.

### Audio or video stutters intermittently
- Use a **USB 3** (blue) port. Two simultaneous H.264 streams + audio comfortably fit USB 2 but USB 3 has more headroom for the highest-quality CBR settings.
- Lower **GLOBAL SCALE** (e.g. 0.5) and **RESTART**.
- Drop **FPS** to 30 for older or low-fps games.
- Make sure no other heavy CPU task is running on the host. (scrcpy already runs at high priority but doesn't preempt all OS work.)

### Button overlay doesn't react
- Confirm USB Debugging is enabled — the live input listener depends on `adb shell getevent`.
- The chassis listens to `/dev/input/event9` (`Odin Controller`); other devices on the Thor's input bus shouldn't be picked up automatically. If it ever is, file an issue with `adb shell getevent -p` output attached.

### Windows won't dock or container looks blank
- Wait a few seconds after launch; the docking monitor needs a tick to find each scrcpy window by title.
- Toggle **DOCK / UNDOCK** once.
- Restart the application.

### Scrcpy won't start
- Confirm `bin\scrcpy.exe` is present.
- Check the latest log for errors.
- Try running scrcpy manually: `bin\scrcpy.exe -s YOUR_DEVICE_SERIAL`.

### Missing DLLs / import errors
- `pip install -r requirements.txt --force-reinstall`.
- Install the latest **Visual C++ Redistributables**.

---

## Credits & upstream

scrcpy-thor-ui is built on top of the excellent
[**ThorCPY**](https://github.com/theswest/ThorCPY) by *the_swest*,
which provided the original dual-screen Win32 docking, layout editor,
preset system, screenshot pipeline and wireless pairing flow. All of
that is preserved here under GPL v3 — please support upstream.

This fork additionally credits:

- **[scrcpy](https://github.com/Genymobile/scrcpy)** by Romain Vimont — the screen-mirroring engine that makes any of this possible.
- **[Cal Sans](https://github.com/calcom/font)** by Cal.com Inc. — UI typography (SIL OFL 1.1).
- **[Pygame](https://www.pygame.org/)** — UI rendering, font drawing, off-screen surfaces for the chassis.
- **[eldermonkey](https://github.com/eldermonkey)** — the original ThorCPY logo.
- The AYN Thor community — for documenting the Odin Controller input map.

---

## License

scrcpy-thor-ui is licensed under the **GNU General Public License
v3.0** (inherited from upstream ThorCPY). See `LICENSE` for the full
text. You're free to modify and redistribute under the same terms.

scrcpy is licensed under Apache 2.0 — see `bin/LICENSE_scrcpy.txt`.
Cal Sans is licensed under SIL OFL 1.1 — see `assets/fonts/OFL.txt`.
