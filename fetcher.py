#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import json
import os
import secrets
import tempfile
import threading
import time
import urllib.parse
import webbrowser
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from io import BytesIO
from pathlib import Path
from typing import Any

import requests
from PIL import Image

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv() -> None:
        return None


AUTH_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
CURRENTLY_PLAYING_URL = "https://api.spotify.com/v1/me/player/currently-playing"
SCOPE = "user-read-currently-playing user-modify-playback-state user-read-playback-state"

DEFAULT_STATE_PATH = Path("/tmp/spotify_matrix_state.json")
DEFAULT_ART_PATH = Path("/tmp/spotify_matrix_art.png")

# CHANGE: one shared, persistent connection pool reused across every poll,
# instead of opening a fresh connection (and TLS handshake) each time.
_session = requests.Session()


@dataclass
class PlaybackArt:
    key: str
    image_url: str
    is_playing: bool


@dataclass
class HttpResponse:
    status: int
    headers: Any
    body: bytes

    def json(self) -> dict[str, Any]:
        return json.loads(self.body.decode("utf-8"))


def http_request(
    method: str,
    url: str,
    *,
    params: dict[str, str] | None = None,
    data: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 10,
) -> HttpResponse:
    # CHANGE: uses the shared _session (keep-alive) instead of urllib.request,
    # which opened a brand-new connection on every call.
    response = _session.request(
        method,
        url,
        params=params,
        data=data,
        headers=headers or {},
        timeout=timeout,
    )
    return HttpResponse(response.status_code, response.headers, response.content)


def raise_http_error(response: HttpResponse, context: str) -> None:
    body = response.body.decode("utf-8", errors="replace")
    raise RuntimeError(f"{context} failed with HTTP {response.status}: {body}")


