"""Tester för zodyer.locate – att hitta mpv utan start.py (pipx)."""

from __future__ import annotations

import zodyer.__main__ as main_module
from zodyer import locate


def _fake_mpv(tmp_path):
    mpv = tmp_path / "MPV Player" / "mpv.exe"
    mpv.parent.mkdir()
    mpv.write_text("", encoding="utf-8")
    return mpv


def test_path_vinner(monkeypatch):
    monkeypatch.setattr(locate.shutil, "which", lambda n: "/usr/bin/mpv")
    assert str(locate.find_mpv((), ())).replace("\\", "/") == "/usr/bin/mpv"


def test_kand_plats_utanfor_path(tmp_path, monkeypatch):
    mpv = _fake_mpv(tmp_path)
    monkeypatch.setattr(locate.shutil, "which", lambda n: None)
    assert locate.find_mpv((str(mpv),), ()) == mpv


def test_sokning_under_rot(tmp_path, monkeypatch):
    mpv = _fake_mpv(tmp_path)
    monkeypatch.setattr(locate.shutil, "which", lambda n: None)
    assert locate.find_mpv((), (str(tmp_path),)) == mpv


def test_ingenting_hittat(tmp_path, monkeypatch):
    monkeypatch.setattr(locate.shutil, "which", lambda n: None)
    assert locate.find_mpv((), (str(tmp_path / "finns-inte"),)) is None


# ---------- main() ----------


def test_main_skickar_hittad_mpv_till_player(tmp_path, monkeypatch):
    """Regression: zodyer via pipx sa "WinError 2" när mpv inte låg i PATH."""
    mpv = _fake_mpv(tmp_path)
    monkeypatch.setattr(main_module, "find_mpv", lambda: mpv)
    monkeypatch.setattr(main_module.shutil, "which", lambda n: "yt-dlp")
    monkeypatch.setattr(main_module, "YTMusicSource", lambda *a, **k: object())
    fick = {}

    class Stopp(Exception):
        pass

    def fake_player(mpv_location, volume):
        fick["mpv"] = mpv_location
        raise Stopp

    monkeypatch.setattr(main_module, "Player", fake_player)
    assert main_module.main([]) == 1          # Stopp fångas som startfel
    assert fick["mpv"] == str(mpv)


def test_main_utan_mpv_ger_installationstips(monkeypatch, capsys):
    monkeypatch.setattr(main_module, "find_mpv", lambda: None)
    monkeypatch.setattr(main_module, "YTMusicSource", lambda *a, **k: object())
    assert main_module.main([]) == 1
    assert "winget install --id shinchiro.mpv" in capsys.readouterr().err


def test_main_utan_ytdlp_ger_installationstips(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(main_module, "find_mpv", lambda: _fake_mpv(tmp_path))
    monkeypatch.setattr(main_module.shutil, "which", lambda n: None)
    monkeypatch.setattr(main_module, "YTMusicSource", lambda *a, **k: object())
    assert main_module.main([]) == 1
    assert "yt-dlp.yt-dlp" in capsys.readouterr().err
