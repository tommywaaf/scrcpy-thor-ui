# scrcpy-thor-ui - Dual-screen scrcpy launcher with virtual controller overlay.
# Fork of ThorCPY by the_swest. Licensed under the GNU GPL v3.
#
# build.py - PyInstaller bundling for scrcpy-thor-ui.
#
# We ship as a `--onedir` build instead of `--onefile` because
# `--onefile` extracts the entire bundle to a fresh `%TEMP%\_MEI...`
# folder on every launch, which Windows Defender then real-time-scans
# block by block - that scanning was the source of the audio/video
# stutter on the .exe build that didn't exist when running from source.
# `--onedir` keeps everything in a permanent folder beside the .exe,
# Defender scans it once, and subsequent launches run at trusted speed.

import PyInstaller.__main__

PyInstaller.__main__.run(
    [
        "main.py",
        "--onedir",
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
