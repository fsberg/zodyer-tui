"""Tester för startskriptet. Rör inte nätet och skapar ingen venv."""

from __future__ import annotations

import json
import time

import pytest

import start


# ---------- argument ----------


def test_flaggor_och_passthrough_separeras():
    flags, mpv, rest = start.parse_args(["--update", "--", "--volume", "50"])
    assert flags == {"--update"}
    assert mpv is None
    assert rest == ["--volume", "50"]


def test_okanda_argument_gar_vidare_till_zodyer():
    flags, mpv, rest = start.parse_args(["--volume", "50"])
    assert flags == set()
    assert rest == ["--volume", "50"]


def test_flera_flaggor():
    flags, mpv, rest = start.parse_args(["--offline", "--check"])
    assert flags == {"--offline", "--check"}
    assert rest == []


def test_mpv_flaggan_plockas_ut():
    flags, mpv, rest = start.parse_args(["--mpv", "C:\\mpv\\mpv.exe", "--check"])
    assert mpv == "C:\\mpv\\mpv.exe"
    assert flags == {"--check"}
    assert rest == [], "--mpv ska inte dubbleras i argumenten"


def test_mpv_med_likhetstecken():
    flags, mpv, rest = start.parse_args(["--mpv=/usr/bin/mpv"])
    assert mpv == "/usr/bin/mpv"
    assert rest == []


def test_mpv_efter_dubbelstreck_uppfattas_ocksa():
    """zodyer har samma flagga, så den kan hamna efter --."""
    flags, mpv, rest = start.parse_args(["--", "--mpv", "/usr/bin/mpv", "--volume", "50"])
    assert mpv == "/usr/bin/mpv"
    assert rest == ["--mpv", "/usr/bin/mpv", "--volume", "50"]


# ---------- hash ----------


def test_hash_andras_med_innehallet(tmp_path, monkeypatch):
    req = tmp_path / "requirements.txt"
    req.write_text("textual>=8.2,<9\n", encoding="utf-8")
    monkeypatch.setattr(start, "REQUIREMENTS", req)
    first = start.requirements_hash()

    req.write_text("textual>=8.2,<9\nytmusicapi>=1.12,<2\n", encoding="utf-8")
    assert start.requirements_hash() != first


def test_hash_tom_strang_om_filen_saknas(tmp_path, monkeypatch):
    monkeypatch.setattr(start, "REQUIREMENTS", tmp_path / "finns-inte.txt")
    assert start.requirements_hash() == ""


# ---------- tillståndsfil ----------


def test_trasig_tillstandsfil_kraschar_inte(tmp_path, monkeypatch):
    stamp = tmp_path / "state.json"
    stamp.write_text("{inte json", encoding="utf-8")
    monkeypatch.setattr(start, "STAMP", stamp)
    assert start.read_state() == {}


def test_tillstand_skrivs_och_lases(tmp_path, monkeypatch):
    monkeypatch.setattr(start, "STAMP", tmp_path / "state.json")
    start.write_state({"requirements": "abc123"})
    assert start.read_state()["requirements"] == "abc123"


# ---------- installationslogik ----------


class FakeRun:
    def __init__(self, returncode=0):
        self.returncode = returncode
        self.calls: list[list[str]] = []
        self.stdout = self.stderr = ""

    def __call__(self, cmd, **kwargs):
        self.calls.append(list(map(str, cmd)))
        return self


def test_installerar_inte_nar_hashen_ar_oforandrad(tmp_path, monkeypatch):
    req = tmp_path / "requirements.txt"
    req.write_text("textual\n", encoding="utf-8")
    monkeypatch.setattr(start, "REQUIREMENTS", req)
    monkeypatch.setattr(start, "STAMP", tmp_path / "state.json")
    start.write_state({"requirements": start.requirements_hash()})

    run = FakeRun()
    monkeypatch.setattr(start.subprocess, "run", run)
    assert start.install_requirements(tmp_path / "python", force=False) is True
    assert run.calls == [], "pip ska inte köras i onödan"


def test_installerar_nar_requirements_andrats(tmp_path, monkeypatch):
    req = tmp_path / "requirements.txt"
    req.write_text("textual\n", encoding="utf-8")
    monkeypatch.setattr(start, "REQUIREMENTS", req)
    monkeypatch.setattr(start, "STAMP", tmp_path / "state.json")
    start.write_state({"requirements": "gammal"})

    run = FakeRun()
    monkeypatch.setattr(start.subprocess, "run", run)
    assert start.install_requirements(tmp_path / "python", force=False) is True
    assert any("pip" in c for c in run.calls[0])
    assert start.read_state()["requirements"] == start.requirements_hash()


