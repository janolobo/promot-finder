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
_CITY_STOP = {
    'polska', 'poland', 'nip', 'regon', 'krs', 'iso', 'nr', 'tel', 'fax', 'email', 'e-mail', 'mail',
    'telefon', 'kontakt', 'adres', 'siedziba', 'www', 'http', 'https', 'com', 'pl', 'sp', 'zoo', 'sa',
    'biuro', 'centrala', 'centrum', 'oddział', 'oddzial', 'zakład', 'zaklad', 'firma', 'spółka', 'spolka',
    'spółki', 'ltd', 'gmbh', 'inc', 'handel', 'produkcja', 'sekretariat', 'recepcja', 'infolinia',
    'mobile', 'kom', 'vat', 'woj', 'województwo', 'wojewodztwo', 'ul', 'ulica', 'aleja', 'plac', 'osiedle',
    'budynek', 'hala', 'magazyn', 'godziny', 'pon', 'wt', 'śr', 'sr', 'czw', 'pt', 'sob',
}
_PLACE_FIRST = {
    'nowy', 'nowa', 'nowe', 'stary', 'stara', 'stare', 'zielona', 'zielony', 'biała', 'biala', 'biały', 'bialy',
    'czarna', 'czarny', 'czerwona', 'czerwony', 'dolny', 'dolna', 'górny', 'gorny', 'górna', 'gorna',
    'wielki', 'wielka', 'mały', 'maly', 'mała', 'mala', 'jelenia', 'kamienna', 'dąbrowa', 'dabrowa',
    'ruda', 'ostrów', 'ostrow', 'gorzów', 'gorzow', 'tomaszów', 'tomaszow', 'aleksandrów', 'aleksandrow',
    'józefów', 'jozefow', 'busko', 'czechowice', 'konstantynów', 'konstantynow',
}
_PLACE_NEXT = {
    'góra', 'gora', 'sącz', 'sacz', 'targ', 'dwór', 'dwor', 'mazowiecki', 'mazowiecka', 'wielkopolski',
    'śląski', 'slaski', 'śląska', 'slaska', 'górnicza', 'gornicza', 'podlaska', 'podlaski', 'lubelski',
    'pomorski', 'pomorska', 'warmiński', 'warminski', 'królewski', 'krolewski', 'kujawski', 'mazurski',
}
_NEAR = re.compile(r'adres|siedzib|centrala|biuro|kontakt|lokalizacj|anschrift|impressum|standort|adresse', re.I)


def _city_words(value):
    """City name only: no company words, phones or a repeated locality."""
    kept = []
    for word in re.sub(r'\s+', ' ', value or '').strip(' ,.;').split():
        token = word.strip('.,;:()')
        key = token.casefold().strip('.')
        if not token or token[:1].islower() or key in _CITY_STOP or '@' in token or re.search(r'\d', token):
            break
        if any(key == item.casefold() for item in kept):
            break
        if kept and key not in _PLACE_NEXT and kept[0].casefold() not in _PLACE_FIRST:
            break
        kept.append(token)
        if len(kept) == 3:
            break
    return ' '.join(kept)


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
    street = re.sub(r'\s+', ' ', street or '').strip(' ,')
    city = _city_words(city)
    if city and street.casefold().endswith(city.casefold()):
        street = street[:-len(city)].strip(' ,')
    tail = ' '.join(part for part in (postal, city) if part)
    return ', '.join(part for part in (street, tail) if part)


def address_rank(value):
    text = ' '.join(str(value or '').split())
    if not text:
        return 0
    postal = bool(re.search(r'\b\d{2}-\d{3}\b', text) or (re.search(r'\b(?:DE|D)-?\d{5}\b|\b\d{5}\b', text) and not re.search(r'\b\d{2}-\d{3}\b', text)))
    house = bool(re.search(r'\d{1,4}[A-Za-z]?(?:/\d{1,4})?', re.sub(r'\d{2}-\d{3}|\b(?:DE|D)-?\d{5}\b|\b\d{5}\b', '', text)))
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
        return foreign_address(body)
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


