"""Tester för PlaylistStore och spellistorna i gränssnittet."""

from __future__ import annotations

import asyncio
import json

import pytest
from textual.widgets import DataTable, Input

from test_tui import TRACKS, FakePlayer, FakeSource, do_search
from zodyer.app import ZodyerApp
from zodyer.library import PlaylistStore, _from_dicts
from zodyer.models import Track
from zodyer.screens import LibraryScreen, SaveScreen


@pytest.fixture
def store(tmp_path):
    return PlaylistStore(tmp_path / "playlists.json")


# ---------- Lagringen ----------


def test_tomt_bibliotek_utan_fil(store):
    assert store.names() == []
    assert store.load_queue() == ([], -1)
    assert store.last_error is None


def test_spara_och_ladda(store):
    assert store.save("Bra grejer", TRACKS) is True
    assert store.names() == ["Bra grejer"]
    assert store.load("Bra grejer") == TRACKS


def test_sparat_overlever_ny_instans(store, tmp_path):
    store.save("Kvällslyssning", TRACKS[:2])
    ny = PlaylistStore(tmp_path / "playlists.json")
    assert ny.load("Kvällslyssning") == TRACKS[:2]


def test_alla_falt_bevaras(store):
    spar = Track("x1", "Titel", "Artist", "Album", "3:21", 201, "1994")
    store.save("En", [spar])
    assert store.load("En")[0] == spar


def test_namn_utan_innehall_avvisas(store):
    assert store.save("", TRACKS) is False
    assert store.save("   ", TRACKS) is False
    assert store.save("Tom", []) is False
    assert store.names() == []


def test_spara_igen_skriver_over(store):
    store.save("Lista", TRACKS)
    store.save("Lista", TRACKS[:1])
    assert store.names() == ["Lista"]
    assert len(store.load("Lista")) == 1


def test_ta_bort(store):
    store.save("A", TRACKS)
    store.save("B", TRACKS)
    assert store.delete("A") is True
    assert store.names() == ["B"]
    assert store.delete("finns-inte") is False


def test_byt_namn(store):
    store.save("Gammalt", TRACKS)
    assert store.rename("Gammalt", "Nytt") is True
    assert store.names() == ["Nytt"]
    assert store.load("Nytt") == TRACKS


def test_nyast_forst(store):
    store.save("Först", TRACKS[:1])
    store._data["playlists"]["Först"]["updated"] = "2020-01-01T00:00:00+00:00"
    store.save("Sedan", TRACKS[:1])
    assert store.names()[0] == "Sedan"


# ---------- Robusthet ----------


def test_trasig_fil_kraschar_inte_utan_sakerhetskopieras(tmp_path):
    fil = tmp_path / "playlists.json"
    fil.write_text("{detta är inte json", encoding="utf-8")
    store = PlaylistStore(fil)
    assert store.names() == []
    assert store.last_error and "kunde inte läsa" in store.last_error
    assert (tmp_path / "playlists.json.trasig").exists(), "originalet ska sparas"


def test_fil_med_fel_struktur_avvisas(tmp_path):
    fil = tmp_path / "playlists.json"
    fil.write_text('["en lista, inte ett objekt"]', encoding="utf-8")
    store = PlaylistStore(fil)
    assert store.names() == []
    assert store.last_error is not None


def test_trasig_rad_tar_inte_hela_spellistan(store):
    store.save("Blandat", TRACKS)
    data = json.loads(store.path.read_text(encoding="utf-8"))
    data["playlists"]["Blandat"]["tracks"].insert(1, {"video_id": None})
    data["playlists"]["Blandat"]["tracks"].insert(2, "inte ett objekt")
    store.path.write_text(json.dumps(data), encoding="utf-8")

    ny = PlaylistStore(store.path)
    assert len(ny.load("Blandat")) == len(TRACKS), "bara de trasiga ska falla bort"


def test_saknad_artist_far_platshallare():
    tracks = _from_dicts([{"video_id": "a", "title": "T"}])
    assert tracks[0].artist == "Okänd artist"


def test_skrivningen_ar_atomar(store, tmp_path):
    """Inga temporärfiler ska bli kvar efter en lyckad skrivning."""
    store.save("Lista", TRACKS)
    kvar = [p.name for p in tmp_path.iterdir() if p.suffix == ".tmp"]
    assert kvar == []


# ---------- Kön mellan sessioner ----------


def test_kon_sparas_och_laddas(store):
    store.save_queue(TRACKS, 1)
    tracks, index = store.load_queue()
    assert tracks == TRACKS
    assert index == 1


