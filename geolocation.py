"""Conservative address lookup using Photon, with persistent caching and throttling."""
import json
import os
import re
import time
import unicodedata
from pathlib import Path
import requests


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
