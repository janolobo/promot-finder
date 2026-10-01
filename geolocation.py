"""Conservative address lookup using Photon, with persistent caching and throttling."""
import json
import os
import re
import time
import unicodedata
from pathlib import Path
import requests


def city_query(address):
    """Turn a city-and-province note into a place lookup. A street address stays unchanged."""
    text = ' '.join(str(address or '').split())
    parts = re.findall(r'([^,;]+?)\s*\(woj\.\s*([^)]+)\)', text, re.I)
    if not parts:
        return ''
    chosen = next((part for part in parts if re.search(r'zakład', part[0], re.I)), parts[0])
    name = chosen[0].split('–')[-1].split('—')[-1]
    name = re.sub(r'\s+k[./]\s*\S.*$', '', name)
    name = re.sub(r'(?i)^zakład produkcyjny\s*', '', name).strip(' -–')
    province = chosen[1].strip()
    if not name or not province:
        return ''
    return f'{name}, {province}, Polska'


def city_note(address):
    text = str(address or '')
    return '(woj.' in text.lower() and not re.search(r'\d', text)


_STREET = re.compile(
    r'(?:(?<!\w)(?i:ul\.|ulica|al\.|aleja|pl\.|plac|os\.|osiedle)\s+)'
    r'(?i:(?:\d{1,3}\s+)?(?:[a-ząćęłńóśźż][\w.\-]*\s+){1,5}\d{1,4}[a-z]?(?:/\d{1,4})?)'
    r'(?:\s*,?\s*\d{2}-\d{3}(?:\s+[A-ZĄĆĘŁŃÓŚŹŻ][\wĄĆĘŁŃÓŚŹŻąćęłńóśźż\-]+){1,3})?')
_BARE = re.compile(
    r'(?<![\w.])([A-ZĄĆĘŁŃÓŚŹŻ][\wĄĆĘŁŃÓŚŹŻąćęłńóśźż\-]{2,}(?:\s+[A-ZĄĆĘŁŃÓŚŹŻ][\wĄĆĘŁŃÓŚŹŻąćęłńóśźż\-]+){0,3})'
    r'\s+(\d{1,4}[A-Za-z]?(?:/\d{1,4})?)\s*,?\s+(\d{2}-\d{3})\s+'
    r'([A-ZĄĆĘŁŃÓŚŹŻ][\wĄĆĘŁŃÓŚŹŻąćęłńóśźż\-]+(?:\s+[A-ZĄĆĘŁŃÓŚŹŻ][\wĄĆĘŁŃÓŚŹŻąćęłńóśźż\-]+){0,2})')
_POSTAL_CITY = re.compile(
    r'\b(\d{2}-\d{3})\s+([A-ZĄĆĘŁŃÓŚŹŻ][\wĄĆĘŁŃÓŚŹŻąćęłńóśźż\-]+(?:\s+[A-ZĄĆĘŁŃÓŚŹŻ][\wĄĆĘŁŃÓŚŹŻąćęłńóśźż\-]+){0,2})')
_AFTER_CITY = re.compile(r'\s*,\s*([A-ZĄĆĘŁŃÓŚŹŻ][\wĄĆĘŁŃÓŚŹŻąćęłńóśźż\-]+(?:\s+[A-ZĄĆĘŁŃÓŚŹŻ][\wĄĆĘŁŃÓŚŹŻąćęłńóśźż\-]+){0,2})')
_CITY_STOP = {'polska', 'poland', 'nip', 'regon', 'krs', 'iso', 'nr', 'tel', 'fax', 'email', 'telefon', 'kontakt', 'adres', 'siedziba', 'www', 'http', 'https', 'com', 'pl', 'sp', 'zoo', 'sa', 'biuro', 'centrala'}
_NEAR = re.compile(r'adres|siedzib|centrala|biuro|kontakt|lokalizacj', re.I)


def _city_words(value):
    words_ = []
    for word in re.sub(r'\s+', ' ', value or '').strip(' ,.;').split():
        if word[:1].islower() or word.casefold().strip('.,;') in _CITY_STOP:
            break
        words_.append(word.strip('.,;'))
        if len(words_) == 3:
            break
    return ' '.join(words_)


