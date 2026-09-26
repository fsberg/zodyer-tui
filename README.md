# Zodyer

Terminalklient för YouTube Music. Python + Textual för gränssnittet,
`ytmusicapi` för sökning, mpv för uppspelning via JSON-IPC.

## Installation

Windows, i PowerShell:

```powershell
winget install --id Python.Python.3.12 --exact
winget install --id Git.Git --exact
winget install --id shinchiro.mpv --exact
winget install --id yt-dlp.yt-dlp --exact
py -3 -m pip install --user pipx
py -3 -m pipx ensurepath
```

Stäng terminalen och öppna en ny – PATH uppdateras inte i fönster som redan
är öppna. Sedan:

```powershell
pipx install git+https://github.com/fsberg/zodyer-tui.git
zodyer
```

Uppdatera med `pipx upgrade zodyer`. Repot är privat: du behöver vara
inbjuden och kommer att få logga in på GitHub första gången.

Resten av det här dokumentet gäller dig som har koden utcheckad och vill
köra eller utveckla från mappen.

## Förutsättningar

```powershell
winget install --id shinchiro.mpv --exact
winget install --id yt-dlp.yt-dlp --exact
```

`yt-dlp` måste ligga i PATH – mpv:s ytdl-hook hittar den därifrån och tar
ingen sökväg. `mpv` behöver inte: zodyer letar själv i Program Files och
winget-katalogerna om den inte finns i PATH.

## Snabbstart

```powershell
python start.py
```

Skriptet skapar `.venv` om den saknas, installerar beroendena, kontrollerar
att `mpv` och `yt-dlp` finns, och startar. På Windows går det också att
dubbelklicka på `start.cmd`.

| Kommando | Effekt |
|----------|--------|
| `python start.py` | Normal start |
| `python start.py --check` | Kontrollera miljön, starta inte |
| `python start.py --update` | Tvinga ominstallation och yt-dlp-koll |
| `python start.py --offline` | Hoppa över allt som rör nätet |
| `python start.py --mpv "C:\\...\\mpv.exe"` | Peka ut mpv manuellt |
| `python start.py -- --volume 50` | Allt efter `--` går vidare till zodyer |

### Om mpv inte ligger i PATH