class SpotifyClient:
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        token_cache: Path,
        open_browser: bool,
    ) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self.token_cache = token_cache
        self.open_browser = open_browser
        self.token = self._load_token()

    def get_currently_playing(self) -> dict[str, Any] | None:
        token = self._valid_access_token()
        response = http_request(
            "GET",
            CURRENTLY_PLAYING_URL,
            params={"additional_types": "track,episode"},
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )

        if response.status == 204:
            return None
        if response.status == 401:
            self._refresh_access_token()
            return self.get_currently_playing()
        if response.status == 429:
            retry_after = int(response.headers.get("Retry-After", "5"))
            time.sleep(max(retry_after, 1))
            return None
        if response.status != 200:
            raise_http_error(response, "Spotify currently-playing request")

        return response.json()

    def toggle_playback(self) -> None:
        playback = self.get_currently_playing()
        is_playing = bool(playback and playback.get("is_playing"))
        endpoint = "pause" if is_playing else "play"

        token = self._valid_access_token()
        response = http_request(
            "PUT",
            f"https://api.spotify.com/v1/me/player/{endpoint}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        if response.status == 401:
            self._refresh_access_token()
            return self.toggle_playback()
        if response.status not in (200, 204):
            raise_http_error(response, f"Spotify {endpoint} request")

    def next_track(self) -> None:
        token = self._valid_access_token()
        response = http_request(
            "POST",
            "https://api.spotify.com/v1/me/player/next",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        if response.status == 401:
            self._refresh_access_token()
            return self.next_track()
        if response.status not in (200, 204):
            raise_http_error(response, "Spotify next request")

    def previous_track(self) -> None:
        token = self._valid_access_token()
        response = http_request(
            "POST",
            "https://api.spotify.com/v1/me/player/previous",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        if response.status == 401:
            self._refresh_access_token()
            return self.previous_track()
        if response.status not in (200, 204):
            raise_http_error(response, "Spotify previous request")

    def set_volume(self, percent: int) -> None:
        percent = max(0, min(100, percent))
        token = self._valid_access_token()
        response = http_request(
            "PUT",
            "https://api.spotify.com/v1/me/player/volume",
            params={"volume_percent": str(percent)},
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        if response.status == 401:
            self._refresh_access_token()
            return self.set_volume(percent)
        if response.status not in (200, 204):
            raise_http_error(response, "Spotify volume request")

    def get_playback_state(self) -> dict[str, Any] | None:
        token = self._valid_access_token()
        response = http_request(
            "GET",
            "https://api.spotify.com/v1/me/player",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        if response.status == 204:
            return None
        if response.status == 401:
            self._refresh_access_token()
            return self.get_playback_state()
        if response.status != 200:
            raise_http_error(response, "Spotify playback-state request")
        return response.json()

    def authorize(self) -> None:
        self._valid_access_token()

    def _valid_access_token(self) -> str:
        if not self.token:
            self.token = self._authorize()

        if time.time() >= float(self.token.get("expires_at", 0)):
            self._refresh_access_token()

        return str(self.token["access_token"])

    def _load_token(self) -> dict[str, Any] | None:
        if not self.token_cache.exists():
            return None

        with self.token_cache.open("r", encoding="utf-8") as token_file:
            return json.load(token_file)

    def _save_token(self, token: dict[str, Any]) -> None:
        self.token_cache.parent.mkdir(parents=True, exist_ok=True)
        token["expires_at"] = time.time() + int(token.get("expires_in", 3600)) - 60

        previous_refresh_token = self.token.get("refresh_token") if self.token else None
        if previous_refresh_token and "refresh_token" not in token:
            token["refresh_token"] = previous_refresh_token

        with self.token_cache.open("w", encoding="utf-8") as token_file:
            json.dump(token, token_file, indent=2)

        self.token = token

    def _authorize(self) -> dict[str, Any]:
        state = secrets.token_urlsafe(18)
        parsed_redirect = urllib.parse.urlparse(self.redirect_uri)
        if parsed_redirect.hostname not in {"127.0.0.1", "localhost"}:
            raise RuntimeError("This script expects a localhost Spotify redirect URI.")

        callback = LocalCallbackServer(
            host=parsed_redirect.hostname or "127.0.0.1",
            port=parsed_redirect.port or 80,
            path=parsed_redirect.path or "/callback",
            expected_state=state,
        )

        query = urllib.parse.urlencode(
            {
                "client_id": self.client_id,
                "response_type": "code",
                "redirect_uri": self.redirect_uri,
                "scope": SCOPE,
                "state": state,
            }
        )
        auth_url = f"{AUTH_URL}?{query}"

        print("Authorize Spotify in your browser:")
        print(auth_url)
        if self.open_browser:
            webbrowser.open(auth_url)

        code = callback.wait_for_code()
        token = self._post_token(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self.redirect_uri,
            }
        )
        self._save_token(token)
        return token

    def _refresh_access_token(self) -> None:
        refresh_token = self.token.get("refresh_token") if self.token else None
        if not refresh_token:
            self.token = self._authorize()
            return

        token = self._post_token(
            {
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            }
        )
        self._save_token(token)

    def _post_token(self, data: dict[str, str]) -> dict[str, Any]:
        credentials = f"{self.client_id}:{self.client_secret}".encode("utf-8")
        basic_auth = base64.b64encode(credentials).decode("ascii")
        response = http_request(
            "POST",
            TOKEN_URL,
            data=data,
            headers={
                "Authorization": f"Basic {basic_auth}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            timeout=10,
        )
        if response.status != 200:
            raise_http_error(response, "Spotify token request")
        return response.json()


class LocalCallbackServer:
    def __init__(self, host: str, port: int, path: str, expected_state: str) -> None:
        self.code: str | None = None
        self.error: str | None = None
        self.state_error: str | None = None
        self.path = path
        self.expected_state = expected_state

        parent = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                parsed = urllib.parse.urlparse(self.path)
                params = urllib.parse.parse_qs(parsed.query)

                if parsed.path != parent.path:
                    self.send_response(404)
                    self.end_headers()
                    self.wfile.write(b"Wrong callback path.")
                    return

                returned_state = params.get("state", [""])[0]
                if returned_state != parent.expected_state:
                    parent.state_error = "Spotify callback state did not match."
                    self.send_response(400)
                    self.end_headers()
                    self.wfile.write(b"State mismatch.")
                    return

                if "error" in params:
                    parent.error = params["error"][0]
                    self.send_response(400)
                    self.end_headers()
                    self.wfile.write(b"Spotify authorization failed.")
                    return

                parent.code = params.get("code", [None])[0]
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"Spotify authorization complete. You can close this tab.")

            def log_message(self, format: str, *args: Any) -> None:
                return

        self.server = HTTPServer((host, port), Handler)

    def wait_for_code(self) -> str:
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        try:
            while not self.code and not self.error and not self.state_error:
                time.sleep(0.1)
        finally:
            self.server.shutdown()
            self.server.server_close()

        if self.state_error:
            raise RuntimeError(self.state_error)
        if self.error:
            raise RuntimeError(f"Spotify authorization failed: {self.error}")
        if not self.code:
            raise RuntimeError("Spotify authorization did not return a code.")
        return self.code


def playback_art_from_response(playback: dict[str, Any] | None) -> PlaybackArt | None:
    if not playback:
        return None

    item = playback.get("item")
    if not item:
        return None

    item_type = item.get("type")
    if item_type == "track":
        images = item.get("album", {}).get("images", [])
    else:
        images = item.get("images", [])

    if not images:
        return None

    image = max(images, key=lambda candidate: candidate.get("width") or 0)
    item_id = item.get("id") or item.get("uri") or image["url"]
    return PlaybackArt(
        key=str(item_id),
        image_url=image["url"],
        is_playing=bool(playback.get("is_playing")),
    )


def atomic_write_bytes(path: Path, data: bytes) -> None:
    directory = path.parent
    directory.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=str(directory), prefix=".tmp-", suffix=path.suffix)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp_path, 0o644)
        os.replace(tmp_path, str(path))
    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


def atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    atomic_write_bytes(path, json.dumps(data).encode("utf-8"))


def encode_png(image: Image.Image) -> bytes:
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def download_and_decode_image(url: str) -> Image.Image:
    # CHANGE: also reuses the shared _session instead of opening a new connection.
    response = _session.get(url, timeout=15)
    response.raise_for_status()
    return Image.open(BytesIO(response.content)).convert("RGB")


def run(args: argparse.Namespace) -> None:
    load_dotenv()

    client_id = os.environ.get("SPOTIFY_CLIENT_ID")
    client_secret = os.environ.get("SPOTIFY_CLIENT_SECRET")
    redirect_uri = os.environ.get("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8888/callback")

    missing = [
        name
        for name, value in (
            ("SPOTIFY_CLIENT_ID", client_id),
            ("SPOTIFY_CLIENT_SECRET", client_secret),
            ("SPOTIFY_REDIRECT_URI", redirect_uri),
        )
        if not value
    ]
    if missing:
        raise SystemExit(f"Missing required environment values: {', '.join(missing)}")

    spotify = SpotifyClient(
        client_id=client_id or "",
        client_secret=client_secret or "",
        redirect_uri=redirect_uri,
        token_cache=args.token_cache,
        open_browser=not args.no_browser,
    )

    if args.auth_only:
        spotify.authorize()
        print(f"Spotify token cached at {args.token_cache}")
        return

    last_status: str | None = None
    last_key: str | None = None

    while True:
        try:
            playback = spotify.get_currently_playing()
            art = playback_art_from_response(playback)

            if art:
                if art.key != last_key:
                    image = download_and_decode_image(art.image_url)
                    atomic_write_bytes(args.art_path, encode_png(image))
                    last_key = art.key

                atomic_write_json(
                    args.state_path,
                    {"art_key": art.key, "is_playing": art.is_playing, "has_art": True},
                )
                status = f"art found, is_playing={art.is_playing}"
            else:
                atomic_write_json(
                    args.state_path,
                    {"art_key": None, "is_playing": False, "has_art": False},
                )
                last_key = None
                status = "no currently playing item"

            if status != last_status:
                print(f"Spotify: {status}", flush=True)
                last_status = status
        except Exception as exc:
            print(f"Spotify poll failed: {exc}", flush=True)

        if args.once:
            break
        time.sleep(args.poll_seconds)


def positive_float(value: str) -> float:
    parsed = float(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Poll Spotify and write album art + state for the display process.")
    # CHANGE: default lowered from 2.0 to 1.0 seconds for faster song-change detection.
    parser.add_argument("--poll-seconds", type=positive_float, default=1.0)
    parser.add_argument("--token-cache", type=Path, default=Path(".cache/spotify_token.json"))
    parser.add_argument("--state-path", type=Path, default=DEFAULT_STATE_PATH)
    parser.add_argument("--art-path", type=Path, default=DEFAULT_ART_PATH)
    parser.add_argument("--auth-only", action="store_true")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--no-browser", action="store_true")
    return parser


if __name__ == "__main__":
    run(build_parser().parse_args())
