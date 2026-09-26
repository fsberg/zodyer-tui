"""Headless-verifiering av TUI:t med stubbad Player och YTMusicSource.

Kör: python -m pytest test_tui.py -v
Ingen mpv, inget nätverk. Syftet är att pröva påståendena i briefen,
inte att ersätta en skarp körning.
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import pytest
from textual.widgets import DataTable, Input

from zodyer.app import NowPlaying, ZodyerApp
from zodyer.models import Track
from zodyer.player import Status

TRACKS = [
    Track("aaa", "Song One", "Artist A", "Album A", "3:01", 181),
    Track("bbb", "Song [Live] Two", "Artist B", None, "4:12", 252),
    Track("ccc", "Song Three", "Artist C", "Album C", "2:30", 150),
]


class FakeSource:
    def __init__(self, tracks=None, error=None):
        self.tracks = TRACKS if tracks is None else tracks
        self.error = error
        self.calls: list[str] = []

    def search(self, query, limit=25):
        self.calls.append(query)
        if self.error:
            raise RuntimeError(self.error)
        return list(self.tracks)


class FakePlayer:
    """Speglar Player-API:t. Räknar anrop så vi ser om UI:t gör onödiga rundturer."""

    def __init__(self):
        self._queue: list[Track] = []
        self.index = -1
        self.paused = False
        self.volume = 70
        self.seeks: list[float] = []
        self.status_calls = 0
        self.terminated = False
        self.errors: list[str] = []
        self.level: float | None = 0.6
        self.repeat = "off"
        self.shuffled = False

    @property
    def queue(self):
        return list(self._queue)

    def play_now(self, track):
        self._queue = [track]
        self.index = 0

    def enqueue(self, track):
        self._queue.append(track)
        if self.index < 0:
            self.index = 0

    def remove(self, index):
        if 0 <= index < len(self._queue):
            self._queue.pop(index)
            if self.index > index:
                self.index -= 1
            elif self.index == index and not self._queue:
                self.index = -1

    def shuffle(self):
        self.shuffled = True
        self._queue = list(reversed(self._queue))   # deterministisk "blandning"

    def unshuffle(self):
        self.shuffled = False
        self._queue = list(reversed(self._queue))

    def set_repeat(self, mode):
        self.repeat = mode

    def cycle_repeat(self):
        from zodyer.player import REPEAT_MODES
        self.repeat = REPEAT_MODES[(REPEAT_MODES.index(self.repeat) + 1) % 3]
        return self.repeat

    def restore(self, tracks, index=-1):
        self._queue = list(tracks)
        self.index = -1          # återställd kö spelar inte av sig själv

    def play_index(self, index):
        if 0 <= index < len(self._queue):
            self.index = index

    def clear_queue(self):
        if 0 <= self.index < len(self._queue):
            self._queue = [self._queue[self.index]]
            self.index = 0
        else:
            self._queue = []
            self.index = -1

    def stop(self):
        self._queue = []
        self.index = -1

    def toggle_pause(self):
        if self.index < 0 and self._queue:
            self.play_index(0)
            return
        self.paused = not self.paused

    def next(self):
        if self.index + 1 < len(self._queue):
            self.index += 1

    def previous(self):
        if self.index > 0:
            self.index -= 1

    def seek(self, seconds):
        self.seeks.append(seconds)

    def change_volume(self, delta):
        self.volume = max(0, min(130, self.volume + delta))

    def status(self):
        self.status_calls += 1
        track = self._queue[self.index] if 0 <= self.index < len(self._queue) else None
        return Status(
            track=track,
            position=30.0 if track else 0.0,
            duration=180.0 if track else 0.0,
            paused=self.paused,
            volume=self.volume,
            queue_index=self.index,
            idle=track is None,
            repeat=self.repeat,
            shuffled=self.shuffled,
        )

    def audio_level(self):
        return self.level

    def take_errors(self):
        errors, self.errors = self.errors, []
        return errors

    def terminate(self):
        self.terminated = True


def temp_store():
    """Aldrig den riktiga konfigkatalogen i tester."""
    from zodyer.library import PlaylistStore
    return PlaylistStore(Path(tempfile.mkdtemp()) / "playlists.json")


def make_app(source=None, player=None, store=None):
    return ZodyerApp(player or FakePlayer(), source or FakeSource(),
                     store or temp_store())


async def do_search(pilot, app, query="test"):
    app.query_one("#search", Input).value = query
    await pilot.press("enter")
    for _ in range(50):
        await asyncio.sleep(0.02)
        if app.results:
            break
    await pilot.pause()


# ---------- Tester ----------


@pytest.mark.asyncio
async def test_startar_och_bygger_kolumner():
    app = make_app()
    async with app.run_test(size=(140, 30)) as pilot:
        await pilot.pause()
        results = app.query_one("#results", DataTable)
        queue = app.query_one("#queue", DataTable)
        assert [str(c.label) for c in results.columns.values()] == [
            "Titel", "Artist", "Album", "År", "Längd",
        ]
        assert [str(c.label) for c in queue.columns.values()] == ["#", "Spår"]
        assert app.query_one("#search", Input).has_focus


@pytest.mark.asyncio
async def test_sokning_fyller_resultattabellen():
    source = FakeSource()
    app = make_app(source=source)
    async with app.run_test() as pilot:
        await do_search(pilot, app, "beatles")
        assert source.calls == ["beatles"]
        assert app.results == TRACKS
        assert app.query_one("#results", DataTable).row_count == 3


@pytest.mark.asyncio
async def test_hakparenteser_renderas_inte_som_markup():
    """'Song [Live] Two' ska visas ordagrant, inte tolkas som Rich-markup."""
    app = make_app()
    async with app.run_test(size=(140, 30)) as pilot:
        await do_search(pilot, app)
        table = app.query_one("#results", DataTable)
        cell = table.get_cell_at((1, 0))
        assert cell.plain == "Song [Live] Two", f"fick {cell!r}"


@pytest.mark.asyncio
async def test_enter_i_resultat_spelar_direkt():
    player = FakePlayer()
    app = make_app(player=player)
    async with app.run_test() as pilot:
        await do_search(pilot, app)
        app.query_one("#results", DataTable).focus()
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert player.queue == [TRACKS[0]]


@pytest.mark.asyncio
async def test_a_koar_utan_att_ersatta():
    player = FakePlayer()
    app = make_app(player=player)
    async with app.run_test() as pilot:
        await do_search(pilot, app)
        app.query_one("#results", DataTable).focus()
        await pilot.pause()
        await pilot.press("enter")
        await pilot.press("down")
        await pilot.press("a")
        await pilot.pause()
        assert len(player.queue) == 2
        assert player.queue[1] == TRACKS[1]
        assert app.query_one("#queue", DataTable).row_count == 2


@pytest.mark.asyncio
async def test_transportbindningar():
    player = FakePlayer()
    app = make_app(player=player)
    async with app.run_test() as pilot:
        await do_search(pilot, app)
        app.query_one("#results", DataTable).focus()
        await pilot.pause()
        await pilot.press("enter")
        await pilot.press("p")
        assert player.paused is True
        await pilot.press("p")
        assert player.paused is False
        await pilot.press("full_stop")
        await pilot.press("comma")
        assert player.seeks == [10, -10]
        await pilot.press("0")
        assert player.volume == 75
        await pilot.press("9")
        await pilot.press("9")
        assert player.volume == 65


@pytest.mark.asyncio
async def test_atgarden_tomma_ko_behaller_aktuellt_spar():
    player = FakePlayer()
    app = make_app(player=player)
    async with app.run_test() as pilot:
        await do_search(pilot, app)
        app.query_one("#results", DataTable).focus()
        await pilot.pause()
        await pilot.press("enter")
        await pilot.press("down")
        await pilot.press("a")
        await pilot.pause()
        # Åtgärden finns kvar men har ingen tangent – den nås via
        # kommandopaletten. Anropas direkt här.
        app.action_clear_queue()
        await pilot.pause()
        assert len(player.queue) == 1


@pytest.mark.asyncio
async def test_sokfel_dodar_inte_appen():
    source = FakeSource(error="rate limited")
    app = make_app(source=source)
    async with app.run_test() as pilot:
        app.query_one("#search", Input).value = "x"
        await pilot.press("enter")
        for _ in range(50):
            await asyncio.sleep(0.02)
            if app.message.startswith("Sökning"):
                break
        await pilot.pause()
        assert "rate limited" in app.message
        assert app.is_running


@pytest.mark.asyncio
async def test_tomt_resultat_ger_meddelande():
    app = make_app(source=FakeSource(tracks=[]))
    async with app.run_test() as pilot:
        app.query_one("#search", Input).value = "asdkjhasd"
        await pilot.press("enter")
        for _ in range(50):
            await asyncio.sleep(0.02)
            if app.message:
                break
        await pilot.pause()
        assert app.message == "Inga träffar."


@pytest.mark.asyncio
async def test_slash_fokuserar_sok():
    app = make_app()
    async with app.run_test() as pilot:
        await do_search(pilot, app)
        app.query_one("#results", DataTable).focus()
        await pilot.pause()
        await pilot.press("slash")
        await pilot.pause()
        assert app.query_one("#search", Input).has_focus


@pytest.mark.asyncio
async def test_bokstavstangent_i_sokfaltet_utloser_inte_action():
    """README påstår detta. Verifiera att 'p' i sökfältet skriver 'p'."""
    player = FakePlayer()
    app = make_app(player=player)
    async with app.run_test() as pilot:
        await pilot.press("p")
        await pilot.pause()
        assert app.query_one("#search", Input).value == "p"
        assert player.paused is False


@pytest.mark.asyncio
async def test_status_pollas_och_ritar_nowplaying():
    player = FakePlayer()
    app = make_app(player=player)
    async with app.run_test() as pilot:
        await do_search(pilot, app)
        app.query_one("#results", DataTable).focus()
        await pilot.pause()
        await pilot.press("enter")
        before = player.status_calls
        await asyncio.sleep(1.2)
        await pilot.pause()
        assert player.status_calls > before


@pytest.mark.asyncio
async def test_terminate_vid_avslut():
    player = FakePlayer()
    app = make_app(player=player)
    async with app.run_test() as pilot:
        await pilot.pause()
    assert player.terminated is True


# ---------- Nya beteenden efter omskrivningen ----------


@pytest.mark.asyncio
async def test_uppspelningsfel_fran_playern_visas_i_ui():
    """Spår som yt-dlp inte kan resolva ska inte hoppas över tyst."""
    player = FakePlayer()
    app = make_app(player=player)
    async with app.run_test() as pilot:
        await pilot.pause()
        player.errors.append("Kunde inte spela Artist A – Song One (error)")
        for _ in range(60):
            await asyncio.sleep(0.02)
            if app.message:
                break
        assert "Kunde inte spela" in app.message


@pytest.mark.asyncio
async def test_meddelanden_forfaller():
    app = make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        app._set_message("tillfälligt", seconds=0.2)
        assert app.message == "tillfälligt"
        for _ in range(80):
            await asyncio.sleep(0.02)
            if not app.message:
                break
        assert app.message == ""


@pytest.mark.asyncio
async def test_ctrl_c_stoppar_uppspelningen():
    player = FakePlayer()
    app = make_app(player=player)
    async with app.run_test() as pilot:
        await do_search(pilot, app)
        app.query_one("#results", DataTable).focus()
        await pilot.pause()
        await pilot.press("enter")
        assert player.queue
        await pilot.press("ctrl+c")
        await pilot.pause()
        assert player.queue == []
        assert app.query_one("#queue", DataTable).row_count == 0


@pytest.mark.asyncio
async def test_stale_status_visas_som_laddar():
    player = FakePlayer()
    app = make_app(player=player)
    async with app.run_test() as pilot:
        await do_search(pilot, app)
        app.query_one("#results", DataTable).focus()
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        now = app.query_one("#now", NowPlaying)
        status = player.status()
        status.stale = True
        now.render_status(status, "")
        assert "laddar" in str(now.content)


@pytest.mark.asyncio
async def test_ui_traden_gor_inga_blockerande_anrop_per_tick():
    """_tick ska bara läsa cachad status, inte trigga IPC-liknande arbete."""
    player = FakePlayer()
    app = make_app(player=player)
    async with app.run_test() as pilot:
        await pilot.pause()
        player.status_calls = 0
        await asyncio.sleep(1.1)
        # ~2 statustick + ~11 EQ-tick på 1,1 s. Alla är cache-läsningar utan
        # IPC, men antalet ska inte skena.
        assert player.status_calls <= 20, player.status_calls