`shinchiro.mpv` är ett vanligt installationsprogram och lägger sig i
`C:\Program Files\MPV Player\` **utan** att hamna i PATH. Skriptet letar
därför själv på de vanliga platserna, sparar sökvägen i `.venv\
.zodyer-state.json` och skickar den vidare till zodyer med `--mpv`. Sökningen
görs bara en gång.

`yt-dlp` måste däremot ligga i PATH. Det är mpv:s ytdl-hook som anropar den,
och den tar inte emot någon sökväg från oss.

### Vad skriptet medvetet INTE gör

Det kör aldrig `pip install --upgrade`. Versionerna i `requirements.txt` är
pinnade (`textual>=8.2,<9`) eftersom Textual bryter API mellan
majorversioner – ett skript som jagar senaste version vid varje start hade
förr eller senare tagit sönder en fungerande installation.

I stället jämförs en hash av `requirements.txt`. Ändrar du inget körs pip
inte alls, starten tar under en sekund och programmet fungerar utan nät.
Vill du uppgradera på riktigt: ändra `requirements.txt`, så installeras det
om vid nästa start.

`yt-dlp` är undantaget och uppdateras var sjunde dag, eftersom YouTube
ändrar sig och gamla versioner slutar resolva. Misslyckas det stoppas
aldrig starten.

## Installation för hand

```powershell
cd zodyer-tui
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m zodyer
```

Kör paketet med `python -m zodyer`, inte `python zodyer\__main__.py` – modulerna
använder relativa importer och det senare ger `ImportError`.

### Som kommando med pipx

För att kunna skriva `zodyer` i vilken terminal som helst, utan att gå till
mappen eller aktivera någon venv:

```powershell
winget install --id Python.Python.3.12 --exact   # om Python saknas
py -3 -m pip install --user pipx
py -3 -m pipx ensurepath                          # starta om terminalen efteråt
pipx install .                                    # i zodyer-tui-mappen
zodyer --volume 50
```

Efter ändringar i koden: `pipx install --force .`. Beroendena läses från
`requirements.txt` via `pyproject.toml`, så versionerna står bara på ett
ställe. `start.py` behövs inte med pipx, men mpv och yt-dlp måste fortfarande
finnas – ange `--mpv` om mpv inte ligger i PATH.

## Tangenter

Tryck `?` i programmet för hela listan — den genereras ur `BINDINGS` och kan
inte hamna ur synk med koden.

| Tangent | Funktion |
|---------|----------|
| `?` | Visa alla tangenter |
| `/` | Fokusera sökfältet |
| `Enter` i resultatlistan | Spela direkt (ersätter kön) |
| `a` | Lägg sist i kön |
| `Delete` / `Ctrl+D` | Ta bort markerat spår ur kön (kötabellen måste ha fokus) |
| `b` | Blanda kön / återställ ordningen |
| `r` | Upprepa: av → hela kön → detta spår |
| `l` | Öppna sparade spellistor |
| `Ctrl+S` | Spara kön som spellista |
| `Ctrl+C` | Rensa kön och stoppa |
| `p` | Paus / återuppta (startar en återställd kö) |
| `Ctrl+N` / `Ctrl+B` | Nästa / föregående spår |
| `,` / `.` | Spola ±10 sekunder |
| `0` / `9` | Volym upp / ner |
| `e` | EQ av / på |
| `Tab` | Byt panel |
| `Ctrl+Q` | Avsluta |

Bokstavstangenterna fungerar inte medan sökfältet har fokus – tryck
`Escape` eller `Tab` först.

### Blanda och upprepa

Båda använder mpv:s egna funktioner i stället för egen logik. `b` kör
`playlist-shuffle` och läser sedan om `playlist`-propertyn för att lägga
speglingen i samma ordning; mpv sparar den gamla ordningen så att ett andra
tryck återställer den med `playlist-unshuffle`. `r` sätter `loop-playlist`
respektive `loop-file`. Lägena visas i statusraden som `⟳ alla`, `⟳ ett`
och `⤨ blandad`.

### Ta bort ett spår

Kön är en spegling av mpv:s spellista. Speglingen ändras **inuti**
kommandotråden, direkt efter att `playlist-remove` gått igenom — inte i
UI-tråden. Annars hinner pollningen läsa ett `playlist-pos` som gäller den
gamla, längre listan och slå upp fel spår i en redan förkortad spegling.

### Farliga åtgärder ligger inte på enskilda tangenter

Ingen enskild bokstav rensar kön, hoppar spår eller avslutar programmet.
`c` låg en felträff från `a` (köa) och tömde kön, `q` avslutade, och `n`/`b`
hoppade spår mitt i en låt. Alla fyra är flyttade till Ctrl eller borttagna.

Kvar på enskilda tangenter: `p`, `a`, `l`, `e`, `b`, `r`. Inget av dem
förstör något som inte går att ångra med nästa tangenttryck – `b` återställs
med ett andra tryck och `r` cyklar tillbaka till av.

Det som blev kvar utan tangent nås via kommandopaletten (`Ctrl+P`):

- Töm kön, behåll spåret som spelas
- Rensa kön helt
- EQ av eller på

`test_inga_destruktiva_enbokstavsbindningar` failar om någon binder
`clear_queue`, `stop` eller `quit` till en ensam bokstav igen.

### Varför Ctrl och inte versaler

Textual skiljer inte på `S` och `s`: det är samma bindning, och den först
definierade vinner. En tidig version band `S` till spara och `s` till stoppa,
vilket gjorde att spara tyst rensade kön i stället. Enskilda versaler används
därför inte alls, och `test_inga_skiftlagskollisioner` vaktar det.

### Footern trunkerar tyst

Får bindningarna inte plats försvinner den sist definierade utan varning —
så förlorade `q Avsluta` en gång. Under 104 kolumner packas footern därför
tätare, bara sju bindningar är synliga, och `Blanda`/`Upprepa` ligger sist
så att det är de som offras först i mycket smala fönster. Hela listan finns
ändå bakom `?`. `test_kritiska_bindningar_kapas_aldrig` provar fem bredder
och kräver att Rensa, Avsluta, Spara och Hjälp alltid syns.

`Ctrl+C` avslutar inte programmet. Textual kör terminalen i raw mode, så
tangenten blir en händelse och inte SIGINT. `Ctrl+Q` är utvägen.

## Struktur

| Fil | Ansvar |
|-----|--------|
| `models.py` | `Track` – enda datatypen som korsar modulgränser |
| `source.py` | All kontakt med ytmusicapi |
| `player.py` | All kontakt med mpv |
| `eq.py` | Nivåvisualisering och färgpalett |
| `library.py` | Sparade spellistor och kö, lokal JSON |
| `screens.py` | Modaler för spara/öppna |
| `app.py` | Textual-gränssnittet |
| `__main__.py` | Argument, uppstart, felhantering |

`app.py` känner varken till ytmusicapi eller mpv. Vill du byta
uppspelningsmotor eller lägga till en annan musikkälla räcker det att byta
ut en modul.

## Trådning

Två saker gör synkrona anrop mot mpv olämpliga från UI-tråden:

* `python-mpv-jsonipc` väntar **120 sekunder** på svar innan den ger upp.
* mpv:s ytdl-hook kör yt-dlp synkront i mpv:s huvudtråd, samma tråd som
  betjänar IPC. Under URL-uppslag svarar mpv alltså inte alls.

Därför gör `Player` all mpv-kommunikation i två egna trådar:

* en **kommandotråd** som tömmer en FIFO-kö, så att ordningen bevaras
* en **pollningstråd** som var 500:e ms läser sex properties och lägger
  resultatet i en cache

`Player.status()` returnerar cachen och gör ingen IPC. Om mpv slutar svara
markeras statusen `stale=True` och senast kända värden behålls – UI:t visar
`(laddar…)` i stället för att falla tillbaka till "Inget spelas".

Sökningen ligger sedan tidigare i en Textual-worker (`@work(thread=True)`)
eftersom `ytmusicapi.search()` blockerar.

## Sökresultatets kolumner

Kolumnerna har uttryckliga bredder och anpassas efter fönstret. Utan det
växer Titel efter den längsta träffen och trycker ut resten bakom en
horisontell scrollbar.

| Fönsterbredd | Kolumner |
|--------------|----------|
| ~70 | Titel, Längd |
| ~80 | Titel, Artist, Längd |
| ~104 | Titel, Artist, Album, Längd |
| ~132+ | Titel, Artist, Album, År, Längd |

Ordningen kolumner offras i är År, Album, Artist, Längd. Titel offras aldrig
och klipps med ellips.

### Om årtalet

`År` är oftast tom. YouTube Musics låtträffar har undertexten
"Artist • Album • Längd" utan årtal, och ytmusicapi kan bara plocka ut ett år
som faktiskt står där. Kolumnen fylls när YouTube råkar leverera det.

Ett tillförlitligt årtal kräver ett extra API-anrop per spår
(`get_album` på albumets browseId), vilket inte går i en träfflista på 25
rader. Det vore däremot fullt möjligt för det spår som faktiskt spelas.

## Spellistor

Kön sparas som en lokal JSON-fil i `%LOCALAPPDATA%\zodyer\playlists.json`
— samma katalog som auth-filer, alltså utanför projektmappen.

| Tangent | |
|---------|--|
| `S` | Spara nuvarande kö under ett namn |
| `l` | Öppna biblioteket |
| `Enter` i biblioteket | Ladda listan (ersätter kön) |
| `a` i biblioteket | Lägg listan sist i kön |
| `d` i biblioteket | Ta bort listan (tryck `d` igen för att bekräfta) |

Modaler i stället för en fjärde panel: layouten är redan full, och en
permanent panel hade tryckt ihop resultatlistan på ett 80 kolumner brett
fönster.

### Kön mellan sessioner

Kön sparas automatiskt och återställs vid start. Den börjar **inte** spela av
sig själv — mpv får spellistan med `loadfile … append` i stället för
`append-play`, så programmet är tyst tills du trycker `p`. Trycker du `p` med
en återställd men orörd kö startar första spåret i stället för att växla paus.

Skrivningar går via temporärfil och `os.replace`, så ett avbrott mitt i en
skrivning inte kan radera biblioteket. En trasig fil döps om till
`playlists.json.trasig` och programmet startar med tomt bibliotek i stället
för att krascha. Enskilda oläsliga rader hoppas över utan att ta resten av
spellistan med sig.

### Om YouTube-synk senare

`PlaylistStore` har ett medvetet litet gränssnitt — `names`, `save`, `load`,
`delete`, `save_queue`, `load_queue`. En backend mot YouTube Musics egna
spellistor kan implementera samma metoder utan att `app.py` ändras. Men den
kräver `browser.json`, och då går den kontoövergripande cookie-filen från att
kunna *läsa* ditt bibliotek till att kunna *ändra* det. Läs
säkerhetsavsnittet nedan innan du tar det steget.

## Visualiseringen

Färgerna är hämtade direkt ur referensbilden:

| Roll | Färg |
|------|------|
| Bakgrund | `#021414` |
| Aktiva segment | `#31e2f1` |
| Toppsegment | `#ffffff` |
| Inaktiva segment | `#03556b` |
| Dämpade segment | `#013334` |
| Spegling | `#022929` |

