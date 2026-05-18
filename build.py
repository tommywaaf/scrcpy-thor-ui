# scrcpy-thor-ui - Dual-screen scrcpy launcher with virtual controller overlay.
# Fork of ThorCPY by the_swest. Licensed under the GNU GPL v3.

# build.py - PyInstaller bundling for scrcpy-thor-ui.

import PyInstaller.__main__

PyInstaller.__main__.run(
    [
        "main.py",
        "--onefile",
        "--noconsole",
        "--clean",
        "--name=scrcpy-thor-ui",
        "--add-data=config;config",
        "--add-data=bin;bin",
        "--add-data=logs;logs",
        "--add-data=assets/fonts;assets/fonts",
        "--add-data=assets/icon.png;assets",
        "--add-data=assets/scrcpy-thor-ui-logo.png;assets",
        "--icon=assets/scrcpy-thor-ui.ico",
    ]
)
