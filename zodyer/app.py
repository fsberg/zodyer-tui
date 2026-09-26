"""TUI:t. Textual-appen är avsiktligt tunn – all logik ligger i player/source."""

from __future__ import annotations

import time

from rich.text import Text
from textual import work
from textual.app import App, ComposeResult, SystemCommand
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import DataTable, Footer, Header, Input, Static

from .eq import Equalizer
from .library import PlaylistStore
from .models import Track
from .screens import HelpScreen, LibraryScreen, SaveScreen
from .player import Player
from .source import YTMusicSource


def _mmss(seconds: float) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds // 60}:{seconds % 60:02d}"


class NowPlaying(Static):
    """Statusrad längst ned: spår, progressbar, volym."""

    def render_status(self, status, message: str = "") -> None:
        if status.track is None:
            self.update(Text(message or "Inget spelas. Tryck / för att söka.", style="dim"))
            return

        width = 30
        filled = 0
        if status.duration > 0:
            filled = int(width * min(1.0, status.position / status.duration))
        bar = "━" * filled + "─" * (width - filled)
        symbol = "▮▮" if status.paused else "▶ "

        line = Text()
        line.append(f"{symbol} ", style="bold")
        line.append(str(status.track), style="bold")
        line.append(f"\n{bar} ", style="cyan")
        line.append(f"{_mmss(status.position)} / {_mmss(status.duration)}")
        line.append(f"   vol {status.volume}%", style="dim")
        if getattr(status, "repeat", "off") == "all":
            line.append("   ⟳ alla", style="cyan")
        elif getattr(status, "repeat", "off") == "one":
            line.append("   ⟳ ett", style="cyan")
        if getattr(status, "shuffled", False):
            line.append("   ⤨ blandad", style="cyan")
        if getattr(status, "stale", False):
            line.append("   (laddar…)", style="dim italic")
        if message:
            line.append(f"   {message}", style="yellow")
        self.update(line)