def test_ingen_upgrade_flagga(tmp_path, monkeypatch):
    """Versionerna är pinnade. Skriptet får aldrig jaga senaste version."""
    req = tmp_path / "requirements.txt"
    req.write_text("textual>=8.2,<9\n", encoding="utf-8")
    monkeypatch.setattr(start, "REQUIREMENTS", req)
    monkeypatch.setattr(start, "STAMP", tmp_path / "state.json")

    run = FakeRun()
    monkeypatch.setattr(start.subprocess, "run", run)
    start.install_requirements(tmp_path / "python", force=True)
    assert "--upgrade" not in run.calls[0]
    assert "-U" not in run.calls[0]


def test_misslyckad_installation_stoppar_inte_fungerande_miljo(tmp_path, monkeypatch):
    """Offline med en miljö som fungerade förut ska ändå starta."""
    req = tmp_path / "requirements.txt"
    req.write_text("textual\n", encoding="utf-8")
    monkeypatch.setattr(start, "REQUIREMENTS", req)
    monkeypatch.setattr(start, "STAMP", tmp_path / "state.json")
    start.write_state({"requirements": "gammal"})

    monkeypatch.setattr(start.subprocess, "run", FakeRun(returncode=1))
    assert start.install_requirements(tmp_path / "python", force=False) is True


def test_misslyckad_forstagangsinstallation_avbryter(tmp_path, monkeypatch):
    req = tmp_path / "requirements.txt"
    req.write_text("textual\n", encoding="utf-8")
    monkeypatch.setattr(start, "REQUIREMENTS", req)
    monkeypatch.setattr(start, "STAMP", tmp_path / "state.json")

    monkeypatch.setattr(start.subprocess, "run", FakeRun(returncode=1))
    assert start.install_requirements(tmp_path / "python", force=False) is False


# ---------- yt-dlp ----------


def test_ytdlp_kontrolleras_inte_varje_start(tmp_path, monkeypatch):
    monkeypatch.setattr(start, "STAMP", tmp_path / "state.json")
    monkeypatch.setattr(start.shutil, "which", lambda name: "/usr/bin/yt-dlp")
    start.write_state({"ytdlp_checked": time.time()})

    run = FakeRun()
    monkeypatch.setattr(start.subprocess, "run", run)
    start.maybe_update_ytdlp(force=False)
    assert run.calls == []


def test_ytdlp_kontrolleras_nar_stampeln_ar_gammal(tmp_path, monkeypatch):
    monkeypatch.setattr(start, "STAMP", tmp_path / "state.json")
    monkeypatch.setattr(start.shutil, "which", lambda name: "/usr/bin/yt-dlp")
    start.write_state({"ytdlp_checked": time.time() - 30 * 86400})

    run = FakeRun()
    monkeypatch.setattr(start.subprocess, "run", run)
    start.maybe_update_ytdlp(force=False)
    assert run.calls and run.calls[0][:2] == ["yt-dlp", "-U"]


def test_ytdlp_fel_stoppar_inte_starten(tmp_path, monkeypatch):
    monkeypatch.setattr(start, "STAMP", tmp_path / "state.json")
    monkeypatch.setattr(start.shutil, "which", lambda name: "/usr/bin/yt-dlp")

    def boom(*args, **kwargs):
        raise OSError("nätet nere")

    monkeypatch.setattr(start.subprocess, "run", boom)
    start.maybe_update_ytdlp(force=True)  # ska inte kasta


def test_saknad_ytdlp_ger_ingen_kontroll(tmp_path, monkeypatch):
    monkeypatch.setattr(start, "STAMP", tmp_path / "state.json")
    monkeypatch.setattr(start.shutil, "which", lambda name: None)
    run = FakeRun()
    monkeypatch.setattr(start.subprocess, "run", run)
    start.maybe_update_ytdlp(force=True)
    assert run.calls == []


# ---------- externa program ----------


def test_saknade_program_rapporteras(monkeypatch, capsys):
    monkeypatch.setattr(start.shutil, "which", lambda name: None)
    assert start.check_externals(None) is False
    ut = capsys.readouterr()
    assert "winget install" in ut.out