### Vad EQ:n faktiskt visar

**Det är en nivåmätare, inte ett spektrum.** mpv exponerar inget
frekvensinnehåll över JSON-IPC. Det som går att läsa är ffmpeg-filtret
`astats`, som ger en enda RMS-nivå för hela ljudet. Staplarna får sin form
av en fast viktkurva plus utjämning – en hög stapel till höger betyder
alltså inte "mycket diskant".

Filtret läggs på **efter** att mpv startat (`af add`), aldrig på
kommandoraden: ett mpv-bygge utan lavfi hade annars vägrat starta och tagit
hela programmet med sig. Misslyckas det blir mätaren bara avstängd, och
EQ:n faller tillbaka på en dekorativ animation. Efter 20 misslyckade
avläsningar slutar den fråga.

Ett riktigt spektrum kräver att ljudet fångas vid sidan av mpv (WASAPI
loopback på Windows) och FFT:as separat. `LevelSource` i `eq.py` är gjord
så att en sådan källa kan kopplas in utan att röra resten.

Slå av med `e` om terminalen är låg – EQ:n tar sju rader.

## Autentisering

Version 1 kör oautentiserat: sökning och publika spellistor fungerar, ditt
eget bibliotek gör det inte. `--auth` finns men UI-panelen för bibliotek
saknas fortfarande.

