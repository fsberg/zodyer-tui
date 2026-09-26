"""Källskikt mot YouTube Music.

All kontakt med ytmusicapi sker här. Resten av programmet ser bara Track.

Om autentisering: ytmusicapi stöder två filformat och de är INTE utbytbara.

* ``browser.json`` – kopierade request-headers från en inloggad session.
  Räcker med ``YTMusic(fil)``. Innehåller kontoövergripande Google-cookies
  i klartext och är giltig så länge webbläsarsessionen är det (i praktiken
  upp till ett par år om du inte loggar ut). Behandla filen som ett lösenord.
* ``oauth.json`` – sedan november 2024 kräver den ett eget Client Id och
  Secret från Google Cloud Console (OAuth-klient av typen "TVs and Limited
  Input devices"), som måste skickas som ``OAuthCredentials``. Enbart
  ``YTMusic('oauth.json')`` räcker inte.
"""

from __future__ import annotations

import json
from pathlib import Path

from ytmusicapi import YTMusic

try:  # OAuthCredentials finns från ytmusicapi 1.4
    from ytmusicapi import OAuthCredentials
except ImportError:  # pragma: no cover
    OAuthCredentials = None

from .models import Track


class NotAuthenticated(RuntimeError):
    """Kastas när en funktion kräver inloggning men ingen auth är laddad."""


class AuthConfigError(RuntimeError):
    """Auth-filen finns men går inte att använda som den är konfigurerad."""


def detect_auth_kind(path: str | Path) -> str:
    """Returnerar 'oauth', 'browser' eller 'unknown' utifrån filens innehåll."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AuthConfigError(f"Kan inte läsa auth-filen {path}: {exc}") from exc

    if not isinstance(data, dict):
        return "unknown"
    keys = {k.lower() for k in data}
    if {"access_token", "refresh_token"} & keys:
        return "oauth"
    if {"cookie", "authorization", "x-goog-authuser"} & keys:
        return "browser"
    return "unknown"


def _join_artists(item: dict) -> str:
    artists = item.get("artists") or []
    names = [a.get("name", "") for a in artists if a.get("name")]
    return ", ".join(names) or "Okänd artist"


def _to_track(item: dict) -> Track | None:
    """Översätt ett sökresultat till Track. Returnerar None om det inte går att spela."""
    video_id = item.get("videoId")
    if not video_id:
        # Album- och artistträffar saknar videoId. Hoppa över dem.
        return None

    album = item.get("album")
    album_name = album.get("name") if isinstance(album, dict) else None

    return Track(
        video_id=video_id,
        title=item.get("title", "Okänd titel"),
        artist=_join_artists(item),
        album=album_name,
        duration=item.get("duration"),
        duration_seconds=item.get("duration_seconds"),
        # Sätts bara när träffens undertext råkar innehålla ett årtal.
        year=item.get("year"),
    )


class YTMusicSource:
    """Sökning mot YouTube Music. Fungerar utan inloggning."""

    def __init__(
        self,
        auth_file: str | Path | None = None,
        oauth_client_id: str | None = None,
        oauth_client_secret: str | None = None,
    ) -> None:
        self._auth_file = str(auth_file) if auth_file else None
        self.auth_kind: str | None = None

        if not self._auth_file:
            # Oautentiserad åtkomst: sökning och publika spellistor fungerar,
            # ditt eget bibliotek gör det inte.
            self._yt = YTMusic()
            return

        self.auth_kind = detect_auth_kind(self._auth_file)

        if self.auth_kind == "oauth":
            if not (oauth_client_id and oauth_client_secret):
                raise AuthConfigError(
                    "oauth.json kräver client id och secret sedan november 2024. "
                    "Ange --oauth-client-id/--oauth-client-secret eller sätt "
                    "ZODYER_OAUTH_CLIENT_ID och ZODYER_OAUTH_CLIENT_SECRET. "
                    "Skapa en OAuth-klient av typen 'TVs and Limited Input "
                    "devices' i Google Cloud Console."
                )
            if OAuthCredentials is None:
                raise AuthConfigError(
                    "Installerad ytmusicapi saknar OAuthCredentials. Uppgradera."
                )
            self._yt = YTMusic(
                self._auth_file,
                oauth_credentials=OAuthCredentials(
                    client_id=oauth_client_id, client_secret=oauth_client_secret
                ),
            )
        elif self.auth_kind == "browser":
            self._yt = YTMusic(self._auth_file)
        else:
            raise AuthConfigError(
                f"{self._auth_file} ser varken ut som browser.json eller oauth.json."
            )

    @property
    def authenticated(self) -> bool:
        return self._auth_file is not None

    def search(self, query: str, limit: int = 25) -> list[Track]:
        """Sök låtar. Blockerande anrop – kör i en worker-tråd."""
        raw = self._yt.search(query, filter="songs", limit=limit)
        tracks = (_to_track(item) for item in raw)
        return [t for t in tracks if t is not None]

    def playlist(self, playlist_id: str, limit: int = 200) -> list[Track]:
        """Hämta spår ur en publik spellista."""
        data = self._yt.get_playlist(playlist_id, limit=limit)
        tracks = (_to_track(item) for item in data.get("tracks", []))
        return [t for t in tracks if t is not None]

    # ---- Kräver auth ----

    def liked_songs(self, limit: int = 200) -> list[Track]:
        if not self.authenticated:
            raise NotAuthenticated("Gillade låtar kräver en auth-fil.")
        data = self._yt.get_liked_songs(limit=limit)
        tracks = (_to_track(item) for item in data.get("tracks", []))
        return [t for t in tracks if t is not None]

    def library_playlists(self, limit: int = 50) -> list[dict]:
        if not self.authenticated:
            raise NotAuthenticated("Biblioteket kräver en auth-fil.")
        return self._yt.get_library_playlists(limit=limit)
