"""Tester för visualiseringen och layouten.

Samtliga tre buggar här hittades genom att rendera TUI:t, inte genom att
läsa koden:

1. Footern trunkerade tyst och `q Avsluta` försvann vid 80 kolumner.
2. EQ-raden blev en cell för bred och Textual radbröt hela visualiseringen.
3. Två `dock: bottom`-widgetar reserverade inte plats för varandra –
   statusraden ritades ovanpå EQ:ns tre nedersta rader.
"""

from __future__ import annotations

import asyncio
import re

import pytest
from textual.widgets import DataTable, Footer, Input

from test_tui import (TRACKS, FakePlayer, FakeSource, do_search, result_headers,
                      temp_store, wait_until)
from zodyer.app import ZodyerApp
from zodyer.eq import BAR_ACTIVE, BAR_IDLE, BAR_PEAK, REFLECTION, Equalizer, LevelSource


def make_app():
    return ZodyerApp(FakePlayer(), FakeSource(TRACKS), temp_store())


def screenshot_lines(app) -> list[str]:
    svg = app.export_screenshot()
    rows: dict[float, list[str]] = {}
    for y, text in re.findall(r'<text[^>]*y="([\d.]+)"[^>]*>(.*?)</text>', svg, re.S):
        rows.setdefault(float(y), []).append(re.sub(r"<[^>]+>", "", text))
    return [
        "".join(rows[y]).replace("&#160;", " ").rstrip("\n") for y in sorted(rows)
    ]


def _footer_text(app) -> str:
    lines = [line for line in screenshot_lines(app) if line.strip()]
    return lines[-1] if lines else ""


async def footer_line(pilot, app) -> str:
    """Sista raden när footern väl är ritad. Före det är sista icke-tomma
    raden panelernas underkant – och testet mätte fel sak."""
    await wait_until(pilot, lambda: not _footer_text(app).lstrip().startswith("╰"))
    return _footer_text(app)


# ---------- 1. Footern ----------


@pytest.mark.asyncio
@pytest.mark.parametrize("width", [80, 100, 120])
async def test_avsluta_syns_i_footern(width):
    app = make_app()
    async with app.run_test(size=(width, 26)) as pilot:
        await pilot.pause()
        # Med sökfältet fokuserat visar Textual bara de bindningar som
        # faktiskt fungerar där, alltså nästan inga. Det är korrekt beteende
        # men gör att footern måste mätas med listan fokuserad.
        app.query_one("#results", DataTable).focus()
        await pilot.pause()
        footer = await footer_line(pilot, app)
        assert "Avsluta" in footer, f"vid {width} kol: {footer!r}"
        # "/ Sök" är dold sedan hjälprutan tog över discovery-rollen.
        assert "Hjälp" in footer


@pytest.mark.asyncio
async def test_dolda_bindningar_fungerar_anda():
    """show=False döljer i footern men ska inte koppla bort tangenten."""
    player = FakePlayer()
    app = ZodyerApp(player, FakeSource(TRACKS), temp_store())
    async with app.run_test(size=(80, 26)) as pilot:
        await pilot.pause()
        app.query_one("#results", DataTable).focus()
        await pilot.press("full_stop")
        await pilot.press("comma")
        assert player.seeks == [10, -10]
        await pilot.press("0")
        assert player.volume == 75


# ---------- 2. Radbrytning ----------


@pytest.mark.asyncio
@pytest.mark.parametrize("width", [60, 72, 80, 104, 132])
async def test_eq_raden_radbryter_aldrig(width):
    app = make_app()
    async with app.run_test(size=(width, 28)) as pilot:
        await pilot.pause()
        eq = app.query_one("#eq", Equalizer)
        eq.refresh_bars(0.9, True)
        await pilot.pause()
        lines = str(eq.content).split("\n")
        assert len(lines) == eq.rows_count + 1, "6 stapelrader + 1 spegling"
        for line in lines:
            assert len(line) <= eq.content_size.width, (
                f"{len(line)} tecken i {eq.content_size.width} kolumner"
            )


# ---------- 3. Layouten ----------


@pytest.mark.asyncio
@pytest.mark.parametrize("height", [24, 28, 32, 40])
async def test_widgetarna_overlappar_inte(height):
    app = make_app()
    async with app.run_test(size=(104, height)) as pilot:
        await pilot.pause()
        summa = (
            1                                              # Header
            + app.query_one("#search", Input).size.height
            + app.query_one("#panes").size.height
            + app.query_one("#eq", Equalizer).size.height
            + app.query_one("#now").size.height
            + 1                                            # Footer
        )
        assert summa == height, f"widgetarna summerar till {summa} av {height} rader"


