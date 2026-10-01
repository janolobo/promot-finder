"""Lookup of public GUS BIR (REGON) data. Search is by NIP, REGON or KRS only."""
from __future__ import annotations
import re
import threading
import time
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

import requests

from core import DENIED, DIRECTORIES, UA, Fetcher, domain, is_domain

ENDPOINT = 'https://wyszukiwarkaregon.stat.gov.pl/wsBIR/UslugaBIRzewnPubl.svc'
NS = 'http://CIS/BIR/PUBL/2014/07'
_JOB = threading.Lock()
_THREAD = None


def _check(digits, weights):
    total = sum(int(a) * b for a, b in zip(digits, weights))
    check = total % 11
    if check == 10:
        check = 0
    return digits[-1] == str(check)


def nip_ok(value):
    return len(value) == 10 and value.isdigit() and _check(value, [6, 5, 7, 2, 3, 4, 5, 6, 7])


def regon_ok(value):
    if not value.isdigit():
        return False
    if len(value) == 9:
        return _check(value, [8, 9, 2, 3, 4, 5, 6, 7])
    if len(value) == 14:
        return _check(value[:9], [8, 9, 2, 3, 4, 5, 6, 7]) and _check(value, [2, 4, 8, 5, 0, 9, 7, 3, 6, 1, 2, 4, 8])
    return False


def _labeled(text, label):
    found = []
    for match in re.finditer(label + r'\s*[:.]?\s*([0-9][0-9 \-]{6,22}[0-9])', text, re.I):
        found.append(re.sub(r'\D', '', match.group(1)))
    return found


def identifiers(text):
    text = text or ''
    nips, regons, krs = [], [], []
    for digits in _labeled(text, 'nip'):
        if nip_ok(digits):
            nips.append(digits)
    for digits in _labeled(text, 'regon'):
        if regon_ok(digits):
            regons.append(digits)
    for digits in _labeled(text, 'krs'):
        if len(digits) == 10:
            krs.append(digits)
    for match in re.findall(r'\b(\d{3}-\d{3}-\d{2}-\d{2})\b', text):
        digits = re.sub(r'\D', '', match)
        if nip_ok(digits):
            nips.append(digits)
    for match in re.findall(r'\b(\d{3}-\d{2}-\d{2}-\d{3})\b', text):
        digits = re.sub(r'\D', '', match)
        if regon_ok(digits):
            regons.append(digits)
    return {'nip': list(dict.fromkeys(nips)), 'regon': list(dict.fromkeys(regons)), 'krs': list(dict.fromkeys(krs))}


def choose_query(text):
    found = identifiers(text)
    if len(found['nip']) > 1 or len(found['regon']) > 1 or len(found['krs']) > 1:
        return None, 'Na stronie jest więcej niż jeden numer NIP, REGON albo KRS'
    if found['nip']:
        return {'Nip': found['nip'][0]}, ''
    if found['regon']:
        return {'Regon': found['regon'][0]}, ''
    if found['krs']:
        return {'Krs': found['krs'][0]}, ''
    return None, 'Brak NIP, REGON albo KRS na stronie firmy. API REGON wyszukuje tylko po tych numerach.'


def _local(tag):
    return tag.rsplit('}', 1)[-1]


def _rows(xml_text):
    if not xml_text or not str(xml_text).strip():
        return []
    root = ET.fromstring(xml_text)
    rows = []
    for node in root.iter():
        if _local(node.tag) != 'dane':
            continue
        row = {}
        for child in list(node):
            row[_local(child.tag)] = (child.text or '').strip()
        if row:
            rows.append(row)
    return rows


def _result_xml(soap, tag):
    root = ET.fromstring(soap)
    for node in root.iter():
        if _local(node.tag) != tag:
            continue
        if (node.text or '').strip():
            return node.text
        if list(node):
            return ''.join(ET.tostring(child, encoding='unicode') for child in list(node))
    return ''


