"""Tester för auth-hanteringen i source.py och __main__.py.

Inget nätverk: YTMusic-konstruktorn stubbas ut.
"""

from __future__ import annotations

import json

import pytest

import zodyer.source as source_module
from zodyer.source import AuthConfigError, NotAuthenticated, YTMusicSource, detect_auth_kind

BROWSER_JSON = {
    "Accept": "*/*",
    "Authorization": "SAPISIDHASH 1234_abcdef",
    "Cookie": "__Secure-3PAPISID=xyz; SID=abc",
    "X-Goog-AuthUser": "0",
}

OAUTH_JSON = {
    "access_token": "ya29.fake",
    "refresh_token": "1//fake",
    "scope": "https://www.googleapis.com/auth/youtube",
    "token_type": "Bearer",
    "expires_at": 1800000000,
}


class FakeYTMusic:
    last_kwargs: dict = {}

    def __init__(self, auth=None, **kwargs):
        self.auth = auth
        FakeYTMusic.last_kwargs = dict(kwargs)


@pytest.fixture(autouse=True)
def stub_ytmusic(monkeypatch):
    monkeypatch.setattr(source_module, "YTMusic", FakeYTMusic)
    FakeYTMusic.last_kwargs = {}


def write(tmp_path, name, data):
    path = tmp_path / name
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


# ---------- Detektering ----------


def test_detekterar_browser_json(tmp_path):
    assert detect_auth_kind(write(tmp_path, "browser.json", BROWSER_JSON)) == "browser"


def test_detekterar_oauth_json(tmp_path):
    assert detect_auth_kind(write(tmp_path, "oauth.json", OAUTH_JSON)) == "oauth"


def test_okand_fil_ger_unknown(tmp_path):
    assert detect_auth_kind(write(tmp_path, "x.json", {"hej": 1})) == "unknown"


def test_trasig_json_ger_tydligt_fel(tmp_path):
    path = tmp_path / "trasig.json"
    path.write_text("{inte json", encoding="utf-8")
    with pytest.raises(AuthConfigError, match="Kan inte läsa"):
        detect_auth_kind(path)


# ---------- Konstruktion ----------


def test_utan_auth_fungerar_och_ar_oautentiserad():
    source = YTMusicSource()
    assert source.authenticated is False
    assert source.auth_kind is None


def test_browser_json_kraver_inga_extra_uppgifter(tmp_path):
    source = YTMusicSource(write(tmp_path, "browser.json", BROWSER_JSON))
    assert source.authenticated is True
    assert source.auth_kind == "browser"
    assert "oauth_credentials" not in FakeYTMusic.last_kwargs


def test_oauth_utan_client_id_ger_forklarande_fel(tmp_path):
    """Regressionen mot README:s gamla påstående att 'ytmusicapi oauth' räcker."""
    with pytest.raises(AuthConfigError, match="client id"):
        YTMusicSource(write(tmp_path, "oauth.json", OAUTH_JSON))


def test_oauth_med_client_id_skickar_oauthcredentials(tmp_path):
    YTMusicSource(
        write(tmp_path, "oauth.json", OAUTH_JSON),
        oauth_client_id="id.apps.googleusercontent.com",
        oauth_client_secret="hemlis",
    )
    creds = FakeYTMusic.last_kwargs.get("oauth_credentials")
    assert creds is not None
    assert creds.client_id == "id.apps.googleusercontent.com"


def test_okand_authfil_avvisas(tmp_path):
    with pytest.raises(AuthConfigError, match="varken"):
        YTMusicSource(write(tmp_path, "x.json", {"hej": 1}))


# ---------- Auth-krävande metoder ----------


def test_liked_songs_utan_auth_kastar():
    with pytest.raises(NotAuthenticated):
        YTMusicSource().liked_songs()


def test_library_playlists_utan_auth_kastar():
    with pytest.raises(NotAuthenticated):
        YTMusicSource().library_playlists()


# ---------- __main__ ----------


def test_volymvalidering(capsys):
    from zodyer.__main__ import main

    assert main(["--volume", "500"]) == 2
    assert "0 och 130" in capsys.readouterr().err


def test_saknad_authfil_ger_fel(capsys, tmp_path):
    from zodyer.__main__ import main

    assert main(["--auth", str(tmp_path / "finns-inte.json")]) == 1
    assert "hittades inte" in capsys.readouterr().err


def test_varning_nar_authfil_ligger_i_git_arbetstrad(tmp_path):
    from zodyer.__main__ import warn_if_auth_in_repo

    (tmp_path / ".git").mkdir()
    auth = write(tmp_path, "browser.json", BROWSER_JSON)
    warning = warn_if_auth_in_repo(auth)
    assert warning and "git-arbetsträd" in warning


def test_ingen_varning_utanfor_git(tmp_path):
    from zodyer.__main__ import warn_if_auth_in_repo

    assert warn_if_auth_in_repo(write(tmp_path, "browser.json", BROWSER_JSON)) is None
