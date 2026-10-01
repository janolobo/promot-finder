import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch, Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import Research, extract, export_xlsx, safe_url, coords, Cancelled, Fetcher
from openpyxl import load_workbook

HTML = '''<html><head><title>Test Manufacturing</title><script type="application/ld+json">{
"@type":"Organization", "name":"Test Manufacturing", "address":{"streetAddress":"Testowa 2", "addressLocality":"Cieszyn"},
"geo":{"latitude":49.75,"longitude":18.63},
"employee":{"@type":"Person","name":"Anna Testowa","jobTitle":"Purchasing Manager","email":"anna@example.com","telephone":"+48 123 456 789"}}
</script></head><body><h1>Agricultural machinery manufacturer</h1><a href="mailto:office@example.com">Office</a>
<a href="tel:+48111222333">Phone</a><a href="/kontakt">Kontakt</a><script>hidden@example.com</script></body></html>'''

class CoreTests(unittest.TestCase):
    def test_explicit_person_and_company_contacts_are_separate(self):
        data = extract(HTML, 'https://example.com')
        anna = next(x for x in data['contacts'] if x['person'] == 'Anna Testowa')
        self.assertEqual(anna['email'], 'anna@example.com')
        self.assertEqual(anna['phone'], '+48 123 456 789')
        office = next(x for x in data['contacts'] if x['email'] == 'office@example.com')
        self.assertEqual(office['person'], '')
        self.assertEqual(data['lat'], 49.75)
        self.assertFalse(any(x['email'] == 'hidden@example.com' for x in data['contacts']))

    def test_malformed_and_missing_coordinates(self):
        self.assertEqual(coords('nan', 5), (None, None))
        self.assertEqual(coords(91, 0), (None, None))
        self.assertEqual(coords(0, 0), (0, 0))
        self.assertEqual(extract('<h1>Test</h1>', 'https://example.com')['lat'], None)

    def test_private_urls_denied(self):
        for url in ['file:///etc/passwd', 'http://user:password@example.com', 'http://127.0.0.1', 'http://[::1]', 'http://example.com:8080']:
            with self.assertRaises(ValueError):
                safe_url(url)

    def test_cancel(self):
        event = threading.Event()
        event.set()
        with self.assertRaises(Cancelled):
            Fetcher(event, lambda _:None).check()

    def test_worker_exports_and_deduplicates(self):
        with tempfile.TemporaryDirectory() as tmp:
            research = Research(tmp)
            with patch('core.Fetcher.page', return_value=(HTML.encode(), 'text/html', 'https://example.com')):
                research.start(dict(categories=['agriculture'], countries=['PL'], sources=[], seeds='https://example.com\nhttps://example.com/products', pages=2, max_firms=10))
                deadline = time.time() + 10
                while research.running and time.time() < deadline:
                    time.sleep(.02)
                self.assertFalse(research.running)
                state = research.snapshot()
                self.assertEqual(len(state['records']), 1)
                self.assertEqual(state['records'][0]['status'], 'Oczekuje')
                research.start_analysis()
                deadline = time.time() + 10
                while research.running and time.time() < deadline:
                    time.sleep(.02)
            self.assertFalse(research.running)
            state = research.snapshot()
            self.assertEqual(state['progress']['done'], 1)
            self.assertIn('Maszyn rolniczych', state['records'][0]['category'])
            folder = Path(tmp) / research.run_id
            self.assertTrue((folder / 'mapa.html').exists())
            self.assertTrue((folder / 'wyniki.xlsx').exists())
            self.assertFalse(json.loads((folder/'wyniki.json').read_text())['running'])
            self.assertNotIn('brave_key', (folder/'wyniki.json').read_text())
            book = load_workbook(folder / 'wyniki.xlsx')
            self.assertEqual(book['Firmy']['E2'].value, 49.75)
            self.assertEqual(book['Firmy']['C2'].hyperlink.target, 'https://example.com')

    def test_xlsx_formula_injection(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = dict(id='1', name='=HYPERLINK("bad")', website='https://example.com', contacts=[], lat=None, lon=None)
            state = dict(records=[r], discoveries=[], progress={'phase':'Zakończono'})
            path = Path(tmp)/'test.xlsx'
            export_xlsx(state,path)
            book = load_workbook(path)
            self.assertEqual(book['Firmy']['B2'].data_type, 's')
            self.assertIsNone(book['Firmy']['E2'].value)
            self.assertEqual(book['Firmy'].freeze_panes,'A2')

    def test_require_key_for_search_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                Research(tmp).start(dict(profile='cnc', categories=['automotive'], countries=['PL'], sources=['web'],engine='brave'))

    def test_manual_sources_alone_are_not_a_search(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                Research(tmp).start(dict(profile='cnc', categories=['automotive'], countries=['PL'], sources=['linkedin']))

    def test_stop_saves_partial_results(self):
        with tempfile.TemporaryDirectory() as tmp:
            research = Research(tmp)
            def stop_on_page(url):
                research.stop.set()
                raise Cancelled()
            with patch('core.Fetcher.page', side_effect=stop_on_page):
                research.start(dict(profile='cnc', categories=['automotive'], countries=['PL'], sources=[], seeds='https://example.com', pages=1, max_firms=1))
                research.worker.join(10)
            self.assertFalse(research.running)
            self.assertTrue(research.progress['phase'].startswith('Zatrzymano'))
            self.assertEqual(research.records, [])  # stopped before the seed page was read
            self.assertTrue((Path(tmp)/research.run_id/'wyniki.xlsx').exists())
            self.assertTrue((Path(tmp)/research.run_id/'mapa.html').exists())

    def test_unavailable_page_is_not_successful_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            research = Research(tmp)
            with patch('core.Fetcher.page', side_effect=ValueError('robots.txt nie pozwala')):
                research.start(dict(profile='cnc', categories=['automotive'], countries=['PL'], sources=[], seeds='https://example.com', pages=1, max_firms=1))
                research.worker.join(10)
                self.assertEqual(research.records[0]['status'], 'Oczekuje')
                research.start_analysis()
                research.worker.join(10)
            self.assertEqual(research.records[0]['status'], 'Nie udało się odczytać strony')
            self.assertEqual(research.progress['errors'], 2)
            self.assertEqual(research.records[0]['contacts'], [])

    def test_osm_and_search_preserve_coordinates_and_source_types(self):
        def response(url, **kwargs):
            r = Mock()
            r.raise_for_status.return_value = None
            if 'overpass' in url:
                r.json.return_value = {'elements':[{'type':'node','id':123,'lat':49.7,'lon':18.6,'tags':{'name':'OSM fixture','website':'https://osm-example.com'}}]}
            else:
                r.json.return_value = {'web':{'results':[{'title':'Company fixture','url':'https://example.com'},{'title':'EEN opportunity','url':'https://een.ec.europa.eu/partnering-opportunities/test'}]}}
            return r
        with tempfile.TemporaryDirectory() as tmp:
            research = Research(tmp)
            with patch('core.requests.get', side_effect=response), patch('core.Fetcher.page', return_value=(HTML.encode(),'text/html','https://example.com')):
                research.start(dict(categories=['agriculture'], countries=['PL'], sources=['osm','web'],brave_key='TEST_SECRET',engine='brave',pages=1,max_firms=3))
                research.worker.join(10)
            self.assertFalse(research.running)
            self.assertEqual(len(research.records),2)
            self.assertEqual(research.records[0]['lat'],49.7)
            self.assertEqual(len(research.discoveries),2)
            self.assertEqual(research.progress['queries_done'],1)
            for f in (Path(tmp)/research.run_id).glob('*'):
                if f.suffix in ['.json','.html','.log']:
                    self.assertNotIn('TEST_SECRET',f.read_text())

    def test_keyless_discovery_and_contacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            research = Research(tmp)
            with patch('core.search_web', return_value=[{'title':'Firma', 'url':'https://example.com'}]), patch('core.Fetcher.page', return_value=(HTML.encode(),'text/html','https://example.com')):
                research.start(dict(categories=['agriculture'],countries=['PL'],sources=['web'],engine='duckduckgo',pages=1,max_firms=1))
                research.worker.join(10)
                self.assertEqual(research.records[0]['status'], 'Oczekuje')
                research.start_analysis()
                research.worker.join(10)
            self.assertFalse(research.running)
            self.assertEqual(len(research.records),1)
            self.assertTrue(research.records[0]['contacts'])
            self.assertEqual(research.progress['errors'],0)

    def test_role_is_not_a_person_and_review_author_is_ignored(self):
        html = '''<div>Inżynier Sprzedaży Marcin Testowy <a href="mailto:marcin@example.com">email</a></div>
        <script type="application/ld+json">{"@type":"Review","author":{"@type":"Person","name":"Anonymous Reviewer"}}</script>
        <a href="tel:0123456789">template phone</a>'''
        data = extract(html, 'https://example.com')
        self.assertEqual(data['contacts'][0]['person'], 'Marcin Testowy')
        self.assertFalse(any(c['person']=='Anonymous Reviewer' for c in data['contacts']))
        self.assertFalse(any(c['phone']=='0123456789' for c in data['contacts']))

if __name__ == '__main__':
    unittest.main()
