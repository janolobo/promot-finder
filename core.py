"""Public-source prospect research. No login automation or guessed personal data."""
from __future__ import annotations
import io
from html import unescape
import ipaddress
import json
import re
import socket
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse, unquote
from urllib.robotparser import RobotFileParser

import requests
from catalogs import DIRECT_SOURCES, collect
from local_osm import LocalIndex, PROVINCES, GROUPS, qualify
from bs4 import BeautifulSoup
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter

CATEGORIES = {
    'automotive': ('Podzespołów samochodowych', ['steering systems manufacturer', 'Hersteller Fahrwerk Lenkung', 'producent podzespołów samochodowych'], ['steering', 'suspension', 'automotive', 'lenkung', 'fahrwerk', 'zawiesze']),
    'agriculture': ('Maszyn rolniczych', ['agricultural machinery manufacturer', 'Landmaschinen Hersteller', 'producent maszyn rolniczych'], ['agricultur', 'landmaschinen', 'rolnicz', 'zeměděl']),
    'valves': ('Armatury i hydrauliki', ['industrial valve manufacturer', 'Industriearmaturen Hersteller', 'producent armatury hydraulicznej'], ['valve', 'armatur', 'hydraul', 'zawor', 'zawór']),
    'drives': ('Napędów i przekładni', ['gearbox coupling manufacturer', 'Getriebe Hersteller', 'producent przekładni napędów'], ['gearbox', 'coupling', 'getriebe', 'przekład', 'napęd']),
    'construction': ('Maszyn budowlanych i transportu', ['construction equipment manufacturer', 'Baumaschinen Hersteller', 'producent maszyn budowlanych'], ['construction equipment', 'baumaschinen', 'budowlan', 'transport']),
}
RECIPIENTS = {
    'forgings': ('Odkuwek', ['buyers of forgings', 'Abnehmer von Schmiedeteilen', 'odbiorcy odkuwek'], ['odkuw', 'schmiedeteil', 'forging']),
    'finished': ('Gotowych części', ['buyers of finished parts', 'Abnehmer von Fertigteilen', 'odbiorcy gotowych części'], ['gotowych części', 'gotowych czesci', 'części gotow', 'czesci gotow', 'fertigteil', 'finished part']),
    'iron': ('Odlewów żeliwnych', ['buyers of iron castings', 'Abnehmer von Eisengussteilen', 'odbiorcy odlewów żeliwnych'], ['żeliw', 'zeliw', 'gusseisen', 'iron casting']),
    'automotive': ('Automotive', ['automotive buyers', 'Automotive-Abnehmer', 'odbiorcy automotive'], ['automotive', 'motoryzac']),
    'forged': ('Elementów kutych', ['buyers of forged parts', 'Abnehmer von Schmiedeteilen', 'odbiorcy elementów kutych'], ['elementów kutych', 'elementow kutych', 'forged part', 'geschmiedet']),
}
PROFILES = {
    'all': ('Wszystkie rodzaje współpracy', ['', '', '']),
    'forgings': ('Odbiorcy odkuwek', ['forged components suppliers', 'Schmiedeteile Lieferanten', 'odkuwki dostawcy']),
    'finished_parts': ('Odbiorcy części gotowych', ['machined components suppliers', 'Fertigteile Zulieferer', 'części gotowe obrabiane dostawcy']),
    'cnc': ('Kooperacja CNC', ['CNC machining subcontracting', 'CNC Bearbeitung Kooperation', 'obróbka CNC kooperacja']),
}

def search_term(category, country, profile):
    language = {'DE': 1, 'PL': 2}.get(country, 0)
    return CATEGORIES[category][1][language] + (' ' + PROFILES[profile][1][language] if profile == 'cnc' else '')

def recipient_term(key, country):
    language = {'DE': 1, 'PL': 2}.get(country, 0)
    return RECIPIENTS[key][1][language]

def audience_label(config):
    names = [RECIPIENTS[k][0] for k in config.get('recipients', []) if k in RECIPIENTS]
    return ', '.join(names) if names else PROFILES[config.get('profile', 'forgings')][0]

SOURCES = {
    'web': ('Internet — strony firm', ''),
    'fairs': ('Katalogi targowe', '(site:hannovermesse.de OR site:agritechnica.com OR site:automechanika.messefrankfurt.com)'),
    'een': ('Enterprise Europe Network', 'site:een.ec.europa.eu/partnering-opportunities'),
    'tenders': ('Przetargi TED i BZP', '(site:ted.europa.eu OR site:ezamowienia.gov.pl)'),
    'directories': ('Katalogi branżowe i dystrybutorzy', '(distributor OR dealer OR dystrybutor OR Branchenverzeichnis)'),
    'pdf': ('Katalogi produktów PDF', 'filetype:pdf'),
}
COUNTRIES = {'PL': ('Polska', 'pl'), 'DE': ('Deutschland', 'de'), 'CZ': ('Česko', 'en')}
DENIED = ('linkedin.com', 'facebook.com', 'instagram.com', 'google.com', 'google.pl', 'google.de', 'maps.app.goo.gl', 'youtube.com')
DIRECTORIES = ('een.ec.europa.eu', 'hannovermesse.de', 'agritechnica.com', 'messefrankfurt.com', 'ted.europa.eu', 'ezamowienia.gov.pl', 'rejestr.io', 'gov.pl', 'panoramafirm.pl', 'pkt.pl', 'europages.pl', 'europages.com', 'europages.de', 'kompass.com', 'wlw.de', 'industrystock.pl', 'industrystock.de', 'industrystock.com', 'lieferanten.de', 'sjn.de', 'factories.pl', 'pgm.org.pl', 'induux.de', 'ffo-info.de', 'yoys.pl', 'yoys.com')
EMAIL = re.compile(r'[A-Z0-9.!#$%&\'*+/=?^_`{|}~-]+@[A-Z0-9.-]+\.[A-Z]{2,}', re.I)
ROLE = re.compile(r'purchas|procurement|buyer|einkauf|zakup|sourcing|supplier|geschäftsführ|dyrektor|manager|sales|sprzedaż|vertrieb|director', re.I)
UA = 'PromotResearch/1.0 (public business directory research; respects robots.txt)'
OSM_SERVERS = {'privatecoffee': 'https://overpass.private.coffee/api/interpreter', 'fossgis': 'https://overpass-api.de/api/interpreter'}

def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')

def domain(url):
    return (urlparse(url).hostname or '').lower().removeprefix('www.')

def is_domain(host, suffixes):
    return any(host == x or host.endswith('.' + x) for x in suffixes)

def safe_url(url):
    p = urlparse(url)
    if p.scheme not in ('http', 'https') or not p.hostname or p.username or p.password:
        raise ValueError('Niedozwolony adres URL')
    if p.port not in (None, 80, 443):
        raise ValueError('Dozwolone są standardowe porty stron WWW')
    addresses = socket.getaddrinfo(p.hostname, p.port or (443 if p.scheme == 'https' else 80))
    if not addresses or any(not ipaddress.ip_address(x[4][0]).is_global for x in addresses):
        raise ValueError('Adres lokalny lub prywatny — pominięto')
    return url

class Cancelled(Exception):
    pass

