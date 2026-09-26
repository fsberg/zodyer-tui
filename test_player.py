"""Tester för Player mot en fejkad mpv.

Poängen är att pröva trådningen: ingen publik metod får blockera anroparen,
inte ens när mpv är upptagen (yt-dlp-uppslag) eller helt slutar svara.
"""

from __future__ import annotations

import threading
import time

import pytest

import zodyer.player as player_module
from zodyer.models import Track
from zodyer.player import Player

T1 = Track("aaa", "One", "A")
T2 = Track("bbb", "Two", "B")
T3 = Track("ccc", "Three", "C")


class FakeMPV:
    """Efterliknar python_mpv_jsonipc.MPV så långt Player använder den."""

    def __init__(self, *args, **kwargs):
        self.init_kwargs = kwargs
        self.properties = [
            "time_pos", "duration", "pause", "volume", "playlist_pos", "idle_active",
        ]
        self.commands: list[tuple] = []
        self.events: dict = {}
        self.terminated = False
        self.command_delay = 0.0
        self.raise_on_get = False
        self.lock = threading.Lock()
        self._values = {
            "time_pos": None,      # None = "property unavailable" i idle
            "duration": None,
            "pause": False,
            "volume": 70,
            "playlist_pos": -1,
            "idle_active": True,
        }

    # -- properties --
    def __getattr__(self, name):
        values = object.__getattribute__(self, "_values")
        if name in values:
            if object.__getattribute__(self, "raise_on_get"):
                raise TimeoutError("No response from MPV.")
            time.sleep(object.__getattribute__(self, "command_delay"))
            return values[name]
        raise AttributeError(name)

    def __setattr__(self, name, value):
        if name != "_values" and "_values" in self.__dict__ and name in self._values:
            time.sleep(self.command_delay)
            self._values[name] = value
            return
        object.__setattr__(self, name, value)

    def set_state(self, **kwargs):
        with self.lock:
            self._values.update(kwargs)

    # -- kommandon --
    def command(self, name, *args):
        time.sleep(self.command_delay)
        with self.lock:
            self.commands.append((name, *args))
        return None

    def bind_event(self, name, callback):
        self.events[name] = callback

    def fire_end_file(self, reason):
        cb = self.events.get("end-file")
        if cb:
            cb({"event": "end-file", "reason": reason})

    def terminate(self):
        self.terminated = True


@pytest.fixture
def fake_mpv(monkeypatch):
    holder = {}

    def factory(*args, **kwargs):
        mpv = FakeMPV(*args, **kwargs)
        holder["mpv"] = mpv
        return mpv

    monkeypatch.setattr(player_module, "MPV", factory)
    return holder


@pytest.fixture
def player(fake_mpv):
    p = Player(volume=70, poll_interval=0.05)
    yield p, fake_mpv["mpv"]
    p.terminate()


