import sys,io,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from catalogs import parse_html,collect
from core import Research
from unittest.mock import patch
from pdf_export import export_pdf
from pypdf import PdfReader

class CatalogTests(unittest.TestCase):
 def test_pgm_scope(self):
  html='<div class="wpb_wrapper"><p>A. Producenci części</p><p><strong><a href="https://firma.pl">Firma SA</a></strong><em>Katowice (woj. śląskie)</em></p><p style="padding-left:40px">Producent zaworów</p><p>CZŁONKOWIE B. Jednostki Naukowe</p><p><a href="https://uni.pl">Uniwersytet</a><em>Warszawa</em></p></div>'
  rows=parse_html('pgm',html,'https://pgm.org.pl/czlonkowie/')
  self.assertEqual(len(rows),1);self.assertEqual(rows[0]['province'],'śląskie');self.assertIn('Producent zaworów',rows[0]['text'])
 def test_agro_country_and_detail(self):
  rows=parse_html('agrotech','<table><tr><td></td><td><div class="main-title"><a href="/agrotech-2026/lista-wystawcow/a,12">Firma A</a></div></td><td>Polska</td></tr></table>','https://www.targikielce.pl/agrotech-2026/lista-wystawcow')
  self.assertEqual(rows[0]['country'],'Polska');self.assertEqual(rows[0]['website'],'')
  self.assertIn('lista-wystawcow/a,12', rows[0]['source'])
 def test_agrotech_list_reads_following_pages(self):
  import json
  from catalogs import DIRECT_SOURCES
  html='<div data-vue-app="exhibitors-list" v-init:settings=\'{"searchUrl":"https://www.targikielce.pl/api/modules/exhibitors-list/search/1/2/pl","pager":{"total":2,"rowCount":30}}\' v-cloak></div><table><tr><td></td><td><div class="main-title"><a href="/agrotech-2026/lista-wystawcow/a,1">Firma A</a></div></td><td>Polska</td></tr></table>'
  page2=json.dumps({'view':'<table><tr><td></td><td><div class="main-title"><a href="/agrotech-2026/lista-wystawcow/b,2">Firma B</a></div></td><td>Polska</td></tr></table>'}).encode()
  calls=[]
  class Fetch:
   def check(self):
    pass
   def page(self, url):
    calls.append(url)
    if 'pageIndex=2' in url:
     return page2, 'application/json', url
    return html.encode(), 'text/html', DIRECT_SOURCES['agrotech'][1]
  rows=list(collect('agrotech', Fetch(), lambda message: None))
  self.assertEqual([row['name'] for row in rows], ['Firma A', 'Firma B'])
  self.assertEqual(DIRECT_SOURCES['agrotech'][1], 'https://www.targikielce.pl/agrotech-2026/lista-wystawcow')
  self.assertTrue(any('pageIndex=2' in url and url.startswith('https://www.targikielce.pl/api/modules/exhibitors-list/search/') for url in calls))
 def test_vdma_card_scoping(self):
  rows=parse_html('vdma','<div class="association-member"><p class="association-member__title">Firma A</p><a href="https://a.de">WWW</a><a href="mailto:a@example.com">Email</a><div class="association-member__info-address"><li>Berlin</li><li>Deutschland</li></div></div><footer>owner@example.com</footer>','https://www.vdma.eu/de/mitglieder')
  self.assertEqual(rows[0]['country'],'Deutschland');self.assertNotIn('owner@example.com',str(rows))
 def test_direct_independent_of_search(self):
  item=dict(name='Firma A',website='https://a.pl',source='https://pgm.org.pl/czlonkowie/#a',address='Katowice',country='Polska',province='śląskie',contacts=[],text='Producent zaworów',lat=None,lon=None)
  with tempfile.TemporaryDirectory() as tmp,patch('core.collect',return_value=iter([item])),patch('core.search_web',side_effect=AssertionError('No search engine')):
   r=Research(tmp);r.start(dict(categories=['valves'],countries=['PL'],sources=['pgm'],enrich_web=False));r.worker.join(10)
   self.assertEqual(len(r.records),1);self.assertEqual(len(r.discoveries),2);self.assertEqual(r.progress['queries_done'],0)
 def test_catalog_without_category_imports_all_and_and_narrows(self):
  items=[
   dict(name='Handlowa',website='',source='https://pgm.org.pl/a',address='',country='Polska',province='śląskie',contacts=[],text='Spółka handlowa',lat=None,lon=None),
   dict(name='Zawory',website='',source='https://pgm.org.pl/b',address='',country='Polska',province='śląskie',contacts=[],text='Producent zaworów',lat=None,lon=None),
   dict(name='Zawory i odkuwki',website='',source='https://pgm.org.pl/c',address='',country='Polska',province='śląskie',contacts=[],text='Producent zaworów, odbiorcy odkuwek',lat=None,lon=None),
  ]
  with tempfile.TemporaryDirectory() as tmp,patch('core.collect',side_effect=lambda *args,**kwargs: iter(items)),patch('core.search_web',side_effect=AssertionError('No search engine')):
   r=Research(tmp);r.start(dict(categories=[],recipients=[],countries=['PL'],sources=['pgm'],enrich_web=False));r.worker.join(10)
   self.assertEqual([row['name'] for row in r.records],['Handlowa','Zawory','Zawory i odkuwki'])
   r=Research(tmp);r.start(dict(categories=['valves'],recipients=[],countries=['PL'],sources=['pgm'],enrich_web=False));r.worker.join(10)
   self.assertEqual([row['name'] for row in r.records],['Zawory','Zawory i odkuwki'])
   r=Research(tmp);r.start(dict(categories=['valves'],recipients=['forgings'],countries=['PL'],sources=['pgm'],enrich_web=False));r.worker.join(10)
   self.assertEqual([row['name'] for row in r.records],['Zawory i odkuwki'])
 def test_filtered_pdf_only_ids(self):
  records=[dict(id='1',name='Kuźnia Śląska',groups=['Konkurencja'],contacts=[],lat=None),dict(id='2',name='NIEEKSPORTOWANA',groups=[],contacts=[],lat=None)]
  body=export_pdf(dict(records=records,run_id='test'),['1'],{'group':'Konkurencja'})
  text=''.join(p.extract_text() for p in PdfReader(io.BytesIO(body)).pages)
  self.assertIn('Kuźnia Śląska',text);self.assertNotIn('NIEEKSPORTOWANA',text)