def map_address(address, country='', source=''):
    """Keep a Czech or German address in that country when the text has no country name."""
    text = ' '.join(str(address or '').split())
    folded = _fold(country)
    czech = folded in ('cz', 'czechia', 'cesko', 'ceska republika') or 'ares.gov.cz' in str(source or '')
    german = folded in ('de', 'deutschland', 'germany', 'niemcy')
    if czech and not re.search(r'cesk|czech', _fold(text)):
        return f'{text}, Česká republika' if text else text
    if german and not re.search(r'deutsch|germany|niemc', _fold(text)):
        return f'{text}, Deutschland' if text else text
    return text


_COUNTRY_ONLY = re.compile(
    r'^(?:polska|poland|deutschland|germany|niemcy|česko|cesko|czechia|czech republic|'
    r'česká republika|ceska republika)$',
    re.I,
)


def is_country_only(text):
    """True when the text is only a country name, not a city or street."""
    return bool(_COUNTRY_ONLY.match(' '.join(str(text or '').split())))


def _city_from_text(text):
    body = ' '.join(str(text or '').split()).strip(' ,')
    if not body or is_country_only(body):
        return ''
    return _tail_city(body) or _city_words(body) or (body if not _street_digits(body) and ',' not in body else '')


def locate_query(address):
    """Query for the map. A city without a street is a city approximation."""
    text = ' '.join(str(address or '').split())
    if is_country_only(text):
        return '', False
    place = city_query(text)
    if place:
        return place, True
    country, body = _country_clause(text)
    if country:
        if country == 'Poland':
            country = 'Polska'
        if not _street_digits(body):
            city = _city_from_text(body)
            return (f'{city}, {country}', True) if city else ('', False)
        return f'{body}, {country}', False
    postal = re.search(r'\b\d{2}-\d{3}\s+([A-Za-zĄĆĘŁŃÓŚŹŻąćęłńóśźż][\wĄĆĘŁŃÓŚŹŻąćęłńóśźż\-]+(?:\s+[A-Za-zĄĆĘŁŃÓŚŹŻąćęłńóśźż][\wĄĆĘŁŃÓŚŹŻąćęłńóśźż\-]+){0,2})', text)
    house = bool(re.search(r'\d', re.sub(r'\d{2}-\d{3}', '', text)))
    if postal and not house:
        city = _city_words(postal.group(1))
        return (f'{city}, Polska', True) if city else ('', False)
    if re.search(r'\b\d{5}\b', text) and not re.search(r'\b\d{2}-\d{3}\b', text):
        return text, False
    if text and not _street_digits(text):
        city = _city_from_text(text)
        if not city:
            return '', False
        if re.search(r'polska|poland|deutsch|germany|czech|česk|cesko', text, re.I):
            return text, True
        return f'{city}, Polska', True
    if text and not re.search(r'polska|poland', text, re.I):
        return text + ', Polska', False
    return text, False


def pin_query(record):
    """Map query from a street, or from a city / locality when that is all we have."""
    address = map_address(record.get('address') or '', record.get('country') or '', record.get('source') or '')
    query, city_only = locate_query(address)
    if query:
        return query, city_only or not _street_digits(address)
    locality = ' '.join(str(record.get('locality') or '').split()).strip(' ,')
    if locality and not is_country_only(locality):
        placed = map_address(locality, record.get('country') or '', record.get('source') or '')
        query, _city_only = locate_query(placed)
        if query:
            return query, True
    return '', False


def _fold(value):
    value = unicodedata.normalize('NFKD', str(value or '').lower().replace('ł', 'l').replace('ß', 'ss'))
    return ''.join(c for c in value if not unicodedata.combining(c))


