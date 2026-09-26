"""Modaler för spellistehantering.

Layouten är redan full — sökfält, resultat, kö, EQ, statusrad, footer. En
fjärde panel hade tryckt ihop resultatlistan på ett 80 kolumner brett
fönster. Modaler kostar noll utrymme när de är stängda.
"""

from __future__ import annotations

from rich.text import Text
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import DataTable, Input, Label

from .library import PlaylistStore


class SaveScreen(ModalScreen[str | None]):
    """Fråga efter namn. Returnerar namnet, eller None vid avbrott."""

    BINDINGS = [Binding("escape", "avbryt", "Avbryt")]

    def __init__(self, store: PlaylistStore, antal: int, forslag: str = "") -> None:
        super().__init__()
        self.store = store
        self.antal = antal
        self.forslag = forslag

    def compose(self):
        with Vertical(id="dialog"):
            yield Label(f"Spara kön ({self.antal} spår) som spellista", id="dialog-title")
            yield Input(value=self.forslag, placeholder="Namn", id="playlist-name")
            yield Label("Enter sparar, Escape avbryter", id="dialog-hint")

    def on_mount(self) -> None:
        namn = self.query_one("#playlist-name", Input)
        namn.focus()
        # Markören sist, inte i början, så förslaget går att bygga vidare på.
        namn.cursor_position = len(namn.value)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        namn = event.value.strip()
        self.dismiss(namn or None)

    def action_avbryt(self) -> None:
        self.dismiss(None)


class LibraryScreen(ModalScreen[tuple[str, str] | None]):
    """Lista sparade spellistor.

    Returnerar (åtgärd, namn) där åtgärd är "load" eller "enqueue",
    eller None om användaren stängde utan att välja.
    """

    BINDINGS = [
        Binding("escape", "avbryt", "Stäng"),
        Binding("enter", "ladda", "Ladda"),
        Binding("a", "koa", "Lägg i kö"),
        Binding("d", "ta_bort", "Ta bort"),
    ]

    def __init__(self, store: PlaylistStore) -> None:
        super().__init__()
        self.store = store
        #: Listan som väntar på ett andra d. Radering går inte att ångra,
        #: så ett ensamt tryck – kanske en felträff från a – räcker inte.
        self._bekrafta: str | None = None

    HINT = "Enter laddar  ·  a lägger i kön  ·  d tar bort  ·  Escape stänger"

    def compose(self):
        with Vertical(id="dialog"):
            yield Label("Sparade spellistor", id="dialog-title")
            yield DataTable(id="playlists", cursor_type="row")
            yield Label(self.HINT, id="dialog-hint")

    def on_mount(self) -> None:
        tabell = self.query_one("#playlists", DataTable)
        tabell.add_column("Namn", width=32)
        tabell.add_column("Spår", width=6)
        tabell.add_column("Ändrad", width=16)
        self._fyll()
        tabell.focus()

    def _fyll(self) -> None:
        tabell = self.query_one("#playlists", DataTable)
        tabell.clear()
        namn = self.store.names()
        for n in namn:
            info = self.store.info(n) or {}
            andrad = str(info.get("updated", ""))[:16].replace("T", " ")
            tabell.add_row(
                Text(n[:32], no_wrap=True),
                Text(str(len(info.get("tracks", []))), no_wrap=True),
                Text(andrad, no_wrap=True, style="dim"),
            )
        if not namn:
            self.query_one("#dialog-hint", Label).update(
                "Inga sparade spellistor än. Tryck Ctrl+S i huvudvyn för att spara kön."
            )

    def _valt(self) -> str | None:
        tabell = self.query_one("#playlists", DataTable)
        namn = self.store.names()
        rad = tabell.cursor_row
        if 0 <= rad < len(namn):
            return namn[rad]
        return None

    def action_ladda(self) -> None:
        namn = self._valt()
        if namn:
            self.dismiss(("load", namn))

    def action_koa(self) -> None:
        namn = self._valt()
        if namn:
            self.dismiss(("enqueue", namn))

    def action_ta_bort(self) -> None:
        namn = self._valt()
        if not namn:
            return
        hint = self.query_one("#dialog-hint", Label)
        if self._bekrafta != namn:
            self._bekrafta = namn
            hint.update(f"Ta bort \"{namn}\"? Tryck d igen för att bekräfta.")
            return
        self._bekrafta = None
        self.store.delete(namn)
        hint.update(self.HINT)
        self._fyll()

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        # Flyttar man markören gäller inte längre frågan om förra listan.
        if self._bekrafta and self._bekrafta != self._valt():
            self._bekrafta = None
            self.query_one("#dialog-hint", Label).update(self.HINT)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        self.action_ladda()

    def action_avbryt(self) -> None:
        self.dismiss(None)


class HelpScreen(ModalScreen[None]):
    """Alla tangenter, genererade ur BINDINGS.

    Footern rymmer bara en handfull. I stället för att krympa beskrivningar
    tills något kapas tyst visas hela listan här, och den kan aldrig hamna
    ur synk med de faktiska bindningarna.
    """

    BINDINGS = [
        Binding("escape", "avbryt", "Stäng"),
        Binding("question_mark", "avbryt", "Stäng"),
    ]

    #: Snyggare namn än Textuals interna tangentnamn.
    VISNING = {
        "slash": "/",
        "comma": ",",
        "full_stop": ".",
        "question_mark": "?",
        "delete": "Delete",
    }

    def __init__(self, bindings) -> None:
        super().__init__()
        self.bindings = list(bindings)

    def compose(self):
        with Vertical(id="dialog"):
            yield Label("Tangenter", id="dialog-title")
            yield DataTable(id="help", cursor_type="row", show_header=False)
            yield Label("Escape stänger", id="dialog-hint")

    def on_mount(self) -> None:
        tabell = self.query_one("#help", DataTable)
        tabell.add_column("Tangent", width=12)
        tabell.add_column("Funktion", width=34)
        for binding in self.bindings:
            if not binding.description:
                continue
            tangent = self.VISNING.get(binding.key, binding.key)
            tangent = tangent.replace("ctrl+", "Ctrl+")
            tabell.add_row(
                Text(tangent, no_wrap=True, style="bold"),
                Text(binding.description, no_wrap=True),
            )
        tabell.focus()

    def action_avbryt(self) -> None:
        self.dismiss(None)