def test_bada_programmen_pa_plats(monkeypatch):
    monkeypatch.setattr(start.shutil, "which", lambda name: f"/usr/bin/{name}")
    assert start.check_externals(start.Path("/usr/bin/mpv")) is True


# ---------- att hitta mpv ----------


def test_mpv_i_path_anvands(monkeypatch):
    monkeypatch.setattr(start.shutil, "which", lambda n: "/usr/bin/mpv" if n == "mpv" else None)
    assert start.find_mpv(None) == start.Path("/usr/bin/mpv")


def test_explicit_mpv_vinner_over_path(tmp_path, monkeypatch):
    egen = tmp_path / "mpv.exe"
    egen.write_text("", encoding="utf-8")
    monkeypatch.setattr(start.shutil, "which", lambda n: "/usr/bin/mpv")
    assert start.find_mpv(str(egen)) == egen


def test_explicit_mpv_som_inte_finns_ger_fel(tmp_path, capsys):
    assert start.find_mpv(str(tmp_path / "finns-inte.exe")) is None
    assert "pekar på något som inte finns" in capsys.readouterr().err


def test_sparad_sokvag_anvands_nasta_gang(tmp_path, monkeypatch):
    """Regression: mpv installerat i Program Files utan att ligga i PATH."""
    mpv = tmp_path / "MPV Player" / "mpv.exe"
    mpv.parent.mkdir()
    mpv.write_text("", encoding="utf-8")
    monkeypatch.setattr(start, "STAMP", tmp_path / "state.json")
    monkeypatch.setattr(start.shutil, "which", lambda n: None)
    start.write_state({"mpv_path": str(mpv)})
    assert start.find_mpv(None) == mpv


def test_sparad_sokvag_som_forsvunnit_ignoreras(tmp_path, monkeypatch):
    monkeypatch.setattr(start, "STAMP", tmp_path / "state.json")
    monkeypatch.setattr(start.shutil, "which", lambda n: None)
    monkeypatch.setattr(start, "MPV_KNOWN_PATHS", ())
    monkeypatch.setattr(start, "MPV_SEARCH_ROOTS", ())
    start.write_state({"mpv_path": str(tmp_path / "borta.exe")})
    assert start.find_mpv(None) is None


def test_kanda_windowsplatser_genomsoks(tmp_path, monkeypatch):
    mpv = tmp_path / "MPV Player" / "mpv.exe"
    mpv.parent.mkdir()
    mpv.write_text("", encoding="utf-8")
    monkeypatch.setattr(start, "STAMP", tmp_path / "state.json")
    monkeypatch.setattr(start.shutil, "which", lambda n: None)
    monkeypatch.setattr(start, "MPV_KNOWN_PATHS", (str(mpv),))
    hittad = start.find_mpv(None)
    assert hittad == mpv
    assert start.read_state()["mpv_path"] == str(mpv)


def test_sokning_under_rot_hittar_mpv(tmp_path, monkeypatch):
    mpv = tmp_path / "MPV Player" / "mpv.exe"
    mpv.parent.mkdir()
    mpv.write_text("", encoding="utf-8")
    monkeypatch.setattr(start, "STAMP", tmp_path / "state.json")
    monkeypatch.setattr(start.shutil, "which", lambda n: None)
    monkeypatch.setattr(start, "MPV_KNOWN_PATHS", ())
    monkeypatch.setattr(start, "MPV_SEARCH_ROOTS", (str(tmp_path),))
    assert start.find_mpv(None) == mpv


# ---------- check_externals med sökväg ----------


def test_externals_ok_nar_mpv_finns_utanfor_path(tmp_path, monkeypatch):
    monkeypatch.setattr(start.shutil, "which", lambda n: "/usr/bin/yt-dlp" if n == "yt-dlp" else None)
    assert start.check_externals(tmp_path / "mpv.exe") is True


def test_externals_faller_utan_mpv(monkeypatch, capsys):
    monkeypatch.setattr(start.shutil, "which", lambda n: "/usr/bin/yt-dlp")
    assert start.check_externals(None) is False
    assert "peka ut den" in capsys.readouterr().out


def test_ytdlp_maste_ligga_i_path(tmp_path, monkeypatch):
    """mpv:s ytdl-hook anropar yt-dlp själv; vi kan inte skicka en sökväg."""
    monkeypatch.setattr(start.shutil, "which", lambda n: None)
    assert start.check_externals(tmp_path / "mpv.exe") is False