_COUNTRY_QUERY = (
    ('ceska republika', 'Czechia'),
    ('cesko', 'Czechia'),
    ('czechia', 'Czechia'),
    ('czech republic', 'Czechia'),
    ('nemecko', 'Germany'),
    ('deutschland', 'Germany'),
    ('germany', 'Germany'),
    ('polska', 'Poland'),
    ('polsko', 'Poland'),
    ('poland', 'Poland'),
    ('slovensko', 'Slovakia'),
    ('slovakia', 'Slovakia'),
    ('rakousko', 'Austria'),
    ('osterreich', 'Austria'),
    ('austria', 'Austria'),
    ('francie', 'France'),
    ('france', 'France'),
    ('italie', 'Italy'),
    ('italy', 'Italy'),
    ('svycarsko', 'Switzerland'),
    ('schweiz', 'Switzerland'),
    ('switzerland', 'Switzerland'),
    ('madarsko', 'Hungary'),
    ('hungary', 'Hungary'),
    ('nizozemsko', 'Netherlands'),
    ('netherlands', 'Netherlands'),
    ('belgie', 'Belgium'),
    ('belgium', 'Belgium'),
    ('spanelsko', 'Spain'),
    ('spain', 'Spain'),
    ('spanien', 'Spain'),
    ('polen', 'Poland'),
    ('niederlande', 'Netherlands'),
    ('frankreich', 'France'),
    ('italien', 'Italy'),
    ('belgien', 'Belgium'),
    ('finnland', 'Finland'),
    ('grossbritannien', 'United Kingdom'),
    ('vereinigtes konigreich', 'United Kingdom'),
    ('schweden', 'Sweden'),
    ('ungarn', 'Hungary'),
    ('norwegen', 'Norway'),
    ('slowakei', 'Slovakia'),
    ('slowakische republik', 'Slovakia'),
    ('tschechien', 'Czechia'),
    ('tschechische republik', 'Czechia'),
    ('danemark', 'Denmark'),
    ('luxemburg', 'Luxembourg'),
    ('irland', 'Ireland'),
    ('united kingdom', 'United Kingdom'),
    ('finland', 'Finland'),
    ('sweden', 'Sweden'),
    ('norway', 'Norway'),
    ('denmark', 'Denmark'),
    ('luxembourg', 'Luxembourg'),
    ('ireland', 'Ireland'),
    ('turkiye', 'Turkey'),
    ('turkey', 'Turkey'),
    ('portugal', 'Portugal'),
    ('lithuania', 'Lithuania'),
    ('latvia', 'Latvia'),
    ('estonia', 'Estonia'),
    ('united states of america', 'United States'),
    ('united states', 'United States'),
    ('usa', 'United States'),
)