class BirClient:
    def __init__(self, key, http=None):
        key = (key or '').strip()
        if not re.fullmatch(r'[A-Za-z0-9_-]{8,80}', key):
            raise ValueError('Wpisz klucz użytkownika API REGON z api.stat.gov.pl')
        self.key = key
        self.http = http or requests.Session()
        self.sid = ''

    def _post(self, action, body):
        envelope = f'''<soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope" xmlns:ns="{NS}" xmlns:dat="{NS}/DataContract">
<soap:Header xmlns:wsa="http://www.w3.org/2005/08/addressing"><wsa:To>{ENDPOINT}</wsa:To><wsa:Action>{action}</wsa:Action></soap:Header>
<soap:Body>{body}</soap:Body></soap:Envelope>'''
        headers = {'Content-Type': f'application/soap+xml; charset=utf-8; action="{action}"', 'User-Agent': UA}
        if self.sid:
            headers['sid'] = self.sid
        response = self.http.post(ENDPOINT, data=envelope.encode('utf-8'), headers=headers, timeout=(8, 40))
        response.raise_for_status()
        return response.text

    def login(self):
        soap = self._post(f'{NS}/IUslugaBIRzewnPubl/Zaloguj', f'<ns:Zaloguj><ns:pKluczUzytkownika>{escape(self.key)}</ns:pKluczUzytkownika></ns:Zaloguj>')
        self.sid = _result_xml(soap, 'ZalogujResult').strip()
        if not self.sid:
            raise ValueError('Klucz API REGON został odrzucony')

    def _rows(self, action, body, tag, retry=True):
        if not self.sid:
            self.login()
        soap = self._post(action, body)
        time.sleep(0.35)
        rows = _rows(_result_xml(soap, tag))
        if any(row.get('ErrorCode') == '7' for row in rows) and retry:
            self.sid = ''
            self.login()
            return self._rows(action, body, tag, retry=False)
        return rows

    def search(self, field, value):
        body = f'<ns:DaneSzukajPodmioty><ns:pParametryWyszukiwania><dat:{field}>{escape(value)}</dat:{field}></ns:pParametryWyszukiwania></ns:DaneSzukajPodmioty>'
        return self._rows(f'{NS}/IUslugaBIRzewnPubl/DaneSzukajPodmioty', body, 'DaneSzukajPodmiotyResult')

    def report(self, regon, name):
        body = f'<ns:DanePobierzPelnyRaport><ns:pRegon>{escape(regon)}</ns:pRegon><ns:pNazwaRaportu>{escape(name)}</ns:pNazwaRaportu></ns:DanePobierzPelnyRaport>'
        return self._rows(f'{NS}/IUslugaBIRzewnPubl/DanePobierzPelnyRaport', body, 'DanePobierzPelnyRaportResult')


def _value(rows, *names):
    wanted = {name.lower() for name in names}
    for row in rows:
        for key, value in row.items():
            if key.lower() in wanted and value:
                return value
    return ''


def _address(row):
    street = ' '.join(part for part in [row.get('Ulica', ''), row.get('NrNieruchomosci', ''), row.get('NrLokalu', '')] if part)
    place = ' '.join(part for part in [row.get('KodPocztowy', ''), row.get('Miejscowosc', '')] if part)
    return ', '.join(part for part in [street, place, row.get('Wojewodztwo', '')] if part)


def _pkd(rows):
    items = []
    for row in rows:
        code = _value([row], 'praw_pkdKod', 'fiz_pkd_Kod', 'fiz_pkdKod')
        if not code:
            continue
        name = _value([row], 'praw_pkdNazwa', 'fiz_pkd_Nazwa', 'fiz_pkdNazwa')
        main = _value([row], 'praw_pkdPrzewazajace', 'fiz_pkd_Przewazajace', 'fiz_pkdPrzewazajace')
        items.append((main in ('1', 'true', 'True'), f'{code} {name}'.strip()))
    items.sort(key=lambda item: not item[0])
    return items


def summarize(hit, general, pkd_rows):
    pkd = _pkd(pkd_rows)
    return dict(
        status='Dane z API REGON',
        regon=hit.get('Regon', ''),
        nip=hit.get('Nip', '') or _value(general, 'praw_nip', 'fiz_nip'),
        krs=_value(general, 'praw_numerWRejestrzeEwidencji', 'praw_numerWrejestrzeEwidencji', 'fizC_numerwRejestrzeEwidencji', 'fizP_numerwRejestrzeEwidencji'),
        legal_form=_value(general, 'praw_szczegolnaFormaPrawna_Nazwa', 'fiz_szczegolnaFormaPrawna_Nazwa'),
        registry=_value(general, 'praw_rodzajRejestruEwidencji_Nazwa', 'fizC_RodzajRejestru_Nazwa', 'fizP_RodzajRejestru_Nazwa'),
        pkd_main=pkd[0][1] if pkd else '',
        pkd=[item[1] for item in pkd],
        address=_address(hit),
        regon_name=hit.get('Nazwa', ''),
        started=_value(general, 'praw_dataRozpoczeciaDzialalnosci', 'fiz_dataRozpoczeciaDzialalnosci'),
    )


