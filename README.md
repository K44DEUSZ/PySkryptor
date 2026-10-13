# PySkryptor

[English version](README.en.md)

PySkryptor zamienia mowę z nagrań na tekst i pozwala ją tłumaczyć. Obsługuje pliki, transkrypcję i tłumaczenie na żywo oraz pobieranie materiałów z adresu URL. Program działa na Windows i wykonuje obliczenia lokalnie.

Wynik z pliku zapiszesz jako TXT, TXT ze znacznikami czasu, SRT, VTT, JSON, CSV lub Markdown. Zapis na żywo obsługuje TXT, Markdown, JSON i CSV; nie tworzy znaczników czasu, których ten tryb obecnie nie zachowuje. Interfejs jest dostępny po polsku i angielsku.

## Uruchomienie z kodu

Potrzebujesz Pythona 3.12, zależności aplikacji, wag modeli oraz FFmpeg. Repozytorium nie zawiera binariów ani wag.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-cpu.txt
.\.venv\Scripts\python.exe app/main.py
```

Zamiast `requirements-cpu.txt` możesz wybrać `requirements-cuda.txt`, jeśli środowisko ma korzystać z NVIDIA. Aktualny zestaw to PyQt6 6.11.0, Qt 6.11.2, Transformers 5.19.0 oraz PyTorch 2.14.1 w wariancie CPU albo CUDA 13.2. Wagi są wczytywane lokalnie, bez wykonywania kodu modelu.

Domyślne modele to `openai/whisper-large-v3-turbo` i `facebook/m2m100_418M`. Umieść ich foldery odpowiednio w `models/whisper-large-v3-turbo/` i `models/m2m100_418M/`, wraz z konfiguracją, tokenizatorem i wagami. Alternatywnie użyj „Dodaj z folderu” w ustawieniach. Obsługiwane są rodziny Whisper i M2M100, pliki safetensors, ich podzielone wagi oraz współczesne archiwa wag PyTorch `.bin`. Inne rodziny wymagają własnego adaptera.

Import zapisuje odwołanie do folderu, bez kopiowania wag. Rozpoznanie formatu nie oznacza gotowości modelu. Po zapisaniu ustawień aplikacja wykonuje krótki test obliczeń; nieudana zmiana przywraca wcześniej działający wybór. Aktywne zadanie transkrypcji lub sesja na żywo muszą zakończyć się przed zmianą modelu.

Umieść `ffmpeg.exe` i `ffprobe.exe` w `bin/ffmpeg/`. Pakiet `yt-dlp[default,deno]` dostarcza zależności pobierania i EJS. Dla uruchomienia z kodu możesz skopiować Deno ze środowiska Pythona do `bin/deno/deno.exe`; build robi to automatycznie. Zdalne pobieranie składników EJS jest wyłączone.

## Struktura

- `app/bootstrap.py`: uruchomienie i składanie aplikacji.
- `app/features/`: transkrypcja, tłumaczenie, pobieranie, źródła, ustawienia i zapis wyników.
- `app/runtime/`: katalog modeli, procesy AI, protokoły i wybór pakietu CPU/NVIDIA.
- `app/application/`: koordynacja działań i zadania wykonywane poza wątkiem GUI.
- `app/platform/`: mikrofon, jedna instancja, zasobnik i współpraca z instalatorem.
- `app/ui/`: panele, komponenty, ikony i prezentacja stanu.
- `app/core/`: wspólne konfiguracje, dane i narzędzia.

Podział katalogów zastępuje wcześniejsze MVC. Duże panele i globalna konfiguracja nadal wymagają stopniowego uproszczenia. Kod używa dwóch spacji; sprawdzają to Ruff, EditorConfig i test struktury bloków.

## Okno i dane

Jedna instancja działa dla danego profilu Windows. Ponowne uruchomienie przywraca istniejące okno. Minimalizację do zasobnika i pracę w tle po zamknięciu okna ustawiasz osobno. Pełne zakończenie wymaga potwierdzenia, a ukrycie zachowuje zadania i nagrywanie. Brak zasobnika powoduje wyświetlenie dialogu zakończenia.

W uruchomieniu z kodu dane trafiają do `userdata/`. Zbudowana aplikacja zapisuje je w `%LOCALAPPDATA%/PySkryptor`. Plik `portable.flag` obok EXE włącza lokalny `userdata/`. Build nie kopiuje ustawień, wyników ani importowanych modeli autora.

## Budowanie

Buduj GUI oraz silniki w osobnych środowiskach Python 3.12. GUI potrzebuje `requirements.txt`, CPU `requirements-cpu.txt`, a opcjonalny pakiet NVIDIA `requirements-cuda.txt`. W każdym środowisku builda zainstaluj PyInstaller 6.19.0 i pyinstaller-hooks-contrib 2026.1.

```powershell
.\tools\build_release.ps1 -GuiPython .venv\Scripts\python.exe `
  -CpuPython .venv-cpu\Scripts\python.exe `
  -CudaPython .venv-cuda\Scripts\python.exe