_ISO_COUNTRY = {
    'de': 'Germany', 'at': 'Austria', 'ch': 'Switzerland', 'it': 'Italy', 'fr': 'France',
    'es': 'Spain', 'nl': 'Netherlands', 'be': 'Belgium', 'cz': 'Czechia', 'sk': 'Slovakia',
    'hu': 'Hungary', 'pl': 'Poland', 'lt': 'Lithuania', 'lv': 'Latvia', 'ee': 'Estonia',
    'tr': 'Turkey', 'pt': 'Portugal', 'ro': 'Romania', 'se': 'Sweden', 'no': 'Norway',
    'dk': 'Denmark', 'fi': 'Finland', 'ie': 'Ireland', 'gb': 'United Kingdom', 'uk': 'United Kingdom',
    'hr': 'Croatia', 'si': 'Slovenia', 'bg': 'Bulgaria', 'gr': 'Greece', 'ua': 'Ukraine',
}
_CITY_END = (
    r'(?=\s*(?:,?\s*(?:Germany|Deutschland|Austria|Österreich|Italy|Italia|Spain|España|'
    r'Turkey|Türkiye|France|Luxembourg|USA|United States)\b|Telephone\b|Telefon\b|Phone\b|'
    r'Tel\.?\b|Fax\b|E-?mail\b|Llámanos\b|Routenplaner\b|Vertreten\b|Sales\b|$))'
)
_FOREIGN_PATTERNS = (
    # Germany and Austria: Am Frohberg 3, Hermann-Blohm-Str. 3, Südstraße 4.
    re.compile(
        r'\b((?:(?:Am|An|Im|Zum|Zur)\s+[A-ZÄÖÜ][\wÄÖÜäöüß.\-]*(?:\s+[A-ZÄÖÜ][\wÄÖÜäöüß.\-]*){0,3}'
        r'|[A-ZÄÖÜ][\wÄÖÜäöüß.\-]*?(?:strasse|straße|str\.|Str\.|weg|gasse|allee|platz|ring))'
        r'\s+\d{1,4}[A-Za-z]?(?:\s*/\s*[A-Z]\d+)?)\s*,?\s*((?:DE|D|AT|A)-)?(\d{4,5})\s+'
        r'([A-ZÄÖÜ][\wÄÖÜäöüß.\-]*(?:\s+[A-ZÄÖÜ][\wÄÖÜäöüß.\-]*){0,3})' + _CITY_END),
    # English addresses: 62 Dayton Ave. Xenia, Ohio 45385.
    re.compile(
        r'\b(\d{1,5}\s+(?:[A-Z][\w.\-]*\s+){0,5}(?:Street|St\.|Road|Rd\.|Avenue|Ave\.|'
        r'Drive|Dr\.|Lane|Ln\.|Boulevard|Blvd\.|Way))\s+'
        r'([A-Z][\w.\-]*(?:\s+[A-Z][\w.\-]*){0,2}),\s*([A-Z][A-Za-z ]{1,20})\s+(\d{5}(?:-\d{4})?)(?=\s*(?:\(|$))',
        re.I),
    # Number before a Romance-language street: 21 rue de Luxembourg, L-5752 Frisange.
    re.compile(
        r'\b(\d{1,5}\s+(?:rue|avenue|boulevard|via|viale|piazza|corso|calle)\s+'
        r'[\wÀ-ž.\-]+(?:\s+[\wÀ-ž.\-]+){0,5})\s*,?\s*((?:L|F|FR|IT|ES)-)?(\d{4,5})\s+'
        r'([A-ZÀ-Ž][\wÀ-ž.\-]*(?:\s+[A-ZÀ-Ž][\wÀ-ž.\-]*){0,3})' + _CITY_END, re.I),
    # Spanish industrial estates: Polígono Industrial ... C/G Nº 25 31191 Esquíroz.
    re.compile(
        r'\b((?:Pol[ií]gono\s+Industrial|Parque\s+Industrial)\s+[\wÀ-ž.\-]+(?:\s+[\wÀ-ž.\-]+){0,4}'
        r'\s+C/[A-Z]?\s+N[ºo.]?\s*\d{1,4}[A-Za-z]?)\s*,?\s*()(?:ES-)?(\d{5})\s+'
        r'([A-ZÀ-Ž][\wÀ-ž.\-]*(?:\s+[A-ZÀ-Ž][\wÀ-ž.\-]*){0,2}(?:,\s*[A-ZÀ-Ž][\wÀ-ž.\-]+)?)'
        r'(?=\s*(?:Llámanos\b|Tel\.?\b|Phone\b|$))', re.I),
    # Turkish addresses: Evrenköy Cad. No:25 ... and 6106/15. Sk 11A ...
    re.compile(
        r'\b((?:[A-ZÇĞİÖŞÜ][\wÇĞİÖŞÜçğıöşü.\-]+(?:,\s*|\s+)){0,4}'
        r'(?:\d{1,5}/\d{1,5}\.\s*Sk\.?\s*\d{1,4}[A-Za-z]?|'
        r'[A-ZÇĞİÖŞÜ][\wÇĞİÖŞÜçğıöşü.\-]+(?:\s+OSB)?\s+Mah\.?\s+'
        r'[A-ZÇĞİÖŞÜ][\wÇĞİÖŞÜçğıöşü.\-]+\s+Cad\.?\s+No:?\s*\d{1,4}[A-Za-z]?))'
        r'\s*,?\s*()(\d{5})\s+([A-ZÇĞİÖŞÜ][\wÇĞİÖŞÜçğıöşü.\-]*(?:/[A-ZÇĞİÖŞÜ][\wÇĞİÖŞÜçğıöşü.\-]+)?)'
        + _CITY_END, re.I),
    # A typed business park street visible after an English/German address label.
    re.compile(
        r'\b((?:Betriebspark|Business\s+Park|Industrial\s+Park)\s+[\wÀ-ž.\-]+(?:\s+[\wÀ-ž.\-]+){0,3}'
        r'\s+\d{1,4}[A-Za-z]?)\s*,?\s*((?:AT|A|DE|D)-)?(\d{4,5})\s+'
        r'([A-ZÀ-Ž][\wÀ-ž.\-]*(?:\s+[A-ZÀ-Ž][\wÀ-ž.\-]*){0,3})' + _CITY_END, re.I),
)
_ADDRESS_LABEL = re.compile(
    r'\b(?:address|our address|office address|registered office|headquarters|location|adresse|'
    r'standort|indirizzo|sede|direcci[oó]n|adres|iletişim|iletisim|kontakt|contact us)\b',
    re.I,
)


