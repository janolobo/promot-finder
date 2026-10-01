import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from local_osm import LocalIndex, PROVINCES, qualify
from core import Research
import osmium


def fixture(root):
    lines=['<osm version="0.6">'];waylines=[];rellines=[]
    for i,name in enumerate(PROVINCES):
        x=14+i*.3;ids=[i*10+j+1 for j in range(4)]
        for nid,(lon,lat) in zip(ids,[(x,49),(x+.25,49),(x+.25,50),(x,50)]):
            lines.append(f'<node id="{nid}" lon="{lon}" lat="{lat}" version="1"/>')
        waylines.append(f'<way id="{i+1}" version="1">'+''.join(f'<nd ref="{n}"/>' for n in ids+[ids[0]])+'</way>')
        rellines.append(f'<relation id="{i+1}" version="1"><member type="way" ref="{i+1}" role="outer"/><tag k="name" v="województwo {name}"/><tag k="ISO3166-2" v="PL-{i:02}"/><tag k="admin_level" v="4"/><tag k="type" v="boundary"/></relation>')
    lines+=['<node id="1001" lat="49.5" lon="14.1" version="1"><tag k="name" v="Kuźnia Testowa"/><tag k="description" v="automotive"/><tag k="man_made" v="works"/></node>', '<node id="1002" lat="49.5" lon="14.4" version="1"><tag k="name" v="Producent maszyn CNC"/><tag k="man_made" v="works"/></node>']
    xml=root/'fixture.osm';xml.write_text('\n'.join(lines+waylines+rellines+['</osm>']))
    with osmium.SimpleWriter(str(root/'poland-latest.osm.pbf')) as w:
        for o in osmium.FileProcessor(str(xml)): w.add(o)

class LocalTests(unittest.TestCase):
    def test_build_cache_and_scope_without_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);fixture(root)
            idx=LocalIndex(root);idx.ensure(threading.Event(),lambda x:None)
            self.assertEqual(len(idx.data['companies']),2)
            self.assertEqual(idx.province(49.5,14.1),PROVINCES[0])
            self.assertEqual(idx.province(48,14),'')
            r=Research(root/'wyniki')
            with patch('requests.get',side_effect=AssertionError('offline operation')):
                r.start(dict(provinces=[PROVINCES[0]],profile='all',categories=['automotive'],sources=['osm'],max_firms=20,enrich_web=False))
                r.worker.join(10)
            self.assertFalse(r.running)
            self.assertEqual(len(r.records),1)
            self.assertEqual(r.records[0]['groups'],['Do weryfikacji'])
            r.start_analysis()
            r.worker.join(10)
            self.assertEqual(r.records[0]['groups'],['Konkurencja'])
            self.assertEqual(r.records[0]['province'],PROVINCES[0])
            self.assertIsNotNone(r.records[0]['lat'])
            self.assertEqual(r.progress['errors'],0)
            from openpyxl import load_workbook
            b=load_workbook(root/'wyniki'/r.run_id/'wyniki.xlsx')
            self.assertEqual(b['Konkurencja'].max_row,2)
            self.assertEqual(b['Odbiorcy odkuwek'].max_row,1)
    def test_qualification(self):
        self.assertEqual(qualify('Firma bez opisu')[0],['Do weryfikacji'])
        self.assertEqual(qualify('Kuźnia Testowa producent odkuwek')[0],['Konkurencja'])
        self.assertIn('Odbiorcy odkuwek',qualify('Producent zaworów przemysłowych')[0])
        self.assertIn('Kooperacja CNC',qualify('Usługi frezowania CNC')[0])
    def test_empty_regions_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError,'województwo'):
                Research(tmp).start({'provinces':[]})
