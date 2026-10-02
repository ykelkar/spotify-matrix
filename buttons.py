#!/usr/bin/env python3
import json
import os
import subprocess
import threading
import time
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

TOKEN_SYMLINK = Path(".cache/spotify_token.json")
TOKENS_DIR = Path(".cache/tokens")
USERS_FILE = Path("users.json")
FLASH_PATH = Path("/tmp/spotify_matrix_flash.json")
FLASH_DURATION = 2.5
TAP_WINDOW = 1.2

play_pause_button = Button(GPIO_19, hold_time=2)
next_button = Button(GPIO_20)
previous_button = Button(GPIO_16)

held = False
tap_count = 0
tap_timer = None


def load_users() -> dict:
    with USERS_FILE.open("r", encoding="utf-8") as f:
        return json.load(f)


def current_user_name() -> str | None:
    try:
        target = os.readlink(TOKEN_SYMLINK)
        return Path(target).stem
    except OSError:
        return None


def write_flash(text: str) -> None:
    tmp_path = FLASH_PATH.with_suffix(".json.tmp")
    tmp_path.write_text(
        json.dumps({"text": text, "written_at": time.time()})
    )
    tmp_path.replace(FLASH_PATH)


def cycle_active_user() -> None:
    try:
        users = load_users()
    except Exception as exc:
        print(f"Could not load users.json: {exc}")
        return

    order = users.get("order", [])
    if not order:
        print("No users configured in users.json")
        return

    current = current_user_name()
    idx = (order.index(current) + 1) % len(order) if current in order else 0
    next_user = order[idx]

    token_file = TOKENS_DIR / f"{next_user}.json"
    if not token_file.exists():
        print(f"No token found for '{next_user}' yet — skipping")
        return

    if TOKEN_SYMLINK.exists() or TOKEN_SYMLINK.is_symlink():
        TOKEN_SYMLINK.unlink()
    TOKEN_SYMLINK.symlink_to(Path("tokens") / f"{next_user}.json")

    # buttons.py runs as root; keep token files writable by the yashkelkar-run services too.
    subprocess.run(["chown", "-R", "yashkelkar:yashkelkar", str(TOKENS_DIR)], check=False)

    display_name = users.get("display", {}).get(next_user, next_user.upper()[:4])
    write_flash(display_name)
    print(f"Switched active user to {next_user}")

    subprocess.Popen(
        ["systemctl", "restart", "spotify-fetcher", "spotify-encoder"],
        start_new_session=True,
    )


def handle_tap_timeout() -> None:
    global tap_count
    count = tap_count
    tap_count = 0

    if count == 1:
        print("Short press — toggling playback")
        try:
            spotify.toggle_playback()
        except Exception as exc:
            print(f"Toggle failed: {exc}")
    elif count >= 3:
        cycle_active_user()
    # count == 2 is reserved / no-op


def on_held():
    global held
    held = True
    print("Long press detected — shutting down")
    subprocess.run(["shutdown", "now"])


def on_play_pause_released():
    global held, tap_count, tap_timer
    if held:
        held = False
        return

    tap_count += 1
    if tap_timer:
        tap_timer.cancel()
    tap_timer = threading.Timer(TAP_WINDOW, handle_tap_timeout)
    tap_timer.daemon = True
    tap_timer.start()


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