@pytest.mark.asyncio
async def test_eq_ar_alltid_sju_rader_hog():
    app = make_app()
    async with app.run_test(size=(104, 30)) as pilot:
        await pilot.pause()
        assert app.query_one("#eq", Equalizer).size.height == 7


@pytest.mark.asyncio
async def test_e_slar_av_och_pa_eq():
    app = make_app()
    async with app.run_test(size=(104, 30)) as pilot:
        await pilot.pause()
        app.query_one("#results", DataTable).focus()
        eq = app.query_one("#eq", Equalizer)
        assert eq.display is True
        await pilot.press("e")
        await pilot.pause()
        assert eq.display is False
        await pilot.press("e")
        await pilot.pause()
        assert eq.display is True


# ---------- Palett och beteende ----------


def test_alla_palettfarger_anvands():
    eq = Equalizer(rows=6)
    eq.source = LevelSource(bands=16, rows=6)
    for _ in range(12):
        heights = eq.source.update(0.85, True)
    text = eq._build(heights, eq.source.caps())
    styles = {str(span.style) for span in text.spans}
    for color in (BAR_ACTIVE, BAR_PEAK, BAR_IDLE, REFLECTION):
        assert color in styles, f"{color} saknas i renderingen"


def test_staplarna_faller_nar_uppspelningen_pausas():
    source = LevelSource(bands=8, rows=6)
    for _ in range(10):
        source.update(0.9, True)
    hojd_spelande = sum(source.update(0.9, True))
    for _ in range(8):
        source.update(None, False)
    assert sum(source._values) < 0.05, "staplarna ska ebba ut vid paus"
    assert hojd_spelande > 0


def test_niva_none_ger_dekorativt_lage_inte_stillastaende():
    """Utan mpv-nivå ska EQ:n fortfarande röra sig, inte frysa."""
    source = LevelSource(bands=8, rows=6)
    prov = [tuple(source.update(None, True)) for _ in range(8)]
    assert len(set(prov)) > 1, "dekorativt läge ska variera"
    assert all(any(v > 0 for v in p) for p in prov[3:])


def test_hogre_niva_ger_hogre_staplar():
    tyst, hogt = LevelSource(bands=16, rows=6), LevelSource(bands=16, rows=6)
    for _ in range(15):
        tyst.update(0.15, True)
        hogt.update(0.95, True)
    assert sum(hogt._values) > sum(tyst._values) * 2


