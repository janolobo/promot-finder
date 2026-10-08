"""Run: python app.py. A local panel, optionally in a native pywebview window."""
from __future__ import annotations
import argparse
import base64
import hmac
import json
import re
import secrets
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from core import DIRECT_SOURCES, GROUPS, PROFILES, CATEGORIES, RECIPIENTS, COUNTRIES, SOURCES, Research, export_xlsx
from catalogs import phrase_csv_content, phrase_csv_rows, save_phrase_csv, save_phrase_rows

ROOT = Path(__file__).resolve().parent
TOKEN = secrets.token_urlsafe(32)
EXPORT_LOCK = threading.Lock()
PUBLIC_MODE = False
AUTH_USER = ''
AUTH_PASSWORD = ''
research = Research(ROOT / 'wyniki')

def map_document(snapshot):
    payload = json.dumps(snapshot, ensure_ascii=False).replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')
    # No API keys in exports. A restricted browser key can be entered on opening.
    return (ROOT / 'static' / 'map.html').read_text(encoding='utf-8').replace('/*SNAPSHOT*/null', payload)

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def reply(self, content, kind='application/json; charset=utf-8', code=200, download='', cache=False):
        if isinstance(content, str):
            content = content.encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', kind)
        self.send_header('Content-Length', str(len(content)))
        self.send_header('Cache-Control', 'private, max-age=86400' if cache else 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'strict-origin-when-cross-origin')
        self.send_header('X-Frame-Options', 'SAMEORIGIN')
        if download:
            self.send_header('Content-Disposition', f'attachment; filename="{download}"')
        self.end_headers()
        self.wfile.write(content)

    def json_reply(self, value, code=200):
        self.reply(json.dumps(value, ensure_ascii=False), code=code)

    def valid_host(self):
        host = self.headers.get('Host', '').lower()
        if host in (f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'):
            return True
        return PUBLIC_MODE and bool(re.fullmatch(r'[a-z0-9-]+\.trycloudflare\.com(?::443)?', host))

    def authorized(self):
        if not PUBLIC_MODE:
            return True
        value = self.headers.get('Authorization', '')
        if not value.startswith('Basic '):
            return False
        try:
            supplied = base64.b64decode(value[6:], validate=True).decode('utf-8')
        except (ValueError, UnicodeDecodeError):
            return False
        expected = f'{AUTH_USER}:{AUTH_PASSWORD}'
        return hmac.compare_digest(supplied, expected)

    def require_access(self):
        if not self.valid_host():
            self.json_reply({'error': 'Niedozwolony host'}, 403)
            return False
        if not self.authorized():
            content = b'Wymagane logowanie do panelu PROMOT.'
            self.send_response(401)
            self.send_header('WWW-Authenticate', 'Basic realm="PROMOT", charset="UTF-8"')
            self.send_header('Content-Type', 'text/plain; charset=utf-8')
            self.send_header('Content-Length', str(len(content)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            self.wfile.write(content)
            return False
        return True

    def do_GET(self):
        if not self.require_access():
            return
        path = urlparse(self.path).path
        if path == '/':
            html = (ROOT / 'static' / 'index.html').read_text(encoding='utf-8').replace('__TOKEN__', TOKEN)
            return self.reply(html, 'text/html; charset=utf-8')
        if path == '/map':
            return self.reply((ROOT / 'static' / 'map.html').read_text(encoding='utf-8'), 'text/html; charset=utf-8')
        if path == '/saved-map':
            files = sorted(research.folder.glob('*/wyniki.json'))
            if not files:
                return self.reply('<meta charset="utf-8"><p>Brak zapisanych wyników. <a href="/">Otwórz panel i rozpocznij wyszukiwanie</a>.</p>', 'text/html; charset=utf-8', 404)
            try:
                snapshot = json.loads(files[-1].read_text(encoding='utf-8'))
                return self.reply(map_document(snapshot), 'text/html; charset=utf-8')
            except (ValueError, OSError):
                return self.json_reply({'error': 'Nie udało się odczytać ostatniej sesji'}, 500)
        if path == '/api/options':
            return self.json_reply(dict(direct_sources={k:v[0] for k,v in DIRECT_SOURCES.items()}, cache_counts=research.cache_counts, phrase_counts={key: len(phrase_csv_rows(key)) for key in ('web', 'osm')}, groups=GROUPS, profiles={k:v[0] for k,v in PROFILES.items()}, categories={k:v[0] for k,v in CATEGORIES.items()}, recipients={k:v[0] for k,v in RECIPIENTS.items()}, sources={k:v[0] for k,v in SOURCES.items()}, countries={k:v[0] for k,v in COUNTRIES.items()}))
        if path == '/api/preview':
            return self.preview()
        if self.headers.get('X-Panel-Token') != TOKEN:
            return self.json_reply({'error': 'Brak tokenu panelu'}, 403)
        if path == '/api/state':
            return self.json_reply(research.snapshot())
        if path == '/api/catalog-rows':
            return self.json_reply({'rows': research.catalog_rows()})
        if path == '/api/phrase-csv':
            source = (parse_qs(urlparse(self.path).query).get('source') or [''])[0]
            return self.json_reply({'source': source, 'content': phrase_csv_content(source), 'rows': phrase_csv_rows(source)})
        if path in ['/api/xlsx', '/api/html', '/api/log']:
            snapshot = research.snapshot()
            if not snapshot['run_id']:
                return self.json_reply({'error': 'Najpierw uruchom wyszukiwanie'}, 400)
            folder = research.folder / snapshot['run_id']
            if path == '/api/xlsx':
                # Separate filename from the worker's automatic export.
                file = folder / 'eksport.xlsx'
                with EXPORT_LOCK:
                    export_xlsx(snapshot, file)
                    content = file.read_bytes()
                return self.reply(content, 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', download='promot_wyniki.xlsx')
            if path == '/api/html':
                html = map_document(snapshot)
                (folder / 'eksport_mapa.html').write_text(html, encoding='utf-8')
                return self.reply(html, 'text/html; charset=utf-8', download='promot_mapa.html')
            return self.reply((folder / 'poszukiwania.log').read_bytes(), 'text/plain; charset=utf-8', download='promot_poszukiwania.log')
        self.json_reply({'error': 'Nie znaleziono'}, 404)

    def preview(self):
        from preview import placeholder, thumbnail
        query = parse_qs(urlparse(self.path).query)
        if (query.get('token') or [''])[0] != TOKEN:
            return self.json_reply({'error': 'Brak tokenu panelu'}, 403)
        ident = (query.get('id') or [''])[0]
        record = next((item for item in research.snapshot()['records'] if item.get('id') == ident), None)
        if record is None:
            return self.json_reply({'error': 'Nie znaleziono firmy'}, 404)
        website = record.get('website') or ''
        try:
            content, kind = thumbnail(website, ROOT / 'wyniki' / 'miniatury') if website else placeholder(record.get('name'))
        except Exception:
            content, kind = placeholder(record.get('name'))
        return self.reply(content, kind, cache=True)

    def do_POST(self):
        if not self.require_access():
            return
        if self.headers.get('X-Panel-Token') != TOKEN:
            return self.json_reply({'error': 'Brak autoryzacji lokalnego panelu'}, 403)
        try:
            length = int(self.headers.get('Content-Length', 0))
            if length < 0 or length > 100000:
                raise ValueError('Zbyt duże żądanie')
            data = json.loads(self.rfile.read(length) or b'{}')
            if not isinstance(data, dict):
                raise ValueError('Niepoprawna konfiguracja')
            if self.path == '/api/pdf':
                from pdf_export import export_pdf
                snapshot=research.snapshot()
                if data.get('run_id') != snapshot['run_id']:
                    raise ValueError('Sesja zmieniła się — odśwież listę przed eksportem')
                ids=data.get('ids',[])
                if not isinstance(ids,list) or not all(isinstance(x,str) for x in ids):
                    raise ValueError('Nieprawidłowy wybór firm')
                with EXPORT_LOCK:
                    content=export_pdf(snapshot,ids,data)
                return self.reply(content,'application/pdf',download='promot_filtrowane.pdf')
            if self.path == '/api/start':
                research.start(data)
                return self.json_reply({'ok': True})
            if self.path == '/api/fill':
                research.start_fill(data)
                return self.json_reply({'ok': True})
            if self.path == '/api/analyze':
                research.start_analysis(data)
                return self.json_reply({'ok': True})
            if self.path == '/api/locate':
                research.start_locations()
                return self.json_reply({'ok': True})
            if self.path == '/api/translate':
                research.start_translate(data)
                return self.json_reply({'ok': True})
            if self.path == '/api/stop':
                research.abort()
                return self.json_reply({'ok': True})
            if self.path == '/api/regon':
                from regon import start_lookup
                start_lookup(research, data.get('ids'), data.get('key', ''))
                return self.json_reply({'ok': True})
            if self.path == '/api/catalog':
                if research.running:
                    raise ValueError('Poczekaj na zakończenie bieżącego zadania')
                removed = research.clear_catalog(str(data.get('catalog') or ''))
                return self.json_reply({'ok': True, 'removed': removed, 'cache_counts': research.cache_counts})
            if self.path == '/api/phrase-csv':
                if research.running:
                    raise ValueError('Poczekaj na zakończenie bieżącego zadania')
                rows = save_phrase_rows(data.get('source'), data.get('rows')) if 'rows' in data else save_phrase_csv(data.get('source'), data.get('content'))
                return self.json_reply({'ok': True, 'rows': rows})
            self.json_reply({'error': 'Nie znaleziono'}, 404)
        except (ValueError, TypeError, KeyError) as exc:
            self.json_reply({'error': str(exc)}, 400)

def main():
    global PUBLIC_MODE, AUTH_USER, AUTH_PASSWORD
    parser = argparse.ArgumentParser(description='PROMOT — panel poszukiwania kontrahentów')
    parser.add_argument('--browser', action='store_true', help='Otwórz panel w przeglądarce zamiast osobnego okna')
    parser.add_argument('--no-open', action='store_true', help='Uruchom sam serwer lokalny')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--latest-map', action='store_true', help='Otwórz mapę ostatniej zapisanej sesji przez lokalny serwer')
    parser.add_argument('--public', action='store_true', help='Wymagaj logowania i zezwól na dostęp przez trycloudflare.com')
    args = parser.parse_args()
    if args.public:
        try:
            auth = json.loads((ROOT / '.promot-auth.json').read_text(encoding='utf-8'))
            AUTH_USER = str(auth['username'])
            AUTH_PASSWORD = str(auth['password'])
            if not AUTH_USER or len(AUTH_PASSWORD) < 16:
                raise ValueError
        except (OSError, ValueError, TypeError, KeyError):
            parser.error('Tryb publiczny wymaga pliku .promot-auth.json z silnym loginem i hasłem')
        PUBLIC_MODE = True
    try:
        server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    except OSError:
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    url = f'http://127.0.0.1:{server.server_port}'
    if args.latest_map:
        url += '/saved-map'
    print(f'PROMOT: {url}', flush=True)
    print(f'Wyniki i logi: {research.folder}', flush=True)
    if not args.browser and not args.no_open:
        try:
            import webview
            webview.settings['ALLOW_DOWNLOADS'] = True
            serving = threading.Thread(target=server.serve_forever, daemon=True)
            serving.start()
            try:
                webview.create_window('PROMOT | Poszukiwanie kontrahentów', url, width=1480, height=960, min_size=(1000, 700))
                webview.start(private_mode=False)
            except Exception as exc:
                print(f'Nie udało się otworzyć okna ({type(exc).__name__}); otwieram panel w przeglądarce.', flush=True)
                webbrowser.open(url)
                try:
                    while serving.is_alive():
                        serving.join(.5)
                except KeyboardInterrupt:
                    pass
            research.shutdown()
            server.shutdown()
            server.server_close()
            return
        except ImportError:
            print('Brak pywebview: otwieram pełny panel z mapą w przeglądarce.', flush=True)
    if not args.no_open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('Zapisuję wyniki częściowe przed zamknięciem…', flush=True)
        research.shutdown()
        server.server_close()

if __name__ == '__main__':
    main()