def _parts(raw):
    text = re.sub(r'\s+', ' ', raw).strip(' ,.;')
    postal = re.search(r'\b(\d{2}-\d{3})\b', text)
    city = ''
    if postal:
        city = _city_words(text[postal.end():])
        text = text[:postal.start()].strip(' ,;')
    else:
        tail = re.search(r',\s*([A-ZĄĆĘŁŃÓŚŹŻ][\wĄĆĘŁŃÓŚŹŻąćęłńóśźż\-]+(?:\s+[A-ZĄĆĘŁŃÓŚŹŻ][\wĄĆĘŁŃÓŚŹŻąćęłńóśźż\-]+){0,2})\s*$', text)
        if tail:
            city = _city_words(tail.group(1))
            text = text[:tail.start()].strip(' ,;')
    street = text if re.search(r'\d', text) else ''
    return street, postal.group(1) if postal else '', city


def _compose(street, postal, city):
    tail = ' '.join(part for part in (postal, city) if part)
    return ', '.join(part for part in (street, tail) if part)


def address_rank(value):
    text = ' '.join(str(value or '').split())
    if not text:
        return 0
    postal = bool(re.search(r'\b\d{2}-\d{3}\b', text))
    house = bool(re.search(r'\d{1,4}[A-Za-z]?(?:/\d{1,4})?', re.sub(r'\d{2}-\d{3}', '', text)))
    if house and postal:
        return 4
    if house and not city_note(text):
        return 3
    if postal:
        return 2
    return 1


def find_address(text):
    """Street, postal code and city from page text. A repeated contact address wins over a passing mention."""
    body = re.sub(r'\s+', ' ', str(text or ''))
    found = []

    def add(raw, start, score):
        street, postal, city = _parts(raw)
        if not street and not (postal and city):
            return
        window = body[max(0, start - 90):start + 20]
        if _NEAR.search(window):
            score += 5
        found.append(dict(street=street, postal=postal, city=city, score=score, key=(street.casefold(), postal, city.casefold())))

    for match in _STREET.finditer(body):
        raw = match.group(0)
        street, postal, city = _parts(raw)
        if street and not postal and not city:
            tail = _POSTAL_CITY.search(body[match.end():match.end() + 48])
            named = _AFTER_CITY.match(body[match.end():match.end() + 40])
            if tail and _city_words(tail.group(2)):
                raw = f'{street}, {tail.group(1)} {_city_words(tail.group(2))}'
            elif named and _city_words(named.group(1)):
                raw = f'{street}, {_city_words(named.group(1))}'
        add(raw, match.start(), 5)
    for match in _BARE.finditer(body):
        street = f'{match.group(1)} {match.group(2)}'
        if match.group(1).casefold() in _CITY_STOP:
            continue
        add(f'{street}, {match.group(3)} {_city_words(match.group(4))}', match.start(), 4)
    for match in _POSTAL_CITY.finditer(body):
        city = _city_words(match.group(2))
        if city:
            add(f'{match.group(1)} {city}', match.start(), 2)
    for match in _STREET.finditer(body):
        street, postal, city = _parts(match.group(0))
        if street and not postal:
            tail = _POSTAL_CITY.search(body[match.end():match.end() + 48])
            if tail and _city_words(tail.group(2)):
                add(f'{street}, {tail.group(1)} {_city_words(tail.group(2))}', match.start(), 6)
    if not found:
        return ''
    best = {}
    for item in found:
        slot = best.setdefault(item['key'], dict(item, count=0))
        slot['count'] += 1
        slot['score'] = max(slot['score'], item['score'])
    ranked = sorted(best.values(), key=lambda item: (bool(item['street'] and item['postal']), bool(item['street']), item['score'], item['count'], len(item['street'])), reverse=True)
    winner = ranked[0]
    rivals = [item for item in ranked[1:] if item['score'] == winner['score'] and item['postal'] != winner['postal'] and item['key'][0] != winner['key'][0]]
    if rivals and not (winner['street'] and winner['postal']):
        return ''
    if rivals and winner['street'] and winner['postal'] and rivals[0]['street'] and rivals[0]['postal']:
        return ''
    if winner['street'] and not winner['postal']:
        places = {(item['postal'], item['city'].casefold()) for item in found if item['postal'] and item['city']}
        if len(places) == 1:
            postal, city = next(iter(places))
            stored = next(item for item in found if item['postal'] == postal)
            return _compose(winner['street'], postal, stored['city'])
    return _compose(winner['street'], winner['postal'], winner['city'])


def street_address(text):
    return find_address(text)