def foreign_address(text):
    """Exact street and postal code found in visible multilingual page text."""
    body = re.sub(r'\s+', ' ', str(text or ''))
    candidates = []
    prefix_country = {
        'DE': 'Germany', 'D': 'Germany', 'AT': 'Austria', 'A': 'Austria',
        'L': 'Luxembourg', 'F': 'France', 'FR': 'France', 'IT': 'Italy', 'ES': 'Spain',
    }
    for index, pattern in enumerate(_FOREIGN_PATTERNS):
        for match in pattern.finditer(body):
            if index == 1:
                street, city, _state, postal = (part.strip() for part in match.groups())
                prefix, country = '', 'United States'
                city = f'{city}, {_state}'
            else:
                street, prefix, postal, city = ((part or '').strip() for part in match.groups())
                country = prefix_country.get(prefix.rstrip('-').upper(), '')
            street = re.sub(
                r'^(?:address|our address|office address|registered office|headquarters|location|adresse|'
                r'standort|indirizzo|sede|direcci[oó]n|adres|iletişim|iletisim|kontakt|contact us)\s*[:\-]?\s*',
                '', street, flags=re.I)
            street = re.sub(r'^.*?\b(?:GmbH|Ltd\.?|S\.?L\.?|S\.?R\.?L\.?|Inc\.?|A\.?S\.?)\b\s*', '', street, flags=re.I)
            city = re.split(
                r'\s+(?:OT|Ortsteil|Telephone|Telefon|Phone|Tel\.?|Fax|E-?mail|Llámanos|Routenplaner|Vertreten|Sales)\b',
                city, maxsplit=1, flags=re.I)[0].strip(' ,')
            city = re.sub(
                r'(?:[/,\s]+)(?:Germany|Deutschland|Austria|Österreich|Italy|Italia|Spain|España|'
                r'Turkey|Türkiye|France|Luxembourg|USA|United States)$',
                '', city, flags=re.I).strip(' ,/')
            window = _fold(body[match.end():match.end() + 80])
            for key, name in _COUNTRY_QUERY:
                if re.search(r'\b' + re.escape(key) + r'\b', window):
                    country = name
                    break
            if not country:
                if index == 0:
                    country = 'Germany'
                elif index == 2 and re.search(r'\brue\b', _fold(street)):
                    country = 'France'
                elif index == 3:
                    country = 'Spain'
                elif index == 4:
                    country = 'Turkey'
            near = body[max(0, match.start() - 90):match.start()]
            score = 5 if _ADDRESS_LABEL.search(near) else 0
            address = f'{street}, {city} {postal}' if index == 1 else f'{street}, {postal} {city}'
            if country:
                address += f', {country}'
            candidates.append((score, address))
    if not candidates:
        return ''
    grouped = {}
    for score, address in candidates:
        slot = grouped.setdefault(address.casefold(), [score, 0, address])
        slot[0] = max(slot[0], score)
        slot[1] += 1
    ranked = sorted(grouped.values(), key=lambda item: (item[0], item[1]), reverse=True)
    if len(ranked) > 1 and ranked[0][:2] == ranked[1][:2]:
        return ''
    return ranked[0][2]


