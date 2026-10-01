# PROMOT — Poszukiwanie kontrahentów 1.2

Aplikacja Python z polskim panelem, osadzoną mapą, logiem na żywo i eksportem XLSX/HTML. Wymaga Python 3.11+ i internetu. Uruchamia lokalny serwer dostępny wyłącznie z tego komputera.

## Uruchomienie

macOS: otwórz `Uruchom.command`. Windows: `Uruchom.bat`. Pierwsze uruchomienie pobiera biblioteki do `.venv` obok programu. Nie wymaga instalowania aplikacji na zewnętrznym serwerze.

Ręcznie:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python app.py
```

Na Windows aktywacja to `.venv\Scripts\activate`. `pywebview` wyświetla panel i mapę w osobnym oknie. Windows może wymagać WebView2 Runtime. Linux wymaga backendu GTK/WebKit lub Qt zgodnie z dokumentacją pywebview. Jeśli instalacja pywebview się nie powiedzie, zainstaluj zależności przez `python -m pip install -r requirements-core.txt` i uruchom `python app.py --browser`. Panel i mapa będą w oknie przeglądarki. Port domyślny: 8765. `--port 9000` zmienia port; jeśli jest zajęty, program wybiera wolny i wypisuje adres.

## Pierwsze wyszukiwanie

1. Bez kluczy: pozostaw wyszukiwarkę DuckDuckGo oraz źródło Internet, wybierz kategorie i kraje. Na pierwszą próbę ustaw jedną kategorię, Polskę, 5 firm i 3 podstrony. OSM jest domyślnie wyłączone, więc awaria publicznego Overpass nie blokuje wyszukiwania. Możesz też dodać własne strony firm.
2. Dodatkowe źródła (targi, EEN, portale zakupowe, przetargi, katalogi, rejestry i PDF) również korzystają z wybranej wyszukiwarki. DuckDuckGo przez bibliotekę DDGS nie wymaga klucza, ale nie gwarantuje dostępności i może ograniczać zapytania. Opcjonalnie wybierz Brave Search API i wpisz klucz z dostępem Web Search. Licznik pokazuje maksymalną liczbę zapytań; po osiągnięciu limitu firm program przechodzi do analizy.
3. Naciśnij „Rozpocznij”. Etap pierwszy odkrywa kandydatów; etap drugi czyta strony firm. Log pokazuje błędy poszczególnych źródeł. Brak wyników i błąd źródła są pokazywane osobno.
4. „Zatrzymaj” zachowuje wyniki częściowe. Zakończenie bieżącego żądania może potrwać do około 45 s.
5. Każda sesja tworzy folder `wyniki/DATA_GODZINA/`: `wyniki.xlsx`, `mapa.html`, `wyniki.json`, `poszukiwania.log`. Przyciski pobierania tworzą dodatkowo aktualny eksport także w trakcie pracy. Zamknięcie osobnego okna zatrzymuje pracę i czeka na zapis wyników częściowych. W trybie przeglądarkowym zamknięcie karty nie wyłącza procesu; zatrzymaj wyszukiwanie przyciskiem, a serwer przez Ctrl+C w terminalu.

## Źródła — rzeczywisty zakres

| Źródło | Co program robi |
|---|---|
| Strony firm | Czyta HTML, dane JSON-LD i microdata oraz ograniczoną liczbę podstron kontaktowych, produktowych i zakupowych. Nie renderuje stron wymagających JavaScript/logowania. |
| OSM | Overpass API: ogólne obiekty przemysłowe w prostokącie. Branża jest sprawdzana dopiero na stronie firmy. To nie jest kompletna baza wszystkich firm. |
| Internet | DuckDuckGo (DDGS) lub Brave Web Search API, do 10 wyników na kombinację źródło × kategoria × kraj. Jedna strona wyników, bez obietnicy pełnego pokrycia. |
| Targi / katalogi | Wyszukiwanie przez wybraną wyszukiwarkę. Ze znalezionych stron targowych pobiera jawnie opisane odnośniki do stron wystawców, jeśli robots.txt pozwala. Nie ma osobnych integracji z każdym systemem targowym. |
| EEN / TED / rejestry | Tropy ze wskazanych domen przez wybraną wyszukiwarkę, zapisane osobno. Nie wykonuje pełnej integracji TED/KRS ani automatycznej weryfikacji prawnej firmy. |
| PDF | Odczyt tekstu maksymalnie 15 pierwszych stron, do 6 MB; brak OCR skanów. Maile z PDF nie są przypisywane automatycznie do osób. |
| LinkedIn | Tworzy odnośniki do ręcznego wyszukiwania osób (purchasing/procurement/Einkauf). Nie loguje się, nie pobiera profili, nie wysyła wiadomości. |
| Google Maps | Osadzona interaktywna mapa po wpisaniu klucza JavaScript API, linki wyszukiwania firmy i trasy. Nie scrapuje wizytówek ani nie pobiera kontaktów przez Places API. |

Połączenie OSM z Internetem rezerwuje co najmniej około połowę limitu kandydatów dla innych źródeł. Własne strony mają pierwszeństwo. Kolejność zapytań i limit mogą wpływać na skład listy; przeglądaj też zakładkę Tropy. Oddziały OSM mają osobne rekordy; wejścia WWW są deduplikowane według domeny, bez automatycznego rozstrzygania grup kapitałowych/NIP.

Serwer OSM wybierzesz w panelu. Domyślny Private.coffee dopuszcza różne projekty według wykazu OSM. FOSSGIS wskazuje na używanie własnego lub płatnego serwera do regularnych zastosowań komercyjnych. Publiczne serwery bywają przeciążone; program loguje HTTP/time-out i kontynuuje inne źródła, nie obchodzi limitów ani nie przełącza automatycznie serwera po odmowie. Zmniejsz obszar albo wróć do próby później. Lista i zasady: https://wiki.openstreetmap.org/wiki/Overpass_API#Public_Overpass_API_instances

## Google Maps i lokalizacje

W Google Cloud włącz **Maps JavaScript API** i rozliczenia, utwórz klucz przeglądarkowy ograniczony do tego API i adresu panelu, np. `http://127.0.0.1:8765/*`. Wpisz go w panelu i kliknij „Włącz mapę Google”. Klucz przeglądarkowy jest widoczny dla przeglądarki z definicji — zabezpiecz go restrykcjami i limitem budżetu w Google Cloud. Nie używaj klucza serwerowego bez ograniczeń.

