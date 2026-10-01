import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch, Mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from geolocation import Locator, choose
from core import Research, extract

F = {'geometry':{'coordinates':[18.6,49.7]},'properties':{'name':'Cieszyn','country':'Polska','countrycode':'PL','osm_type':'N','osm_id':123}}
class LocationTests(unittest.TestCase):
    def test_published_directions_coordinates(self):
        html = '<a href="https://www.google.com/maps/dir/?api=1&destination=51.052247,16.203842">Dojazd</a>'
        result = extract(html, 'https://directory.test/company')
        self.assertEqual(result['lat'], 51.052247)
        self.assertEqual(result['lon'], 16.203842)
        self.assertIsNone(extract(html + html.replace('51.052247','52.1'), 'https://directory.test/list')['lat'])

    def test_approximate_and_ambiguous(self):
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
