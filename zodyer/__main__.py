"""Startpunkt: python -m zodyer"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

from .app import ZodyerApp
from .library import config_dir
from .locate import find_mpv
from .player import Player
from .source import AuthConfigError, YTMusicSource


def warn_if_auth_in_repo(path: Path) -> str | None:
    """Varna om auth-filen ligger där den riskerar att committas."""
    try:
        resolved = path.resolve()
    except OSError:
        return None
    for parent in [resolved.parent, *resolved.parents]:
        if (parent / ".git").exists():
            return (
                f"Varning: {resolved} ligger i ett git-arbetsträd ({parent}). "
                "Filen innehåller en giltig Google-session. Flytta den till "
                f"{config_dir()} eller lägg den i .gitignore."
            )
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="zodyer", description="Terminalklient för YouTube Music")
    parser.add_argument("--auth", help="Sökväg till ytmusicapi-auth-fil (browser.json eller oauth.json)")
    parser.add_argument("--oauth-client-id", default=os.environ.get("ZODYER_OAUTH_CLIENT_ID"),
                        help="Krävs för oauth.json. Kan även sättas via ZODYER_OAUTH_CLIENT_ID.")
    parser.add_argument("--oauth-client-secret", default=os.environ.get("ZODYER_OAUTH_CLIENT_SECRET"),
                        help="Krävs för oauth.json. Kan även sättas via ZODYER_OAUTH_CLIENT_SECRET.")
    parser.add_argument("--mpv", help="Sökväg till mpv.exe om den inte ligger i PATH")
    parser.add_argument("--volume", type=int, default=70, help="Startvolym (0-130)")
    args = parser.parse_args(argv)

    if not 0 <= args.volume <= 130:
        print("--volume måste vara mellan 0 och 130.", file=sys.stderr)
        return 2

    if args.auth:
        auth_path = Path(args.auth)
        if not auth_path.exists():
            print(f"Auth-filen hittades inte: {auth_path}", file=sys.stderr)
            return 1
        warning = warn_if_auth_in_repo(auth_path)
        if warning:
            print(warning, file=sys.stderr)

    try:
        source = YTMusicSource(
            args.auth,
            oauth_client_id=args.oauth_client_id,
            oauth_client_secret=args.oauth_client_secret,
        )
    except AuthConfigError as exc:
        print(f"Auth-fel: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Kunde inte initiera YouTube Music: {exc}", file=sys.stderr)
        return 1

    mpv_path = args.mpv
    if not mpv_path:
        found = find_mpv()
        if not found:
            print("mpv hittades varken i PATH eller på de vanliga platserna.", file=sys.stderr)
            print("Installera:  winget install --id shinchiro.mpv --exact", file=sys.stderr)
            print('eller ange:  --mpv "C:\\sökväg\\mpv.exe"', file=sys.stderr)
            return 1
        mpv_path = str(found)

    # mpv:s ytdl-hook anropar yt-dlp själv och tar ingen sökväg från oss.
    # Utan den startar allt, men varje låt misslyckas tyst.
    if not shutil.which("yt-dlp"):
        print("yt-dlp hittades inte i PATH.", file=sys.stderr)
        print("Installera:  winget install --id yt-dlp.yt-dlp --exact", file=sys.stderr)
        print("och öppna sedan ett nytt terminalfönster.", file=sys.stderr)
        return 1

    try:
        player = Player(mpv_location=mpv_path, volume=args.volume)
    except Exception as exc:
        print(f"Kunde inte starta mpv: {exc}", file=sys.stderr)
        print(f"Sökväg som användes: {mpv_path}", file=sys.stderr)
        print("Kontrollera att filen går att köra.", file=sys.stderr)
        return 1

    try:
        ZodyerApp(player, source).run()
    finally:
        player.terminate()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