_COUNTRY_CODE = {
    'Poland': 'PL', 'Germany': 'DE', 'Czechia': 'CZ', 'Slovakia': 'SK', 'Austria': 'AT',
    'France': 'FR', 'Italy': 'IT', 'Switzerland': 'CH', 'Hungary': 'HU', 'Netherlands': 'NL',
    'Belgium': 'BE', 'Spain': 'ES', 'Finland': 'FI', 'United Kingdom': 'GB', 'Sweden': 'SE',
    'Norway': 'NO', 'Denmark': 'DK', 'Luxembourg': 'LU', 'Ireland': 'IE',
    'Turkey': 'TR', 'Portugal': 'PT', 'Lithuania': 'LT', 'Latvia': 'LV', 'Estonia': 'EE',
    'United States': 'US',
}
_COUNTRY_WORDS = {token for key, _name in _COUNTRY_QUERY for token in key.split()}
_STREET_SUFFIX = {'str', 'strasse', 'ul', 'ulica', 'weg', 'gasse', 'allee', 'street', 'st', 'road', 'rd'}


def _country_clause(text):
    """Last address segment when it is a country name, and the address without it."""
    parts = [part.strip() for part in text.split(',') if part.strip()]
    if not parts:
        return '', text
    folded = _fold(parts[-1])
    for key, name in _COUNTRY_QUERY:
        if folded == key:
            return name, ', '.join(parts[:-1]).strip()
    if folded in _ISO_COUNTRY:
        return _ISO_COUNTRY[folded], ', '.join(parts[:-1]).strip()
    return '', text


def _street_digits(text):
    stripped = re.sub(r'\b\d{2}-\d{3}\b', ' ', text)
    stripped = re.sub(r'\b\d{3}\s?\d{2}\b', ' ', stripped)
    stripped = re.sub(r'\b\d{5}\b', ' ', stripped)
    return bool(re.search(r'\d', stripped))


def _tail_city(text):
    match = re.search(r'\b(?:\d{5}|\d{3}\s?\d{2}|\d{2}-\d{3})\s+([^,]+)$', text.strip())
    return match.group(1).strip() if match else ''


def words(value):
    return set(re.findall(r'[a-z0-9]+', _fold(value)))


def _match_words(value):
    """Compare streets after expanding Str. and without country or generic suffix tokens."""
    folded = set()
    for token in words(value):
        if token.endswith('str') and not token.endswith('strasse') and len(token) > 3:
            token += 'asse'
        if token in _STREET_SUFFIX or token in _COUNTRY_WORDS:
            continue
        folded.add(token)
    return folded


def _house_tokens(query):
    text = re.sub(r'\b\d{2}-\d{3}\b', ' ', query)
    text = re.sub(r'\b\d{3}\s?\d{2}\b', ' ', text)
    text = re.sub(r'\b\d{5}\b', ' ', text)
    text = re.sub(r'\b\d{4}\b(?=\s+\D)', ' ', text)
    return set(re.findall(r'\d{1,4}[a-z]?', _fold(text)))


def _expected_country(query):
    name, _body = _country_clause(query)
    if name in _COUNTRY_CODE:
        return _COUNTRY_CODE[name]
    folded = words(query)
    for key, english in _COUNTRY_QUERY:
        if set(key.split()) <= folded and english in _COUNTRY_CODE:
            return _COUNTRY_CODE[english]
    return ''


def _km(a, b):
    from math import asin, cos, radians, sin, sqrt
    lat1, lon1, lat2, lon2 = (radians(a['lat']), radians(a['lon']), radians(b['lat']), radians(b['lon']))
    hav = sin((lat2 - lat1) / 2) ** 2 + cos(lat1) * cos(lat2) * sin((lon2 - lon1) / 2) ** 2
    return 6371 * 2 * asin(sqrt(hav))