class Fetcher:
    def __init__(self, stop, log):
        self.stop, self.log = stop, log
        self.session = requests.Session()
        self.session.headers['User-Agent'] = UA
        self.robots, self.last = {}, {}

    def check(self):
        if self.stop.is_set():
            raise Cancelled()

    def raw(self, url, **kwargs):
        for _ in range(6):
            self.check()
            safe_url(url)
            if is_domain(domain(url), DENIED):
                raise ValueError('Źródło dostępne tylko do ręcznego wyszukiwania')
            host = domain(url)
            wait = max(0, 1.1 - (time.monotonic() - self.last.get(host, 0)))
            if self.stop.wait(wait):
                raise Cancelled()
            self.last[host] = time.monotonic()
            response = self.session.get(url, timeout=(8, 18), allow_redirects=False, stream=True, **kwargs)
            if response.is_redirect:
                target = urljoin(url, response.headers.get('Location', ''))
                response.close()
                if not self.allowed(target):
                    raise ValueError('Przekierowanie zablokowane przez robots.txt')
                url = target
                continue
            if response.status_code in (429, 503):
                delay = min(15, int(response.headers.get('Retry-After', '3')) if response.headers.get('Retry-After', '3').isdigit() else 3)
                response.close()
                if self.stop.wait(delay):
                    raise Cancelled()
                continue
            try:
                response.raise_for_status()
                chunks, size = [], 0
                for chunk in response.iter_content(65536):
                    self.check()
                    size += len(chunk)
                    if size > 6_000_000:
                        raise ValueError('Plik przekracza limit 6 MB')
                    chunks.append(chunk)
                return b''.join(chunks), response.headers.get('Content-Type', ''), url
            finally:
                response.close()
        raise ValueError('Przekroczony limit ponowień/przekierowań')

    def allowed(self, url):
        origin = '{0.scheme}://{0.netloc}'.format(urlparse(url))
        if origin not in self.robots:
            # Mark pending to avoid recursive robots redirects.
            self.robots[origin] = False
            try:
                data, _, _ = self.raw(origin + '/robots.txt')
                parser = RobotFileParser()
                parser.parse(data.decode('utf-8', 'replace').splitlines())
                self.robots[origin] = parser
            except requests.HTTPError as exc:
                self.robots[origin] = bool(exc.response is not None and exc.response.status_code == 404)
            except Cancelled:
                raise
            except Exception:
                self.robots[origin] = False
        rule = self.robots[origin]
        return rule if isinstance(rule, bool) else rule.can_fetch(UA, url)

    def page(self, url):
        if not self.allowed(url):
            raise ValueError('robots.txt nie pozwala na pobranie lub jest niedostępny')
        return self.raw(url)

def objects(value):
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from objects(item)
    elif isinstance(value, list):
        for item in value:
            yield from objects(item)

def coords(lat, lon):
    try:
        a, b = float(lat), float(lon)
        return (a, b) if -90 <= a <= 90 and -180 <= b <= 180 else (None, None)
    except (ValueError, TypeError):
        return None, None

def extract(html, url):
    soup = BeautifulSoup(html, 'html.parser')
    result = {'name': '', 'address': '', 'lat': None, 'lon': None, 'geo_source': '', 'contacts': [], 'links': [], 'text': ''}
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.string or script.get_text())
        except (ValueError, TypeError):
            continue
        for obj in objects(data):
            types = obj.get('@type', [])
            types = [types] if isinstance(types, str) else types
            if 'Person' in types and isinstance(obj.get('name'), str) and any(obj.get(k) for k in ['jobTitle', 'email', 'telephone']):
                emails = EMAIL.findall(str(obj.get('email', '')))
                phone = str(obj.get('telephone', '')) if isinstance(obj.get('telephone', ''), str) else ''
                result['contacts'].append(dict(person=obj['name'], role=str(obj.get('jobTitle', '')), email='; '.join(emails), phone=phone, source=url, status='Osoba — dane strukturalne strony, do weryfikacji'))
            if any(t in types for t in ['Organization', 'LocalBusiness', 'Corporation', 'Store', 'ProfessionalService']):
                email = '; '.join(EMAIL.findall(str(obj.get('email', ''))))
                phone = obj.get('telephone', '')
                if email or isinstance(phone, str) and phone:
                    result['contacts'].append(dict(person='', role='', email=email, phone=phone if isinstance(phone, str) else '', source=url, status='Kontakt firmy — dane strukturalne'))
            if 'latitude' in obj and 'longitude' in obj and result['lat'] is None:
                result['lat'], result['lon'] = coords(obj['latitude'], obj['longitude'])
                result['geo_source'] = url if result['lat'] is not None else ''
            if 'streetAddress' in obj and not result['address']:
                result['address'] = ', '.join(str(obj.get(k, '')) for k in ['streetAddress', 'postalCode', 'addressLocality', 'addressCountry'] if isinstance(obj.get(k), str))
            if any(t in types for t in ['Organization', 'LocalBusiness', 'Corporation', 'AutomotiveBusiness', 'Store', 'ProfessionalService']) and not result['name']:
                result['name'] = str(obj.get('name', ''))
    address_node = soup.select_one('[itemprop=address], address')
    if address_node and not result['address']:
        result['address'] = address_node.get_text(' ', strip=True)
    if result['lat'] is None:
        destinations = set(re.findall(r'https://www\.google\.com/maps/dir/[^\s\"\'<>]*?[?&]destination=(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)', unescape(unquote(str(soup)))))
        if len(destinations) == 1:
            result['lat'], result['lon'] = coords(*next(iter(destinations)))
            result['geo_source'] = url if result['lat'] is not None else ''
    for node in soup(['script', 'style', 'noscript']):
        node.decompose()
    result['text'] = soup.get_text(' ', strip=True)[:100000]
    if not result['name']:
        result['name'] = soup.title.get_text(' ', strip=True)[:180] if soup.title else domain(url)
    # Explicit Person microdata preserves association, unlike page-wide email matching.
    for card in soup.select('[itemtype$="/Person"]'):
        def field(key):
            tag = card.select_one('[itemprop="' + key + '"]')
            return (tag.get('content') or tag.get_text(' ', strip=True)) if tag else ''
        if field('name'):
            result['contacts'].append(dict(person=field('name'), role=field('jobTitle'), email='; '.join(EMAIL.findall(field('email'))), phone=field('telephone'), source=url, status='Osoba — microdata, do weryfikacji'))
    for a in soup.select('a[href]'):
        href = a.get('href', '').strip()
        if href.startswith('mailto:'):
            for email in EMAIL.findall(unquote(href.split('?')[0])):
                # A small contact card is evidence for a candidate name, never definitive attribution.
                parent = a.find_parent(['p', 'li', 'article', 'section', 'div'])
                context = parent.get_text(' ', strip=True) if parent else ''
                if len(context) > 500:
                    context = ''
                candidates = re.findall(r'\b([A-ZĄĆĘŁŃÓŚŹŻÄÖÜ][a-ząćęłńóśźżäöüß]+(?:-[A-ZĄĆĘŁŃÓŚŹŻÄÖÜ][a-ząćęłńóśźżäöüß]+)? [A-ZĄĆĘŁŃÓŚŹŻÄÖÜ][a-ząćęłńóśźżäöüß]+(?:-[A-ZĄĆĘŁŃÓŚŹŻÄÖÜ][a-ząćęłńóśźżäöüß]+)?)\b', context)
                title_words = {'inżynier', 'sprzedaży', 'kierownik', 'dyrektor', 'dział', 'zakupów', 'manager', 'director', 'sales', 'purchasing', 'contact', 'kontakt', 'general', 'customer', 'service', 'export', 'technical'}
                candidates = list(dict.fromkeys(n for n in candidates if not any(w.lower() in title_words for w in n.split())))
                person = candidates[0] if len(candidates) == 1 and ROLE.search(context) else ''
                result['contacts'].append(dict(person=person, role=context[:240] if person else '', email=email, phone='', source=url, status='Kandydat na osobę — sprawdź kontekst' if person else 'Kontakt strony — bez przypisania do osoby'))
        elif href.startswith('tel:'):
            phone = unquote(href[4:]).split('?')[0][:60]
            digits = re.sub(r'\D', '', phone)
            if 7 <= len(digits) <= 15 and digits not in ('0123456789', '123456789') and len(set(digits)) > 1:
                result['contacts'].append(dict(person='', role='', email='', phone=phone, source=url, status='Telefon strony — bez przypisania do osoby'))
        else:
            full = urljoin(url, href).split('#')[0]
            if full.startswith(('https://', 'http://')):
                result['links'].append((full, a.get_text(' ', strip=True)[:100]))
    existing = {c['email'].lower() for c in result['contacts']}
    for email in sorted(set(EMAIL.findall(result['text']))):
        if email.lower() not in existing:
            result['contacts'].append(dict(person='', role='', email=email, phone='', source=url, status='Kontakt w tekście — bez przypisania do osoby'))
    return result