def locate_query(address):
    """Query for the map. A postal code with only a city is a city approximation."""
    text = ' '.join(str(address or '').split())
    place = city_query(text)
    if place:
        return place, True
    postal = re.search(r'\b\d{2}-\d{3}\s+([A-Za-zĄĆĘŁŃÓŚŹŻąćęłńóśźż][\wĄĆĘŁŃÓŚŹŻąćęłńóśźż\-]+(?:\s+[A-Za-zĄĆĘŁŃÓŚŹŻąćęłńóśźż][\wĄĆĘŁŃÓŚŹŻąćęłńóśźż\-]+){0,2})', text)
    house = bool(re.search(r'\d', re.sub(r'\d{2}-\d{3}', '', text)))
    if postal and not house:
        city = _city_words(postal.group(1))
        return (f'{city}, Polska', True) if city else ('', False)
    if text and not re.search(r'polska|poland', text, re.I):
        return text + ', Polska', False
    return text, False


def words(value):
    value = unicodedata.normalize('NFKD', value.lower().replace('ł', 'l'))
    return set(re.findall(r'[a-z0-9]+', ''.join(c for c in value if not unicodedata.combining(c))))


def choose(features, query):
    """Do not silently pick an ambiguous city/address or an unrelated fuzzy result."""
    candidates = []
    wanted = words(query) - {'polska', 'poland', 'deutschland', 'germany', 'czechia', 'cesko'}
    for f in features:
        p = f.get('properties', {})
        kind = p.get('type', '')
        if kind in ('state', 'country', 'county', 'other'):
            continue
        if kind == 'house' and not p.get('housenumber') and not (words(p.get('name', '')) <= wanted):
            continue
        xy = f.get('geometry', {}).get('coordinates', [])
        if len(xy) != 2 or not all(isinstance(x, (int,float)) for x in xy):
            continue
        lon, lat = xy
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            continue
        label = ', '.join(str(p[k]) for k in ['name','street','housenumber','postcode','city','district','state','country'] if p.get(k))
        matched = words(label)
        if not wanted or len(wanted & matched) / len(wanted) < .75:
            continue
        country = p.get('countrycode', '').upper()
        expected = next((code for token,code in [('polska','PL'),('poland','PL'),('deutschland','DE'),('germany','DE'),('cesko','CZ')] if token in words(query)), '')
        if expected and country != expected:
            continue
        precision = 'Adres — wymaga weryfikacji' if p.get('housenumber') else 'Przybliżenie: ulica / miejscowość, nie siedziba'
        candidates.append(dict(lat=lat,lon=lon,geo_precision=precision,geo_label=label,geo_source='https://www.openstreetmap.org/' + {'N':'node','W':'way','R':'relation'}.get(p.get('osm_type'), 'node') + '/' + str(p.get('osm_id',''))))
    if len(candidates) != 1:
        return dict(geo_precision='Brak jednoznacznej lokalizacji')
    return candidates[0]


class Locator:
    def __init__(self, folder, stop):
        self.path = Path(folder) / 'geocoding_cache.json'
        try:
            self.cache = json.loads(self.path.read_text())
        except (OSError, ValueError):
            self.cache = {}
        self.stop, self.last, self.disabled = stop, 0, False
        self.endpoint = os.environ.get('PROMOT_PHOTON_URL', 'https://photon.komoot.io/api/')

    def locate(self, address):
        if not address.strip():
            return dict(geo_precision='Brak adresu — nie wyznaczono lokalizacji')
        key = 'v2|' + self.endpoint + '|' + ' '.join(address.lower().split())
        if key in self.cache:
            return self.cache[key]
        if self.disabled:
            return dict(geo_precision='Usługa lokalizacji niedostępna — spróbuj później')
        if self.stop.wait(max(0, 1.2 - (time.monotonic() - self.last))):
            return {}
        self.last = time.monotonic()
        try:
            r = requests.get(self.endpoint, params=dict(q=address,limit=5), headers={'User-Agent':'PromotFinder/1.4 (business-address lookup)'}, timeout=(8,15))
            r.raise_for_status()
            result = choose(r.json().get('features', []), address)
        except (requests.RequestException, ValueError):
            self.disabled = True
            return dict(geo_precision='Usługa lokalizacji niedostępna — spróbuj później')
        self.cache[key] = result
        tmp = self.path.with_suffix('.tmp')
        tmp.write_text(json.dumps(self.cache, ensure_ascii=False), encoding='utf-8')
        tmp.replace(self.path)
        return result