def choose(features, query):
    """Place the address. A city query still needs one clear place; a street query keeps the best nearby hit."""
    candidates = []
    wanted = _match_words(query)
    houses = _house_tokens(query)
    expected = _expected_country(query)
    for f in features:
        p = f.get('properties', {})
        kind = p.get('type', '')
        if kind in ('state', 'country', 'county', 'other'):
            continue
        if kind == 'house' and not p.get('housenumber') and not (words(p.get('name', '')) <= words(query)):
            continue
        xy = f.get('geometry', {}).get('coordinates', [])
        if len(xy) != 2 or not all(isinstance(x, (int, float)) for x in xy):
            continue
        lon, lat = xy
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            continue
        country = p.get('countrycode', '').upper()
        if expected and country != expected:
            continue
        label = ', '.join(str(p[k]) for k in ['name', 'street', 'housenumber', 'postcode', 'city', 'district', 'state', 'country'] if p.get(k))
        matched = _match_words(label)
        distinctive = {token for token in wanted if not token.isdigit()}
        if not wanted or len(wanted & matched) / len(wanted) < .6 or (distinctive and not (distinctive & matched)):
            continue
        number_parts = set(re.findall(r'\d{1,4}[a-z]?', _fold(p.get('housenumber') or '')))
        exact = bool(houses) and houses <= number_parts
        overlap = len(wanted & matched) / len(wanted)
        score = (3 if exact else 0) + overlap + (0.3 if kind == 'house' else 0) + (0.1 if not p.get('name') else 0)
        precision = 'Adres — wymaga weryfikacji' if exact else 'Przybliżenie: ulica / miejscowość, nie siedziba'
        candidates.append(dict(lat=lat, lon=lon, geo_precision=precision, geo_label=label, geo_source='https://www.openstreetmap.org/' + {'N': 'node', 'W': 'way', 'R': 'relation'}.get(p.get('osm_type'), 'node') + '/' + str(p.get('osm_id', '')), score=score, exact=exact))
    if not candidates:
        return dict(geo_precision='Brak jednoznacznej lokalizacji')
    if not houses:
        if len(candidates) != 1:
            return dict(geo_precision='Brak jednoznacznej lokalizacji')
        chosen = candidates[0]
    else:
        exacts = [item for item in candidates if item['exact']]
        pool = exacts or candidates
        pool.sort(key=lambda item: item['score'], reverse=True)
        chosen = pool[0]
        peers = exacts if exacts else [item for item in pool if abs(item['score'] - chosen['score']) < 0.05]
        limit = 0.4 if exacts else 2
        if any(_km(chosen, item) > limit for item in peers):
            return dict(geo_precision='Brak jednoznacznej lokalizacji')
    return {key: chosen[key] for key in ('lat', 'lon', 'geo_precision', 'geo_label', 'geo_source')}


class Locator:
    def __init__(self, folder, stop):
        self.path = Path(folder) / 'geocoding_cache.json'
        try:
            self.cache = json.loads(self.path.read_text())
        except (OSError, ValueError):
            self.cache = {}
        self.stop, self.last, self.disabled = stop, 0, False
        self.endpoint = os.environ.get('PROMOT_PHOTON_URL', 'https://photon.komoot.io/api/')
        from core import live_session
        self.session = live_session({'User-Agent': 'PromotFinder/1.4 (business-address lookup)'})

    def locate(self, address):
        if not address.strip():
            return dict(geo_precision='Brak adresu — nie wyznaczono lokalizacji')
        folded = ' '.join(address.lower().split())
        key = 'v3|' + self.endpoint + '|' + folded
        if key in self.cache:
            return self.cache[key]
        previous = self.cache.get('v2|' + self.endpoint + '|' + folded)
        if isinstance(previous, dict) and isinstance(previous.get('lat'), (int, float)):
            self.cache[key] = previous
            return previous
        if self.disabled:
            return dict(geo_precision='Usługa lokalizacji niedostępna — spróbuj później')
        if self.stop.wait(max(0, 1.2 - (time.monotonic() - self.last))):
            return {}
        self.last = time.monotonic()
        try:
            r = self.session.get(self.endpoint, params=dict(q=address,limit=5), timeout=(8,15))
            r.raise_for_status()
            result = choose(r.json().get('features', []), address)
        except (requests.RequestException, ValueError):
            if self.stop.is_set():
                return {}
            self.disabled = True
            return dict(geo_precision='Usługa lokalizacji niedostępna — spróbuj później')
        self.cache[key] = result
        tmp = self.path.with_suffix('.tmp')
        tmp.write_text(json.dumps(self.cache, ensure_ascii=False), encoding='utf-8')
        tmp.replace(self.path)
        return result