def company_list(html, url):
    """Read explicit business entries, never contacts from the whole directory."""
    soup = BeautifulSoup(html, 'html.parser')
    entries, seen = [], set()
    def append(name, target, fragment):
        if not isinstance(name, str) or not name.strip() or not isinstance(target, str):
            return
        target = urljoin(url, target)
        if not target or urlparse(target).scheme not in ('http', 'https') or target == url or target in seen:
            return
        seen.add(target)
        parsed = extract(fragment, target)
        fragment_soup = BeautifulSoup(fragment, 'html.parser')
        address_node = fragment_soup.select_one('[itemprop=address], address')
        if address_node and not parsed['address']:
            parsed['address'] = address_node.get_text(' ', strip=True)
        entries.append(dict(name=name.strip(), target=target, parsed=parsed))
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.string or script.get_text())
        except (ValueError, TypeError):
            continue
        for obj in objects(data):
            if obj.get('@type') != 'ItemList':
                continue
            for entry in obj.get('itemListElement', []):
                if not isinstance(entry, dict):
                    continue
                item = entry.get('item', entry)
                if isinstance(item, str):
                    item = dict(name=entry.get('name', ''), url=item)
                if isinstance(item, dict):
                    append(item.get('name', entry.get('name', '')), item.get('url', item.get('@id', '')),
                           '<script type="application/ld+json">' + json.dumps(item).replace('</', r'<\/') + '</script>')
    for card in soup.select('[itemtype$="/LocalBusiness"], [itemtype$="/Organization"], .company-card, .company-item, .business-card, .listing-item,  .search-result, .company, [id^="comp_"]'):
        anchor = card.select_one('[itemprop="name"] a[href], a[itemprop="url"][href], h2 a[href], h3 a[href], a.company-name[href]')
        if anchor is None:
            continue
        name_node = card.select_one('[itemprop="name"], h2, h3, .company-name')
        append((name_node or anchor).get_text(' ', strip=True), anchor.get('href', ''), str(card))
    return entries

def classify(text, selected):
    lowered = text.lower()
    hits = []
    for key in selected:
        matched = [w for w in CATEGORIES[key][2] if w in lowered]
        if matched:
            word = matched[0]
            at = lowered.find(word)
            hits.append((CATEGORIES[key][0], text[max(0, at - 70):at + 150]))
    return '; '.join(x[0] for x in hits), ' | '.join(x[1] for x in hits)[:1000]

def matches_selection(text, config):
    """Producers and recipients combine with AND. An empty list adds no constraint."""
    body = text or ''
    lowered = body.lower()
    cats = config.get('categories') or []
    recs = [key for key in config.get('recipients') or [] if key in RECIPIENTS]
    if cats and not classify(body, cats)[0]:
        return False
    if recs and not any(any(word in lowered for word in RECIPIENTS[key][2]) for key in recs):
        return False
    return True

class SearchBlocked(ValueError):
    """Explicit provider refusal; do not retry or rotate providers."""


def search_web(query, country, config):
    """One chosen provider, no proxy rotation or CAPTCHA bypass."""
    if config.get('engine', 'duckduckgo') == 'brave':
        response = requests.get('https://api.search.brave.com/res/v1/web/search', params={'q': query, 'count': 10, 'country': country, 'search_lang': COUNTRIES[country][1]}, headers={'X-Subscription-Token': config['brave_key'], 'Accept': 'application/json'}, timeout=(8, 20))
        response.raise_for_status()
        return response.json().get('web', {}).get('results', [])
    from ddgs import DDGS
    region = {'PL': 'pl-pl', 'DE': 'de-de', 'CZ': 'cz-cs'}[country]
    from ddgs.exceptions import DDGSException, RatelimitException, TimeoutException
    try:
        results = DDGS(timeout=15).text(query, region=region, safesearch='moderate', max_results=10, backend='duckduckgo')
    except RatelimitException:
        raise SearchBlocked('DuckDuckGo ograniczył zapytania — zatrzymuję wyszukiwanie') from None
    except TimeoutException:
        raise ValueError('DuckDuckGo: przekroczono czas odpowiedzi dla tego zapytania') from None
    except DDGSException as exc:
        message = str(exc).strip().lower()
        if message == 'no results found.':
            return []
        if re.search(r'captcha|ratelimit|rate.limit|too many requests|\b(403|429)\b', message):
            raise SearchBlocked('DuckDuckGo odmówił dostępu lub ograniczył zapytania') from None
        raise ValueError('DuckDuckGo nie dostarczył poprawnej odpowiedzi dla tego zapytania') from None
    return [{'title': r.get('title', ''), 'url': r.get('href', r.get('url', ''))} for r in results]

