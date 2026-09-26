"""Start- och underhållsskript för zodyer.

    python start.py            normal start
    python start.py --update   tvinga ominstallation av beroenden + yt-dlp
    python start.py --offline  hoppa över allt som rör nätet
    python start.py --check    kontrollera miljön, starta inte
    python start.py --mpv "C:\\Program Files\\MPV Player\\mpv.exe"
    python start.py -- --volume 50    allt efter -- går vidare till zodyer

Designval värda att känna till:

* Beroendena installeras bara när ``requirements.txt`` faktiskt ändrats.
  Vi jämför en hash, inte versionsnummer mot PyPI. Det gör starten snabb och
  gör att programmet fungerar utan nät.
* Vi kör aldrig ``pip install --upgrade`` automatiskt. Versionerna är pinnade
  (``textual<9``) för att Textual bryter API mellan majorversioner – att
  jaga senaste version vid varje start hade förr eller senare tagit sönder
  en fungerande installation.
* yt-dlp är undantaget. YouTube ändrar sig ofta och gamla versioner slutar
  fungera, så den uppdateras – men bara var sjunde dag, och ett misslyckande
  stoppar aldrig starten.

Använder bara standardbiblioteket. Kör med systemets Python; skriptet byter
själv till venv-tolken.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"
REQUIREMENTS = ROOT / "requirements.txt"
STAMP = VENV / ".zodyer-state.json"
PACKAGE = ROOT / "zodyer"

MIN_PYTHON = (3, 10)
YTDLP_MAX_AGE_DAYS = 7

WINGET_HINTS = {
    "mpv": "winget install --id shinchiro.mpv --exact",
    "yt-dlp": "winget install --id yt-dlp.yt-dlp --exact",
}

#: shinchiro.mpv är ett vanligt installationsprogram och lägger sig i
#: Program Files UTAN att hamna i PATH. Leta där innan vi ger upp.
MPV_KNOWN_PATHS = (
    r"C:\Program Files\MPV Player\mpv.exe",
    r"C:\Program Files\mpv\mpv.exe",
    r"C:\Program Files (x86)\MPV Player\mpv.exe",
    r"C:\Program Files (x86)\mpv\mpv.exe",
)
MPV_SEARCH_ROOTS = (
    r"C:\Program Files",
    r"C:\Program Files (x86)",
    "%LOCALAPPDATA%\\Programs",
    "%LOCALAPPDATA%\\Microsoft\\WinGet\\Packages",
)


# ---------- utskrift ----------

def say(text: str) -> None:
    print(f"  {text}")


def fail(text: str) -> None:
    print(f"  FEL: {text}", file=sys.stderr)


# ---------- venv ----------

def check_layout() -> bool:
    """Kontrollera projektstrukturen INNAN vi rör något.

    Tidigare skapades .venv först och först därefter upptäcktes att
    requirements.txt saknades – vilket lämnade en halvfärdig miljö efter sig
    och sa inget om vad som faktiskt var fel.
    """
    saknas = []
    if not REQUIREMENTS.exists():
        saknas.append(REQUIREMENTS.name)
    if not (PACKAGE / "__main__.py").exists():
        saknas.append(f"{PACKAGE.name}{os.sep}__main__.py")

    if not saknas:
        return True

    fail(f"ofullständig projektmapp: {ROOT}")
    for namn in saknas:
        say(f"      saknas: {namn}")
    say("")
    say("start.py måste ligga TILLSAMMANS med resten av projektet:")
    say("")
    say(f"  {ROOT.name}\\")
    say("    start.py")
    say("    requirements.txt")
    say("    zodyer\\        <- paketet med __main__.py, app.py, player.py …")
    say("")
    say("Packa upp hela zodyer.zip, inte enskilda filer.")
    return False


def venv_python() -> Path:
    if os.name == "nt":
        return VENV / "Scripts" / "python.exe"
    return VENV / "bin" / "python"


def in_venv() -> bool:
    try:
        return Path(sys.executable).resolve() == venv_python().resolve()
    except OSError:
        return False


def create_venv() -> bool:
    say(f"Skapar virtuell miljö i {VENV.name}…")
    result = subprocess.run([sys.executable, "-m", "venv", str(VENV)])
    if result.returncode:
        fail("kunde inte skapa venv. Är Python installerad med venv-modulen?")
        return False
    return True


# ---------- tillstånd ----------

def read_state() -> dict:
    try:
        return json.loads(STAMP.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def write_state(state: dict) -> None:
    try:
        STAMP.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except OSError:
        pass  # inte värt att stoppa starten för


def requirements_hash() -> str:
    try:
        return hashlib.sha256(REQUIREMENTS.read_bytes()).hexdigest()[:16]
    except OSError:
        return ""


# ---------- beroenden ----------

def install_requirements(python: Path, force: bool) -> bool:
    """Installera bara om requirements.txt ändrats sedan sist."""
    state = read_state()
    current = requirements_hash()
    if not force and state.get("requirements") == current:
        say("Beroenden är aktuella.")
        return True

    say("Installerar beroenden…")
    result = subprocess.run(
        [str(python), "-m", "pip", "install", "-q", "-r", str(REQUIREMENTS)]
    )
    if result.returncode:
        if state.get("requirements"):
            fail("installationen misslyckades – kör vidare på befintliga paket.")
            return True  # troligen offline, men miljön fungerade förut
        fail("kunde inte installera beroenden. Kontrollera nätverket.")
        return False

    state["requirements"] = current
    write_state(state)
    return True


def check_imports(python: Path) -> bool:
    code = "import textual, ytmusicapi, python_mpv_jsonipc"
    result = subprocess.run([str(python), "-c", code], capture_output=True)
    if result.returncode:
        fail("beroendena går inte att importera:")
        fail(result.stderr.decode(errors="replace").strip().splitlines()[-1])
        return False
    return True


# ---------- externa program ----------

def check_externals(mpv_path: Path | None) -> bool:
    ok = True

    if mpv_path:
        say(f"mpv: {mpv_path}")
    else:
        ok = False
        fail("mpv hittades varken i PATH eller på de vanliga platserna.")
        say(f"      installera med:  {WINGET_HINTS['mpv']}")
        say("      eller peka ut den:  --mpv \"C:\\sökväg\\mpv.exe\"")

    # yt-dlp MÅSTE ligga i PATH: det är mpv:s ytdl-hook som anropar den,
    # och den tar inte emot någon sökväg från oss.
    ytdlp = shutil.which("yt-dlp")
    if ytdlp:
        say(f"yt-dlp: {ytdlp}")
    else:
        ok = False
        fail("yt-dlp hittades inte i PATH.")
        say(f"      installera med:  {WINGET_HINTS['yt-dlp']}")

    if not ok:
        say("Öppna ett nytt terminalfönster efter installationen – PATH")
        say("uppdateras inte i fönster som redan är öppna.")
    return ok


def find_mpv(explicit: str | None) -> Path | None:
    """Hitta mpv i tur och ordning: --mpv, PATH, sparad sökväg, kända platser.

    Resultatet sparas i tillståndsfilen så att sökningen bara görs en gång.
    """
    if explicit:
        path = Path(explicit)
        if path.is_file():
            return path
        fail(f"--mpv pekar på något som inte finns: {path}")
        return None

    found = shutil.which("mpv")
    if found:
        return Path(found)

    state = read_state()
    saved = state.get("mpv_path")
    if saved and Path(saved).is_file():
        return Path(saved)

    # Ingen plattformskontroll: sökvägarna nedan existerar helt enkelt inte
    # på annat än Windows, och att kunna köra funktionen överallt gör den
    # testbar utan att låtsas vara ett annat operativsystem.
    for candidate in MPV_KNOWN_PATHS:
        path = Path(candidate)
        if path.is_file():
            return _remember_mpv(path)

    say("Söker efter mpv.exe…")
    for root in MPV_SEARCH_ROOTS:
        base = Path(os.path.expandvars(root))
        if not base.is_dir():
            continue
        try:
            for path in base.glob("*/mpv.exe"):
                return _remember_mpv(path)
            for path in base.glob("*/*/mpv.exe"):
                return _remember_mpv(path)
        except OSError:
            continue
    return None


def _remember_mpv(path: Path) -> Path:
    state = read_state()
    state["mpv_path"] = str(path)
    write_state(state)
    return path


def maybe_update_ytdlp(force: bool) -> None:
    """YouTube ändrar sig ofta. Misslyckande får aldrig stoppa starten."""
    if not shutil.which("yt-dlp"):
        return

    state = read_state()
    last = state.get("ytdlp_checked", 0)
    age_days = (time.time() - last) / 86400
    if not force and age_days < YTDLP_MAX_AGE_DAYS:
        say(f"yt-dlp kontrollerad för {age_days:.0f} dagar sedan.")
        return

    say("Kontrollerar yt-dlp…")
    try:
        result = subprocess.run(
            ["yt-dlp", "-U"], capture_output=True, timeout=90, text=True
        )
        output = (result.stdout or result.stderr or "").strip().splitlines()
        if output:
            say(f"      {output[-1]}")
        if result.returncode:
            say("      kunde inte uppdatera (installerad via paketerare?).")
            say("      kör:  winget upgrade --id yt-dlp.yt-dlp")
    except (subprocess.TimeoutExpired, OSError):
        say("      kontrollen gick inte att genomföra – hoppar över.")

    state["ytdlp_checked"] = time.time()
    write_state(state)


# ---------- huvudflöde ----------

def parse_args(argv: list[str]) -> tuple[set[str], str | None, list[str]]:
    """Returnerar (flaggor, mpv-sökväg, argument som går vidare till zodyer)."""
    flags: set[str] = set()
    mpv: str | None = None
    passthrough: list[str] = []

    if "--" in argv:
        split = argv.index("--")
        argv, passthrough = argv[:split], argv[split + 1 :]

    # --mpv kan också stå efter --, eftersom zodyer har samma flagga.
    for lista in (argv, passthrough):
        for i, arg in enumerate(lista):
            if arg == "--mpv" and i + 1 < len(lista):
                mpv = lista[i + 1]
            elif arg.startswith("--mpv="):
                mpv = arg.split("=", 1)[1]

    rensad = []
    hoppa = False
    for arg in argv:
        if hoppa:
            hoppa = False
            continue
        if arg == "--mpv":
            hoppa = True
        elif arg.startswith("--mpv="):
            continue
        elif arg in {"--update", "--offline", "--check", "--help", "-h"}:
            flags.add(arg)
        else:
            rensad.append(arg)

    return flags, mpv, rensad + passthrough


def main(argv: list[str]) -> int:
    flags, mpv_arg, passthrough = parse_args(argv)

    if {"--help", "-h"} & flags:
        print(__doc__)
        return 0

    if sys.version_info < MIN_PYTHON and not in_venv():
        fail(
            f"Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+ krävs, "
            f"hittade {sys.version_info.major}.{sys.version_info.minor}."
        )
        return 1

    print("zodyer")

    if not check_layout():
        return 1

    offline = "--offline" in flags
    force = "--update" in flags

    if not venv_python().exists():
        if offline:
            fail("ingen venv finns och --offline angavs.")
            return 1
        if not create_venv():
            return 1
        force = True

    python = venv_python()

    if not offline:
        if not install_requirements(python, force):
            return 1
    if not check_imports(python):
        say("Kör  python start.py --update  för att installera om.")
        return 1

    mpv_path = find_mpv(mpv_arg)
    externals_ok = check_externals(mpv_path)
    if not offline:
        maybe_update_ytdlp(force)

    if "--check" in flags:
        say("Miljökontroll klar." if externals_ok else "Miljön är ofullständig.")
        return 0 if externals_ok else 1

    if not externals_ok:
        fail("startar inte utan mpv och yt-dlp.")
        return 1

    # Ligger mpv inte i PATH måste zodyer få sökvägen. Vi lägger till den
    # bara om användaren inte redan angett en själv.
    argument = list(passthrough)
    if mpv_path and not shutil.which("mpv") and "--mpv" not in argument:
        argument += ["--mpv", str(mpv_path)]

    say("Startar…")
    print()
    return subprocess.run([str(python), "-m", "zodyer", *argument], cwd=ROOT).returncode


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