Bez klucza mapa używa Leaflet i kafelków OpenStreetMap. Mapy mają zoom, przesuwanie, punkty, aktywne linki i odnośniki do tras Google. Nawigacja drogowa otwiera Google Maps w nowej karcie, nie jest własnym silnikiem tras programu. Kolor punktu odzwierciedla etap analizy, a licznik na mapie pokazuje postęp i liczbę firm bez współrzędnych. W trakcie odkrywania nie jest znana całkowita liczba firm; licznik zapytań i licznik analizy są oddzielne.

Współrzędne pochodzą wyłącznie z OSM lub danych strukturalnych stron. Program nie zgaduje lokalizacji, nie geokoduje po samym mieście i nie używa masowo publicznego Nominatim. Dane strony mogą oznaczać siedzibę zamiast konkretnego zakładu — sprawdź źródło. Firma bez współrzędnych pozostaje w XLSX i tabeli.

Eksport HTML przechowuje wyniki, ale mapę należy otwierać przez HTTP, nie bezpośrednio z dysku (`file://`). OSM wymaga prawidłowego nagłówka Referer, którego pliki lokalne zwykle nie wysyłają. W trybie pliku program nie pobiera kafelków i wyświetla instrukcję zamiast blokady 403. Najłatwiej otworzyć ostatnią zapisaną mapę przez `Otworz_mape.command` (Mac) lub `Otworz_mape.bat` (Windows). Te przyciski uruchamiają serwer lokalny i odczytują ostatnią sesję, również z wcześniejszych wersji programu.

Dowolny eksport HTML można też otworzyć przez lokalny serwer: w folderze eksportu uruchom `python -m http.server 8766 --bind 127.0.0.1`, następnie otwórz `http://127.0.0.1:8766/mapa.html`. Plik `static/map.html` jest szablonem programu, nie wynikami wyszukiwania. Klucze nie są zapisywane w HTML ani XLSX. Mapę można przełączyć na Google po wpisaniu klucza ograniczonego do aktualnego adresu HTTP. Nie publikuj pliku z kontaktami bez sprawdzenia zakresu udostępniania.

Program nie zmienia tożsamości klienta, nie używa proxy do obchodzenia blokad ani nie pobiera map na zapas. Po błędach pobierania podkład jest wyłączany, a punkty pozostają. Okno pywebview używa trwałej pamięci podręcznej przeglądarki. Przy utrzymującej się blokadzie także w trybie HTTP sprawdź, czy przeglądarka lub rozszerzenie nie usuwa nagłówka Referer; dostęp OSM nie jest gwarantowany. Zasady: https://operations.osmfoundation.org/policies/tiles/

## Kontakty i wiarygodność