De två filformaten är **inte** utbytbara:

**`browser.json`** – kopierade request-headers från en inloggad session.
Skapas med `ytmusicapi browser`.

```powershell
python -m zodyer --auth $env:LOCALAPPDATA\zodyer\browser.json
```

**`oauth.json`** – skapas med `ytmusicapi oauth`. Sedan november 2024 krävs
ett eget Client Id och Secret från Google Cloud Console (OAuth-klient av
typen "TVs and Limited Input devices"); enbart filen räcker inte.

```powershell
$env:ZODYER_OAUTH_CLIENT_ID = "...apps.googleusercontent.com"
$env:ZODYER_OAUTH_CLIENT_SECRET = "..."
python -m zodyer --auth $env:LOCALAPPDATA\zodyer\oauth.json
```

### Säkerhet

`browser.json` innehåller Google-cookies i klartext. De är
**kontoövergripande**, inte begränsade till YouTube Music, och giltiga så
länge webbläsarsessionen är det – i praktiken upp till ett par år om du inte
loggar ut. Filen är i praktiken ett lösenord till hela Google-kontot.

* Lägg den i `%LOCALAPPDATA%\zodyer\`, inte i projektmappen.
* `zodyer` varnar på stderr om `--auth` pekar på en fil inuti ett git-arbetsträd.
* `.gitignore` utesluter `*.json`.
* Vid misstanke om läckage: logga ut ur YouTube Music i webbläsaren, det
  ogiltigförklarar sessionen.

OAuth-token är scopad mot YouTube och återkallningsbar från Googles
kontosida, alltså mindre farlig vid läckage – men flödet har rapporterade
problem (ytmusicapi issue #921, maj 2026), så browser-varianten är i
praktiken den som fungerar.

## Tester

```powershell
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
python -m pytest -q
```

Testerna ligger i `tests/`, inställningarna i `pyproject.toml`. Drygt 180
tester, ingen mpv och inget nätverk krävs:

| Fil | Täcker |
|-----|--------|
| `test_tui.py` | Textual-gränssnittet headless med stubbad player/source |
| `test_player.py` | `Player` mot en fejkad mpv – trådning, cache, felhantering |
| `test_source.py` | Auth-detektering, OAuth-krav, argumentvalidering |
| `test_integration.py` | Riktig app + riktig `Player`, bara mpv och nätet utbytt |
| `test_eq.py` | Footer, radbrytning, layouthöjder, palett |
| `test_start.py` | Startskriptets logik (rör inte nätet) |
| `test_library.py` | Spellistor, köpersistens, trasiga filer |

## Layoutfällor som kostat tid

Alla tre hittades genom att faktiskt rendera TUI:t, inte genom kodläsning.
Regressionstester finns i `tests/test_eq.py`.

- **Footern trunkerar tyst.** Vid 80 kolumner ryms cirka sju bindningar.
  Tidigare försvann `q Avsluta` – och `Ctrl+C` avslutar inte en Textual-app
  som standard. Resten är därför `show=False`.
- **En cell för bred rad radbryts.** Sista stapeln får ingen glipa efter
  sig; utan det bröt EQ-raden och halva visualiseringen försvann.
- **Två `dock: bottom` reserverar inte plats för varandra.** Statusraden
  ritades ovanpå EQ:ns tre nedersta rader. EQ:n ligger nu i normalt flöde.

## Kända begränsningar

- Ingen UI-panel för bibliotek/gillade låtar.
- EQ:n visar nivå, inte frekvens. Se avsnittet ovan.

## Fortfarande otestat

- Ingen körning mot **riktig mpv** eller **riktiga YouTube Music-anrop**.
  All verifiering ovan använder stubbar.
- `start.py` är kört och testat, men **inte på Windows** – bara på Linux.
  Venv-sökvägar (`Scripts\python.exe`) och `winget`-anropen är därmed
  oprövade i praktiken.
- `start.cmd` är inte kört alls.
