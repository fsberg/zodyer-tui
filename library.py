"""Sparade spellistor och kö, lagrade lokalt som JSON.

Gränssnittet mot appen är avsiktligt litet — ``names``, ``save``, ``load``,
``delete``, ``save_queue``, ``load_queue``. En framtida backend mot YouTube
Musics egna spellistor kan implementera samma metoder utan att app.py ändras,
precis som ``Player`` döljer mpv och ``YTMusicSource`` döljer ytmusicapi.

Filen ligger utanför projektmappen, i samma katalog som auth-filer hör hemma.
Skrivningar är atomära: vi skriver till en temporärfil och byter namn. Ett
strömavbrott mitt i en skrivning ska inte kunna radera hela biblioteket.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .models import Track

FORMAT_VERSION = 1
#: Fler än så är inte en spellista längre, och skyddar mot att en trasig
#: eller manipulerad fil sväljer allt minne.
MAX_TRACKS = 5000
MAX_PLAYLISTS = 500


def config_dir() -> Path:
    """Katalog för användardata. Inte projektmappen – den kan ligga i git."""
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_CONFIG_HOME")
    if base:
        return Path(base) / "zodyer"
    return Path.home() / ".config" / "zodyer"


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class PlaylistStore:
    """Spellistor och senaste kö i en JSON-fil."""

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path else config_dir() / "playlists.json"
        self.last_error: str | None = None
        self._data = self._read()

    # ---- läsning och skrivning ----

    def _empty(self) -> dict:
        return {"version": FORMAT_VERSION, "playlists": {}, "queue": None}

    def _read(self) -> dict:
        if not self.path.exists():
            return self._empty()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            # Hellre ett tomt bibliotek plus en säkerhetskopia än en krasch
            # vid uppstart. Användaren kan rädda filen för hand.
            self.last_error = f"kunde inte läsa {self.path.name}: {exc}"
            self._backup_corrupt()
            return self._empty()

        if not isinstance(data, dict) or "playlists" not in data:
            self.last_error = f"{self.path.name} har oväntat innehåll"
            self._backup_corrupt()
            return self._empty()

        data.setdefault("version", FORMAT_VERSION)
        data.setdefault("queue", None)
        if not isinstance(data.get("playlists"), dict):
            data["playlists"] = {}
        return data

    def _backup_corrupt(self) -> None:
        try:
            if self.path.exists():
                self.path.replace(self.path.with_suffix(".json.trasig"))
        except OSError:
            pass

    def _write(self) -> bool:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self.path.parent, suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(self._data, fh, ensure_ascii=False, indent=1)
            os.replace(tmp, self.path)
            return True
        except OSError as exc:
            self.last_error = f"kunde inte spara: {exc}"
            return False

    # ---- spellistor ----

    def names(self) -> list[str]:
        """Namn sorterade efter när de senast ändrades, nyast först."""
        objekt = self._data["playlists"]
        return sorted(objekt, key=lambda n: objekt[n].get("updated", ""), reverse=True)

    def info(self, name: str) -> dict | None:
        return self._data["playlists"].get(name)

    def count(self, name: str) -> int:
        data = self.info(name)
        return len(data.get("tracks", [])) if data else 0

    def save(self, name: str, tracks: list[Track]) -> bool:
        name = name.strip()
        if not name:
            self.last_error = "spellistan behöver ett namn"
            return False
        if not tracks:
            self.last_error = "kön är tom"
            return False
        if name not in self._data["playlists"] and len(self._data["playlists"]) >= MAX_PLAYLISTS:
            self.last_error = f"max {MAX_PLAYLISTS} spellistor"
            return False

        self._data["playlists"][name] = {
            "updated": _now(),
            "tracks": [_to_dict(t) for t in tracks[:MAX_TRACKS]],
        }
        return self._write()

    def load(self, name: str) -> list[Track]:
        data = self._data["playlists"].get(name)
        if not data:
            return []
        return _from_dicts(data.get("tracks", []))

    def delete(self, name: str) -> bool:
        if name not in self._data["playlists"]:
            return False
        del self._data["playlists"][name]
        return self._write()

    def rename(self, old: str, new: str) -> bool:
        new = new.strip()
        if old not in self._data["playlists"] or not new:
            return False
        self._data["playlists"][new] = self._data["playlists"].pop(old)
        self._data["playlists"][new]["updated"] = _now()
        return self._write()

    # ---- kön mellan sessioner ----

    def save_queue(self, tracks: list[Track], index: int = -1) -> bool:
        if not tracks:
            self._data["queue"] = None
        else:
            self._data["queue"] = {
                "updated": _now(),
                "index": max(-1, min(index, len(tracks) - 1)),
                "tracks": [_to_dict(t) for t in tracks[:MAX_TRACKS]],
            }
        return self._write()

    def load_queue(self) -> tuple[list[Track], int]:
        data = self._data.get("queue")
        if not isinstance(data, dict):
            return [], -1
        tracks = _from_dicts(data.get("tracks", []))
        index = data.get("index", -1)
        if not isinstance(index, int) or not -1 <= index < len(tracks):
            index = -1
        return tracks, index


# ---- serialisering ----
# Ligger här och inte i models.py: Track ska förbli fri från kunskap om
# hur den råkar lagras.


def _to_dict(track: Track) -> dict:
    return {
        "video_id": track.video_id,
        "title": track.title,
        "artist": track.artist,
        "album": track.album,
        "duration": track.duration,
        "duration_seconds": track.duration_seconds,
        "year": track.year,
    }


def _from_dicts(rows) -> list[Track]:
    """Hoppa tyst över poster som inte går att tolka – en trasig rad ska
    inte ta hela spellistan med sig."""
    tracks: list[Track] = []
    if not isinstance(rows, list):
        return tracks
    for row in rows[:MAX_TRACKS]:
        if not isinstance(row, dict):
            continue
        video_id = row.get("video_id")
        title = row.get("title")
        if not isinstance(video_id, str) or not isinstance(title, str):
            continue
        sekunder = row.get("duration_seconds")
        tracks.append(
            Track(
                video_id=video_id,
                title=title,
                artist=row.get("artist") or "Okänd artist",
                album=row.get("album"),
                duration=row.get("duration"),
                duration_seconds=sekunder if isinstance(sekunder, int) else None,
                year=row.get("year"),
            )
        )
    return tracks