- Osoby są odczytywane z jawnych pól Person (JSON-LD/microdata). Podstawowe rozpoznawanie imienia i nazwiska w małej sekcji kontaktowej daje wyłącznie **kandydata do weryfikacji**.
- Mail ogólny albo telefon z `tel:` nie staje się automatycznie kontaktem konkretnej osoby. Nie są generowane adresy według wzorca imię.nazwisko.
- Telefony w zwykłym tekście bez oznaczenia nie są zgadywane z ciągów cyfr. Część kontaktów może przez to nie zostać wykryta.
- Każdy kontakt ma URL źródłowy i status przypisania. Nie ma sprawdzania dostarczalności emaila, ani potwierdzenia zatrudnienia.
- Dopasowanie oznacza słowa branżowe w treści — nie potwierdzenie popytu, masy detalu, tolerancji, materiału, serii ani roli odbiorcy. Kooperant CNC może być konkurentem. Przed kontaktem sprawdź firmę.
- Odczyt stron firm respektuje robots.txt i limituje zapytania i blokuje adresy sieci prywatnej. robots.txt nie zastępuje warunków korzystania ze źródła. Brak dostępnego robots.txt (poza 404) oznacza pominięcie.
- Aplikacja nie wysyła wiadomości. Publikacja kontaktu nie jest zgodą na marketing; wykorzystanie kontaktów wymaga osobnej oceny obowiązujących zasad.
- Dane OSM: © OpenStreetMap contributors, ODbL. Zachowaj atrybucję i sprawdź obowiązki licencyjne przed rozpowszechnianiem bazy.

## Testy

```sh
python -m unittest discover -s tests -v
```

Testy korzystają z syntetycznych danych, sprawdzają przypisanie osób, brakujące współrzędne, eksport i linki XLSX, zabezpieczenie przed formułami w danych, blokowanie prywatnych adresów i walidację konfiguracji. Nie potwierdzają dostępności płatnych API bez kluczy.

Wcześniejszy test wersji 1.0 (30.09.2026): odczyt publicznej strony Promot znalazł dwa kontakty i utworzył eksporty. Panel i mapa bazowa OSM zostały sprawdzone w przeglądarce. Publiczne endpointy Overpass podczas prób zwracały 406/504 albo timeout; pełne wyszukiwanie OSM na żywo nie zostało potwierdzone. Parser odpowiedzi OSM sprawdzono na danych testowych. Brave i Google Maps nie były testowane z prawdziwymi kluczami. Osobne okno pywebview oraz instalatory na Windows nie zostały uruchomione w tym środowisku.