class ZodyerApp(App):
    """Terminalklient för YouTube Music."""

    CSS_PATH = "app.tcss"
    TITLE = "Zodyer"

    # Footern rymmer omkring sju poster och trunkerar tyst resten – förr
    # försvann "q Avsluta" vid 80 kolumner. Bara det mest använda är
    # show=True; hela listan finns bakom "?".
    #
    # Textual skiljer inte på gemener och versaler i tangentnamn: "S" och
    # "s" är samma bindning och den först definierade vinner. Därför ligger
    # spara på Ctrl+S. test_inga_skiftlagskollisioner vaktar det. Enskilda
    # bokstäver används bara till ofarliga åtgärder.
    #
    # Ctrl+C blir en tangenthändelse, inte SIGINT: Textual kör terminalen i
    # raw mode. Ctrl+Q är Textuals egen avslutsbindning med priority=True.
    BINDINGS = [
        Binding("ctrl+c", "stop", "Rensa", priority=True),
        Binding("ctrl+q", "quit", "Avsluta", priority=True),
        Binding("question_mark", "help", "Hjälp"),
        Binding("ctrl+s", "save_playlist", "Spara"),
        Binding("l", "library", "Listor"),
        # Sist bland de synliga: i smala fönster är det de här som offras,
        # och de står ändå i hjälprutan.
        Binding("b", "shuffle", "Blanda"),
        Binding("r", "repeat", "Upprepa"),
        Binding("slash", "focus_search", "Sök", show=False),
        Binding("ctrl+f", "focus_search", "Sök", show=False),
        Binding("p", "toggle_pause", "Paus / spela", show=False),
        Binding("a", "enqueue", "Lägg i kön", show=False),
        Binding("ctrl+n", "next", "Nästa spår", show=False),
        Binding("ctrl+b", "previous", "Föregående spår", show=False),
        Binding("delete", "remove_track", "Ta bort markerat spår ur kön", show=False),
        Binding("ctrl+d", "remove_track", "Ta bort markerat spår ur kön", show=False),
        Binding("comma", "rewind", "Spola 10 s bakåt", show=False),
        Binding("full_stop", "forward", "Spola 10 s framåt", show=False),
        Binding("9", "volume_down", "Volym ner", show=False),
        Binding("0", "volume_up", "Volym upp", show=False),
        Binding("e", "toggle_eq", "EQ av eller på", show=False),
    ]

    def __init__(
        self,
        player: Player,
        source: YTMusicSource,
        store: PlaylistStore | None = None,
    ) -> None:
        super().__init__()
        self.player = player
        self.source = source
        self.store = store or PlaylistStore()
        self.results: list[Track] = []
        self.message = ""
        self._message_until = 0.0
        #: (queue_version, queue_index) som kötabellen senast ritades för.
        self._queue_drawn: tuple[int, int] | None = None
        #: (queue_version, index) som senast skrevs till disk.
        self._queue_saved: tuple[int, int] | None = None
        # Sätts här och inte i on_mount: on_resize kan komma först.
        self._results_layout: tuple | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        yield Input(placeholder="Sök efter låt eller artist…", id="search")
        with Horizontal(id="panes"):
            yield DataTable(id="results", cursor_type="row")
            yield DataTable(id="queue", cursor_type="row")
        yield Equalizer(rows=6, id="eq")
        yield NowPlaying(id="now")
        yield Footer()

    def on_mount(self) -> None:
        results = self.query_one("#results", DataTable)
        results.border_title = "Sökresultat"
        # Widgetarna har ingen bredd än under on_mount – vänta på första
        # ritningen innan kolumnbredderna beräknas.
        self.call_after_refresh(self._ensure_columns)

        queue = self.query_one("#queue", DataTable)
        queue.add_columns("#", "Spår")
        queue.border_title = "Kö"

        self.set_interval(0.5, self._tick)
        self.set_interval(0.1, self._eq_tick)
        self.query_one("#search", Input).focus()
        # Sist i on_mount: kötabellen måste ha sina kolumner först.
        self._restore_queue()

    # ---- Spellistor ----

    def _restore_queue(self) -> None:
        if self.store.last_error:
            self._set_message(f"Biblioteket: {self.store.last_error}", 10.0)
        tracks, index = self.store.load_queue()
        if not tracks:
            return
        self.player.restore(tracks, index)
        # Redan på disk – skriv inte tillbaka samma kö, och inte med index -1
        # bara för att inget spelar än.
        self._queue_saved = (self.player.queue_version, index)
        self._refresh_queue()
        self._set_message(f"Återställde {len(tracks)} spår. Tryck p för att spela.", 8.0)

    def _sync_queue(self) -> None:
        """Rita om och spara kön när spelaren säger att den ändrats.

        Styrs av Player.queue_version i stället för av knapptrycken:
        blandning och borttagning sker i mpv-tråden, och en flagga satt vid
        knapptrycket hann spara och rita den gamla ordningen.
        """
        version = self.player.queue_version
        index = self.player.status().queue_index

        if (version, index) != self._queue_drawn:
            self._queue_drawn = (version, index)
            self._refresh_queue()

        saved_index = self._queue_saved[1] if self._queue_saved else -1
        # -1 betyder "spelar inget just nu", inte "börja om" – behåll då det
        # senast sparade spåret så att nästa start fortsätter därifrån.
        spara_index = index if index >= 0 else saved_index
        if (version, spara_index) != self._queue_saved:
            self._queue_saved = (version, spara_index)
            self.store.save_queue(self.player.queue, spara_index)

    def action_save_playlist(self) -> None:
        ko = self.player.queue
        if not ko:
            self._set_message("Kön är tom – inget att spara.")
            return
        forslag = ko[0].artist if len(ko) == 1 else ""
        self.push_screen(SaveScreen(self.store, len(ko), forslag), self._spara_klar)

    def _spara_klar(self, namn: str | None) -> None:
        if not namn:
            return
        ko = self.player.queue
        if self.store.save(namn, ko):
            self._set_message(f"Sparade \"{namn}\" ({len(ko)} spår).")
        else:
            self._set_message(f"Kunde inte spara: {self.store.last_error}", 10.0)

    def action_library(self) -> None:
        self.push_screen(LibraryScreen(self.store), self._bibliotek_klar)

    def _bibliotek_klar(self, val: tuple[str, str] | None) -> None:
        if not val:
            return
        atgard, namn = val
        tracks = self.store.load(namn)
        if not tracks:
            self._set_message(f"\"{namn}\" är tom.")
            return

        if atgard == "load":
            self.player.play_now(tracks[0])
            for track in tracks[1:]:
                self.player.enqueue(track)
            self._set_message(f"Spelar \"{namn}\" ({len(tracks)} spår).")
        else:
            for track in tracks:
                self.player.enqueue(track)
            self._set_message(f"Köade {len(tracks)} spår från \"{namn}\".")
        self._refresh_queue()

    # ---- Resultattabellens kolumner ----

    #: (rubrik, bredd). Utan uttryckliga bredder växer Titel efter den
    #: längsta träffen och trycker ut resten ur bild bakom en scrollbar.
    RESULT_COLUMNS = (("Artist", 16), ("Album", 16), ("År", 4), ("Längd", 5))
    #: Ordning kolumner offras i när det blir trångt. Titel offras aldrig.
    DROP_ORDER = ("År", "Album", "Artist", "Längd")
    #: Titel får aldrig bli smalare än så här utan att en kolumn stryks.
    MIN_TITLE = 18
    #: Textuals DataTable lägger en cells padding på varje sida.
    CELL_PADDING = 2

    def _column_layout(self) -> tuple:
        """Vilka kolumner ryms, och hur bred blir Titel?"""
        bredd = self.query_one("#results", DataTable).content_size.width
        if bredd <= 0:
            return (self.MIN_TITLE, ())

        valda = dict(self.RESULT_COLUMNS)
        for offer in (None, *self.DROP_ORDER):
            if offer:
                valda.pop(offer, None)
            upptaget = sum(w + self.CELL_PADDING for w in valda.values())
            titel = bredd - upptaget - self.CELL_PADDING
            if titel >= self.MIN_TITLE:
                break
        titel = max(8, titel)
        ordnade = tuple((n, w) for n, w in self.RESULT_COLUMNS if n in valda)
        return (titel, ordnade)

    #: Under så här många kolumner packas footern tätare. Textual trunkerar
    #: tyst när bindningarna inte får plats, och den som försvinner då är den
    #: sist definierade – inte den minst viktiga.
    COMPACT_FOOTER_BELOW = 104
    def _fit_footer(self) -> None:
        footers = self.query(Footer)
        if not footers:
            return
        footers.first(Footer).compact = self.size.width < self.COMPACT_FOOTER_BELOW

    def _ensure_columns(self) -> bool:
        """Bygg om kolumnerna om bredden ändrats. Returnerar True vid ombygge.

        Anropas från _tick i stället för on_resize: on_resize hann komma innan
        widgetarna hade någon bredd, och kördes sedan aldrig om.
        """
        layout = self._column_layout()
        if layout == self._results_layout:
            return False
        self._results_layout = layout
        titel_bredd, valda = layout

        table = self.query_one("#results", DataTable)
        table.clear(columns=True)
        table.add_column("Titel", width=titel_bredd)
        for namn, bredd in valda:
            table.add_column(namn, width=bredd)
        self._fill_results()
        return True

    @staticmethod
    def _fit(text: str, width: int) -> str:
        """Klipp med ellips i stället för att låta kolumnen växa."""
        if len(text) <= width:
            return text
        return text[: max(1, width - 1)] + "…"

    def _fill_results(self) -> None:
        table = self.query_one("#results", DataTable)
        table.clear()
        if not self._results_layout:
            return
        titel_bredd, valda = self._results_layout
        varden = {
            "Artist": lambda t: (t.artist, ""),
            "Album": lambda t: (t.album or "", "dim"),
            "År": lambda t: (t.year or "", "dim"),
            "Längd": lambda t: (t.duration or "", ""),
        }
        for track in self.results:
            celler = [Text(self._fit(track.title, titel_bredd), no_wrap=True)]
            for namn, bredd in valda:
                text, stil = varden[namn](track)
                celler.append(Text(self._fit(text, bredd), no_wrap=True, style=stil))
            table.add_row(*celler)

    def _set_message(self, text: str, seconds: float = 6.0) -> None:
        """Meddelanden ska försvinna av sig själva, annars står gamla fel kvar."""
        self.message = text
        self._message_until = time.monotonic() + seconds if text else 0.0

    # ---- Sökning ----

    def on_input_submitted(self, event: Input.Submitted) -> None:
        query = event.value.strip()
        if query:
            self._set_message("Söker…", 60.0)
            self.search(query)

    @work(thread=True, exclusive=True, group="search")
    def search(self, query: str) -> None:
        """Nätverksanrop – måste ligga utanför UI-tråden."""
        try:
            tracks = self.source.search(query)
        except Exception as exc:  # nätverk, API-ändringar, rate limiting
            self.call_from_thread(self._search_failed, str(exc))
            return
        self.call_from_thread(self._show_results, tracks)

    def _show_results(self, tracks: list[Track]) -> None:
        self.results = tracks
        if not self._ensure_columns():
            self._fill_results()
        self._set_message("" if tracks else "Inga träffar.")
        if tracks:
            self.query_one("#results", DataTable).focus()

    def _search_failed(self, error: str) -> None:
        self._set_message(f"Sökning misslyckades: {error}", 10.0)

    # ---- Uppspelning ----

    def _selected_track(self) -> Track | None:
        table = self.query_one("#results", DataTable)
        row = table.cursor_row
        if 0 <= row < len(self.results):
            return self.results[row]
        return None

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.data_table.id != "results":
            return
        track = self._selected_track()
        if track:
            self.player.play_now(track)
            self._refresh_queue()

    def action_enqueue(self) -> None:
        track = self._selected_track()
        if track:
            self.player.enqueue(track)
            self._set_message(f"Köad: {track.title}")
            self._refresh_queue()

    def action_clear_queue(self) -> None:
        innan = len(self.player.queue)
        self.player.clear_queue()
        kvar = len(self.player.queue)
        self._set_message(
            f"Tog bort {innan - kvar} spår, behöll det som spelas."
            if innan > kvar
            else "Inget att ta bort – bara aktuellt spår i kön."
        )
        self._refresh_queue()

    def action_stop(self) -> None:
        antal = len(self.player.queue)
        self.player.stop()
        self._refresh_queue()
        self._set_message(f"Rensade kön ({antal} spår)." if antal else "Kön var redan tom.")

    def action_toggle_pause(self) -> None:
        self.player.toggle_pause()

    def action_next(self) -> None:
        self.player.next()

    def action_previous(self) -> None:
        self.player.previous()

    def action_forward(self) -> None:
        self.player.seek(10)

    def action_rewind(self) -> None:
        self.player.seek(-10)

    def action_volume_up(self) -> None:
        self.player.change_volume(5)

    def action_volume_down(self) -> None:
        self.player.change_volume(-5)

    def get_system_commands(self, screen):
        """Kommandopaletten (Ctrl+P).

        Hit flyttas åtgärder som är nyttiga men för farliga att ha på en
        enskild bokstav. "c" tömde kön och satt en felträff från "a".
        """
        yield from super().get_system_commands(screen)
        yield SystemCommand(
            "Töm kön, behåll spåret som spelas",
            "Tar bort allt utom det som spelas just nu",
            self.action_clear_queue,
        )
        yield SystemCommand(
            "Rensa kön helt",
            "Stoppar uppspelningen och tömmer kön (Ctrl+C)",
            self.action_stop,
        )
        yield SystemCommand(
            "EQ av eller på",
            "Döljer visualiseringen och ger sju rader till listorna",
            self.action_toggle_eq,
        )

    def action_remove_track(self) -> None:
        """Ta bort markerat spår ur kön. Bara när kötabellen har fokus."""
        tabell = self.query_one("#queue", DataTable)
        if not tabell.has_focus:
            self._set_message("Tryck Tab till kön först, sedan Delete.")
            return
        ko = self.player.queue
        rad = tabell.cursor_row
        if not 0 <= rad < len(ko):
            return
        titel = ko[rad].title
        self.player.remove(rad)
        self._set_message(f"Tog bort {titel}.")

    def action_shuffle(self) -> None:
        if self.player.status().shuffled:
            self.player.unshuffle()
            self._set_message("Återställde ordningen.")
        else:
            self.player.shuffle()
            self._set_message("Blandade kön.")

    def action_repeat(self) -> None:
        lage = self.player.cycle_repeat()
        self._set_message(
            {"off": "Upprepning av.",
             "all": "Upprepar hela kön.",
             "one": "Upprepar detta spår."}[lage]
        )

    def action_help(self) -> None:
        self.push_screen(HelpScreen(self.BINDINGS))

    def action_toggle_eq(self) -> None:
        eq = self.query_one("#eq", Equalizer)
        eq.display = not eq.display

    def action_focus_search(self) -> None:
        self.query_one("#search", Input).focus()

    # ---- Löpande uppdatering ----

    def _tick(self) -> None:
        # Uppspelningsfel uppstår i mpv-tråden och plockas upp här. Utan detta
        # hoppas spår som yt-dlp inte kan resolva över helt tyst.
        for error in self.player.take_errors():
            self._set_message(error, 8.0)

        if self._message_until and time.monotonic() > self._message_until:
            self.message = ""
            self._message_until = 0.0

        now = self.query("#now")
        if not now:
            return
        self._ensure_columns()
        self._fit_footer()
        self._sync_queue()
        now.first(NowPlaying).render_status(self.player.status(), self.message)

    def _eq_tick(self) -> None:
        # Intervallet kan trigga innan compose är klar och under nedstängning.
        widgets = self.query("#eq")
        if not widgets:
            return
        eq = widgets.first(Equalizer)
        if not eq.display:
            return
        status = self.player.status()
        playing = status.track is not None and not status.paused
        eq.refresh_bars(self.player.audio_level(), playing)

    def _refresh_queue(self) -> None:
        tables = self.query("#queue")
        if not tables:
            return
        status = self.player.status()
        table = tables.first(DataTable)
        # clear() flyttar markören till första raden. Utan detta kunde en
        # omritning mitt i navigeringen få Delete att ta bort fel spår.
        rad = table.cursor_row
        table.clear()
        ko = self.player.queue
        for i, track in enumerate(ko):
            marker = "▶" if i == status.queue_index else str(i + 1)
            style = "bold" if i == status.queue_index else ""
            table.add_row(Text(marker, style=style), Text(str(track), no_wrap=True, style=style))
        if ko:
            table.move_cursor(row=min(rad, len(ko) - 1), animate=False)

    def on_unmount(self) -> None:
        self._sync_queue()
        self.player.terminate()
