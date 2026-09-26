"""Uppspelning via mpv:s JSON-IPC.

All mpv-kommunikation sker i två egna trådar. Publika metoder blockerar
aldrig anroparen, och det är inte kosmetik:

* python-mpv-jsonipc väntar 120 sekunder på svar innan den ger upp
  (``TIMEOUT`` i biblioteket).
* mpv:s ytdl-hook kör yt-dlp synkront i mpv:s huvudtråd, samma tråd som
  betjänar IPC. Under URL-uppslag svarar mpv alltså inte.

Ett synkront ``status()`` från Textuals UI-tråd kunde därför frysa
gränssnittet i upp till två minuter. Nu läser UI:t bara en cachad
ögonblicksbild, och kommandon läggs i en FIFO-kö som en egen tråd tömmer.

Kön är mpv:s egen spellista. Vi speglar den i en Python-lista för att kunna
visa titlar innan yt-dlp hunnit resolva URL:erna.
"""

from __future__ import annotations

import queue
import threading
from dataclasses import dataclass, replace

from python_mpv_jsonipc import MPV

from .models import Track

POLL_INTERVAL = 0.5
#: Nivåmätaren behöver tätare uppdatering än statusraden.
LEVEL_INTERVAL = 0.1
#: Etikett på ffmpeg-filtret vi lägger till i efterhand för nivåmätning.
_LEVEL_FILTER_LABEL = "zodeq"
_LEVEL_FILTER = f"@{_LEVEL_FILTER_LABEL}:lavfi=[astats=metadata=1:reset=1]"
#: RMS under detta i dBFS räknas som tystnad.
_LEVEL_FLOOR_DB = -55.0

# Properties vi läser. Underscore-namn; biblioteket översätter till bindestreck.
# Kontrolleras mot mpv vid uppstart så att ett felstavat namn inte tyst
# returnerar default för all framtid.
_PROPERTIES = ("time_pos", "duration", "pause", "volume", "playlist_pos", "idle_active")

# Fel som betyder "mpv svarar inte" snarare än "denna property saknas just nu".
# De MÅSTE bubbla upp till pollningen, annars förvandlas ett hängt mpv till
# "position 0, idle" och UI:t visar "Inget spelas" mitt under laddning.
_TRANSPORT_ERRORS = (TimeoutError, OSError)

#: Upprepningslägen. "one" använder mpv:s loop-file, "all" loop-playlist.
REPEAT_MODES = ("off", "all", "one")

# end-file-reasons som är normala och inte ska visas som fel.
_BENIGN_END_REASONS = frozenset({"eof", "stop", "quit", "redirect", ""})


@dataclass(slots=True)
class Status:
    track: Track | None = None
    position: float = 0.0
    duration: float = 0.0
    paused: bool = False
    volume: int = 0
    queue_index: int = -1
    repeat: str = "off"
    shuffled: bool = False
    idle: bool = True
    #: True när senaste pollningen inte fick svar från mpv (t.ex. under
    #: yt-dlp-uppslag). Övriga värden är då de senast kända.
    stale: bool = False