def test_bandantal_anpassas_efter_bredd():
    for width, forvantat in [(30, 10), (60, 20), (99, 33)]:
        source = LevelSource(bands=max(4, (width + 1) // 3), rows=6)
        assert source.bands == forvantat


@pytest.mark.asyncio
async def test_footern_ar_avskalad_medan_sokfaltet_har_fokus():
    """Dokumenterar beteendet: bokstavsbindningarna göms där de inte gäller."""
    app = make_app()
    async with app.run_test(size=(100, 26)) as pilot:
        await pilot.pause()
        assert app.query_one("#search", Input).has_focus
        footer = await footer_line(pilot, app)
        assert "Paus" not in footer


# ---------- Resultattabellens kolumner ----------


@pytest.mark.asyncio
@pytest.mark.parametrize("width", [70, 80, 104, 132, 160])
async def test_kolumnerna_ryms_utan_horisontell_scroll(width):
    """Regressionen: Titel växte efter längsta träffen och tryckte ut resten."""
    app = make_app()
    async with app.run_test(size=(width, 30)) as pilot:
        await pilot.pause()
        await do_search(pilot, app)
        table = app.query_one("#results", DataTable)
        assert table.virtual_size.width <= table.content_size.width, (
            f"{width} kol: tabellen är {table.virtual_size.width} bred i "
            f"{table.content_size.width} tillgängliga"
        )


@pytest.mark.asyncio
async def test_alla_kolumner_visas_i_brett_fonster():
    app = make_app()
    async with app.run_test(size=(140, 30)) as pilot:
        await wait_until(pilot, lambda: result_headers(app))
        rubriker = result_headers(app)
        assert rubriker == ["Titel", "Artist", "Album", "År", "Längd"]


@pytest.mark.asyncio
async def test_kolumner_offras_i_prioritetsordning_nar_det_blir_trangt():
    app = make_app()
    async with app.run_test(size=(80, 30)) as pilot:
        await wait_until(pilot, lambda: result_headers(app))
        rubriker = result_headers(app)
        assert "Titel" in rubriker and "Artist" in rubriker
        assert "År" not in rubriker, "År är minst värdefull och ska offras först"


@pytest.mark.asyncio
async def test_titel_klipps_med_ellips_inte_med_bredare_kolumn():
    app = make_app()
    async with app.run_test(size=(70, 30)) as pilot:
        await do_search(pilot, app)
        table = app.query_one("#results", DataTable)
        titel_bredd = app._results_layout[0]
        for rad in range(table.row_count):
            cell = table.get_cell_at((rad, 0))
            assert len(cell.plain) <= titel_bredd


@pytest.mark.asyncio
async def test_artist_och_album_syns_i_cellerna():
    app = make_app()
    async with app.run_test(size=(140, 30)) as pilot:
        await do_search(pilot, app)
        table = app.query_one("#results", DataTable)
        assert table.get_cell_at((0, 1)).plain == "Artist A"
        assert table.get_cell_at((0, 2)).plain == "Album A"


def test_ar_hamtas_fran_apiet_nar_det_finns():
    from zodyer.source import _to_track
    utan = _to_track({"videoId": "a", "title": "T", "artists": [{"name": "A"}]})
    assert utan.year is None
    med = _to_track({"videoId": "a", "title": "T", "artists": [{"name": "A"}],
                     "year": "1994"})
    assert med.year == "1994"


# ---------- Tangentbindningar ----------


def test_inga_skiftlagskollisioner():
    """Textual skiljer inte på 'S' och 's' – den först definierade vinner.

    Den kollisionen gjorde att S (spara) tyst blev s (stoppa) och rensade
    kön i stället för att spara den.
    """
    nycklar = [b.key for b in ZodyerApp.BINDINGS]
    normaliserade = [k.lower() for k in nycklar]
    dubbletter = {k for k in normaliserade if normaliserade.count(k) > 1}
    assert not dubbletter, f"kolliderande bindningar: {dubbletter}"


def test_inga_versaler_som_enskild_tangent():
    """En ensam versal fungerar inte som bindning. Använd ctrl+ i stället."""
    ensamma = [b.key for b in ZodyerApp.BINDINGS if len(b.key) == 1]
    assert all(k.islower() or not k.isalpha() for k in ensamma), ensamma


@pytest.mark.asyncio
async def test_ctrl_c_rensar_utan_att_avsluta():
    """Textual kör raw mode, så ctrl+c är en tangent och inte SIGINT."""
    player = FakePlayer()
    app = ZodyerApp(player, FakeSource(TRACKS), temp_store())
    async with app.run_test(size=(100, 28)) as pilot:
        await do_search(pilot, app)
        app.query_one("#results", DataTable).focus()
        await pilot.pause()
        await pilot.press("enter")
        await pilot.press("down")
        await pilot.press("a")
        await pilot.pause()
        assert len(player.queue) == 2

        await pilot.press("ctrl+c")
        await pilot.pause()
        assert player.queue == []
        assert app.is_running, "ctrl+c får inte avsluta programmet"
        assert "Rensade" in app.message


@pytest.mark.asyncio
async def test_ctrl_s_sparar_i_stallet_for_att_stoppa():
    """Regressionen: S träffade action_stop och rensade kön."""
    from zodyer.screens import SaveScreen

    player = FakePlayer()
    app = ZodyerApp(player, FakeSource(TRACKS), temp_store())
    async with app.run_test(size=(110, 30)) as pilot:
        await do_search(pilot, app)
        app.query_one("#results", DataTable).focus()
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()

        await pilot.press("ctrl+s")
        await pilot.pause()
        assert isinstance(app.screen, SaveScreen)
        assert len(player.queue) == 1, "kön ska vara orörd"


@pytest.mark.asyncio
async def test_ctrl_c_och_ctrl_s_syns_i_footern():
    app = ZodyerApp(FakePlayer(), FakeSource(TRACKS), temp_store())
    async with app.run_test(size=(90, 28)) as pilot:
        await pilot.pause()
        app.query_one("#results", DataTable).focus()
        await pilot.pause()
        footer = await footer_line(pilot, app)
        for tecken in ("Rensa", "Avsluta", "Spara", "Listor"):
            assert tecken in footer, f"{tecken} saknas: {footer!r}"


@pytest.mark.asyncio
async def test_inga_destruktiva_enbokstavsbindningar():
    """Inget som avbryter eller förstör får ligga på en ensam bokstav.

    c låg en felträff från a och tömde kön; n och b hoppade spår mitt i låten.
    """
    farliga = {"clear_queue", "stop", "quit", "next", "previous"}
    for binding in ZodyerApp.BINDINGS:
        if binding.action in farliga:
            assert not (len(binding.key) == 1 and binding.key.isalpha()), (
                f'"{binding.key}" -> {binding.action} är för lätt att träffa'
            )


@pytest.mark.asyncio
async def test_c_gor_ingenting_langre():
    player = FakePlayer()
    app = ZodyerApp(player, FakeSource(TRACKS), temp_store())
    async with app.run_test(size=(100, 28)) as pilot:
        await do_search(pilot, app)
        app.query_one("#results", DataTable).focus()
        await pilot.pause()
        await pilot.press("enter")
        await pilot.press("down")
        await pilot.press("a")
        await pilot.pause()
        assert len(player.queue) == 2

        await pilot.press("c")
        await pilot.pause()
        assert len(player.queue) == 2, "c ska inte längre röra kön"


@pytest.mark.asyncio
async def test_q_avslutar_inte_langre():
    """Enda vägen ut ska vara Ctrl+Q."""
    app = ZodyerApp(FakePlayer(), FakeSource(TRACKS), temp_store())
    async with app.run_test(size=(100, 28)) as pilot:
        await pilot.pause()
        app.query_one("#results", DataTable).focus()
        await pilot.press("q")
        await pilot.pause()
        assert app.is_running


@pytest.mark.asyncio
async def test_bortflyttade_atgarder_finns_i_kommandopaletten():
    app = ZodyerApp(FakePlayer(), FakeSource(TRACKS), temp_store())
    async with app.run_test(size=(100, 28)) as pilot:
        await pilot.pause()
        titlar = [c.title for c in app.get_system_commands(app.screen)]
        assert any("behåll spåret" in t for t in titlar)
        assert any("Rensa kön helt" in t for t in titlar)


@pytest.mark.asyncio
async def test_n_och_b_hoppar_inte_langre_spar():
    player = FakePlayer()
    app = ZodyerApp(player, FakeSource(TRACKS), temp_store())
    async with app.run_test(size=(100, 28)) as pilot:
        await do_search(pilot, app)
        app.query_one("#results", DataTable).focus()
        await pilot.pause()
        await pilot.press("enter")
        await pilot.press("down")
        await pilot.press("a")
        await pilot.pause()
        assert player.index == 0

        await pilot.press("n")
        await pilot.press("b")
        await pilot.pause()
        assert player.index == 0, "bokstäverna ska inte längre flytta spår"

        await pilot.press("ctrl+n")
        await pilot.pause()
        assert player.index == 1
        await pilot.press("ctrl+b")
        await pilot.pause()
        assert player.index == 0


def test_endast_ofarliga_bokstavsbindningar_kvar():
    """Dokumenterar vad som får ligga kvar på en ensam tangent."""
    bokstaver = {b.key for b in ZodyerApp.BINDINGS
                 if len(b.key) == 1 and b.key.isalpha()}
    assert bokstaver == {"p", "a", "l", "e", "b", "r"}, bokstaver


@pytest.mark.asyncio
@pytest.mark.parametrize("width", [70, 80, 88, 110, 140])
async def test_kritiska_bindningar_kapas_aldrig(width):
    """Footern trunkerar tyst. Rensa, Avsluta, Spara och Listor måste överleva."""
    app = ZodyerApp(FakePlayer(), FakeSource(TRACKS), temp_store())
    async with app.run_test(size=(width, 28)) as pilot:
        await pilot.pause()
        app.query_one("#results", DataTable).focus()
        await asyncio.sleep(0.6)
        await pilot.pause()
        footer = await footer_line(pilot, app)
        for text in ("Rensa", "Avsluta", "Spara", "Hjälp"):
            assert text in footer, f"{text} kapad vid {width} kol: {footer!r}"


@pytest.mark.asyncio
async def test_footern_packas_tatare_i_smalt_fonster():
    smal = ZodyerApp(FakePlayer(), FakeSource(TRACKS), temp_store())
    async with smal.run_test(size=(90, 28)) as pilot:
        await pilot.pause()
        await asyncio.sleep(0.6)
        assert smal.query_one(Footer).compact is True

    bred = ZodyerApp(FakePlayer(), FakeSource(TRACKS), temp_store())
    async with bred.run_test(size=(130, 28)) as pilot:
        await pilot.pause()
        await asyncio.sleep(0.6)
        assert bred.query_one(Footer).compact is False
