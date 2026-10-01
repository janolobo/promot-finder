import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch, Mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from geolocation import Locator, choose, city_query, street_address
from core import Research, extract

F = {'geometry':{'coordinates':[18.6,49.7]},'properties':{'name':'Cieszyn','country':'Polska','countrycode':'PL','osm_type':'N','osm_id':123}}
class LocationTests(unittest.TestCase):
    def test_published_directions_coordinates(self):
        html = '<a href="https://www.google.com/maps/dir/?api=1&destination=51.052247,16.203842">Dojazd</a>'
        result = extract(html, 'https://directory.test/company')
        self.assertEqual(result['lat'], 51.052247)
        self.assertEqual(result['lon'], 16.203842)
        self.assertIsNone(extract(html + html.replace('51.052247','52.1'), 'https://directory.test/list')['lat'])

    def test_street_address_from_page_text(self):
        from geolocation import find_address, locate_query
        self.assertEqual(street_address('Biuro: ul. Przemysłowa 4, 15-001 Białystok'), 'ul. Przemysłowa 4, 15-001 Białystok')
        self.assertEqual(street_address('ul. Krótka 1, 00-001 Warszawa oraz ul. Długa 2, 30-001 Kraków'), '')
        self.assertEqual(find_address('Siedziba: Przemysłowa 4, 15-001 Białystok'), 'Przemysłowa 4, 15-001 Białystok')
        self.assertEqual(find_address('ul. Leśna 8 40-001 Katowice'), 'ul. Leśna 8, 40-001 Katowice')
        self.assertEqual(find_address('Adres korespondencyjny 15-001 Białystok'), '15-001 Białystok')
        self.assertEqual(locate_query('15-001 Białystok'), ('Białystok, Polska', True))
        self.assertEqual(extract('<footer>Siedziba ul. Leśna 8, 40-001 Katowice</footer><p>oddział ul. Inna 1, 30-001 Kraków</p>', 'https://firma.example')['address'], 'ul. Leśna 8, 40-001 Katowice')

    def test_city_query_from_catalog_note(self):
        self.assertEqual(city_query('Białystok (woj. podlaskie)'), 'Białystok, podlaskie, Polska')
        self.assertEqual(city_query('Jasionka k/Rzeszowa (woj. podkarpackie)'), 'Jasionka, podkarpackie, Polska')
        self.assertEqual(city_query('ul. Długa 1, Katowice'), '')

    def test_exact_name_search_finds_site_and_address(self):
        html = '<html><head><title>AC S.A.</title></head><body><p>Siedziba: ul. Leśna 8, 40-001 Katowice</p></body></html>'
        with tempfile.TemporaryDirectory() as tmp, patch('core.search_web', return_value=[{'title': 'AC S.A.', 'url': 'https://ac.example'}]) as search, patch('core.Fetcher.page', return_value=(html.encode(), 'text/html', 'https://ac.example')), patch('geolocation.Locator.locate', return_value=dict(lat=50.26, lon=19.02, geo_precision='Adres — wymaga weryfikacji', geo_label='Katowice', geo_source='https://www.openstreetmap.org/node/2')):
            research = Research(tmp)
            research.records = [dict(id='1', name='AC S.A.', website='', address='', lat=None, lon=None, contacts=[], source='https://www.targikielce.pl/agrotech-2026/lista-wystawcow/ac,1', status='Oczekuje', osm_text='', groups=['Do weryfikacji'], category='', evidence='')]
            research.config = dict(pages=1, enrich_web=True, categories=[])
            research.start_fill()
            research.worker.join(10)
            row = research.records[0]
            self.assertEqual(search.call_args.args[0], '"AC S.A."')
            self.assertEqual(search.call_args.args[2]['engine'], 'duckduckgo')
            self.assertEqual(row['website'], 'https://ac.example')
            self.assertEqual(row['address'], 'ul. Leśna 8, 40-001 Katowice')
            self.assertEqual(row['lat'], 50.26)

    def test_analysis_adds_address_link_and_map_point(self):
        html = '<html><head><title>AC S.A.</title></head><body><address>ul. Przemysłowa 4, Białystok</address><a href="https://ac.example/kontakt">Kontakt</a></body></html>'
        with tempfile.TemporaryDirectory() as tmp, patch('core.Fetcher.page', return_value=(html.encode(), 'text/html', 'https://ac.example')), patch('geolocation.Locator.locate', return_value=dict(lat=53.13, lon=23.16, geo_precision='Adres — wymaga weryfikacji', geo_label='Białystok', geo_source='https://www.openstreetmap.org/node/1')):
            research = Research(tmp)
            research.records = [dict(id='1', name='AC', website='https://ac.example', address='Białystok (woj. podlaskie)', lat=None, lon=None, contacts=[], source='https://pgm.org.pl/x', status='Oczekuje', osm_text='', groups=['Do weryfikacji'], category='', evidence='')]
            research.config = dict(pages=1, enrich_web=True, categories=[])
            research.start_fill()
            research.worker.join(10)
            row = research.records[0]
            self.assertEqual(row['website'], 'https://ac.example')
            self.assertEqual(row['address'], 'ul. Przemysłowa 4, Białystok')
            self.assertEqual(row['lat'], 53.13)
            self.assertEqual(row['lon'], 23.16)
        self.assertIn('Przybliżenie', choose([F],'Cieszyn Polska')['geo_precision'])
        self.assertNotIn('lat',choose([F,F],'Cieszyn Polska'))
        self.assertNotIn('lat',choose([F],'Berlin Deutschland'))
        self.assertNotIn('lat',choose([F],'Cieszyn Testowa 123 Polska'))
    def test_cache_and_no_address(self):
        with tempfile.TemporaryDirectory() as tmp, patch('geolocation.requests.get',return_value=Mock(json=lambda:{'features':[F]},raise_for_status=lambda:None)) as get:
            loc=Locator(tmp,threading.Event())
            self.assertNotIn('lat',loc.locate(''))
            self.assertEqual(loc.locate('Cieszyn Polska')['lat'],49.7)
            Locator(tmp,threading.Event()).locate('Cieszyn Polska')
            self.assertEqual(get.call_count,1)
    def test_remaining_queries_run_after_limit(self):
        with tempfile.TemporaryDirectory() as tmp, patch('core.search_web',return_value=[{'url':'https://example.com'}]) as search, patch('core.Fetcher.page',return_value=('<title>Firma</title>','text/html','https://example.com')):
            r=Research(tmp)
            r.start(dict(categories=['automotive','valves'],countries=['PL'],sources=['web'],max_firms=1,pages=1,all_queries=True))
            r.worker.join(15)
            self.assertEqual(search.call_count,2)
            self.assertEqual(r.progress['queries_done'],2)
            self.assertEqual(len(r.records),1)
    def test_restore_and_map_export(self):
        import json
        with tempfile.TemporaryDirectory() as tmp, patch('geolocation.Locator.locate',return_value=dict(lat=49.7,lon=18.6,geo_precision='Przybliżenie')):
            old=Path(tmp)/'20000101';old.mkdir()
            (old/'wyniki.json').write_text(json.dumps(dict(records=[dict(id='1',name='Firma',address='Cieszyn',lat=None,lon=None,contacts=[])],progress={},discoveries=[])))
            r=Research(tmp);r.start_locations();r.worker.join(10)
            self.assertFalse(r.running)
            self.assertEqual(r.records[0]['lat'],49.7)
            self.assertTrue((Path(tmp)/r.run_id/'mapa.html').exists())
