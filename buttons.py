#!/usr/bin/env python3
import os
import subprocess
from pathlib import Path

from dotenv import load_dotenv
from gpiozero import Button
from signal import pause

from fetcher import SpotifyClient

load_dotenv()

spotify = SpotifyClient(
    client_id=os.environ["SPOTIFY_CLIENT_ID"],
    client_secret=os.environ["SPOTIFY_CLIENT_SECRET"],
    redirect_uri=os.environ["SPOTIFY_REDIRECT_URI"],
    token_cache=Path(".cache/spotify_token.json"),
    open_browser=False,
)

GPIO_19 = 19
GPIO_20 = 20
GPIO_16 = 16

play_pause_button = Button(GPIO_19, hold_time=2)
next_button = Button(GPIO_20)
previous_button = Button(GPIO_16)

held = False


def on_held():
    global held
    held = True
    print("Long press detected — shutting down")
    subprocess.run(["sudo", "shutdown", "now"])


def on_play_pause_released():
    global held
    if held:
        held = False
        return
    print("Short press — toggling playback")
    try:
        spotify.toggle_playback()
    except Exception as exc:
        print(f"Toggle failed: {exc}")


def on_next():
    print("Next track")
    try:
        spotify.next_track()
    except Exception as exc:
        print(f"Next failed: {exc}")


def on_previous():
    print("Previous track")
    try:
        spotify.previous_track()
    except Exception as exc:
        print(f"Previous failed: {exc}")


play_pause_button.when_held = on_held
play_pause_button.when_released = on_play_pause_released
next_button.when_pressed = on_next
previous_button.when_pressed = on_previous

print("Button listener running. Ctrl+C to stop.")
pause()