Dokumentacja: [Brave Search](https://api-dashboard.search.brave.com/app/documentation/web-search/get-started), [Google Maps](https://developers.google.com/maps/documentation/javascript/load-maps-js-api), [Overpass](https://wiki.openstreetmap.org/wiki/Overpass_API), [OSM](https://www.openstreetmap.org/copyright), [LinkedIn](https://www.linkedin.com/help/recruiter/answer/a1341387).

## Zmiany 1.2

Domyślne wyszukiwanie internetowe bez klucza, OSM jako opcja, limit kolejnych błędów wyszukiwarki, jednoznaczny status awarii zamiast pozornego sukcesu. Poprawiono otwieranie map przez HTTP i dodano Otworz_mape.command / Otworz_mape.bat. Po aktualizacji zamknij stare okno i uruchom nowy Uruchom.command lub Uruchom.bat; instalator doinstaluje DDGS. W panelu powinna być widoczna wersja 1.2 i opcja DuckDuckGo.

Końcowy test wersji 1.2: wyszukiwanie internetowe bez klucza, Armatura i hydraulika, Polska, 5 firm, 3 podstrony. Odczytano 5 stron firm i zapisano 62 wpisy kontaktowe, w tym 6 kandydatów na osoby. Zero błędów źródeł; czas około 32 sekund. Wpisy kontaktowe nie oznaczają unikalnych osób ani potwierdzonych leadów. 14 testów automatycznych zakończyło się powodzeniem.


## Wersja 1.3 — profile i listy firm

Wybierz jeden profil: **Odbiorcy odkuwek**, **Odbiorcy części gotowych** albo **Kooperacja CNC**. Profil zmienia zapytania internetowe i jest zapisany przy firmie w panelu oraz XLSX. Pięć branż producentów wybiera się niezależnie. Wynik nie potwierdza zapotrzebowania zakupowego.

Adres katalogu można wkleić w pole „Własne strony firm / katalogów”. Program rozpoznaje listy JSON-LD ItemList i karty firm, w tym listy Yoys, również w wynikach wyszukiwania. Importuje wpisy z odczytanej strony do limitu firm. Nie przechodzi automatycznie przez wszystkie strony paginacji. Każda firma otrzymuje osobny rekord i link źródłowy. Jeśli brak WWW, zachowuje wpis do weryfikacji. Kontakty pochodzą z konkretnej karty, nie stopki katalogu. Nietypowe listy w artykułach, treści generowane wyłącznie JavaScriptem i strony blokujące odczyt mogą wymagać osobnego parsera.

## Wersja 1.4 — pełny plan i lokalizowanie

Opcja „Wykonaj cały plan zapytań” jest domyślnie włączona. Po osiągnięciu limitu firm kolejne zapytania nadal zbierają tropy, ale nie zwiększają listy firm. Dwa kolejne błędy wyszukiwarki wciąż zatrzymują zapytania; panel pokazuje powód i liczbę pominiętych zapytań.

„Lokalizuj adresy firm na mapie” oraz „Znajdź firmy na mapie” korzystają z Photon. Drugi przycisk działa również na ostatniej zapisanej sesji i zapisuje wynik w nowej sesji. Brak adresu lub niejednoznaczny wynik nie tworzą punktu. Adresy publicznych firm trafiają do usługi Photon. Wyniki są buforowane lokalnie; żądania wykonywane pojedynczo, nie częściej niż co 1,2 sekundy. Błąd usługi wyłącza dalsze zapytania w danej sesji. Usługa demonstracyjna nie gwarantuje dostępności; do większych regularnych zadań ustaw własny Photon przez PROMOT_PHOTON_URL. Zasady: https://github.com/komoot/photon#demo-server . Dane © OpenStreetMap contributors, ODbL.

Punkty opisane jako przybliżenia ulicy/miejscowości nie potwierdzają lokalizacji siedziby. Firmy z tej samej miejscowości mogą mieć nakładające się punkty. Dokładność i dopasowany adres są widoczne w panelu, dymkach mapy i XLSX.

## Wersja 1.4.1 — rozróżnianie pustych wyników i awarii

Komunikat biblioteki DuckDuckGo „No results found.” jest teraz pustym wynikiem, a nie błędem przerywającym plan. Liczniki pokazują osobno puste i błędne zapytania. Po przejściowym błędzie program odczekuje 5 sekund i przechodzi do następnego zapytania. Dopiero pięć kolejnych awarii przerywa plan. Jawna odmowa dostępu / limit (np. 403, 429, CAPTCHA) zatrzymuje zapytania od razu. To zastępuje wcześniejszą zasadę dwóch błędów. Puste wyniki nie dowodzą braku firm; mogą też wynikać z ograniczeń dostawcy lub odczytu odpowiedzi. Reguły dostępu do stron firm pozostają respektowane.

## Wersja 1.5 — Polska, województwa, lokalny PBF

Umieść `poland-latest.osm.pbf` obok `app.py`. Program tworzy `osm_index.json` z nazwanych zakładów (`man_made=works`, `industrial` i wybranych rzemiosł metalowych), ich lokalizacji i granic 16 województw. Pierwszy odczyt może potrwać kilka minut; kolejne używają indeksu. Zmiana rozmiaru lub daty pliku powoduje przebudowę. Wybór województw opiera się na wielokątach administracyjnych OSM, nie prostokątach. Indeks nie jest kompletnym rejestrem wszystkich przedsiębiorstw w Polsce.

Domyślnie źródłem jest lokalny OSM, a regionami Śląskie i Małopolskie. Internet można włączyć dodatkowo. Aby pracować całkowicie lokalnie przy wyszukiwaniu danych, wyłącz „Pobieraj opisy i kontakty ze stron firm”. Podkład mapy nadal wymaga internetu. Nie są potrzebne klucze API. W tym trybie brak dopasowania lokalizacji firmy internetowej do lokalnego OSM pozostaje jawnie oznaczony do uzupełnienia; nie korzystamy z zewnętrznego geokodowania.

Tabela i mapa mają filtr grupy oraz potwierdzenia obszaru. XLSX zawiera osobne arkusze: Odbiorcy odkuwek, Odbiorcy części gotowych, Kooperacja CNC, Konkurencja i Do weryfikacji. Jedna firma może trafić do kilku grup współpracy. To kwalifikacja słów z opisu, nie potwierdzenie zakupów; przy firmie jest uzasadnienie. Nieznane branże pozostają do sprawdzenia. Kuźnie trafiają do konkurencji. Odbiorców wyszukujemy według wytwarzanych produktów, bez frazy „odkuwki dostawcy”.

Limit firm wynosi 1–10000, domyślnie 500. W lokalnym OSM import przeplata województwa, aby pierwsze nie zajęło całego limitu. Brak obiektu na mapie OSM nie oznacza, że firma nie istnieje. Punkty na obszarach zakładów są punktami wewnątrz ich geometrii, nie gwarantowanymi adresami wejścia.

Dane © OpenStreetMap contributors, ODbL: https://www.openstreetmap.org/copyright . Plik PBF i indeks nie są dołączane do ZIP programu; pozostaw je w głównym katalogu aplikacji.
