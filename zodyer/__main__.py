"""Startpunkt: python -m zodyer"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .app import ZodyerApp
from .player import Player
from .source import AuthConfigError, YTMusicSource


def default_auth_dir() -> Path:
    """Auth-filer hör hemma utanför projektmappen – de innehåller credentials."""
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_CONFIG_HOME")
    if base:
        return Path(base) / "zodyer"
    return Path.home() / ".config" / "zodyer"


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
                f"{default_auth_dir()} eller lägg den i .gitignore."
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

    try:
        player = Player(mpv_location=args.mpv, volume=args.volume)
    except Exception as exc:
        print(f"Kunde inte starta mpv: {exc}", file=sys.stderr)
        if args.mpv:
            print(f"Sökväg som användes: {args.mpv}", file=sys.stderr)
            print("Kontrollera att filen går att köra.", file=sys.stderr)
        else:
            print("Kontrollera att mpv finns i PATH, eller ange --mpv.", file=sys.stderr)
        return 1

    try:
        ZodyerApp(player, source).run()
    finally:
        player.terminate()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