```

Pomiń `-CudaPython`, aby zbudować wariant CPU. Wynik to `dist/PySkryptor/` z oddzielnymi bibliotekami GUI, `runtimes/cpu/` i opcjonalnym `runtimes/cuda/`. Skrypt zbiera także licencje z analiz PyInstaller. Folder docelowy musi być nowy, aby nie mieszać wersji.

Budowanie ogranicza wyszukiwanie DLL do wybranego środowiska Python i Windows. Biblioteki znalezione w katalogach innych narzędzi powodują błąd builda. Zapobiega to dołączaniu niezgodnych plików, np. innej wersji ICU zamiast biblioteki systemowej wymaganej przez Qt.

Program preferuje zainstalowany pakiet NVIDIA przy ustawieniu automatycznym. Błąd jego uruchomienia albo testu powoduje próbę użycia osobnego pakietu CPU. Gotowość jest potwierdzana przez obliczenia modelu. Wariant CUDA 13.2 jest przeznaczony dla architektur Turing i nowszych; starsze karty oraz GPU AMD/Intel korzystają obecnie z CPU. Instalator nie zmienia sterowników systemowych.

Źródło instalatora Inno Setup znajduje się w `installer/PySkryptor.iss`. Kompilacja z gotowej paczki:

```powershell
ISCC.exe /DReleaseRoot="C:\pełna\ścieżka\do\PySkryptor" installer/PySkryptor.iss
```

Dodaj `/DIncludeCuda`, jeśli paczka ma zawierać opcjonalny składnik NVIDIA. Instalacja jest dla bieżącego użytkownika. Aktualizacja i odinstalowanie wymagają zamknięcia aplikacji; dane użytkownika pozostają poza folderem programu. Definicja instalatora wymaga jeszcze testu instalacji, aktualizacji i odinstalowania na czystym Windows.

## Sprawdzanie zmian

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m ruff check app tests tools PySkryptor.spec AIModelHost.spec
.\.venv\Scripts\python.exe -m pytest -q
```

Testy modeli wymagają osobnych wag i rzeczywistej próbki inferencji. Test GUI ani samo zaimportowanie biblioteki nie potwierdzają zgodności sprzętowej. Obecny zestaw sprawdzono lokalnie na CPU oraz RTX 5080 Laptop z własnym nagraniem syntetycznym, także dla wejścia na żywo i znaczników czasu.

Przy zamkniętej aplikacji sprawdź również gotowy EXE:

```powershell
.\.venv\Scripts\python.exe tools/check_release.py --root dist/PySkryptor --require-models
```

Ten test używa bieżącego profilu, tworzy rzeczywiste okno Windows i sprawdza zamknięcie aplikacji. `--require-models` wymaga gotowości obu skonfigurowanych modeli; bez tej opcji sprawdza start GUI także przy braku wag. Tryb diagnostyczny pomija powitanie i kończy własny przebieg automatycznie. Zwykłe uruchomienie nadal wymaga potwierdzenia pełnego zakończenia.

## Licencje i pobieranie

Kod ma licencję GNU GPL v3. Składniki zewnętrzne zachowują własne warunki; opis zawiera `THIRD_PARTY_NOTICES.txt`, a złożona paczka pełne teksty i inwentarze w `licenses/`.

Publiczne wydanie wymaga końcowej weryfikacji bibliotek natywnych, źródeł odpowiadających FFmpeg, składników Qt/NVIDIA oraz oferowanych modeli. Importowany model nie staje się automatycznie składnikiem instalatora.

Pobieranie wymaga oceny praw do materiału i warunków konkretnej usługi. Testy lokalnego HTTP/HLS potwierdzają zapis formatów i wybór ścieżek dźwiękowych. Nie potwierdzają dopuszczalności pobierania każdego materiału z YouTube ani działania wszystkich źródeł.
