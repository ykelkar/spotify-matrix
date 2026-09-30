# Spotify Matrix

Shows the current Spotify album art on a 64x64 RGB matrix as a circular record. The album art is the record surface itself: it is cropped to a disk, spun while Spotify reports playback as active, and left stopped at the current angle when paused.

This uses Spotify's Web API `currently-playing` endpoint, not the browser-only Web Playback SDK. The first run opens Spotify OAuth, then the script stores a refresh token in `.cache/spotify_token.json`.

## Files

- `spotify_matrix.py` - Pi runtime script.
- `.env` - local Spotify credentials, ignored by Git.
- `.env.example` - template for recreating local config.
- `requirements.txt` - Python dependencies, excluding the hardware-specific RGB matrix bindings.

## Raspberry Pi setup

Install the RGB matrix Python bindings from the `hzeller/rpi-rgb-led-matrix` project for your HAT/wiring, then install this project's dependencies:

```bash
python3 -m venv .venv --system-site-packages
source .venv/bin/activate
pip install -r requirements.txt
```

The `--system-site-packages` flag is useful if the `rgbmatrix` bindings were installed system-wide.

This install sometimes crashes the raspberry pi zero, I had to do some fancy workarounds. Might be easier to use a pi with more memory!

## Spotify setup

In the Spotify developer dashboard, make sure this redirect URI is allowlisted exactly:

```text
http://127.0.0.1:8888/callback
```

For a headless Pi, forward the callback port from your computer:

```bash
ssh -L 8888:127.0.0.1:8888 pi@raspberrypi.local
```

Then run the script on the Pi and open the printed authorization URL in your local browser.

## Run

This is the working command to run the script on your raspberry pi:

```bash
sudo -E .venv/bin/python spotify_matrix.py \
  --rows 64 \
  --cols 64 \
  --chain-length 1 \
  --parallel 1 \
  --gpio-slowdown 4 \
  --no-hardware-pulse \
  --hardware-mapping adafruit-hat
```

Useful hardware options:

```bash
sudo -E .venv/bin/python spotify_matrix.py \
  --hardware-mapping regular \
  --gpio-slowdown 2 \
  --brightness 65
```

For a non-Pi test that writes one PNG frame instead of using matrix hardware:

```bash
python spotify_matrix.py --mock-output /tmp/spotify-matrix-frame.png --once
```

To verify the album art is what spins on the disk, render four local preview frames:

```bash
python spotify_matrix.py --preview-frames /tmp/spotify-matrix-preview
```

## Hardware controls

Three momentary push buttons and one rotary encoder (KY-040 style) are wired to the SEENGREAT breakout header, all handled by `buttons.py` and `encoder.py`.

### Buttons (`buttons.py`)

| Button | GPIO (BCM) | Short press | Long press (2s+) |
|---|---|---|---|
| Play/Pause | 19 | Toggle playback | Clean shutdown (`sudo shutdown now`) |
| Next | 20 | Skip to next track | - |
| Previous | 16 | Skip to previous track | - |

### Volume encoder (`encoder.py`)

| Signal | GPIO (BCM) |
|---|---|
| CLK | 21 |
| DT | 13 |
| SW (push-button) | 6 |

- Rotate clockwise/counter-clockwise: volume up/down in 5% steps.
- Press the knob: mute/unmute (remembers the volume to restore to).

Volume changes are batched (see `TICK` and `STEPS_PER_CLICK` in `encoder.py`) rather than firing one API call per encoder step, to avoid Spotify's rate limit on fast spins.

**Note on GPIO pin choice:** with `--hardware-mapping regular`, the matrix library uses GPIO 17, 18, 22, 23, 24, 25 for panel control. Do not wire buttons or the encoder to those pins — they will conflict with the display and cause flicker or garbage input.

## Limitations

- **Play/pause/skip/volume/mute all require an active Spotify device.** If nothing is currently playing anywhere, the first press after a cold start returns a 404 (`NO_ACTIVE_DEVICE`). Start playback on any device first, then the buttons/knob will control it.
- **Volume control depends on the playing device supporting it.** Spotify's iOS and Android apps report `supports_volume: False` and reject remote volume/mute commands (`403 VOLUME_CONTROL_DISALLOW`), even when playing to a Bluetooth speaker. This works reliably from the Spotify desktop app or a native Spotify Connect speaker (Sonos, Chromecast, etc.), but not from a phone.
- **Skip-previous can return a 403** if Spotify's own "restart vs. skip back" restriction kicks in near the start of a track. This is normal Spotify behavior, not a bug.
- **The app is registered in Spotify's Development Mode**, so only accounts explicitly added under the app's dashboard (Settings -> Users and Access) can authorize it, up to 25 people.
- **Only one Spotify account is "active" at a time.** Tokens for multiple people are supported via `.cache/tokens/<name>.json` and `switch_user.sh <name>`, but switching requires restarting the services — there's no automatic multi-user detection yet.

## Running on boot (systemd)

Four services keep everything running automatically after power-on, with no SSH required:

- `spotify-fetcher.service` - polls Spotify and writes album art/state (`fetcher.py`)
- `spotify-display.service` - renders the LED matrix (`display.py`)
- `spotify-buttons.service` - play/pause/next/previous (`buttons.py`)
- `spotify-encoder.service` - volume/mute knob (`encoder.py`)

Unit files live in `/etc/systemd/system/`. To check status or logs:

```bash
sudo systemctl status spotify-fetcher spotify-display spotify-buttons spotify-encoder
sudo journalctl -u spotify-buttons -u spotify-encoder -n 50 --no-pager
```

To restart everything (e.g. after changing `.env` or a script):

```bash
sudo systemctl restart spotify-fetcher spotify-display spotify-buttons spotify-encoder
```

### Shutting down safely

Do not unplug power directly. Long-press the play/pause button (GPIO 19) to trigger a clean shutdown, wait for the green activity LED to go dark and stay off, then it's safe to disconnect power.