def _reports_for(hit):
    if hit.get('Typ') == 'P' or hit.get('SilosID') == '6':
        return 'BIR11OsPrawna', 'BIR11OsPrawnaPkd'
    silos = {'1': 'BIR11OsFizycznaDzialalnoscCeidg', '2': 'BIR11OsFizycznaDzialalnoscRolnicza', '3': 'BIR11OsFizycznaDzialalnoscPozostala', '4': 'BIR11OsFizycznaDzialalnoscSkreslonaDo20141108'}
    return silos.get(hit.get('SilosID'), 'BIR11OsFizycznaDzialalnoscCeidg'), 'BIR11OsFizycznaPkd'


def _pick(rows):
    usable = [row for row in rows if not row.get('ErrorCode')]
    if any(row.get('ErrorCode') == '4' for row in rows) and not usable:
        return None
    heads = [row for row in usable if row.get('Typ') in ('P', 'F')]
    chosen = heads or usable
    regons = {row.get('Regon', '')[:9] for row in chosen}
    if len(chosen) == 1 or len(regons) == 1:
        return chosen[0]
    return None


def record_text(record):
    parts = [record.get('name', ''), record.get('address', ''), record.get('evidence', ''), record.get('osm_text', '')]
    return '\n'.join(parts)


def lookup_record(record, client, fetch=None):
    query, reason = choose_query(record_text(record))
    if query is None and fetch is not None and record.get('website') and not is_domain(domain(record['website']), DENIED + DIRECTORIES):
        try:
            body, kind, _ = fetch.page(record['website'])
            if 'html' in kind or not kind:
                query, reason = choose_query(body.decode('utf-8', 'replace'))
        except Exception as exc:
            reason = 'Nie udało się odczytać strony w poszukiwaniu NIP, REGON albo KRS: ' + type(exc).__name__
    if query is None:
        return dict(status=reason, regon='', nip='', krs='', legal_form='', registry='', pkd_main='', pkd=[], address='', regon_name='', started='')
    field, value = next(iter(query.items()))
    rows = client.search(field, value)
    hit = _pick(rows)
    if hit is None:
        return dict(status='API REGON nie zwróciło jednego podmiotu dla tego numeru', regon='', nip='', krs='', legal_form='', registry='', pkd_main='', pkd=[], address='', regon_name='', started='')
    general_name, pkd_name = _reports_for(hit)
    regon = hit.get('Regon', '')
    general = client.report(regon, general_name) if regon else []
    pkd_rows = client.report(regon, pkd_name) if regon else []
    return summarize(hit, general, pkd_rows)


def run_lookup(research, ids, key):
    client = BirClient(key)
    fetch = Fetcher(threading.Event(), lambda _message: None)
    with research.lock:
        research.progress.update(regon_running=True, regon_done=0, regon_total=len(ids), regon_phase='Pobieranie danych REGON')
    try:
        for done, ident in enumerate(ids, 1):
            with research.lock:
                record = next((item for item in research.records if item.get('id') == ident), None)
            if record is None:
                continue
            try:
                result = lookup_record(record, client, fetch)
            except Exception as exc:
                result = dict(status='Błąd API REGON: ' + type(exc).__name__, regon='', nip='', krs='', legal_form='', registry='', pkd_main='', pkd=[], address='', regon_name='', started='')
            with research.lock:
                record['regon'] = result
                research.progress.update(regon_done=done, regon_phase='Pobieranie danych REGON')
            if done % 5 == 0:
                research.checkpoint()
    finally:
        with research.lock:
            research.progress.update(regon_running=False, regon_phase='Dane REGON zapisane przy firmach')
        research.checkpoint()


def start_lookup(research, ids, key):
    global _THREAD
    if not isinstance(ids, list) or not ids or not all(isinstance(item, str) for item in ids) or len(ids) > 100:
        raise ValueError('Wybierz do 100 firm z bieżącej strony')
    BirClient(key)
    with research.lock:
        if not research.records:
            raise ValueError('Najpierw wyszukaj firmy')
        if research.progress.get('regon_running'):
            raise ValueError('Pobieranie REGON już trwa')
    with _JOB:
        if _THREAD is not None and _THREAD.is_alive():
            raise ValueError('Pobieranie REGON już trwa')
        _THREAD = threading.Thread(target=run_lookup, args=(research, ids, key), daemon=True)
        _THREAD.start()
