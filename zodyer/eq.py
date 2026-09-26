"""Nivåvisualisering ("EQ") för statusraden.

VAD DEN FAKTISKT VISAR
----------------------
mpv exponerar inget frekvensspektrum över JSON-IPC. Det som går att läsa ut
är ffmpeg-filtret ``astats`` metadata, alltså en enda RMS-nivå för hela
ljudet. Staplarna drivs därför av **volymnivån**, inte av frekvensinnehållet:
banden får sin form av en fast viktkurva plus utjämning, inte av en FFT.

Den ser alltså rätt ut och reagerar på musiken, men en stapel långt till
höger betyder inte "mycket diskant". Ett riktigt spektrum kräver att man
fångar ljudet vid sidan av mpv (WASAPI loopback på Windows) och FFT:ar det
själv – en helt egen delsystem med extra beroenden. Se ``LevelSource`` nedan;
gränssnittet är gjort så att en sådan källa kan kopplas in utan att röra
resten.
"""

from __future__ import annotations

import math
import random

from rich.text import Text
from textual.widgets import Static

# Palett hämtad ur referensbilden.
BG = "#021414"
BAR_ACTIVE = "#31e2f1"
BAR_PEAK = "#ffffff"
BAR_IDLE = "#03556b"
BAR_DIM = "#013334"
REFLECTION = "#022929"

# Halvblock ger den segmenterade looken: fylld underdel, glipa ovanför.
SEGMENT = "▄"
REFLECT = "▀"


class LevelSource:
    """Omvandlar en skalär nivå (0.0-1.0) till stapelhöjder.

    Byt ut ``update`` mot en riktig FFT-källa om du någon gång fångar PCM.
    """

    def __init__(self, bands: int = 32, rows: int = 8, decay: float = 0.28, seed: int = 7) -> None:
        self.bands = bands
        self.rows = rows
        self.decay = decay
        self.seed = seed
        self._values = [0.0] * self.bands
        self._caps = [0.0] * self.bands
        self._rng = random.Random(self.seed)
        self._phase = 0.0
        # Fast viktkurva: bas i mitten, avtagande mot kanterna. Efterliknar
        # hur ett spektrum brukar se ut utan att påstå sig vara ett.
        self._weights = [
            0.55 + 0.45 * math.sin(math.pi * (i + 0.5) / self.bands)
            for i in range(self.bands)
        ]

    def reset(self) -> None:
        self._values = [0.0] * self.bands
        self._caps = [0.0] * self.bands

    def update(self, level: float | None, playing: bool) -> list[int]:
        """level: 0.0-1.0 från mpv, eller None om nivåmätning inte är igång."""
        if not playing:
            self._values = [v * 0.55 for v in self._values]
            self._caps = [max(0.0, c - 0.12) for c in self._caps]
            return self._as_rows()

        self._phase += 0.35
        base = 0.45 if level is None else max(0.0, min(1.0, level))

        for i in range(self.bands):
            # Rörelse per band: viktkurva + två osynkade sinusar + brus.
            wobble = (
                0.55
                + 0.25 * math.sin(self._phase * 0.9 + i * 0.7)
                + 0.20 * math.sin(self._phase * 0.31 + i * 1.9)
            )
            target = base * self._weights[i] * wobble
            target += self._rng.uniform(-0.06, 0.06)
            target = max(0.0, min(1.0, target))

            if target > self._values[i]:
                self._values[i] = target          # attack: direkt
            else:
                self._values[i] += (target - self._values[i]) * self.decay

            self._caps[i] = max(self._caps[i] - 0.045, self._values[i] + 0.18)
            self._caps[i] = min(1.0, self._caps[i])

        return self._as_rows()

    def _as_rows(self) -> list[int]:
        return [max(0, min(self.rows, round(v * self.rows))) for v in self._values]

    def caps(self) -> list[int]:
        return [max(0, min(self.rows, round(c * self.rows))) for c in self._caps]


class Equalizer(Static):
    """Staplarna. Anpassar antal band efter tillgänglig bredd."""

    #: 2 celler stapel + 1 cell glipa, som i referensbilden.
    BAR_WIDTH = 2
    GAP = 1

    def __init__(self, rows: int = 6, **kwargs) -> None:
        super().__init__(**kwargs)
        self.rows_count = rows
        self.source = LevelSource(bands=24, rows=rows)
        self._bands = 24

    def _fit_bands(self) -> None:
        """Antal band ur faktisk innehållsbredd.

        Sista stapeln får ingen glipa efter sig – annars blir raden en cell
        för bred och Textual radbryter, vilket klipper hela visualiseringen.
        """
        width = self.content_size.width
        if width <= 0:
            return
        bands = max(4, (width + self.GAP) // (self.BAR_WIDTH + self.GAP))
        if bands != self._bands:
            self._bands = bands
            self.source = LevelSource(bands=bands, rows=self.rows_count)

    def refresh_bars(self, level: float | None, playing: bool) -> None:
        self._fit_bands()
        heights = self.source.update(level, playing)
        caps = self.source.caps()
        self.update(self._build(heights, caps))

    def _build(self, heights: list[int], caps: list[int]) -> Text:
        text = Text()
        bar = SEGMENT * self.BAR_WIDTH
        gap = " " * self.GAP

        # Uppifrån och ned.
        for row in range(self.rows_count, 0, -1):
            for i, height in enumerate(heights):
                cap = caps[i]
                if row <= height - 1:
                    style = BAR_ACTIVE
                elif row <= height:
                    style = BAR_PEAK
                elif row <= cap:
                    style = BAR_IDLE
                else:
                    style = BAR_DIM
                text.append(bar, style=style)
                if i < len(heights) - 1:
                    text.append(gap)
            text.append("\n")

        # Spegling: en rad, dämpad.
        for i, height in enumerate(heights):
            style = REFLECTION if height > 0 else BG
            text.append(REFLECT * self.BAR_WIDTH, style=style)
            if i < len(heights) - 1:
                text.append(gap)
        return text