def test_tom_ko_nollstaller(store):
    store.save_queue(TRACKS, 0)
    store.save_queue([], -1)
    assert store.load_queue() == ([], -1)


def test_orimligt_index_klamps(store):
    store.save_queue(TRACKS, 99)
    _, index = store.load_queue()
    assert index == len(TRACKS) - 1


def test_index_utanfor_intervallet_i_filen_ignoreras(store):
    store.save_queue(TRACKS, 1)
    data = json.loads(store.path.read_text(encoding="utf-8"))
    data["queue"]["index"] = 999
    store.path.write_text(json.dumps(data), encoding="utf-8")
    _, index = PlaylistStore(store.path).load_queue()
    assert index == -1


def test_kon_och_spellistor_stor_inte_varandra(store):
    store.save("Lista", TRACKS[:2])
    store.save_queue(TRACKS, 0)
    ny = PlaylistStore(store.path)
    assert ny.load("Lista") == TRACKS[:2]
    assert ny.load_queue()[0] == TRACKS


# ---------- Gränssnittet ----------


def make_app(store, player=None):
    return ZodyerApp(player or FakePlayer(), FakeSource(TRACKS), store)


@pytest.mark.asyncio
async def test_spara_kon_via_modal(store):
    player = FakePlayer()
    app = make_app(store, player)
    async with app.run_test(size=(120, 32)) as pilot:
        await do_search(pilot, app)
        app.query_one("#results", DataTable).focus()
        await pilot.pause()
        await pilot.press("enter")
        await pilot.press("down")
        await pilot.press("a")
        await pilot.pause()

        await pilot.press("ctrl+s")
        await pilot.pause()
        assert isinstance(app.screen, SaveScreen)
        app.screen.query_one("#playlist-name", Input).value = "Testlista"
        await pilot.press("enter")
        await pilot.pause()

    assert PlaylistStore(store.path).load("Testlista") == TRACKS[:2]


@pytest.mark.asyncio
async def test_escape_avbryter_utan_att_spara(store):
    app = make_app(store)
    async with app.run_test(size=(120, 32)) as pilot:
        await do_search(pilot, app)
        app.query_one("#results", DataTable).focus()
        await pilot.press("enter")
        await pilot.pause()
        await pilot.press("ctrl+s")
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        assert store.names() == []


@pytest.mark.asyncio
async def test_tom_ko_gar_inte_att_spara(store):
    app = make_app(store)
    async with app.run_test(size=(120, 32)) as pilot:
        await pilot.pause()
        app.query_one("#results", DataTable).focus()
        await pilot.press("ctrl+s")
        await pilot.pause()
        assert not isinstance(app.screen, SaveScreen)
        assert "tom" in app.message.lower()


@pytest.mark.asyncio
async def test_ladda_spellista_ersatter_kon(store):
    store.save("Kväll", TRACKS)
    player = FakePlayer()
    app = make_app(store, player)
    async with app.run_test(size=(120, 32)) as pilot:
        await pilot.pause()
        app.query_one("#results", DataTable).focus()
        await pilot.press("l")
        await pilot.pause()
        assert isinstance(app.screen, LibraryScreen)
        await pilot.press("enter")
        await pilot.pause()
        assert player.queue == TRACKS


@pytest.mark.asyncio
async def test_a_i_biblioteket_lagger_till_utan_att_ersatta(store):
    store.save("Extra", TRACKS[:2])
    player = FakePlayer()
    app = make_app(store, player)
    async with app.run_test(size=(120, 32)) as pilot:
        await do_search(pilot, app)
        app.query_one("#results", DataTable).focus()
        await pilot.press("enter")
        await pilot.pause()
        assert len(player.queue) == 1

        await pilot.press("l")
        await pilot.pause()
        await pilot.press("a")
        await pilot.pause()
        assert len(player.queue) == 3


@pytest.mark.asyncio
async def test_d_tar_bort_i_biblioteket(store):
    store.save("Bort", TRACKS)
    app = make_app(store)
    async with app.run_test(size=(120, 32)) as pilot:
        await pilot.pause()
        app.query_one("#results", DataTable).focus()
        await pilot.press("l")
        await pilot.pause()
        await pilot.press("d")
        await pilot.pause()
        assert store.names() == []


@pytest.mark.asyncio
async def test_kon_sparas_vid_avslut_och_aterstalls(store):
    player = FakePlayer()
    app = make_app(store, player)
    async with app.run_test(size=(120, 32)) as pilot:
        await do_search(pilot, app)
        app.query_one("#results", DataTable).focus()
        await pilot.press("enter")
        await pilot.press("down")
        await pilot.press("a")
        await pilot.pause()
        await asyncio.sleep(0.7)   # låt _tick tömma flaggan

    sparad, _ = PlaylistStore(store.path).load_queue()
    assert sparad == TRACKS[:2]

    ny_player = FakePlayer()
    app2 = make_app(PlaylistStore(store.path), ny_player)
    async with app2.run_test(size=(120, 32)) as pilot:
        await pilot.pause()
        await asyncio.sleep(0.3)
        assert ny_player.queue == TRACKS[:2]


