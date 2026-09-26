"""Datamodeller. Håll dessa fria från beroenden mot ytmusicapi och mpv."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Track:
    """Ett spelbart spår, oberoende av var det kom ifrån."""

    video_id: str
    title: str
    artist: str
    album: str | None = None
    duration: str | None = None
    duration_seconds: int | None = None
    #: YouTube Music anger sällan år för låtträffar – oftast None.
    year: str | None = None

    @property
    def url(self) -> str:
        # mpv:s ytdl-hook skickar detta vidare till yt-dlp.
        return f"https://music.youtube.com/watch?v={self.video_id}"

    def __str__(self) -> str:
        return f"{self.artist} – {self.title}"