class Research:
    def __init__(self, folder):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.running = False
        self.records, self.discoveries, self.logs = [], [], []
        self.progress = dict(phase='Gotowy', done=0, total=0, queries_done=0, queries_total=0, errors=0)
        self.run_id = ''
        self.config = {}
        self.worker = None
        self.local_index = None

    def log(self, message):
        with self.lock:
            entry = f'{datetime.now():%H:%M:%S}  {message}'
            self.logs.append(entry)
            self.logs = self.logs[-2000:]
            if self.run_id:
                with (self.folder / self.run_id / 'poszukiwania.log').open('a', encoding='utf-8') as f:
                    f.write(entry + '\n')

    def snapshot(self):
        with self.lock:
            return json.loads(json.dumps(dict(running=self.running, records=self.records, discoveries=self.discoveries, logs=self.logs, progress=self.progress, run_id=self.run_id), ensure_ascii=False))

    def checkpoint(self):
        if self.run_id:
            path = self.folder / self.run_id / 'wyniki.json'
            temp = path.with_suffix('.tmp')
            temp.write_text(json.dumps(self.snapshot(), ensure_ascii=False, indent=2), encoding='utf-8')
            temp.replace(path)

    def start(self, config):
        with self.lock:
            if self.running:
                raise ValueError('Poszukiwania już trwają')
            provinces = config.get('provinces')
            if provinces is not None and (not isinstance(provinces, list) or not provinces or any(p not in PROVINCES for p in provinces)):
                raise ValueError('Wybierz co najmniej jedno województwo')
            profile = config.get('profile', 'forgings')
            if profile not in PROFILES:
                raise ValueError('Wybierz profil współpracy')
            cats = [k for k in config.get('categories', []) if k in CATEGORIES]
            raw_recipients = config.get('recipients', [])
            if not isinstance(raw_recipients, list) or any(not isinstance(item, str) for item in raw_recipients):
                raise ValueError('Wybierz odbiorców z listy')
            recs = [k for k in raw_recipients if k in RECIPIENTS]
            countries = ['PL'] if provinces is not None else [k for k in config.get('countries', []) if k in COUNTRIES]
            sources = [k for k in config.get('sources', []) if k in SOURCES or k in DIRECT_SOURCES or k in ['osm', 'linkedin', 'maps']]
            if not countries:
                raise ValueError('Wybierz kraj')
            listed = any(k in DIRECT_SOURCES or k == 'osm' for k in sources) or bool(config.get('seeds', '').strip())
            if not (cats or recs) and not listed:
                raise ValueError('Zaznacz producenta albo odbiorcę. Bez kategorii katalog, na przykład PGM albo Agrotech, wczytuje się w całości.')
            if not any(s in SOURCES or s in DIRECT_SOURCES or s == 'osm' for s in sources) and not config.get('seeds', '').strip():
                raise ValueError('Wybierz automatyczne źródło (np. OSM) lub podaj strony firm. LinkedIn i Google Maps są źródłami ręcznymi.')
            if config.get('engine', 'duckduckgo') not in ('duckduckgo', 'brave'):
                raise ValueError('Wybierz DuckDuckGo albo Brave API')
            if config.get('engine', 'duckduckgo') == 'brave' and any(s in SOURCES for s in sources) and not config.get('brave_key', '').strip():
                raise ValueError('Wybrane źródła wyszukiwarkowe wymagają klucza Brave Search API. Bez klucza wybierz OSM lub podaj strony firm.')
            bbox = [float(x.strip()) for x in config.get('bbox', '49,14,55,24').split(',')]
            if len(bbox) != 4 or not (-90 <= bbox[0] < bbox[2] <= 90 and -180 <= bbox[1] < bbox[3] <= 180):
                raise ValueError('Obszar: południe,zachód,północ,wschód')
            max_firms = int(config.get('max_firms', 50))
            pages = int(config.get('pages', 4))
            if not 1 <= max_firms <= 10000 or not 1 <= pages <= 8:
                raise ValueError('Limit: 1–10000 firm i 1–8 stron na firmę')
            if config.get('osm_server', 'privatecoffee') not in OSM_SERVERS:
                raise ValueError('Nieznany serwer OSM')
            self.config = dict(config, profile=profile, recipients=recs, categories=cats, countries=countries, sources=sources, bbox=bbox, max_firms=max_firms, pages=pages)
            self.records, self.discoveries, self.logs = [], [], []
            self.progress = dict(phase='Wyszukiwanie źródeł', done=0, total=0, queries_done=0, queries_total=0, errors=0)
            self.run_id = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
            (self.folder / self.run_id).mkdir()
            self.stop.clear()
            self.running = True
            self.worker = threading.Thread(target=self.run, daemon=True)
            self.worker.start()

    def shutdown(self):
        """Stop cooperatively and let the worker finish its partial exports."""
        self.stop.set()
        if self.worker and self.worker.is_alive():
            self.worker.join()

    def error(self, label, exc):
        with self.lock:
            self.progress['errors'] += 1
        # Do not expose request headers, query keys or traceback in UI/log.
        detail = str(exc)[:180] if isinstance(exc, ValueError) else 'źródło niedostępne; kontynuuję'
        if isinstance(exc, requests.HTTPError) and exc.response is not None:
            detail = f'HTTP {exc.response.status_code} — źródło niedostępne; kontynuuję'
        self.log(f'{label}: {type(exc).__name__} — {detail}')

    def run(self):
        c = self.config
        fetch = Fetcher(self.stop, self.log)
        queue, seen = [], set()
        with self.lock:
            self.records = queue
        def add(url, name='', source='', lat=None, lon=None, address='', contacts=None, osm_id='', listing_id=''):
            if url and not url.startswith(('https://', 'http://')):
                url = 'https://' + url
            host = domain(url)
            ident = osm_id or listing_id or host
            if not ident or ident in seen or is_domain(host, DENIED) or is_domain(host, DIRECTORIES):
                return
            if len(queue) >= c['max_firms']:
                return
            # Keep separate OSM sites, merge generic domain seeds where possible.
            if not osm_id and not listing_id and any(domain(x['website']) == host for x in queue):
                return
            seen.add(ident)
            with self.lock:
                queue.append(dict(id=str(len(queue)+1), name=name or host, website=url, source=source or url, lat=lat, lon=lon, address=address, geo_source=source if lat is not None else '', contacts=contacts or [], province='', groups=['Do weryfikacji'], role_evidence='', profile=audience_label(c), category='', evidence='', status='Oczekuje', checked_at='', manual_links={}))
        inspected = set()
        def discover(url):
            if not url.startswith(('https://', 'http://')):
                url = 'https://' + url
            if url in inspected or is_domain(domain(url), DENIED):
                return
            if len(queue) >= c['max_firms']:
                return
            inspected.add(url)
            try:
                body, kind, final = fetch.page(url)
                entries = company_list(body, final) if 'html' in kind or not kind else []
                if entries:
                    self.log(f'Lista firm: {final} — znaleziono {len(entries)} wpisów.')
                    for entry in entries:
                        parsed, target = entry['parsed'], entry['target']
                        internal = domain(target) == domain(final) or is_domain(domain(target), DIRECTORIES)
                        website = '' if internal else target
                        if internal:
                            for href, label in parsed['links']:
                                if domain(href) != domain(final) and not is_domain(domain(href), DENIED + DIRECTORIES) and re.search(r'website|homepage|strona|www', label, re.I):
                                    website = href
                                    break
                        for contact in parsed['contacts']:
                            contact['source'] = final
                        add(website, name=entry['name'], source=target, address=parsed['address'], lat=parsed['lat'], lon=parsed['lon'], contacts=parsed['contacts'], listing_id=target if not website else '')
                    self.checkpoint()
                elif not is_domain(domain(final), DIRECTORIES):
                    add(final, source=url)
                else:
                    self.log('Katalog: nie rozpoznano listy firm — ' + final)
            except Cancelled:
                raise
            except Exception as exc:
                self.error('Odczyt strony ' + domain(url), exc)
                if not is_domain(domain(url), DIRECTORIES):
                    add(url, source=url)
        try:
            if c.get('provinces'):
                self.progress['phase'] = 'Przygotowanie lokalnych danych OSM'
                self.local_index = LocalIndex(self.folder.parent)
                self.local_index.ensure(self.stop, self.log)
            self.log('Start. Zbieram wyłącznie opublikowane kontakty; brakujące dane pozostają puste.')
            catalog_keys=[k for k in c['sources'] if k in DIRECT_SOURCES]
            self.progress.update(catalogs_total=len(catalog_keys),catalogs_done=0,catalog_entries=0)
            for key in catalog_keys:
                fetch.check()
                name,url=DIRECT_SOURCES[key]
                lead=dict(title=name,url=url,source=name,category='Katalog bezpośredni',query='',status='Odczyt katalogu',checked_at=now())
                self.discoveries.append(lead)
                imported=0;found=0;filtered=0
                try:
                    for item in collect(key,fetch,self.log):
                        found+=1
                        country=item.get('country','').strip().casefold()
                        province=item.get('province','')
                        outside=(country and country not in ('polska','poland','pl') and not c.get('foreign_catalogs',False)) or (province and c.get('provinces') and province not in c['provinces'])
                        narrow=not matches_selection(item.get('text',''), c)
                        excluded=outside or narrow
                        if outside:
                            lead_status='Poza wybranym obszarem — pominięto import'
                        elif narrow:
                            lead_status='Nie spełnia warunku oraz dla producentów i odbiorców'
                        else:
                            lead_status='Wpis firmy w katalogu'
                        self.discoveries.append(dict(title=item['name'],url=item['source'],source=name,category=country or 'Kraj do sprawdzenia',query='',status=lead_status,checked_at=now()))
                        if excluded:
                            filtered+=1
                            continue
                        before=len(queue)
                        add(item['website'],item['name'],item['source'],item.get('lat'),item.get('lon'),item['address'],item['contacts'],listing_id=item['source']+'|'+item['name'] if not item['website'] else '')
                        if len(queue)>before:
                            row=queue[-1]
                            row.update(catalog=name,country=item.get('country',''),catalog_province=province,province=province,osm_text=item.get('text',''),groups=qualify(item.get('text',''))[0],role_evidence=qualify(item.get('text',''))[1])
                            imported+=1
                            if self.local_index:
                                match=self.local_index.find(row)
                                row['osm_check']='Brak jednoznacznego dopasowania w lokalnym OSM'
                                if match:
                                    row.update(lat=match['lat'],lon=match['lon'],geo_source=match['source'],province=match['province'],geo_precision=match['geo_precision'],osm_check='Dopasowano lokalny OSM')
                        self.progress['catalog_entries']+=1
                        self.checkpoint()
                        if len(queue)>=c['max_firms']:
                            self.log('Limit firm — import katalogu częściowy.');break
                    lead['status']=f'Odczytano {found}; dodano {imported}; poza obszarem {filtered}.'
                except Cancelled:
                    raise
                except Exception as exc:
                    lead['status']='Nie udało się ukończyć odczytu — sprawdź log'
                    self.error(name,exc)
                self.progress['catalogs_done']+=1
                self.log(name+': '+lead['status'])
                self.checkpoint()
            for url in c.get('seeds', '').splitlines():
                if url.strip():
                    discover(url.strip())
            if 'osm' in c['sources'] and self.local_index is not None:
                local = [r for r in self.local_index.data['companies'] if r['province'] in c['provinces']]
                # Put useful descriptions first, retaining unclassified plants as candidates.
                local.sort(key=lambda r: (not bool(classify(r['text'], c['categories'])[0]), qualify(r['text'])[0] == ['Do weryfikacji'], r['province'], r['name']))
                self.log(f'Lokalny OSM: {len(local)} obiektów w wybranych województwach; limit importu {c["max_firms"]}.')
                # Round-robin provinces prevents the first province consuming the entire limit.
                buckets={p:[r for r in local if r['province']==p] for p in c['provinces']}
                import itertools
                for batch in itertools.zip_longest(*buckets.values()):
                    for item in batch:
                        fetch.check()
                        if item is None or not matches_selection(item['text'], c): continue
                        before=len(queue)
                        ct=dict(person='',role='',email=item['email'],phone=item['phone'],source=item['source'],status='Kontakt obiektu OSM — do sprawdzenia')
                        add(item['website'],item['name'],item['source'],item['lat'],item['lon'],item['address'],[ct] if item['email'] or item['phone'] else [],item['source'])
                        if len(queue)>before:
                            queue[-1].update(province=item['province'],geo_precision=item['geo_precision'],groups=qualify(item['text'])[0],role_evidence=qualify(item['text'])[1],osm_text=item['text'])
                    if len(queue)>=c['max_firms']: break
                self.checkpoint()
            elif 'osm' in c['sources']:
                fetch.check()
                self.log('OSM: wyszukiwanie obiektów przemysłowych w wybranym prostokącie. Kategorie sprawdzę na stronach firm.')
                box = ','.join(map(str, c['bbox']))
                query = f'[out:json][timeout:35];(nwr["man_made"="works"]["name"]({box});nwr["industrial"]["name"]({box});nwr["craft"="metal_construction"]["name"]({box}););out center tags {c["max_firms"]};'
                try:
                    endpoint = OSM_SERVERS[c.get('osm_server', 'privatecoffee')]
                    self.log('Serwer OSM: ' + domain(endpoint))
                    response = requests.get(endpoint, params={'data': query}, headers={'User-Agent': UA, 'Accept': 'application/json'}, timeout=(8, 45))
                    response.raise_for_status()
                    payload = response.json()
                    if payload.get('remark'):
                        self.log('OSM: serwer zgłosił niepełny wynik: ' + payload['remark'][:180])
                    osm_limit = max(1, (c['max_firms'] - len(queue)) // 2) if any(s in SOURCES for s in c['sources']) else c['max_firms']
                    osm_added = 0
                    for item in payload.get('elements', []):
                        if osm_added >= osm_limit:
                            break
                        t = item.get('tags', {})
                        centre = item.get('center', item)
                        lat, lon = coords(centre.get('lat'), centre.get('lon'))
                        source = f'https://www.openstreetmap.org/{item["type"]}/{item["id"]}'
                        contact = dict(person='', role='', email=t.get('contact:email', t.get('email', '')), phone=t.get('contact:phone', t.get('phone', '')), source=source, status='Kontakt OSM — do weryfikacji')
                        add(t.get('website', t.get('contact:website', '')), t.get('name', ''), source, lat, lon, ', '.join(t.get(k, '') for k in ['addr:street', 'addr:housenumber', 'addr:postcode', 'addr:city'] if t.get(k)), [contact] if contact['email'] or contact['phone'] else [], source)
                        osm_added += 1
                except Exception as exc:
                    self.error('OSM', exc)
            provinces = c.get('provinces', [''])
            web = [s for s in c['sources'] if s in SOURCES]
            producer_keys = c['categories']
            recipient_keys = c.get('recipients', [])
            jobs = []
            if producer_keys or recipient_keys:
                for source in web:
                    for country in c['countries']:
                        for province in provinces:
                            producer_terms = [(search_term(cat, country, c.get('profile', 'forgings')), CATEGORIES[cat][0]) for cat in producer_keys] or [('', '')]
                            recipient_terms = [(recipient_term(rec, country), RECIPIENTS[rec][0]) for rec in recipient_keys] or [('', '')]
                            for producer_term, producer_label in producer_terms:
                                for recipient_term_text, recipient_label in recipient_terms:
                                    term = ' '.join(part for part in (producer_term, recipient_term_text) if part)
                                    label = ' oraz '.join(part for part in (producer_label, recipient_label) if part)
                                    jobs.append((source, country, province, term, label))
            with self.lock:
                self.progress['queries_total'] = len(jobs)
            search_failures = 0
            for source, country, province, term, label in jobs:
                fetch.check()
                if len(queue) >= c['max_firms'] and not c.get('all_queries', True):
                    self.progress['search_stop_reason'] = 'Osiągnięto limit firm'
                    self.log('Osiągnięto limit kandydatów — przechodzę do analizy zamiast wykonywać pozostałe zapytania.')
                    break
                query = ' '.join(part for part in (term, COUNTRIES[country][0], province, SOURCES[source][1]) if part)
                self.log('Szukam: ' + query)
                try:
                    if self.stop.wait(1.1):
                        raise Cancelled()
                    hits = search_web(query, country, c)
                    search_failures = 0
                    self.log(f'Wyszukiwarka zwróciła {len(hits)} wyników.')
                    if not hits:
                        self.progress['queries_empty'] = self.progress.get('queries_empty', 0) + 1
                        self.log('Brak wyników zwróconych dla tego zapytania — przechodzę do kolejnego; to nie jest potwierdzenie braku firm.')
                    for hit in hits:
                        url = hit.get('url', '')
                        with self.lock:
                            if not any(d['url'] == url for d in self.discoveries):
                                self.discoveries.append(dict(title=hit.get('title', ''), url=url, source=SOURCES[source][0], category=label, query=query, status='Trop — niepotwierdzona firma / potrzeba', checked_at=now()))
                        discover(url)
                except Cancelled:
                    raise
                except Exception as exc:
                    self.error('Wyszukiwarka', exc)
                    search_failures += 1
                    self.progress['queries_failed'] = self.progress.get('queries_failed', 0) + 1
                    response = getattr(exc, 'response', None)
                    if isinstance(exc, SearchBlocked) or (response is not None and response.status_code in (401, 403, 429)):
                        self.progress['search_stop_reason'] = 'Dostawca odmówił dostępu lub ograniczył zapytania'
                        self.progress['queries_done'] += 1
                        self.log(self.progress['search_stop_reason'] + ' — zapisuję zebrane wyniki.')
                        break
                with self.lock:
                    self.progress['queries_done'] += 1
                self.checkpoint()
                if search_failures >= 5:
                    self.progress['search_stop_reason'] = 'Pięć kolejnych awarii wyszukiwarki — przerwano plan'
                    self.log(self.progress['search_stop_reason'])
                    break
                if search_failures:
                    self.log('Przerwa 5 sekund po błędzie; następnie kolejne zapytanie.')
                    if self.stop.wait(5):
                        raise Cancelled()
            with self.lock:
                self.progress['queries_skipped'] = self.progress['queries_total'] - self.progress['queries_done']
                self.records = queue
                self.progress.update(phase='Zebrano firmy — analiza przyciskiem nad mapą', done=0, total=0)
            self.log(f'Znaleziono {len(queue)} kandydatów. Analiza stron czeka na przycisk „Analizuj firmy”.')
            if c.get('geocode', False) or self.local_index is not None:
                self.locate_records()
            with self.lock:
                self.progress['phase'] = 'Zakończono zbieranie — analiza przyciskiem nad mapą' if queue else ('Nie udało się wyszukać firm — błąd źródła; sprawdź log' if self.progress['errors'] else 'Brak firm dla tych ustawień — sprawdź tropy i zmień zapytania')
            if 'linkedin' in c['sources'] or 'maps' in c['sources']:
                self.log('LinkedIn / Google Maps: linki wyszukiwania ręcznego są przy firmach; serwisy nie były scrapowane.')
        except (Cancelled, InterruptedError):
            with self.lock:
                self.progress['phase'] = 'Zatrzymano — wyniki częściowe'
                for record in self.records:
                    if record['status'] in ('Analiza', 'Oczekuje'):
                        record['status'] = 'Zatrzymano — analiza nieukończona'
            self.log('Zatrzymano na żądanie użytkownika.')
        except Exception as exc:
            with self.lock:
                self.progress['phase'] = 'Błąd — wyniki częściowe'
            self.error('Poszukiwania', exc)
        finally:
            with self.lock:
                if not self.records:
                    self.records = queue
            self.log(f'Zapisuję {len(self.records)} firm i {len(self.discoveries)} tropów.')
            try:
                export_xlsx(self.snapshot(), self.folder / self.run_id / 'wyniki.xlsx')
                payload = json.dumps(self.snapshot(), ensure_ascii=False).replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')
                template = (Path(__file__).parent / 'static' / 'map.html').read_text(encoding='utf-8')
                (self.folder / self.run_id / 'mapa.html').write_text(template.replace('/*SNAPSHOT*/null', payload), encoding='utf-8')
                self.log('Gotowe: wyniki.xlsx, mapa.html i log sesji.')
            except Exception as exc:
                self.error('Eksport plików', exc)
                with self.lock:
                    self.progress['phase'] += ' — błąd eksportu; sprawdź log'
            with self.lock:
                self.running = False
                self.checkpoint()

    def locate_records(self):
        from geolocation import Locator
        locator = Locator(self.folder, self.stop)
        fetch = Fetcher(self.stop, self.log)
        if self.local_index is None and (self.folder.parent / 'poland-latest.osm.pbf').exists():
            self.local_index = LocalIndex(self.folder.parent)
            self.local_index.ensure(self.stop, self.log)
        with self.lock:
            self.progress.update(phase='Lokalizowanie firm na mapie', geo_done=0, geo_total=len(self.records))
        for record in self.records:
            if self.stop.is_set():
                raise Cancelled()
            if self.local_index is not None:
                record['osm_check'] = 'Sprawdzono lokalny OSM — brak jednoznacznego dopasowania'
                if record.get('lat') is None:
                    match = self.local_index.find(record)
                    if match:
                        record['osm_check'] = 'Dopasowano lokalny OSM'
                        record.update(lat=match['lat'],lon=match['lon'],geo_source=match['source'],province=match['province'],geo_precision=match['geo_precision'])
                        record['address'] = record.get('address') or match['address']
                if record.get('lat') is not None:
                    record['province'] = self.local_index.province(record['lat'],record['lon'])
                    record['osm_check'] = 'Sprawdzono punkt względem granic lokalnego OSM; źródło punktu przy firmie'
            if record.get('lat') is None or record.get('geo_precision', '').startswith('Przybliżenie'):
                if not record.get('catalog') and is_domain(domain(record.get('source', '')), DIRECTORIES):
                    try:
                        body, kind, final = fetch.page(record['source'])
                        parsed = extract(body, final) if 'html' in kind else {}
                        with self.lock:
                            if parsed.get('address'):
                                record['address'] = parsed['address']
                            if parsed.get('lat') is not None:
                                record.update(lat=parsed['lat'], lon=parsed['lon'], geo_source=final, geo_precision='Współrzędne z wpisu firmy — do weryfikacji')
                    except Cancelled:
                        raise
                    except Exception as exc:
                        self.error('Adres z wpisu katalogowego', exc)
            if record.get('lat') is None:
                self.log('Lokalizuję: ' + record['name'])
                result = locator.locate(record.get('address', '')) if self.local_index is None else dict(geo_precision='Brak jednoznacznego dopasowania w lokalnym OSM — do uzupełnienia')
                with self.lock:
                    record.update(result)
            else:
                with self.lock:
                    record.setdefault('geo_precision', 'Współrzędne ze źródła')
            with self.lock:
                if self.local_index is not None:
                    record['province'] = self.local_index.province(record.get('lat'),record.get('lon'))
                    record['location_scope'] = 'Wybrane województwo' if record['province'] in self.config.get('provinces',PROVINCES) else ('Poza wybranym obszarem' if record.get('lat') is not None else 'Lokalizacja niepotwierdzona')
                self.progress['geo_done'] += 1
            if self.progress['geo_done'] % 20 == 0 or self.progress['geo_done'] == len(self.records):
                self.checkpoint()

    def start_analysis(self, config=None):
        config = config or {}
        with self.lock:
            if self.running:
                raise ValueError('Poczekaj na zakończenie bieżącego zadania')
            if not self.records:
                files = sorted(self.folder.glob('*/wyniki.json'))
                if not files:
                    raise ValueError('Najpierw wyszukaj firmy')
                saved = json.loads(files[-1].read_text(encoding='utf-8'))
                self.records = saved.get('records', [])
                self.discoveries = saved.get('discoveries', [])
                self.progress = saved.get('progress', self.progress)
                self.run_id = files[-1].parent.name
            if not self.records:
                raise ValueError('Najpierw wyszukaj firmy')
            if 'pages' in config:
                pages = int(config['pages'])
                if not 1 <= pages <= 8:
                    raise ValueError('Limit: 1–8 stron na firmę')
                self.config['pages'] = pages
            elif 'pages' not in self.config:
                self.config['pages'] = 4
            if 'enrich_web' in config:
                self.config['enrich_web'] = bool(config['enrich_web'])
            if isinstance(config.get('categories'), list):
                self.config['categories'] = [k for k in config['categories'] if k in CATEGORIES]
            self.logs = []
            self.stop.clear()
            self.running = True
            self.worker = threading.Thread(target=self.analysis_worker, daemon=True)
            self.worker.start()

    def analysis_worker(self):
        fetch = Fetcher(self.stop, self.log)
        try:
            records = list(self.records)
            with self.lock:
                self.progress.update(phase='Analiza firm', done=0, total=len(records))
            self.log(f'Analiza {len(records)} firm na żądanie.')
            for record in records:
                fetch.check()
                self.log('Analizuję: ' + record['name'])
                with self.lock:
                    record['status'] = 'Analiza'
                try:
                    if self.config.get('enrich_web', True):
                        self.enrich(record, fetch)
                    else:
                        record['category'], record['evidence'] = classify(record.get('osm_text', record['name']), self.config.get('categories', []))
                        record['status'] = 'Dane lokalne OSM — do kwalifikacji'
                except Cancelled:
                    raise
                except Exception as exc:
                    with self.lock:
                        record['status'] = 'Błąd pobrania — do sprawdzenia'
                    self.error(record['name'], exc)
                with self.lock:
                    record['checked_at'] = now()
                    self.progress['done'] += 1
                self.checkpoint()
            with self.lock:
                self.progress['phase'] = 'Zakończono analizę firm'
            self.log('Analiza firm zakończona.')
        except Cancelled:
            with self.lock:
                self.progress['phase'] = 'Zatrzymano analizę — wyniki częściowe'
                for record in self.records:
                    if record['status'] == 'Analiza':
                        record['status'] = 'Zatrzymano — analiza nieukończona'
            self.log('Zatrzymano analizę na żądanie użytkownika.')
        except Exception as exc:
            with self.lock:
                self.progress['phase'] = 'Błąd analizy — wyniki częściowe'
            self.error('Analiza firm', exc)
        finally:
            self.checkpoint()
            try:
                if self.run_id:
                    export_xlsx(self.snapshot(), self.folder / self.run_id / 'wyniki.xlsx')
            except Exception as exc:
                self.error('Eksport po analizie', exc)
            with self.lock:
                self.running = False
                self.checkpoint()

    def start_locations(self):
        with self.lock:
            if self.running:
                raise ValueError('Poczekaj na zakończenie bieżącego zadania')
            if not self.records:
                files = sorted(self.folder.glob('*/wyniki.json'))
                if not files:
                    raise ValueError('Najpierw wyszukaj firmy')
                saved = json.loads(files[-1].read_text(encoding='utf-8'))
                self.records = saved['records']
                self.discoveries = saved.get('discoveries', [])
                self.progress = saved['progress']
            self.run_id = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
            (self.folder / self.run_id).mkdir()
            self.logs = []
            self.stop.clear()
            self.running = True
            self.worker = threading.Thread(target=self.locations_worker, daemon=True)
            self.worker.start()

    def locations_worker(self):
        try:
            self.locate_records()
            self.progress['phase'] = 'Zakończono lokalizowanie — sprawdź dokładność punktów'
        except Cancelled:
            self.progress['phase'] = 'Zatrzymano lokalizowanie — wyniki częściowe'
        except Exception as exc:
            self.progress['phase'] = 'Błąd lokalizowania — wyniki częściowe'
            self.error('Lokalizowanie', exc)
        finally:
            try:
                self.checkpoint()
                export_xlsx(self.snapshot(), self.folder / self.run_id / 'wyniki.xlsx')
                payload = json.dumps(self.snapshot(), ensure_ascii=False).replace('<', '\\u003c')
                template = (Path(__file__).parent / 'static' / 'map.html').read_text(encoding='utf-8')
                (self.folder / self.run_id / 'mapa.html').write_text(template.replace('/*SNAPSHOT*/null', payload), encoding='utf-8')
            except Exception as exc:
                self.error('Eksport', exc)
            with self.lock:
                self.running = False
                self.checkpoint()

    def enrich(self, record, fetch):
        if not record['website']:
            with self.lock:
                record['status'] = 'Wpis firmy z katalogu — strona WWW do sprawdzenia'
            return
        pending, visited, texts = [record['website']], set(), []
        for _ in range(self.config['pages']):
            if not pending:
                break
            url = pending.pop(0)
            if url in visited:
                continue
            visited.add(url)
            try:
                body, kind, final = fetch.page(url)
                if 'pdf' in kind or url.lower().endswith('.pdf'):
                    from pypdf import PdfReader
                    reader = PdfReader(io.BytesIO(body))
                    text = '\n'.join((p.extract_text() or '')[:20000] for p in reader.pages[:15])
                    parsed = dict(text=text, name='', address='', lat=None, lon=None, contacts=[dict(person='', role='', email=e, phone='', source=final, status='Kontakt PDF — bez przypisania do osoby') for e in set(EMAIL.findall(text))], links=[])
                elif 'html' in kind or not kind:
                    parsed = extract(body, final)
                else:
                    continue
            except Cancelled:
                raise
            except Exception as exc:
                self.error('Podstrona ' + domain(url), exc)
                continue
            texts.append((parsed['text'], final))
            with self.lock:
                if len(texts) == 1 and not record['source'].startswith('https://www.openstreetmap.org/'):
                    record['name'] = parsed['name'] or record['name']
                if not record['address']:
                    record['address'] = parsed['address']
                if record['lat'] is None and parsed['lat'] is not None:
                    record['lat'], record['lon'], record['geo_source'] = parsed['lat'], parsed['lon'], final
                for contact in parsed['contacts']:
                    key = tuple(contact.get(k, '').lower() for k in ['person', 'email', 'phone'])
                    if not any(tuple(x.get(k, '').lower() for k in ['person', 'email', 'phone']) == key for x in record['contacts']):
                        record['contacts'].append(contact)
            for href, label in parsed['links']:
                if domain(href) == domain(final) and href not in visited and href not in pending and re.search(r'contact|kontakt|team|about|unternehmen|impressum|purchas|supplier|produkt|product|oferta|zakup', href + ' ' + label, re.I):
                    pending.append(href)
            pending.sort(key=lambda x: 0 if re.search(r'contact|kontakt|team|purchas|supplier', x, re.I) else 1)
            if len(texts) == 1 and domain(final):
                root = '{0.scheme}://{0.netloc}/'.format(urlparse(final))
                if root not in visited and root not in pending:
                    pending.append(root)
        categories, evidence = [], []
        for text, url in texts:
            cat, proof = classify(text, self.config['categories'])
            if cat:
                categories.append(cat)
                evidence.append(proof + ' [' + url + ']')
        with self.lock:
            record['groups'], record['role_evidence'] = qualify(record['name'] + ' ' + record.get('osm_text', '') + ' ' + ' '.join(t for t,u in texts))
            record['category'] = '; '.join(dict.fromkeys(categories))
            record['evidence'] = '\n'.join(evidence)[:3000]
            record['status'] = 'Dopasowanie słów — wymaga kwalifikacji' if categories else ('Brak potwierdzonego dopasowania' if texts else 'Nie udało się odczytać strony')

def safe_cell(value):
    if isinstance(value, list):
        value = '; '.join(map(str,value))
    if isinstance(value, str):
        value = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', value)[:32000]
        # Force literal content, including externally supplied formula-like names.
        if value.lstrip().startswith(('=', '+', '-', '@')):
            value = "'" + value
    return value

def export_xlsx(snapshot, path):
    book = Workbook()
    firms = book.active
    firms.title = 'Firmy'
    firms.append(['ID', 'Firma / tytuł strony', 'WWW', 'Adres', 'Szerokość', 'Długość', 'Źródło współrzędnych', 'Kategorie', 'Dowód dopasowania', 'Status', 'Źródło firmy', 'Sprawdzono UTC', 'Profil poszukiwań', 'Dokładność lokalizacji', 'Dopasowany adres', 'Województwo', 'Grupy', 'Uzasadnienie roli', 'Zakres lokalizacji', 'Katalog', 'Kraj z katalogu', 'Sprawdzenie OSM'])
    contacts = book.create_sheet('Kontakty')
    contacts.append(['ID firmy', 'Firma', 'Osoba / kandydat', 'Rola / kontekst', 'Email', 'Telefon', 'Źródło', 'Pewność przypisania'])
    leads = book.create_sheet('Tropy')
    leads.append(['Tytuł', 'URL', 'Źródło', 'Kategoria', 'Zapytanie', 'Status', 'Sprawdzono UTC'])
    for r in snapshot['records']:
        firms.append([safe_cell(r.get(k, '')) for k in ['id', 'name', 'website', 'address', 'lat', 'lon', 'geo_source', 'category', 'evidence', 'status', 'source', 'checked_at', 'profile', 'geo_precision', 'geo_label', 'province', 'groups', 'role_evidence', 'location_scope', 'catalog', 'country', 'osm_check']])
        for c in r['contacts']:
            contacts.append([safe_cell(v) for v in [r['id'], r['name']] + [c.get(k, '') for k in ['person', 'role', 'email', 'phone', 'source', 'status']]])
    for d in snapshot['discoveries']:
        leads.append([safe_cell(d.get(k, '')) for k in ['title', 'url', 'source', 'category', 'query', 'status', 'checked_at']])
    for group in GROUPS:
        sheet = book.create_sheet(group)
        sheet.append([c.value for c in firms[1]])
        for i,r in enumerate(snapshot['records'],2):
            if group in r.get('groups',['Do weryfikacji']):
                sheet.append([c.value for c in firms[i]])
    info = book.create_sheet('Informacje')
    for row in [['PROMOT — wyszukiwanie kontrahentów', ''], ['Stan', snapshot['progress']['phase']], ['Utworzono UTC', now()], ['Kontakty', 'Wyłącznie znalezione publicznie. Brak danych = puste pole. Kandydaci na osoby wymagają weryfikacji.'], ['Dopasowanie', 'Ocena słów kluczowych nie potwierdza zapotrzebowania ani kwalifikacji technicznej.'], ['Lokalizacja', 'OSM lub dane strukturalne strony. Photon: geokodowanie adresu, dokładność podana przy firmie. Przybliżenie miejscowości nie oznacza siedziby.'], ['OSM', '© OpenStreetMap contributors — https://www.openstreetmap.org/copyright — ODbL; sprawdź warunki przed udostępnieniem bazy.'], ['LinkedIn / Google Maps', 'Ręczne wyszukiwanie; brak automatycznego pobierania kontaktów.']]:
        info.append(row)
    for sheet in book:
        sheet.freeze_panes = 'A2'
        sheet.auto_filter.ref = sheet.dimensions
        sheet.row_dimensions[1].height = 30
        for cell in sheet[1]:
            cell.fill = PatternFill('solid', fgColor='153C47')
            cell.font = Font(color='FFFFFF', bold=True)
        for row in sheet.iter_rows(min_row=2):
            for cell in row:
                cell.alignment = Alignment(vertical='top', wrap_text=True)
                if isinstance(cell.value, str) and cell.value.startswith(('https://', 'http://')) and '\n' not in cell.value and ' ' not in cell.value:
                    cell.hyperlink = cell.value
                    cell.font = Font(color='176B9A', underline='single')
        for col in sheet.columns:
            width = min(65, max(15, max(len(str(x.value or '')) for x in col[:40]) + 2))
            sheet.column_dimensions[get_column_letter(col[0].column)].width = width
    for row in firms.iter_rows(min_row=2):
        row[4].number_format = row[5].number_format = '0.000000'
    temporary = Path(path).with_suffix('.tmp.xlsx')
    book.save(temporary)
    temporary.replace(path)
