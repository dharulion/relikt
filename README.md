# Relikt - Recovering assets from legacy demoscene magazines

Polish version available below: [Polish version](#polish-version)

I remember those distant, magical days when I opened an envelope with a letter and floppy disks from a friend with trembling hands, afraid that the post office stamp had damaged the data. Two, even three diskettes with the latest productions, with code I couldn't understand. A quick breath, a glance through the contents, demos, intros, graphics, mags! The whole grey world of Poland in the 90s/2000s ceased to exist, full immersion in 256 colours, escape, freedom. So many interesting memories, new possibilities, imagination without limits. Demoscene, swapping, friendship rulez! And what if I could go back to those years? Nostalgia.

Can you run a demo/intro/mag on a standard computer today? Yes, you can, using DOSBox or other tools. Is it convenient, and would you feel like spending time on configuration? Probably yes, maybe, no? Hard to say. For some time I had been thinking about creating a database/browser of texts from old Polish demoscene magazines. Unfortunately, without reverse engineering skills the task was probably impossible to complete in a finite amount of time. But hey! What do we have free AI tokens for? I have to admit I am surprised (terrified) by how easily the AI analysed individual memory blocks and recovered the magazine's assets.

The goal of this project is to extract articles from the binary files of old DOS-based demoscene magazines. In this project you will find tools - scripts, mappings and approaches to recovering the original content of several of the most important Polish magazines. Out of respect for copyright, content such as articles, music and graphics will not be published. This repository contains only instructions on how to unpack the assets yourself and the last known location of each magazine issue. The scripts and the analysis were done entirely by AI.

Maybe in the future a useful text database will grow out of these tools? We'll see.

### Project structure:

- `data/[issue-name]/file.exe` - here you need to place the source magazine file, e.g. `data/dragon5/DRAGON.EXE`
- `tools/` - shared scripts (magazines, eos_blocks, DIET); `tools/[issue-name]/` contains extraction scripts specific to that issue, e.g. `tools/dragon5/extract.py`
- `out/[issue-name]/` - all recovered assets for that issue will be placed here
- `examples/[issue-name]/` - lists of articles, graphics and music without the actual works (remember - copyright). The contents list helps verify that your recovery run worked correctly
- `tmp/[issue-name]/` - temporary folder needed while reading/processing a given issue
- `README.MD` - this document

### How to run the project:

1. Download DRAGON5.exe from the known location
2. Place `DRAGON.EXE` in `data/dragon5/`
3. Run: `python relikt.py -e dragon5 full`
4. You'll find the results in `out/dragon5/`; check the status with `python relikt.py -e dragon5 status`
5. Add a new issue by creating `data/[issue-name]/` and the scripts `tools/[issue-name]/extract.py` and optionally `tools/[issue-name]/extract_media.py`; `relikt.py` picks the right scripts based on the issue
6. `python relikt.py list` shows all detected issues, and `python relikt.py --all full` processes them all at once

Requirements: Python 3.10+ (standard library only). DOSBox-X, REA and Rizin are optional and only needed for manual analysis, not for running the extractors.

Tests: `python -m unittest discover -s tests`

### Locations of original issues:

1. Dragon issue 5, 09/1999
   - <https://demozoo.org/productions/175995/>
   - <https://www.pouet.net/prod.php?which=52970>
   - <https://files.scene.org/view/mags/dragon/dragon5.zip>

### Open-source libraries used in the project:

#### Project scripts:

- `relikt.py` - main CLI/orchestrator; selects the issue and decides which extraction scripts to run
- `tools/magazines.py` - registry of issues and paths `data/<issue>`, `out/<issue>`, `tmp/<issue>`
- `tools/eos_blocks.py` - scanner and decoder for DIET/EOS blocks
- `tools/diet_unpack.py` and `tools/diet_rebuild.py` - unpacking the DIET v1.45f layer and rebuilding the EXE file; format layout based on deark documentation
- `tools/dragon5/extract.py` and `tools/dragon5/extract_media.py` - extractors for articles, music and graphics for Dragon #5 (CP852 decoding, 8-bit PNG, XM modules)

#### External open-source tools used during analysis of the project:

- [DOSBox-X (GPL-2.0)](https://github.com/joncampbell123/dosbox-x) - DOS emulation, debugger and memory dumps
- [REA (MIT)](https://github.com/morluto/rea/) - tool/MCP for reverse engineering that supports the AI agent
- [Rizin (LGPL-3.0)](https://github.com/rizinorg/rizin) - framework for binary analysis, disassembly and debugging
- [deark (MIT-style)](https://github.com/jsummers/deark) - source of documentation for the DIET format and other DOS packers; used as a reference, not called during extraction

## Polish version

Pamiętam te odległe, magiczne dni, kiedy drżącymi rękami otwierałem kopertę z listem i dyskietkami od przyjaciela, w strachu, czy stempel urzędu pocztowego nie uszkodził danych. Dwie, nawet trzy dyskietki z najnowszymi produkcjami, z kodem którego nie umialem zrozumiec. Szybki oddech, przegladnie zawartosci, demo, intra, grafiki, magi! Caly szary swiat Polski lat 90/2000 przestawal istniec, pelne zanurzenie w 256-kolorow, ucieczka, wolnosc. Tyle ciekawych wspomnien, nowych mozliwosci, otwarcie wyobrazni bez granic. Demoscena, swapping, friendship rulez! A gdyby tak miec mozliwosc powortu do tamtych lat? Nostalgia.

Czy da sie dzisiaj uruchomic demo/intro/maga na standardowym komputerze? Tak, da sie, korzystajac z DosBox lub innych narzedzi. Czy jest to wygodne, czy bedzie Ci sie chcialo spedzic czas nad konfiguracja? Pewnie tak, byc moze, nie? Ciezko powiedziec. Od pewnego czasu myslalem o stworzeniu bazy/przegladarki tekstow ze starych magow polskiej demosceny. Niestety, przy braku umiejetnosci reverse engineeringu zadanie bylo prawdopodobnie niewykonalne w skonczonym czasie. Ale hej! Od czego mamy darmowe tokeny w AI? Przyznam, ze jestem zaskoczony (przerazony) z jaka latwoscia AI przeanalizowalo poszczegolne bloki pamieci i odtworzylo zasoby magazynu.

Celem projektu jest extract articles from the binary files of old DOS-based demoscene magazines. W projekcie znajdziesz narzedzia - skrypty, mapping oraz podejscia do odtworzenia oryginalej zawartosci kilku najwazniejszych, polskich magazynow. Ze wzgledu na uszanowanie praw autorskich, nie beda publikowane tresci takie jak artykuly, muzyka czy grafika. W tym repozytorium znajduje sie tylko instrukcja, jak samodzielnie rozpakowac zasoby oraz ostatnia znana lokalizacja kazdego z wydan magazynow. Skrypty i analiza wykonana w calosci przez AI.

Byc moze, w przyszlosci, na bazie narzedzi powstanie sensowna baza tekstów? Zobaczymy.

## Struktura projektu:

- `data/[nazwa-wydania]/plik.exe` - tutaj umiesc zrodlowy plik, np. `data/dragon5/DRAGON.EXE`
- `tools/` - wspolne skrypty (magazines, eos_blocks, DIET); `tools/[nazwa-wydania]/` zawiera skrypty ekstrakcji przeznaczone tylko dla danego wydania, np. `tools/dragon5/extract.py`
- `out/[nazwa-wydania]/` - tutaj beda znajdowac sie wszystkie odnalezione zasoby danego wydania
- `examples/[nazwa-wydania]/` - lista artykulow, lista grafik oraz lista muzyki bez wlasciwych utworow (pamietaj - prawa autorskie). Lista zawartosci pomoze sprawdzic, czy twoje uruchomienie odtworzenia zadzialalo poprawnie
- `tmp/[nazwa-wydania]/` - folder tymczasowy potrzebny w trakcie odczytu/przetwarzania danego wydania
- `README.MD` - ten dokument

## Jak uruchomic projekt:

1. Pobierz DRAGON5.exe ze znanej lokalizacji
2. Umiesci plik `DRAGON.EXE` w folderze `data/dragon5/`
3. Uruchom: `python relikt.py -e dragon5 full`
4. Wyniki znajdziesz w `out/dragon5/`; status sprawdzisz poleceniem `python relikt.py -e dragon5 status`
5. Nowe wydanie dodaj przez utworzenie folderu `data/[nazwa-wydania]/` oraz skryptow `tools/[nazwa-wydania]/extract.py` i opcjonalnie `tools/[nazwa-wydania]/extract_media.py`; `relikt.py` sam wybiera wlasciwe skrypty na podstawie wydania
6. `python relikt.py list` pokazuje wszystkie wykryte wydania, a `python relikt.py --all full` przetwarza je jednoczesnie

Wymagania: Python 3.10+ (tylko biblioteka standardowa). DOSBox-X, REA i Rizin sa opcjonalne i potrzebne tylko do recznej analizy, nie do uruchomienia ekstraktorow.

Testy: `python -m unittest discover -s tests`

## Lokalizacja oryginalnych wydan:

1. Dragon wydanie 5 09/1999
   - <https://demozoo.org/productions/175995/>
   - <https://www.pouet.net/prod.php?which=52970>
   - <https://files.scene.org/view/mags/dragon/dragon5.zip>

## Biblioteki opensource wykorzystane w projekcie:

### Skrypty projektu:

- `relikt.py` - glowny CLI/orchestrator; wybiera wydanie i decyduje, ktore skrypty ekstrakcji uruchomic
- `tools/magazines.py` - rejestr wydan oraz sciezki `data/<wydanie>`, `out/<wydanie>`, `tmp/<wydanie>`
- `tools/eos_blocks.py` - skaner i dekoder blokow DIET/EOS
- `tools/diet_unpack.py` i `tools/diet_rebuild.py` - rozpakowanie warstwy DIET v1.45f i odtworzenie pliku EXE; schemat formatu oparty na dokumentacji deark
- `tools/dragon5/extract.py` i `tools/dragon5/extract_media.py` - ekstraktory artykulow, muzyki i grafik dla Dragon #5 (dekodowanie CP852, 8-bitowe PNG, moduly XM)

### Zewnetrzne narzedzia opensource uzyte podczas analizy projektu:

- [DOSBox-X (GPL-2.0)](https://github.com/joncampbell123/dosbox-x) - emulacja DOS, debugger i zrzuty pamieci
- [REA (MIT)](https://github.com/morluto/rea/) - narzedzie/MCP do reverse engineeringu wspierajace agenta AI
- [Rizin (LGPL-3.0)](https://github.com/rizinorg/rizin) - framework do analizy binariow, disassemblera i debugowania
- [deark (MIT-style)](https://github.com/jsummers/deark) - zrodlo dokumentacji formatu DIET i innych DOS-owych packerow; uzywany jako odniesienie, nie jest wywolywany w trakcie ekstrakcji