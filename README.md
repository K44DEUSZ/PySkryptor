# PySkryptor

[English version](README.en.md)

PySkryptor zamienia mowę z nagrań na tekst i pozwala ją tłumaczyć. Możesz pracować na pliku, transkrybować na żywo albo pobrać materiał z adresu URL. Program ma interfejs okienkowy i jest przygotowany dla Windows.

Transkrypcja i tłumaczenie korzystają z modeli uruchamianych lokalnie. Wynik zapiszesz jako tekst lub napisy SRT. Pobieraniem materiałów z sieci zajmuje się `yt-dlp`, a przygotowaniem dźwięku FFmpeg.

## Uruchomienie z kodu

Repozytorium zawiera kod aplikacji, ale nie zawiera wag modeli ani plików wykonywalnych FFmpeg. Przed uruchomieniem potrzebujesz ich lokalnych kopii. Samo zainstalowanie zależności Pythona nie wystarczy.

Na Windows, w katalogu repozytorium:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe app/main.py
```

Plik `requirements.txt` wskazuje PyTorch z CUDA 12.4. Dobór sprzętu i zgodnego środowiska pozostaje po Twojej stronie; program nie zakłada, że każdy komputer ma kartę obsługującą CUDA.

Modele umieść w `models/`, każdy w osobnym katalogu z jego konfiguracją i wagami. Kod rozpoznaje modele transkrypcji Whisper oraz tłumaczenia M2M100. Narzędzia `ffmpeg.exe` i `ffprobe.exe` powinny trafić do `bin/ffmpeg/`. Pobieranie może również wymagać Deno w `bin/deno/deno.exe`, zależnie od źródła materiału. Modele i narzędzia mają własne licencje; sprawdź je przy wyborze plików.

## Gdzie szukać w kodzie

- `app/model/transcription/`: transkrypcja, przygotowanie dźwięku i zapis wyników.
- `app/model/translation/`: tłumaczenie.
- `app/model/download/`: pobieranie materiałów.
- `app/model/engines/`: uruchamianie i obsługa modeli.
- `app/view/` oraz `app/controller/`: interfejs i obsługa jego działań.
- `assets/`: zasoby aplikacji, w tym tłumaczenia interfejsu.

Ustawienia, pobrane pliki, wyniki i logi trafiają do `userdata/`, poza historią Git.

## Zbudowanie programu

Zainstaluj `requirements-dev.txt`, a następnie uruchom `build_onedir.bat`. Skrypt korzysta z PyInstaller i tworzy `dist/PySkryptor/` z programem oraz osobnym procesem `AIModelHost.exe`. Katalogi `assets/`, `bin/`, `models/` i `userdata/` pozostają obok plików EXE; skrypt kopiuje te, które są dostępne lokalnie.

## Licencja

Kod PySkryptora jest dostępny na [GNU GPL v3](LICENSE). Biblioteki, narzędzia i modele zachowują swoje licencje. Informacje o składnikach zewnętrznych znajdziesz w [THIRD_PARTY_NOTICES.txt](THIRD_PARTY_NOTICES.txt).