class Player:
    """Tunn, icke-blockerande fasad över mpv."""

    def __init__(
        self,
        mpv_location: str | None = None,
        volume: int = 70,
        poll_interval: float = POLL_INTERVAL,
        level_meter: bool = True,
    ) -> None:
        self._mpv = MPV(
            start_mpv=True,
            mpv_location=mpv_location,
            discard_output=True,
            # --vid=no: bara ljud, inget fönster.
            vid="no",
            ytdl="yes",
            ytdl_format="bestaudio/best",
            volume=volume,
            gapless_audio="yes",
            prefetch_playlist="yes",
            keep_open="no",
        )

        self._lock = threading.RLock()
        self._queue: list[Track] = []
        self._status = Status(volume=volume)
        self._errors: list[str] = []

        self._commands: queue.Queue = queue.Queue()
        self._stop_event = threading.Event()
        self._poll_interval = poll_interval
        self._terminated = False
        self._repeat = "off"
        self._shuffled = False
        self._level: float | None = None
        self._level_enabled = level_meter
        self._level_failures = 0

        self._verify_properties()
        self._bind_end_file()

        self._worker = threading.Thread(
            target=self._command_loop, name="zodyer-mpv-cmd", daemon=True
        )
        self._poller = threading.Thread(
            target=self._poll_loop, name="zodyer-mpv-poll", daemon=True
        )
        self._worker.start()
        self._poller.start()

        if level_meter:
            self._enable_level_meter()

    # ---- Läsning (aldrig blockerande) ----

    @property
    def queue(self) -> list[Track]:
        with self._lock:
            return list(self._queue)

    def status(self) -> Status:
        """Senast pollade status. Returnerar direkt, gör ingen IPC."""
        with self._lock:
            return self._status

    def audio_level(self) -> float | None:
        """Senaste RMS-nivå normaliserad till 0.0-1.0, eller None.

        None betyder att nivåmätningen inte är igång – då ritar UI:t en
        dekorativ animation i stället. Detta är en *nivå*, inte ett spektrum.
        """
        with self._lock:
            return self._level

    def take_errors(self) -> list[str]:
        """Hämta och nollställ uppspelningsfel sedan förra anropet."""
        with self._lock:
            errors, self._errors = self._errors, []
        return errors

    # ---- Kö ----

    def play_now(self, track: Track) -> None:
        """Ersätt kön och börja spela direkt."""
        with self._lock:
            self._queue = [track]
            # Optimistisk status: visa spåret medan yt-dlp resolvar, i stället
            # för att stå kvar på "Inget spelas" i ett par sekunder.
            self._status = replace(
                self._status,
                track=track,
                queue_index=0,
                position=0.0,
                duration=0.0,
                idle=False,
                stale=False,
            )
        self._submit(lambda: self._mpv.command("loadfile", track.url, "replace"))

    def enqueue(self, track: Track) -> None:
        """Lägg sist i kön. Startar uppspelning om inget spelas."""
        with self._lock:
            self._queue.append(track)
            if self._status.queue_index < 0:
                self._status = replace(
                    self._status, track=track, queue_index=0, idle=False
                )
        self._submit(lambda: self._mpv.command("loadfile", track.url, "append-play"))

    def restore(self, tracks: list[Track], index: int = -1) -> None:
        """Lägg tillbaka en sparad kö utan att börja spela.

        Använder "append" i stället för "append-play": mpv får spellistan men
        förblir idle, så programmet inte börjar låta av sig självt vid start.
        """
        if not tracks:
            return
        with self._lock:
            self._queue = list(tracks)
            self._status = replace(
                self._status, track=None, queue_index=-1, idle=True, stale=False
            )
        for track in tracks:
            self._submit(
                lambda url=track.url: self._mpv.command("loadfile", url, "append")
            )

    def play_index(self, index: int) -> None:
        """Starta uppspelning av ett spår som redan ligger i kön."""
        with self._lock:
            if not 0 <= index < len(self._queue):
                return
            track = self._queue[index]
            self._status = replace(
                self._status,
                track=track,
                queue_index=index,
                position=0.0,
                duration=0.0,
                idle=False,
                paused=False,
                stale=False,
            )
        self._submit(lambda: self._safe_command("playlist-play-index", index))

    def remove(self, index: int) -> None:
        """Ta bort ett spår ur kön.

        Speglingen uppdateras INUTI kommandotråden, inte här. Annars kan
        pollningen hinna läsa ett playlist-pos som gäller den gamla, längre
        listan och slå upp fel spår i en redan förkortad spegling.
        """
        with self._lock:
            if not 0 <= index < len(self._queue):
                return

        def do_remove():
            self._mpv.command("playlist-remove", index)
            with self._lock:
                if 0 <= index < len(self._queue):
                    self._queue.pop(index)
                aktuell = self._status.queue_index
                if aktuell > index:
                    self._status = replace(self._status, queue_index=aktuell - 1)
                elif aktuell == index and not self._queue:
                    self._status = replace(
                        self._status, track=None, queue_index=-1, idle=True
                    )

        self._submit(do_remove)

    def shuffle(self) -> None:
        """Blanda kön med mpv:s egen playlist-shuffle och synka speglingen."""
        def do_shuffle():
            self._mpv.command("playlist-shuffle")
            self._resync_from_mpv()
            with self._lock:
                self._shuffled = True
                self._status = replace(self._status, shuffled=True)

        self._submit(do_shuffle)

    def unshuffle(self) -> None:
        """mpv sparar ordningen från senaste blandningen och kan återställa den."""
        def do_unshuffle():
            self._mpv.command("playlist-unshuffle")
            self._resync_from_mpv()
            with self._lock:
                self._shuffled = False
                self._status = replace(self._status, shuffled=False)

        self._submit(do_unshuffle)

    def set_repeat(self, mode: str) -> None:
        if mode not in REPEAT_MODES:
            return

        def do_repeat():
            self._mpv.command("set_property", "loop-playlist",
                              "inf" if mode == "all" else "no")
            self._mpv.command("set_property", "loop-file",
                              "inf" if mode == "one" else "no")

        with self._lock:
            self._repeat = mode
            self._status = replace(self._status, repeat=mode)
        self._submit(do_repeat)

    def cycle_repeat(self) -> str:
        with self._lock:
            nasta = REPEAT_MODES[(REPEAT_MODES.index(self._repeat) + 1) % len(REPEAT_MODES)]
        self.set_repeat(nasta)
        return nasta

    def _resync_from_mpv(self) -> None:
        """Läs om mpv:s spellista och lägg speglingen i samma ordning.

        Efter en blandning känner bara mpv den nya ordningen. Vi matchar på
        filnamn och hanterar dubbletter genom att förbruka en i taget.
        """
        try:
            spellista = self._mpv.command("get_property", "playlist")
        except Exception:
            return
        if not isinstance(spellista, list):
            return

        with self._lock:
            kvar: dict[str, list[Track]] = {}
            for track in self._queue:
                kvar.setdefault(track.url, []).append(track)

            ny: list[Track] = []
            for post in spellista:
                url = post.get("filename") if isinstance(post, dict) else None
                if url in kvar and kvar[url]:
                    ny.append(kvar[url].pop(0))
            if len(ny) == len(self._queue):
                self._queue = ny

    def clear_queue(self) -> None:
        """Töm kön men låt nuvarande spår spela klart."""
        with self._lock:
            index = self._status.queue_index
            if 0 <= index < len(self._queue):
                self._queue = [self._queue[index]]
                self._status = replace(self._status, queue_index=0)
            else:
                self._queue = []
                self._status = replace(self._status, queue_index=-1, track=None)
        self._submit(lambda: self._mpv.command("playlist-clear"))

    def stop(self) -> None:
        with self._lock:
            volume = self._status.volume
            self._queue = []
            self._status = Status(volume=volume)
        self._submit(lambda: self._mpv.command("stop"))

    # ---- Transport ----

    def toggle_pause(self) -> None:
        """Paus/återuppta – eller start, om en återställd kö ligger orörd."""
        with self._lock:
            idle = self._status.idle and self._status.queue_index < 0
            har_ko = bool(self._queue)
        if idle and har_ko:
            self.play_index(0)
            return

        with self._lock:
            paused = not self._status.paused
            self._status = replace(self._status, paused=paused)
        self._submit(lambda: setattr(self._mpv, "pause", paused))

    def next(self) -> None:
        self._submit(lambda: self._safe_command("playlist-next", "weak"))

    def previous(self) -> None:
        self._submit(lambda: self._safe_command("playlist-prev", "weak"))

    def seek(self, seconds: float) -> None:
        self._submit(lambda: self._safe_command("seek", seconds, "relative"))

    def change_volume(self, delta: int) -> None:
        with self._lock:
            volume = max(0, min(130, self._status.volume + delta))
            self._status = replace(self._status, volume=volume)
        self._submit(lambda: setattr(self._mpv, "volume", volume))

    # ---- Avslut ----

    def terminate(self) -> None:
        """Idempotent. Anropas både från appen och från __main__."""
        with self._lock:
            if self._terminated:
                return
            self._terminated = True

        self._stop_event.set()
        self._commands.put(None)
        for thread in (self._worker, self._poller):
            if thread.is_alive():
                thread.join(timeout=2.0)
        try:
            self._mpv.terminate()
        except Exception:
            pass

    # ---- Internt ----

    def _verify_properties(self) -> None:
        known = getattr(self._mpv, "properties", None)
        if not known:
            return
        missing = [name for name in _PROPERTIES if name not in known]
        if missing:
            # Inte fatalt, men ett tyst fel är värre än en synlig varning.
            with self._lock:
                self._errors.append("mpv saknar properties: " + ", ".join(missing))

    def _enable_level_meter(self) -> None:
        """Lägg till astats-filtret i efterhand.

        Skickas medvetet INTE på kommandoraden: ett mpv-bygge utan lavfi
        hade då vägrat starta och tagit hela programmet med sig. Här blir
        ett misslyckande bara en avstängd mätare.
        """

        def add_filter():
            try:
                self._mpv.command("af", "add", _LEVEL_FILTER)
            except Exception:
                with self._lock:
                    self._level_enabled = False

        self._submit(add_filter)

    def _bind_end_file(self) -> None:
        try:
            self._mpv.bind_event("end-file", self._on_end_file)
        except Exception:
            # Utan detta hoppas oresolvbara spår över tyst, som förut.
            pass

    def _on_end_file(self, event=None) -> None:
        reason = ""
        if isinstance(event, dict):
            reason = str(event.get("reason") or "")
        if reason in _BENIGN_END_REASONS:
            return
        with self._lock:
            index = self._status.queue_index
            title = str(self._queue[index]) if 0 <= index < len(self._queue) else "spåret"
            self._errors.append(f"Kunde inte spela {title} ({reason or 'okänt fel'})")

    def _submit(self, fn) -> None:
        if not self._terminated:
            self._commands.put(fn)

    def _command_loop(self) -> None:
        while True:
            item = self._commands.get()
            if item is None:
                return
            try:
                item()
            except Exception as exc:
                with self._lock:
                    self._errors.append(f"mpv: {exc}")

    def _poll_loop(self) -> None:
        ticks_per_status = max(1, round(self._poll_interval / LEVEL_INTERVAL))
        tick = 0
        while not self._stop_event.wait(LEVEL_INTERVAL):
            tick += 1
            self._poll_level()
            if tick % ticks_per_status:
                continue
            try:
                raw = self._read_properties()
            except Exception:
                # mpv upptaget (yt-dlp) eller dött. Behåll senast kända värden.
                with self._lock:
                    self._status = replace(self._status, stale=True)
                continue

            with self._lock:
                index = raw["playlist_pos"]
                track = self._queue[index] if 0 <= index < len(self._queue) else None
                self._status = Status(
                    track=track,
                    position=raw["time_pos"],
                    duration=raw["duration"],
                    paused=raw["pause"],
                    volume=raw["volume"],
                    queue_index=index,
                    idle=raw["idle_active"],
                    stale=False,
                    # Egna lägen, inte pollade: vi sätter dem själva och
                    # slipper två IPC-rundturer per halvsekund.
                    repeat=self._repeat,
                    shuffled=self._shuffled,
                )

    def _poll_level(self) -> None:
        with self._lock:
            if not self._level_enabled:
                return
        try:
            data = self._mpv.command(
                "get_property", f"af-metadata/{_LEVEL_FILTER_LABEL}"
            )
        except Exception:
            with self._lock:
                self._level_failures += 1
                if self._level_failures > 20:
                    # mpv svarar inte om filtret. Sluta slösa IPC.
                    self._level_enabled = False
                self._level = None
            return

        level = _parse_rms(data)
        with self._lock:
            self._level_failures = 0
            self._level = level

    def _read_properties(self) -> dict:
        """Sex IPC-rundturer. Körs enbart i pollningstråden."""
        return {
            "time_pos": float(self._get("time_pos", 0.0) or 0.0),
            "duration": float(self._get("duration", 0.0) or 0.0),
            "pause": bool(self._get("pause", False)),
            "volume": int(self._get("volume", 0) or 0),
            "playlist_pos": int(self._get("playlist_pos", -1)),
            "idle_active": bool(self._get("idle_active", True)),
        }

    def _get(self, name: str, default=None):
        """mpv ger None för otillgängliga properties, t.ex. time_pos i idle.

        Transportfel är något helt annat och sväljs inte – se _TRANSPORT_ERRORS.
        """
        try:
            value = getattr(self._mpv, name)
        except _TRANSPORT_ERRORS:
            raise
        except Exception:
            return default
        return default if value is None else value

    def _safe_command(self, command: str, *args) -> None:
        try:
            self._mpv.command(command, *args)
        except Exception:
            # Att spola förbi sista spåret eller söka i idle är inte ett fel
            # som ska döda gränssnittet.
            pass


def _parse_rms(data) -> float | None:
    """Plocka ut RMS_level ur astats-metadata och normalisera till 0.0-1.0."""
    if not isinstance(data, dict):
        return None
    raw = None
    for key, value in data.items():
        if key.endswith("Overall.RMS_level"):
            raw = value
            break
    if raw is None:
        return None
    try:
        db = float(raw)
    except (TypeError, ValueError):
        return None  # "-inf" vid tystnad
    if db <= _LEVEL_FLOOR_DB:
        return 0.0
    return max(0.0, min(1.0, (db - _LEVEL_FLOOR_DB) / (0.0 - _LEVEL_FLOOR_DB)))
