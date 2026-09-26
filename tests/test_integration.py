"""Riktiga ZodyerApp + riktiga Player, med enbart mpv och nätverket utbytt.

Det här är det närmaste en skarp körning man kommer utan mpv installerat.
"""

from __future__ import annotations

import asyncio

import pytest
from textual.widgets import DataTable, Input

import zodyer.player as player_module
from test_player import FakeMPV
from test_tui import TRACKS, FakeSource, temp_store
from zodyer.app import ZodyerApp
from zodyer.player import Player


@pytest.fixture
def wired(monkeypatch):
    holder = {}

    def factory(*args, **kwargs):
        mpv = FakeMPV(*args, **kwargs)
        holder["mpv"] = mpv
        return mpv

    monkeypatch.setattr(player_module, "MPV", factory)
    player = Player(volume=70, poll_interval=0.05)
    app = ZodyerApp(player, FakeSource(), temp_store())
    yield app, player, holder["mpv"]
    player.terminate()


async def search_and_play(pilot, app):
    app.query_one("#search", Input).value = "test"
    await pilot.press("enter")
    for _ in range(60):
        await asyncio.sleep(0.02)
        if app.results:
            break
    app.query_one("#results", DataTable).focus()
    await pilot.pause()
    await pilot.press("enter")
    await pilot.pause()


@pytest.mark.asyncio
async def test_hela_kedjan_sok_spela_koa(wired):
    app, player, mpv = wired
    async with app.run_test() as pilot:
        await search_and_play(pilot, app)

        assert ("loadfile", TRACKS[0].url, "replace") in mpv.commands
        assert app.query_one("#queue", DataTable).row_count == 1

        await pilot.press("down")
        await pilot.press("a")
        await pilot.pause()
        assert player.queue == [TRACKS[0], TRACKS[1]]
        assert app.query_one("#queue", DataTable).row_count == 2


@pytest.mark.asyncio
async def test_ui_forblir_responsivt_nar_mpv_hanger(wired):
    """Kärnan i hela omskrivningen: 3 s trög mpv får inte frysa TUI:t."""
    app, player, mpv = wired
    async with app.run_test() as pilot:
        await pilot.pause()
        mpv.command_delay = 3.0

        app.query_one("#search", Input).value = "test"
        await pilot.press("enter")
        for _ in range(60):
            await asyncio.sleep(0.02)
            if app.results:
                break
        app.query_one("#results", DataTable).focus()
        await pilot.pause()

        loop = asyncio.get_running_loop()
        start = loop.time()
        await pilot.press("enter")   # play_now mot en mpv som tar 3 s
        await pilot.press("p")
        await pilot.press("0")
        await pilot.press("n")
        await pilot.pause()
        elapsed = loop.time() - start

        assert elapsed < 1.0, f"UI-tråden blockerades {elapsed:.1f}s"
        # Spåret visas direkt trots att mpv inte svarat än.
        assert app.query_one("#queue", DataTable).row_count == 1
        assert player.status().track == TRACKS[0]


@pytest.mark.asyncio
async def test_misslyckat_spar_syns_i_ui(wired):
    app, player, mpv = wired
    async with app.run_test() as pilot:
        await search_and_play(pilot, app)
        mpv.set_state(playlist_pos=0, idle_active=False)
        for _ in range(60):
            await asyncio.sleep(0.02)
            if player.status().queue_index == 0:
                break

        mpv.fire_end_file("error")
        for _ in range(80):
            await asyncio.sleep(0.02)
            if app.message:
                break
        assert "Kunde inte spela" in app.message
        assert "Song One" in app.message


@pytest.mark.asyncio
async def test_avslut_stanger_ner_traadar_och_mpv(wired):
    app, player, mpv = wired
    async with app.run_test() as pilot:
        await search_and_play(pilot, app)
    assert mpv.terminated is True
    assert not player._poller.is_alive()
