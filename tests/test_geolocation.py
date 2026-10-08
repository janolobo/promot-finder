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
        from geolocation import find_address, locate_query, map_address
        self.assertEqual(street_address('Biuro: ul. Przemysłowa 4, 15-001 Białystok'), 'ul. Przemysłowa 4, 15-001 Białystok')
        self.assertEqual(street_address('ul. Krótka 1, 00-001 Warszawa oraz ul. Długa 2, 30-001 Kraków'), '')
        self.assertEqual(find_address('Siedziba: Przemysłowa 4, 15-001 Białystok'), 'Przemysłowa 4, 15-001 Białystok')
        self.assertEqual(find_address('ul. Leśna 8 40-001 Katowice'), 'ul. Leśna 8, 40-001 Katowice')
        self.assertEqual(find_address('Adres korespondencyjny 15-001 Białystok'), '15-001 Białystok')
        self.assertEqual(find_address('ul. Przemysłowa 4, 15-001 Białystok Białystok Tel. 123 Polska NIP 5250000000'), 'ul. Przemysłowa 4, 15-001 Białystok')
        self.assertEqual(find_address('Siedziba 33-300 Nowy Sącz, ul. Parkowa 2'), 'ul. Parkowa 2, 33-300 Nowy Sącz')
        self.assertEqual(find_address('Herausgeber: EGB Getriebe GmbH Am Klingenweg 9 65396 Walluf Vertreten durch'), 'Am Klingenweg 9, 65396 Walluf, Germany')
        self.assertEqual(find_address('Gutenbergstrasse 1 99869 Drei Gleichen OT Günthersleben'), 'Gutenbergstrasse 1, 99869 Drei Gleichen, Germany')
        self.assertEqual(find_address('Address Hermann-Blohm-Str. 3 20457 Hamburg GERMANY Telephone'), 'Hermann-Blohm-Str. 3, 20457 Hamburg, Germany')
        self.assertEqual(find_address('Kontakt Weisig Maschinenbau GmbH Am Frohberg 3 31061 Alfeld Tel.'), 'Am Frohberg 3, 31061 Alfeld, Germany')
        self.assertEqual(find_address('Contact Us 62 Dayton Ave. Xenia, Ohio 45385 (937)'), '62 Dayton Ave., Xenia, Ohio 45385, United States')
        self.assertEqual(find_address('Standort Hasieber Hydraulik GmbH Betriebspark Ehrenfeld 2 A-4694 Ohlsdorf Routenplaner'), 'Betriebspark Ehrenfeld 2, 4694 Ohlsdorf, Austria')
        self.assertEqual(find_address('Kontakt AXXERON Hydraulics GmbH Westfalen Südstraße 4 DE-32457 Porta Westfalica Sales'), 'Südstraße 4, 32457 Porta Westfalica, Germany')
        from geolocation import address_rank
        self.assertGreaterEqual(address_rank('Münsterstraße 33, 33428 Harsewinkel, Germany'), 4)
        self.assertEqual(find_address('İletişim Büyükkayacık OSB Mah. Evrenköy Cad. No:25 42280 Konya/Türkiye'), 'Büyükkayacık OSB Mah. Evrenköy Cad. No:25, 42280 Konya, Turkey')
        self.assertEqual(find_address('Our address 21 rue de Luxembourg, L-5752 Frisange'), '21 rue de Luxembourg, 5752 Frisange, Luxembourg')
        self.assertEqual(locate_query('Am Klingenweg 9, 65396 Walluf, Germany'), ('Am Klingenweg 9, 65396 Walluf, Germany', False))
        self.assertEqual(locate_query('Eisiskiu pl. 36, 02184, Vilnius, LT'), ('Eisiskiu pl. 36, 02184, Vilnius, Lithuania', False))
        self.assertEqual(extract('<p>Napisz Office@Example.COM</p>', 'https://firma.example')['contacts'][0]['email'], 'office@example.com')
        self.assertEqual(locate_query('15-001 Białystok'), ('Białystok, Polska', True))
        self.assertEqual(locate_query('Industriestraße 8-12, 78559 Gosheim, Německo'), ('Industriestraße 8-12, 78559 Gosheim, Germany', False))
        self.assertEqual(locate_query('Hauptstr. 2, 10115 Berlin, Deutschland'), ('Hauptstr. 2, 10115 Berlin, Germany', False))
        self.assertEqual(locate_query('391 65 Bechyně, Česká republika'), ('Bechyně, Czechia', True))
        self.assertEqual(locate_query('Na Libuši 891, 391 65 Bechyně, Česká republika'), ('Na Libuši 891, 391 65 Bechyně, Czechia', False))
        czech = locate_query(map_address('Varnsdorf, Karolíny Světlé 3018', 'CZ'))[0]
        self.assertIn('Czechia', czech)
        self.assertNotIn('Polska', czech)
        self.assertEqual(locate_query('Pühretstr. 3, 4661 Roitham, Österreich'), ('Pühretstr. 3, 4661 Roitham, Austria', False))
        self.assertEqual(locate_query('Hauptstr. 2, 10115 Berlin, Polen'), ('Hauptstr. 2, 10115 Berlin, Polska', False))
        self.assertEqual(locate_query('Berlin, Deutschland'), ('Berlin, Germany', True))
        self.assertEqual(locate_query('Praha, Česká republika'), ('Praha, Czechia', True))
        self.assertEqual(locate_query('Katowice'), ('Katowice, Polska', True))
        self.assertEqual(locate_query('Polska'), ('', False))
        self.assertEqual(locate_query('Deutschland'), ('', False))
        from geolocation import pin_query
        self.assertEqual(pin_query(dict(address='Hamburg', country='Deutschland'))[0], 'Hamburg, Germany')
        self.assertTrue(pin_query(dict(address='Hamburg', country='Deutschland'))[1])
        self.assertEqual(pin_query(dict(address='', locality='Praha', country='CZ'))[0], 'Praha, Czechia')
        self.assertEqual(pin_query(dict(address='Polska', source='https://een.ec.europa.eu/x')), ('', False))
        self.assertEqual(extract('<footer>Siedziba ul. Leśna 8, 40-001 Katowice</footer><p>oddział ul. Inna 1, 30-001 Kraków</p>', 'https://firma.example')['address'], 'ul. Leśna 8, 40-001 Katowice')

    def test_city_query_from_catalog_note(self):
        self.assertEqual(city_query('Białystok (woj. podlaskie)'), 'Białystok, podlaskie, Polska')
        self.assertEqual(city_query('Jasionka k/Rzeszowa (woj. podkarpackie)'), 'Jasionka, podkarpackie, Polska')
        self.assertEqual(city_query('ul. Długa 1, Katowice'), '')

    def test_exact_name_search_finds_site_and_address(self):
        html = '<html><head><title>AC S.A.</title></head><body><p>Siedziba: ul. Leśna 8, 40-001 Katowice</p></body></html>'
        with tempfile.TemporaryDirectory() as tmp, patch('core.search_web', return_value=[{'title': 'AC S.A.', 'url': 'https://ac.example'}]) as search, patch('core.Fetcher.page', return_value=(html.encode(), 'text/html', 'https://ac.example')), patch('geolocation.Locator.locate', return_value=dict(lat=50.26, lon=19.02, geo_precision='Adres — wymaga weryfikacji', geo_label='Katowice', geo_source='https://www.openstreetmap.org/node/2')):
            research = Research(tmp)
            research.records = [dict(id='1', name='AC S.A.', website='', address='', lat=None, lon=None, contacts=[], source='https://katalog.example/ac', status='Oczekuje', osm_text='', groups=['Do weryfikacji'], category='', evidence='')]
            research.config = dict(pages=1, enrich_web=True, categories=[])
            research.start_fill()
            research.worker.join(10)
            row = research.records[0]
            self.assertEqual(search.call_args.args[0], '"AC S.A."')
            self.assertEqual(search.call_args.args[2]['engine'], 'duckduckgo')
            self.assertEqual(row['website'], 'https://ac.example')
            self.assertEqual(row['address'], 'ul. Leśna 8, 40-001 Katowice')
            self.assertEqual(row['lat'], 50.26)

    def test_german_impressum_fills_street_address(self):
        home = b'<html><body>CLAAS Landmaschinen <a href="/produkte">Produkte</a></body></html>'
        impressum = b'<html><body>Impressum CLAAS KGaA mbH Anschrift Muensterstrasse 33 33428 Harsewinkel Deutschland Telefon</body></html>'
        def page(url, *args, **kwargs):
            if 'impressum' in url:
                return impressum, 'text/html', url
            return home, 'text/html', url
        with tempfile.TemporaryDirectory() as tmp, patch('core.Fetcher.page', side_effect=page), patch('geolocation.Locator.locate', return_value=dict(lat=51.96, lon=8.23, geo_precision='Adres — wymaga weryfikacji', geo_label='Harsewinkel', geo_source='https://www.openstreetmap.org/node/9')):
            research = Research(tmp)
            research.records = [dict(id='1', name='CLAAS', website='https://www.claas.de/', address='', lat=None, lon=None, contacts=[], source='https://www.claas.de/', status='Oczekuje', osm_text='', country='Deutschland', catalog='Internet — strony firm', groups=['Do weryfikacji'], category='', evidence='')]
            research.config = dict(pages=4, enrich_web=True, categories=[], countries=['DE'], geocode=True)
            research.start_fill()
            research.worker.join(10)
        row = research.records[0]
        self.assertIn('33428', row['address'])
        self.assertIn('Harsewinkel', row['address'])
        self.assertIn('Germany', row['address'])

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
        house={'geometry':{'coordinates':[13.395591,52.5133502]},'properties':{'type':'house','name':'Hausvogteiplatz','street':'Hausvogteiplatz','postcode':'10117','city':'Berlin','country':'Deutschland','countrycode':'DE','osm_type':'N','osm_id':9}}
        street=dict(house,geometry={'coordinates':[13.3962238,52.5131577]},properties=dict(house['properties'],type='street',name='',housenumber=''))
        placed=choose([house,street,street],'Hausvogteiplatz 10, 10117 Berlin, Germany')
        self.assertAlmostEqual(placed['lat'],52.5133502,places=4)
        self.assertIn('Przybliżenie',placed['geo_precision'])
        numbered={'geometry':{'coordinates':[13.8280254,48.0241226]},'properties':{'type':'house','street':'Pühretstraße','housenumber':'3','postcode':'4661','city':'Roitham am Traunfall','country':'Österreich','countrycode':'AT','osm_type':'N','osm_id':8}}
        exact=choose([numbered],'Pühretstr. 3, 4661 Roitham, Austria')
        self.assertAlmostEqual(exact['lat'],48.0241226,places=4)
        self.assertIn('Adres',exact['geo_precision'])
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

    def test_city_without_street_goes_on_the_map(self):
        with tempfile.TemporaryDirectory() as tmp, patch('geolocation.Locator.locate', return_value=dict(lat=53.55, lon=9.99, geo_precision='Przybliżenie')) as locate:
            research = Research(tmp)
            city = dict(id='1', name='Firma Hamburg', address='Hamburg', country='Deutschland', lat=None, lon=None, contacts=[], source='https://firma.de')
            country = dict(id='2', name='Oferta EEN', address='Polska', country='PL', lat=None, lon=None, contacts=[], source='https://een.ec.europa.eu/x')
            locality = dict(id='3', name='Firma Praha', address='', locality='Praha', country='CZ', lat=None, lon=None, contacts=[], source='https://www.europages.com/x')
            research.place_record(city)
            research.place_record(country)
            research.place_record(locality)
            self.assertEqual(city['lat'], 53.55)
            self.assertTrue(str(city['geo_precision']).startswith('Miasto'))
            self.assertIsNone(country['lat'])
            self.assertEqual(locality['lat'], 53.55)
            self.assertGreaterEqual(locate.call_count, 2)