@pytest.mark.asyncio
async def test_aterstalld_ko_borjar_inte_spela_av_sig_sjalv(store):
    """Programmet ska inte börja låta bara för att det startas."""
    store.save_queue(TRACKS, 0)
    player = FakePlayer()
    app = make_app(store, player)
    async with app.run_test(size=(120, 32)) as pilot:
        await pilot.pause()
        await asyncio.sleep(0.3)
        assert player.queue == TRACKS
        assert player.index == -1, "inget spår ska vara aktivt"


@pytest.mark.asyncio
async def test_trasigt_bibliotek_visas_men_stoppar_inte_starten(tmp_path):
    fil = tmp_path / "playlists.json"
    fil.write_text("{trasig", encoding="utf-8")
    app = make_app(PlaylistStore(fil))
    async with app.run_test(size=(120, 32)) as pilot:
        await pilot.pause()
        assert app.is_running
        assert "Biblioteket" in app.message


# ---------- Skiftlägeskollisioner ----------


def test_inga_bindningar_krockar_i_skiftlage():
    """Textual skiljer inte på "s" och "S" i en riktig terminal.

    Regressionen: Ctrl+S hette "S", vilket i praktiken utlöste action_stop
    och tömde kön i stället för att spara den. pilot.press("S") i testerna
    matchade rätt bindning och dolde felet.
    """
    from zodyer.app import ZodyerApp

    sedda: dict[str, str] = {}
    for binding in ZodyerApp.BINDINGS:
        nyckel = binding.key.lower()
        assert nyckel not in sedda or sedda[nyckel] == binding.action, (
            f"{binding.key} krockar med en annan bindning: "
            f"{sedda.get(nyckel)} vs {binding.action}"
        )
        sedda[nyckel] = binding.action


def test_inga_enkla_versaler_som_bindningar():
    """En ensam versal fungerar inte tillförlitligt. Använd ctrl+ i stället."""
    from zodyer.app import ZodyerApp

    for binding in ZodyerApp.BINDINGS:
        assert not (len(binding.key) == 1 and binding.key.isupper()), (
            f'"{binding.key}" är en ensam versal – använd ctrl+{binding.key.lower()}'
        )


@pytest.mark.asyncio
async def test_ctrl_c_rensar_och_ctrl_s_sparar(store):
    """De två får inte förväxlas: den ena tömmer kön, den andra bevarar den."""
    player = FakePlayer()
    app = make_app(store, player)
    async with app.run_test(size=(120, 32)) as pilot:
        await do_search(pilot, app)
        app.query_one("#results", DataTable).focus()
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()

        await pilot.press("ctrl+s")
        await pilot.pause()
        assert isinstance(app.screen, SaveScreen), "ctrl+s ska öppna spara-dialogen"
        assert player.queue, "kön ska vara orörd"
        await pilot.press("escape")
        await pilot.pause()

        await pilot.press("ctrl+c")
        await pilot.pause()
        assert player.queue == [], "ctrl+c ska rensa kön"


# ---------- Ta bort spår, blanda, upprepa ----------


@pytest.mark.asyncio
async def test_delete_tar_bort_markerat_spar_ur_kon(store):
    player = FakePlayer()
    app = make_app(store, player)
    async with app.run_test(size=(120, 32)) as pilot:
        await do_search(pilot, app)
        app.query_one("#results", DataTable).focus()
        await pilot.pause()
        await pilot.press("enter")
        for _ in range(2):
            await pilot.press("down")
            await pilot.press("a")
        await pilot.pause()
        assert len(player.queue) == 3

        ko_tabell = app.query_one("#queue", DataTable)
        ko_tabell.focus()
        ko_tabell.move_cursor(row=1)
        await pilot.pause()
        borttagen = player.queue[1]

        await pilot.press("delete")
        await pilot.pause()
        assert len(player.queue) == 2
        assert borttagen not in player.queue


@pytest.mark.asyncio
async def test_delete_utan_fokus_pa_kon_gor_ingenting(store):
    player = FakePlayer()
    app = make_app(store, player)
    async with app.run_test(size=(120, 32)) as pilot:
        await do_search(pilot, app)
        app.query_one("#results", DataTable).focus()
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        await pilot.press("delete")
        await pilot.pause()
        assert len(player.queue) == 1
        assert "Tab" in app.message


