import os
from pathlib import Path
from dotenv import load_dotenv
from fetcher import SpotifyClient

load_dotenv()
c = SpotifyClient(
    os.environ["SPOTIFY_CLIENT_ID"],
    os.environ["SPOTIFY_CLIENT_SECRET"],
    os.environ["SPOTIFY_REDIRECT_URI"],
    Path(".cache/spotify_token.json"),
    False,
)
state = c.get_playback_state()
if not state:
    print("No playback state (nothing active)")
else:
    d = state.get("device", {})
    print(d.get("name"), d.get("type"), "volume:", d.get("volume_percent"), "supports_volume:", d.get("supports_volume"))
