#!/usr/bin/env python3
import os
import threading
import time
from pathlib import Path

from dotenv import load_dotenv
from gpiozero import Button, RotaryEncoder

from fetcher import SpotifyClient

load_dotenv()

spotify = SpotifyClient(
    client_id=os.environ["SPOTIFY_CLIENT_ID"],
    client_secret=os.environ["SPOTIFY_CLIENT_SECRET"],
    redirect_uri=os.environ["SPOTIFY_REDIRECT_URI"],
    token_cache=Path(".cache/spotify_token.json"),
    open_browser=False,
)

GPIO_CLK = 21
GPIO_DT = 13
GPIO_SW = 6

VOLUME_STEP = 5        # percent per click
STEPS_PER_CLICK = 4    # encoder steps per physical detent (tune if needed)
TICK = 0.15            # seconds between batched updates
RESYNC_AFTER = 10      # re-read real volume after this many idle seconds

encoder = RotaryEncoder(GPIO_CLK, GPIO_DT, max_steps=0)
mute_button = Button(GPIO_SW, bounce_time=0.05)

lock = threading.Lock()
volume = None              # locally tracked volume (None = unknown)
last_activity = 0.0
last_volume_before_mute = None


def read_volume():
    state = spotify.get_playback_state()
    if not state or "device" not in state:
        return None
    return state["device"].get("volume_percent")


def on_mute_toggle():
    global volume, last_volume_before_mute, last_activity
    with lock:
        try:
            if volume is not None and time.time() - last_activity < RESYNC_AFTER:
                current = volume
            else:
                current = read_volume()
            if current is None:
                print("No active device or volume info")
                return
            if current == 0:
                restore_to = last_volume_before_mute or 50
                spotify.set_volume(restore_to)
                volume = restore_to
                print(f"Unmuted, restored to {restore_to}%")
            else:
                last_volume_before_mute = current
                spotify.set_volume(0)
                volume = 0
                print("Muted")
            last_activity = time.time()
        except Exception as exc:
            volume = None
            print(f"Mute toggle failed: {exc}")


mute_button.when_pressed = on_mute_toggle

print("Encoder listener running. Ctrl+C to stop.")

last_steps = 0
pending = 0.0  # accumulated percent not yet applied

try:
    while True:
        time.sleep(TICK)
        steps = encoder.steps
        delta = steps - last_steps
        last_steps = steps
        if delta:
            pending += delta * VOLUME_STEP / STEPS_PER_CLICK

        change = int(pending / VOLUME_STEP) * VOLUME_STEP
        if change == 0:
            continue

        with lock:
            now = time.time()
            try:
                if volume is None or now - last_activity > RESYNC_AFTER:
                    volume = read_volume()
                if volume is None:
                    print("No active device or volume info")
                    pending = 0.0
                    continue
                new_volume = max(0, min(100, volume + change))
                pending -= change
                if new_volume != volume:
                    spotify.set_volume(new_volume)
                    print(f"Volume: {volume}% -> {new_volume}%")
                    volume = new_volume
                last_activity = time.time()
            except Exception as exc:
                print(f"Volume adjust failed: {exc}")
                volume = None
                pending = 0.0
                time.sleep(2)  # back off, likely rate limited
except KeyboardInterrupt:
    pass