@pytest.mark.asyncio
async def test_blanda_och_aterstall(store):
    player = FakePlayer()
    app = make_app(store, player)
    async with app.run_test(size=(120, 32)) as pilot:
        await do_search(pilot, app)
        app.query_one("#results", DataTable).focus()
        await pilot.pause()
        await pilot.press("enter")
        await pilot.press("down")
        await pilot.press("a")
        await pilot.pause()
        original = player.queue

        await pilot.press("b")
        await pilot.pause()
        assert player.shuffled is True
        assert player.queue != original

        await pilot.press("b")
        await pilot.pause()
        assert player.shuffled is False
        assert player.queue == original


@pytest.mark.asyncio
async def test_upprepa_cyklar_i_tre_lagen(store):
    player = FakePlayer()
    app = make_app(store, player)
    async with app.run_test(size=(120, 32)) as pilot:
        await pilot.pause()
        app.query_one("#results", DataTable).focus()
        assert player.repeat == "off"
        await pilot.press("r")
        await pilot.pause()
        assert player.repeat == "all"
        await pilot.press("r")
        await pilot.pause()
        assert player.repeat == "one"
        await pilot.press("r")
        await pilot.pause()
        assert player.repeat == "off"


@pytest.mark.asyncio
async def test_lagena_syns_i_statusraden(store):
    from zodyer.app import NowPlaying

    player = FakePlayer()
    app = make_app(store, player)
    async with app.run_test(size=(120, 32)) as pilot:
        await do_search(pilot, app)
        app.query_one("#results", DataTable).focus()
        await pilot.pause()
        await pilot.press("enter")
        await pilot.press("r")
        await pilot.press("b")
        await pilot.pause()
        await asyncio.sleep(0.6)
        await pilot.pause()
        text = str(app.query_one("#now", NowPlaying).content)
        assert "alla" in text
        assert "blandad" in text


@pytest.mark.asyncio
async def test_hjalprutan_listar_alla_bindningar(store):
    from zodyer.screens import HelpScreen

    app = make_app(store)
    async with app.run_test(size=(120, 34)) as pilot:
        await pilot.pause()
        app.query_one("#results", DataTable).focus()
        await pilot.press("question_mark")
        await pilot.pause()
        assert isinstance(app.screen, HelpScreen)
        tabell = app.screen.query_one("#help", DataTable)
        med_beskrivning = [b for b in app.BINDINGS if b.description]
        assert tabell.row_count == len(med_beskrivning)
        tangenter = [tabell.get_cell_at((r, 0)).plain for r in range(tabell.row_count)]
        assert "Delete" in tangenter
        assert "Ctrl+d" in tangenter
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, HelpScreen)


@pytest.mark.asyncio
async def test_omritning_av_kon_flyttar_inte_markoren(store):
    """Kötabellen ritas om när kön eller aktuellt spår ändras. Markören får
    inte hoppa till första raden då – nästa Delete träffade fel spår."""
    player = FakePlayer()
    app = make_app(store, player)
    async with app.run_test(size=(120, 32)) as pilot:
        for track in TRACKS:
            player.enqueue(track)
        app._refresh_queue()
        ko_tabell = app.query_one("#queue", DataTable)
        ko_tabell.focus()
        ko_tabell.move_cursor(row=2)
        await pilot.pause()

        player.next()                 # nytt aktuellt spår → omritning
        app._sync_queue()
        await pilot.pause()
        assert ko_tabell.cursor_row == 2


@pytest.mark.asyncio
async def test_blandad_ordning_sparas_aven_nar_mpv_ar_sen(store):
    """Blandningen sker i mpv-tråden, efter knapptrycket. Den gamla
    smutsflaggan hann spara den oblandade ordningen och sparade sedan aldrig
    om. Nu styr Player.queue_version när kön skrivs till disk."""

    class LangsamPlayer(FakePlayer):
        def shuffle(self):
            self.shuffled = True
            self.pending = list(reversed(self._queue))   # mpv inte klar än

    player = LangsamPlayer()
    app = make_app(store, player)
    async with app.run_test(size=(120, 32)) as pilot:
        for track in TRACKS:
            player.enqueue(track)
        player.play_index(0)
        app._sync_queue()
        assert store.load_queue()[0] == TRACKS

        app.action_shuffle()
        app._sync_queue()                      # tick innan mpv blandat klart
        player._queue = player.pending         # mpv-tråden blir klar
        app._sync_queue()
        await pilot.pause()

        sparad, _ = store.load_queue()
        assert sparad == list(reversed(TRACKS))