def wait_for(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


# ---------- Grundläggande ----------


def test_startar_mpv_med_ratt_flaggor(player):
    p, mpv = player
    assert mpv.init_kwargs["vid"] == "no"
    assert mpv.init_kwargs["ytdl"] == "yes"
    assert mpv.init_kwargs["volume"] == 70


def test_status_ar_tillganglig_direkt_utan_ipc(player):
    p, mpv = player
    status = p.status()
    assert status.volume == 70
    assert status.track is None
    assert status.idle is True


def test_play_now_speglar_kon_och_skickar_loadfile(player):
    p, mpv = player
    p.play_now(T1)
    assert p.queue == [T1]
    assert p.status().track == T1, "status ska visa spåret direkt, inte efter pollning"
    assert wait_for(lambda: ("loadfile", T1.url, "replace") in mpv.commands)


def test_enqueue_lagger_sist(player):
    p, mpv = player
    p.play_now(T1)
    p.enqueue(T2)
    assert p.queue == [T1, T2]
    assert wait_for(lambda: ("loadfile", T2.url, "append-play") in mpv.commands)


def test_clear_queue_behaller_aktuellt_spar(player):
    p, mpv = player
    p.play_now(T1)
    p.enqueue(T2)
    p.enqueue(T3)
    p.clear_queue()
    assert p.queue == [T1]
    assert p.status().queue_index == 0
    assert wait_for(lambda: ("playlist-clear",) in mpv.commands)


def test_stop_tommer_kon_men_behaller_volym(player):
    p, mpv = player
    p.change_volume(10)
    p.play_now(T1)
    p.stop()
    assert p.queue == []
    assert p.status().track is None
    assert p.status().volume == 80


def test_toggle_pause_och_volym_uppdaterar_cache_direkt(player):
    p, mpv = player
    p.toggle_pause()
    assert p.status().paused is True
    p.toggle_pause()
    assert p.status().paused is False
    p.change_volume(-100)
    assert p.status().volume == 0, "ska klampas vid 0"
    p.change_volume(500)
    assert p.status().volume == 130, "ska klampas vid 130"


def test_kommandon_kors_i_ordning(player):
    p, mpv = player
    p.play_now(T1)
    p.next()
    p.previous()
    p.seek(10)
    assert wait_for(lambda: len(mpv.commands) >= 5)
    # Första kommandot är att koppla på nivåmätaren; resten ska ligga i ordning.
    names = [c[0] for c in mpv.commands if c[0] != "af"]
    assert names == ["loadfile", "playlist-next", "playlist-prev", "seek"]


# ---------- Pollning ----------


def test_pollningen_uppdaterar_status(player):
    p, mpv = player
    p.play_now(T1)
    mpv.set_state(playlist_pos=0, time_pos=42.5, duration=180.0, idle_active=False)
    assert wait_for(lambda: p.status().position == 42.5)
    status = p.status()
    assert status.duration == 180.0
    assert status.track == T1
    assert status.idle is False
    assert status.stale is False


def test_otillgangliga_properties_i_idle_ger_defaults(player):
    """time_pos/duration är None i idle. Ska inte krascha pollningen."""
    p, mpv = player
    assert wait_for(lambda: p.status().stale is False)
    status = p.status()
    assert status.position == 0.0
    assert status.duration == 0.0
    assert status.idle is True


def test_mpv_som_slutar_svara_markeras_stale_men_behaller_varden(player):
    p, mpv = player
    p.play_now(T1)
    mpv.set_state(playlist_pos=0, time_pos=30.0, duration=180.0, idle_active=False)
    assert wait_for(lambda: p.status().position == 30.0)

    mpv.raise_on_get = True
    assert wait_for(lambda: p.status().stale is True)
    status = p.status()
    assert status.position == 30.0, "senast kända värde ska behållas"
    assert status.track == T1


# ---------- Det som var själva poängen ----------


def test_publika_metoder_blockerar_inte_nar_mpv_hanger(player):
    """mpv tar 2 s per kommando (yt-dlp). UI-tråden får inte vänta."""
    p, mpv = player
    mpv.command_delay = 2.0

    start = time.monotonic()
    p.play_now(T1)
    p.enqueue(T2)
    p.next()
    p.toggle_pause()
    p.change_volume(5)
    for _ in range(20):
        p.status()
        p.queue
    elapsed = time.monotonic() - start

    assert elapsed < 0.5, f"anropen tog {elapsed:.2f}s – blockerar fortfarande"


def test_status_blockerar_inte_under_langsam_pollning(player):
    p, mpv = player
    mpv.command_delay = 1.5
    time.sleep(0.1)  # låt pollningstråden fastna i ett getattr
    start = time.monotonic()
    for _ in range(50):
        p.status()
    assert time.monotonic() - start < 0.2


# ---------- Felrapportering ----------


def test_end_file_med_fel_rapporteras(player):
    p, mpv = player
    p.play_now(T1)
    mpv.set_state(playlist_pos=0, idle_active=False)
    assert wait_for(lambda: p.status().queue_index == 0)

    mpv.fire_end_file("error")
    errors = p.take_errors()
    assert len(errors) == 1
    assert "One" in errors[0]
    assert p.take_errors() == [], "fel ska konsumeras en gång"


@pytest.mark.parametrize("reason", ["eof", "stop", "quit", "redirect"])
def test_normala_end_file_reasons_ger_inget_fel(player, reason):
    p, mpv = player
    p.play_now(T1)
    mpv.fire_end_file(reason)
    assert p.take_errors() == []


def test_kommandofel_fangas_och_rapporteras(player):
    p, mpv = player

    def boom(*args):
        raise RuntimeError("socket stängd")

    mpv.command = boom
    p.play_now(T1)
    assert wait_for(lambda: p.take_errors() != [] or False)


def test_saknad_property_ger_varning(fake_mpv, monkeypatch):
    original = player_module.MPV

    def factory(*args, **kwargs):
        mpv = original(*args, **kwargs)
        mpv.properties = ["time_pos", "volume"]
        return mpv

    monkeypatch.setattr(player_module, "MPV", factory)
    p = Player(poll_interval=0.05)
    try:
        errors = p.take_errors()
        assert errors and "saknar properties" in errors[0]
        assert "playlist_pos" in errors[0]
    finally:
        p.terminate()


# ---------- Avslut ----------


def test_terminate_ar_idempotent(fake_mpv):
    p = Player(poll_interval=0.05)
    mpv = fake_mpv["mpv"]
    p.terminate()
    p.terminate()
    assert mpv.terminated is True


def test_tradar_avslutas_vid_terminate(fake_mpv):
    p = Player(poll_interval=0.05)
    p.terminate()
    assert not p._worker.is_alive()
    assert not p._poller.is_alive()


# ---------- Nivåmätare ----------


def test_nivamataren_kopplas_pa_efter_uppstart(player):
    """Filtret får INTE ligga på kommandoraden – ett mpv utan lavfi
    hade då vägrat starta."""
    p, mpv = player
    assert wait_for(lambda: any(c[0] == "af" for c in mpv.commands))
    af = next(c for c in mpv.commands if c[0] == "af")
    assert af[1] == "add"
    assert "astats" in af[2]
    assert "zodeq" in af[2]


def test_nivamataren_kan_stangas_av(fake_mpv):
    p = Player(poll_interval=0.05, level_meter=False)
    try:
        time.sleep(0.2)
        assert not any(c[0] == "af" for c in fake_mpv["mpv"].commands)
        assert p.audio_level() is None
    finally:
        p.terminate()


def test_niva_lases_fran_astats_metadata(player):
    p, mpv = player
    original = mpv.command

    def with_metadata(name, *args):
        if name == "get_property" and args and str(args[0]).startswith("af-metadata"):
            return {"lavfi.astats.Overall.RMS_level": "-11.0"}
        return original(name, *args)

    mpv.command = with_metadata
    assert wait_for(lambda: p.audio_level() is not None)
    level = p.audio_level()
    assert 0.7 < level < 0.85, level


def test_tystnad_ger_noll(player):
    p, mpv = player
    mpv.command = lambda name, *a: (
        {"lavfi.astats.Overall.RMS_level": "-inf"}
        if name == "get_property" and a and str(a[0]).startswith("af-metadata")
        else None
    )
    assert wait_for(lambda: p.audio_level() == 0.0)


def test_mataren_ger_upp_efter_upprepade_fel(player):
    """Om mpv saknar filtret ska vi sluta slösa IPC, inte spamma."""
    p, mpv = player

    def failing(name, *args):
        if name == "get_property" and args and str(args[0]).startswith("af-metadata"):
            raise RuntimeError("property not found")
        return None

    mpv.command = failing
    assert wait_for(lambda: p._level_enabled is False, timeout=6.0)
    assert p.audio_level() is None
