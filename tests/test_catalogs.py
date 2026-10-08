import sys,io,tempfile,threading,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from catalogs import ANALYSIS_CSV, PHRASE_CSV_FILES, ares_companies, ares_website, parse_html, collect, load_ares_phrases, load_europages_phrases, load_internet_phrases, load_osm_phrases, load_wlw_phrases, match_analysis_phrases, match_csv_phrases, phrase_csv_content, phrase_csv_rows, save_phrase_csv, save_phrase_rows, selected_ares_phrases, selected_europages_phrases, selected_wlw_phrases
from core import Research
from unittest.mock import patch
from pdf_export import export_pdf
from pypdf import PdfReader

class CatalogTests(unittest.TestCase):
 def test_wlw_csv_supplies_ranked_and_category_filtered_phrases(self):
  rows=load_wlw_phrases()
  self.assertEqual(len(rows),100)
  self.assertEqual(rows[0]['priority'],100)
  self.assertIn('Gelenkwellen', [row['phrase'] for row in rows])
  drives=selected_wlw_phrases(['drives'])
  self.assertTrue(drives)
  self.assertTrue(all(row['category']=='transmissions' for row in drives))
  self.assertTrue(all(drives[index]['priority']>=drives[index+1]['priority'] for index in range(len(drives)-1)))

 def test_europages_csv_supplies_ranked_and_category_filtered_phrases(self):
  rows=load_europages_phrases()
  self.assertEqual(len(rows),100)
  self.assertEqual(rows[0]['priority'],100)
  self.assertIn('Drive shafts', [row['phrase'] for row in rows])
  valves=selected_europages_phrases(['valves'])
  self.assertTrue(valves)
  self.assertTrue(all(row['category']=='hydraulics' for row in valves))

 def test_ares_csv_and_api_include_register_data_and_establishments(self):
  self.assertGreaterEqual(len(load_ares_phrases()),50)
  construction=selected_ares_phrases(['construction'])
  self.assertTrue(construction)
  self.assertTrue(all(row['category'] in {'mining','railway'} for row in construction))
  company={'ico':'02351854','obchodniJmeno':'Kovárna Polák s.r.o.','dic':'CZ02351854','sidlo':{'textovaAdresa':'K Vrtilce 23/31, Praha','nazevStatu':'Česká republika'},'czNace2008':['255'],'seznamRegistraci':{'stavZdrojeRzp':'AKTIVNI'}}
  detail={'zaznamy':[{'primarniZaznam':True,'zivnosti':[{'predmetPodnikani':'Kovářství','oboryCinnosti':[{'oborNazev':'Zpracování kovů'}],'provozovny':[{'icp':1016292392,'sidloProvozovny':{'textovaAdresa':'Dílna 1, Praha','nazevStatu':'Česká republika'},'typProvozovny':'1'}]}]}]}
  class Response:
   def __init__(self,data):self.data=data
   def raise_for_status(self):pass
   def json(self):return self.data
   def close(self):pass
  class Session:
   def request(self,method,url,**kwargs):
    return Response(detail if 'ekonomicke-subjekty-rzp/' in url else {'ekonomickeSubjekty':[company]})
  class Fetch:
   stop=threading.Event();last={};session=Session()
   def check(self):pass
  rows=list(ares_companies(Fetch(),lambda message:None,[dict(phrase='kovárna',negative_keywords=())]))
  self.assertEqual(len(rows),1)
  self.assertEqual(rows[0]['business_ids']['ico'],['02351854'])
  self.assertEqual(rows[0]['business_ids']['dic'],['CZ02351854'])
  self.assertEqual(rows[0]['business_ids']['nace'],['255'])
  self.assertEqual(rows[0]['establishments'][0]['icp'],'1016292392')
  self.assertIn('Dílna 1',rows[0]['establishments'][0]['address'])
  item=rows[0]
  with tempfile.TemporaryDirectory() as tmp,patch('core.collect',return_value=iter([item])),patch('geolocation.Locator.locate',return_value=None):
   research=Research(tmp);research.start(dict(countries=['CZ'],sources=['ares'],max_firms=5,enrich_web=False));research.worker.join(10)
  self.assertEqual(research.records[0]['business_ids']['ico'],['02351854'])
  self.assertEqual(research.records[0]['establishments'][0]['icp'],'1016292392')
  hits=[
   {'title':'Kovárna Polák s.r.o. 02351854','url':'https://www.podnikatel.cz/rejstrik/kovarna','body':'rejstřík'},
   {'title':'Důl Kovárna','url':'https://www.krkonose.eu/kovarna','body':'turistika'},
   {'title':'Kovárna Polák s.r.o.','url':'https://www.kovarnapolak.cz/','body':'kovářství'},
  ]
  with patch('catalogs._europages_rows',return_value=hits):
   self.assertEqual(ares_website('Kovárna Polák s.r.o.','02351854'),'https://www.kovarnapolak.cz/')

 def test_analysis_csv_files_have_ten_keywords(self):
  for kind, source in ANALYSIS_CSV.items():
   rows=phrase_csv_rows(source)
   self.assertEqual(len(rows),10,kind)
   self.assertTrue(all(row['phrase'].strip() for row in rows))
  self.assertIn('kuźnia',match_analysis_phrases('competition','Kuźnia Testowa producent odkuwek'))
  self.assertFalse(match_analysis_phrases('competition','Kuźnia handel sklep'))
  self.assertIn('CNC',match_analysis_phrases('cooperation','Usługi CNC i frezowanie'))
  self.assertIn('odbiorcy odkuwek',match_analysis_phrases('clients','Szukamy odbiorcy odkuwek'))

 def test_internet_and_osm_phrase_csvs_copy_producer_recipient_terms(self):
  web=load_internet_phrases()
  osm=load_osm_phrases()
  self.assertGreaterEqual(len(web),40)
  self.assertEqual([row['phrase'] for row in web],[row['phrase'] for row in osm])
  self.assertTrue(any(row['phrase']=='producent maszyn rolniczych' for row in web))
  self.assertTrue(any(row['category']=='forgings' for row in web))
  self.assertIn('automotive',match_csv_phrases('osm','Berliner Schmiede Gesenkschmiede automotive'))
  self.assertIn('zeměděl',match_csv_phrases('web','výrobce zemědělských strojů'))

 def test_phrase_csv_editor_validates_and_saves_atomically(self):
  content='phrase,category,priority,forging_probability,negative_keywords\nDrive shafts,transmissions,100,high,Dealer\n'
  with tempfile.TemporaryDirectory() as tmp, patch.dict(PHRASE_CSV_FILES, {'wlw':Path(tmp)/'phrases.csv'}):
   self.assertEqual(save_phrase_csv('wlw',content),1)
   self.assertEqual(phrase_csv_content('wlw'),content)
   rows=phrase_csv_rows('wlw')
   rows[0]['phrase']='Drive shafts, forged'
   self.assertEqual(save_phrase_rows('wlw',rows),1)
   self.assertEqual(phrase_csv_rows('wlw')[0]['phrase'],'Drive shafts, forged')
   with self.assertRaises(ValueError):
    save_phrase_csv('wlw',content.replace(',100,',',999,'))
  with self.assertRaises(ValueError):
   phrase_csv_content('../secret')

 def test_pgm_scope(self):
  html='<div class="wpb_wrapper"><p>A. Producenci części</p><p><strong><a href="https://firma.pl">Firma SA</a></strong><em>Katowice (woj. śląskie)</em></p><p style="padding-left:40px">Producent zaworów</p><p>CZŁONKOWIE B. Jednostki Naukowe</p><p><a href="https://uni.pl">Uniwersytet</a><em>Warszawa</em></p></div>'
  rows=parse_html('pgm',html,'https://pgm.org.pl/czlonkowie/')
  self.assertEqual(len(rows),1);self.assertEqual(rows[0]['province'],'śląskie');self.assertIn('Producent zaworów',rows[0]['text'])
 def test_vdma_card_scoping(self):
  rows=parse_html('vdma','<div class="association-member"><p class="association-member__title">Firma A</p><a href="https://a.de">WWW</a><a href="mailto:a@example.com">Email</a><div class="association-member__info-address"><li>Berlin</li><li>Deutschland</li></div></div><footer>owner@example.com</footer>','https://www.vdma.eu/de/mitglieder')
  self.assertEqual(rows[0]['country'],'Deutschland');self.assertNotIn('owner@example.com',str(rows))
 def test_vdma_list_reads_following_pages(self):
  import json
  from catalogs import DIRECT_SOURCES
  html='''<div class="association-member"><p class="association-member__title">Firma A</p><a href="https://a.de">WWW</a><div class="association-member__info-address"><ul><li>Ulica 1</li><li>00-001 Warszawa</li><li>Polska</li></ul></div></div>
<script>function loadPage(searchQuery, letters, currentPage) { $.ajax({ url: 'https://www.vdma.eu/pl/mitglieder?p_p_id=x&p_p_lifecycle=2&p_p_resource_id=getPage&p_p_cacheability=cacheLevelPage', data: { '_org_vdma_portlet_page': currentPage } }); }
var paginationInfo = { pageCount: 2 };</script>'''
  page2=json.dumps({'publicUserList': json.dumps({'content':[{'id':2,'companyName':'Firma B','webAddr':'b.de','address':'Hauptstr. 2','plz':'10115','city':'Berlin','country':'Deutschland','email':'Info@B.DE','phoneNum':'+49 30'}]})}).encode()
  calls=[]
  class Fetch:
   def check(self):
    pass
   def page(self, url):
    calls.append(url)
    if 'getPage' in url:
     return page2, 'application/json', url
    return html.encode(), 'text/html', DIRECT_SOURCES['vdma'][1]
  rows=list(collect('vdma', Fetch(), lambda message: None))
  self.assertEqual([row['name'] for row in rows], ['Firma A', 'Firma B'])
  self.assertEqual(rows[1]['address'], 'Hauptstr. 2, 10115 Berlin, Deutschland')
  self.assertEqual(rows[1]['contacts'][0]['email'], 'info@b.de')
  self.assertEqual(DIRECT_SOURCES['vdma'][1], 'https://www.vdma.eu/pl/mitglieder')
  self.assertTrue(any('getPage' in url and 'page=2' in url and url.startswith('https://www.vdma.eu/pl/mitglieder?') for url in calls))
 def test_direct_independent_of_search(self):
  item=dict(name='Firma A',website='https://a.pl',source='https://pgm.org.pl/czlonkowie/#a',address='Katowice',country='Polska',province='śląskie',contacts=[],text='Producent zaworów',lat=None,lon=None)
  point=dict(lat=50.26,lon=19.02,geo_precision='Miasto — przybliżenie, nie siedziba firmy',geo_label='Katowice',geo_source='https://www.openstreetmap.org/node/4')
  with tempfile.TemporaryDirectory() as tmp,patch('core.collect',return_value=iter([item])),patch('geolocation.Locator.locate',return_value=point),patch('core.search_web',side_effect=AssertionError('No search engine')):
   r=Research(tmp);r.start(dict(categories=['valves'],countries=['PL'],sources=['pgm'],enrich_web=False));r.worker.join(10)
   self.assertEqual(len(r.records),1);self.assertEqual(r.records[0]['lat'],50.26);self.assertEqual(len(r.discoveries),2);self.assertEqual(r.progress['queries_done'],0)
 def test_catalog_without_category_imports_all_and_and_narrows(self):
  items=[
   dict(name='Handlowa',website='',source='https://pgm.org.pl/a',address='',country='Polska',province='śląskie',contacts=[],text='Spółka handlowa',lat=None,lon=None),
   dict(name='Zawory',website='',source='https://pgm.org.pl/b',address='',country='Polska',province='śląskie',contacts=[],text='Producent zaworów',lat=None,lon=None),
   dict(name='Zawory i odkuwki',website='',source='https://pgm.org.pl/c',address='',country='Polska',province='śląskie',contacts=[],text='Producent zaworów, odbiorcy odkuwek',lat=None,lon=None),
  ]
  with tempfile.TemporaryDirectory() as tmp,patch('core.collect',side_effect=lambda *args,**kwargs: iter(items)),patch('core.search_web',side_effect=AssertionError('No search engine')):
   r=Research(tmp+'/all/out');r.start(dict(categories=[],recipients=[],countries=['PL'],sources=['pgm'],enrich_web=False));r.worker.join(10)
   self.assertEqual([row['name'] for row in r.records],['Handlowa','Zawory','Zawory i odkuwki'])
   r=Research(tmp+'/valves/out');r.start(dict(categories=['valves'],recipients=[],countries=['PL'],sources=['pgm'],enrich_web=False));r.worker.join(10)
   self.assertEqual([row['name'] for row in r.records],['Zawory','Zawory i odkuwki'])
   r=Research(tmp+'/both/out');r.start(dict(categories=['valves'],recipients=['forgings'],countries=['PL'],sources=['pgm'],enrich_web=False));r.worker.join(10)
   self.assertEqual([row['name'] for row in r.records],['Zawory i odkuwki'])
 def test_msv_category_11_reads_every_list_page(self):
  home='https://tikatalog.bvv.cz/'
  fair26='https://tikatalog.bvv.cz/msv-fond-ex/_fexc2&nom=0'
  stub='https://tikatalog.bvv.cz/msv/imt-obrabecich/_fexc2&nom=11'
  sub='https://tikatalog.bvv.cz/msv/imt-obrabecich/frezky/_fexc2&nom=1101'
  fair25='https://tikatalog.bvv.cz/msv/_fexc3&nom=0'
  category='https://tikatalog.bvv.cz/msv/obrabeci/_fexc3&nom=11'
  page2=category+'&pg=2'
  other='https://tikatalog.bvv.cz/msv/materialy/_fexc2&nom=2'
  future='https://tikatalog.bvv.cz/msv/_fexc9&nom=0'
  featured='https://tikatalog.bvv.cz/msv/featured/_fdc2&fir=1'
  hermle='https://tikatalog.bvv.cz/msv/hermle/_fdc2&fir=9'
  acme='https://tikatalog.bvv.cz/msv/acme/_fdc2&fir=8'
  pages={
   home:'<a href="msv/_fexc9&nom=0">MSV 2029</a><a href="msv-fond-ex/_fexc2&nom=0">MSV, FOND-EX 2026</a><a href="msv/_fexc3&nom=0">MSV 2025</a>',
   fair26:'<div>TESTOVACÍ VERZE</div><a href="/msv/imt-obrabecich/_fexc2&nom=11">11 IMT - stroje</a><a href="/msv/materialy/_fexc2&nom=2">02 Materialy</a>',
   stub:'<a href="/msv/imt-obrabecich/frezky/_fexc2&nom=1101">11.01 Frézky</a><a href="/msv/featured/_fdc2&fir=1">Featured</a>',
   fair25:'<a href="/msv/obrabeci/_fexc3&nom=11">11 Obráběcí stroje</a><a href="/msv/materialy/_fexc2&nom=2">02 Materialy</a>',
   category:'<p>1 - 1 z 2</p><a href="/msv/hermle/_fdc2&fir=9">Hermle AG</a><a href="'+page2+'">2</a>',
   page2:'<p>2 - 2 z 2</p><a href="/msv/acme/_fdc2&fir=8">ACME</a>',
   hermle:'<h1 class="company-detail-name">Hermle AG</h1><div class="profile-item"><div class="profile-label">Adresa:</div><div class="profile-value">Industriestraße 8-12<br>78559 Gosheim<br>Německo</div></div><div class="profile-item"><div class="profile-label">E-mail:</div><div class="profile-value"><a href="mailto:Info@Hermle.de">Info@Hermle.de</a></div></div><div class="profile-item"><div class="profile-label">Telefon:</div><div class="profile-value">+49-7426950</div></div><div class="profile-item"><div class="profile-label">WWW:</div><div class="profile-value"><a href="http://www.hermle.cz">www.hermle.cz</a></div></div><div class="company-block-without-padding">Profil Frézky i obrábění.</div>',
   acme:'<h1 class="company-detail-name">ACME s.r.o.</h1><div class="profile-item"><div class="profile-label">Adresa:</div><div class="profile-value">Na Libuši 891<br>391 65 Bechyně<br>Česká republika</div></div><div class="company-block-without-padding">Profil Obráběcí stroje.</div>',
  }
  calls=[]
  class Fetch:
   def check(self):
    pass
   def page(self, url):
    calls.append(url.split('#')[0])
    body=pages.get(url.split('#')[0])
    if body is None:raise ValueError(url)
    return body,'text/html',url.split('#')[0]
  rows=list(collect('bvv', Fetch(), lambda message: None))
  self.assertEqual([row['name'] for row in rows], ['Hermle AG', 'ACME s.r.o.'])
  self.assertEqual(rows[0]['address'], 'Industriestraße 8-12, 78559 Gosheim, Německo')
  self.assertEqual(rows[0]['website'], 'http://www.hermle.cz')
  self.assertEqual(rows[0]['contacts'][0]['email'], 'info@hermle.de')
  self.assertEqual(rows[1]['address'], 'Na Libuši 891, 391 65 Bechyně, Česká republika')
  self.assertIn(page2, calls)
  self.assertNotIn(sub, calls)
  self.assertNotIn(other, calls)
  self.assertNotIn(future, calls)
  self.assertNotIn(featured, calls)
  self.assertEqual(rows[0]['catalog'], 'BVV / MSV — kategoria 11')
 def test_vdma_import_puts_address_on_the_map(self):
  item=dict(name='Firma B',website='https://b.de',source='https://www.vdma.eu/pl/mitglieder#member-2',address='Hauptstr. 2, 10115 Berlin, Deutschland',country='Deutschland',province='',contacts=[],text='Firma B',lat=None,lon=None)
  point=dict(lat=52.53,lon=13.38,geo_precision='Adres — wymaga weryfikacji',geo_label='Berlin',geo_source='https://www.openstreetmap.org/node/3')
  with tempfile.TemporaryDirectory() as tmp,patch('core.collect',return_value=iter([item])),patch('geolocation.Locator.locate',return_value=point) as locate,patch('core.search_web',side_effect=AssertionError('No search engine')):
   r=Research(tmp);r.start(dict(categories=['valves'],countries=['PL'],sources=['vdma'],enrich_web=False));r.worker.join(10)
   self.assertEqual(len(r.records),1)
   self.assertEqual(r.records[0]['lat'], 52.53)
   self.assertEqual(r.records[0]['lon'], 13.38)
   self.assertEqual(locate.call_args.args[-1], 'Hauptstr. 2, 10115 Berlin, Germany')
 def test_msv_import_keeps_foreign_address_on_the_map(self):
  item=dict(name='Hermle AG',website='http://www.hermle.cz',source='https://tikatalog.bvv.cz/msv/hermle/_fdc2&fir=9',address='Industriestraße 8-12, 78559 Gosheim, Německo',country='Německo',province='',contacts=[],text='Frézky',lat=None,lon=None)
  point=dict(lat=48.13,lon=8.75,geo_precision='Adres — wymaga weryfikacji',geo_label='Gosheim',geo_source='https://www.openstreetmap.org/node/1')
  with tempfile.TemporaryDirectory() as tmp,patch('core.collect',return_value=iter([item])),patch('geolocation.Locator.locate',return_value=point),patch('core.search_web',side_effect=AssertionError('No search engine')):
   r=Research(tmp);r.start(dict(categories=['valves'],countries=['PL'],sources=['bvv'],enrich_web=False));r.worker.join(10)
   self.assertEqual(len(r.records),1)
   self.assertEqual(r.records[0]['address'], item['address'])
   self.assertEqual(r.records[0]['lat'], 48.13)
   self.assertEqual(r.records[0]['lon'], 8.75)
 def test_europages_phrases_stay_leads_until_fill(self):
  from catalogs import EUROPAGES_PHRASES, europages_address, europages_company
  self.assertIsNone(europages_company('lista', 'https://www.europages.co.uk/companies/hydraulic-components.html'))
  name, profile = europages_company('Drive Shaft', 'https://www.europages.co.uk/en/company/herbert-metzendorff-co-kg-22157852/products/drive-shaft-1')
  self.assertEqual(name, 'Herbert Metzendorff Co KG')
  self.assertEqual(profile, 'https://www.europages.co.uk/en/company/herbert-metzendorff-co-kg-22157852')
  self.assertEqual(europages_company('HYDRACOM GMBH in Erkrath', 'https://www.europages.co.uk/HYDRACOM-GMBH/00000005394073-001.html')[0], 'HYDRACOM GMBH')
  self.assertEqual(europages_company('Flowmac Makina in Izmir on europages', 'https://www.europages.co.uk/en/company/flowmac-makina-hidrolik-sistemleri-22306898')[0], 'Flowmac Makina')
  self.assertIsNone(europages_company('SP. Z O.O. in Toruń on europages', 'https://www.europages.co.uk/en/company/sp-z-oo-22328268'))
  self.assertEqual(europages_address('Location. GermanyHauptstrasse 52, Eisenbach 79871. Contact'), 'Hauptstrasse 52, Eisenbach 79871, Germany')
  from catalogs import europages_locality, europages_website
  self.assertEqual(europages_locality('Weisig Maschinenbau GmbH in Alfeld on europages'), 'Alfeld')
  site_rows=[{'title':'katalog','url':'https://www.europages.co.uk/en/company/weisig-maschinenbau-gmbh-293561','body':''},{'title':'wizytowka','url':'https://web2.cylex.de/firma-home/weisig','body':''},{'title':'social','url':'https://x.com/hakanchelik','body':''},{'title':'firma','url':'https://www.weiro.de/de/unternehmen/','body':''}]
  with patch('catalogs._europages_rows', return_value=site_rows):
   self.assertEqual(europages_website('Weisig Maschinenbau GmbH', 'Alfeld'), 'https://www.weiro.de/de/unternehmen/')
   self.assertEqual(europages_website('Weisig Maschinenbau GmbH', 'Alfeld', ('weiro.de',)), '')
  calls=[]
  def search(query, config):
   calls.append(query)
   if 'Axle components' in query:
    raise ValueError('DuckDuckGo nie dostarczył poprawnej odpowiedzi dla frazy Europages')
   if 'Gearbox manufacturers' in query:
    return [dict(title='Drive Shaft', url='https://www.europages.co.uk/en/company/herbert-metzendorff-co-kg-22157852/products/drive-shaft-1', body='Location. GermanyHauptstrasse 52, Eisenbach 79871. Contact'), dict(title='lista', url='https://www.europages.co.uk/companies/gear-manufacturer.html', body='')]
   return []
  with tempfile.TemporaryDirectory() as tmp, patch('core.europages_search', side_effect=search), patch('geolocation.Locator.locate', return_value=None):
   r=Research(tmp);r.start(dict(categories=[],countries=['PL'],sources=['europages'],enrich_web=False));r.worker.join(15)
   self.assertEqual(len(r.records), 1)
   self.assertEqual(r.records[0]['name'], 'Herbert Metzendorff Co KG')
   self.assertEqual(r.records[0]['source'], profile)
   self.assertIn('Hauptstrasse', r.records[0]['address'])
   self.assertEqual(r.records[0]['website'], '')
   self.assertEqual(len(calls), len(EUROPAGES_PHRASES))
   html='<html><head><title>Hydracom</title></head><body><p>Siedziba: ul. Leśna 8, 40-001 Katowice</p></body></html>'
   fetched=[]
   def page(self, url):
    fetched.append(url)
    if 'europages' in url: raise AssertionError(url)
    return html.encode(), 'text/html', url
   point=dict(lat=50.26,lon=19.02,geo_precision='Adres — wymaga weryfikacji',geo_label='Katowice',geo_source='https://www.openstreetmap.org/node/2')
   r.records[0]['name'] = 'Tytuł strony zamiast nazwy firmy'
   r.records[0].pop('catalog_name', None)
   with patch('core.Fetcher.page', page), patch('catalogs.europages_website', return_value='https://hydracom.example'), patch('geolocation.Locator.locate', return_value=point):
    r.start_fill(dict(enrich_web=True, pages=1));r.worker.join(15)
   self.assertEqual(len(r.records), 1)
   self.assertEqual(r.records[0]['name'], 'Herbert Metzendorff Co KG')
   self.assertEqual(r.records[0]['website'], 'https://hydracom.example')
   self.assertIn('Leśna', r.records[0]['address'])
   self.assertEqual(r.records[0]['lat'], 50.26)
   self.assertEqual(r.records[0]['source'], profile)
   self.assertEqual(r.records[0]['status'], 'Dane uzupełnione — oczekuje na analizę')
   self.assertTrue(fetched)
   self.assertTrue(all('europages' not in url for url in fetched))
 def test_wlw_uses_public_index_without_fetching_catalog(self):
  from catalogs import DIRECT_SOURCES, WLW_PHRASES, wlw_address, wlw_company
  title='Pulsgetriebe GmbH & Co. KG | Getriebebau in Karlsruhe'
  profile='https://www.wlw.de/de/firma/pulsgetriebe-gmbh-co-kg-349446'
  self.assertEqual(wlw_company(title, profile), ('Pulsgetriebe GmbH & Co. KG', profile))
  self.assertIsNone(wlw_company('Lista', 'https://www.wlw.de/de/suche?q=getriebe'))
  self.assertEqual(wlw_address('Pulsgetriebe GmbH Am Heegwald 18, Karlsruhe 76227 Hersteller/Fabrikant'), 'Am Heegwald 18, 76227 Karlsruhe, Germany')
  calls=[]
  def search(query, config):
   calls.append(query)
   if 'Getriebehersteller' in query:
    return [dict(title=title,url=profile,body='Pulsgetriebe GmbH & Co. KG Am Heegwald 18, Karlsruhe 76227 Hersteller/Fabrikant')]
   return []
  with tempfile.TemporaryDirectory() as tmp, patch('core.europages_search', side_effect=search), patch('geolocation.Locator.locate', return_value=None):
   r=Research(tmp);r.start(dict(categories=[],countries=['PL'],sources=['wlw'],enrich_web=False));r.worker.join(15)
  self.assertEqual(DIRECT_SOURCES['wlw'][0], 'WLW / Wer liefert was')
  self.assertEqual(len(calls), len(WLW_PHRASES))
  self.assertEqual(len(r.records), 1)
  self.assertEqual(r.records[0]['name'], 'Pulsgetriebe GmbH & Co. KG')
  self.assertEqual(r.records[0]['address'], 'Am Heegwald 18, 76227 Karlsruhe, Germany')
  self.assertEqual(r.records[0]['website'], '')
  self.assertTrue(all('site:wlw.de/de/firma' in query for query in calls))
  self.assertTrue(any('-"Händler"' in query for query in calls))
 def test_industrystock_uses_public_index_and_proposed_csv(self):
  from catalogs import DIRECT_SOURCES, INDUSTRYSTOCK_PHRASES, industrystock_address, industrystock_company, load_industrystock_phrases
  title='Products and Services of Viro Schmiedeteile GmbH'
  profile='https://www.industrystock.com/en/company/profile/Viro-Schmiedeteile-GmbH/12345'
  self.assertEqual(industrystock_company(title, profile), ('Viro Schmiedeteile GmbH', profile))
  self.assertIsNone(industrystock_company('Die Forging', 'https://www.industrystock.com/en/companies/Manufacturing-Method/Forging-Technology/Die-Forging'))
  self.assertEqual(industrystock_address('Viro Schmiedeteile GmbH 36124 Eichenzell, Germany'), 'Eichenzell, 36124, Germany')
  self.assertEqual(industrystock_address('Sosaer Str. 3901257 Dresden, Germany'), 'Sosaer Str. 39, 01257 Dresden, Germany')
  rows=load_industrystock_phrases()
  self.assertGreaterEqual(len(rows),40)
  self.assertIn('Die Forging', INDUSTRYSTOCK_PHRASES)
  self.assertIn('Schmiedeteile', INDUSTRYSTOCK_PHRASES)
  calls=[]
  def search(query, config):
   calls.append(query)
   if 'Die Forging' in query:
    return [dict(title=title,url=profile,body='Viro Schmiedeteile GmbH 36124 Eichenzell, Germany Manufacturer')]
   return []
  with tempfile.TemporaryDirectory() as tmp, patch('core.europages_search', side_effect=search), patch('geolocation.Locator.locate', return_value=None):
   r=Research(tmp);r.start(dict(categories=[],countries=['DE'],sources=['industrystock'],enrich_web=False));r.worker.join(15)
  self.assertEqual(DIRECT_SOURCES['industrystock'][0], 'IndustryStock')
  self.assertEqual(len(calls), len(INDUSTRYSTOCK_PHRASES))
  self.assertEqual(len(r.records), 1)
  self.assertEqual(r.records[0]['name'], 'Viro Schmiedeteile GmbH')
  self.assertEqual(r.records[0]['address'], 'Eichenzell, 36124, Germany')
  self.assertEqual(r.records[0]['website'], '')
  self.assertTrue(all('site:industrystock.com/en/company/profile' in query for query in calls))
 def test_hannovermesse_uses_public_index_and_csv(self):
  from catalogs import DIRECT_SOURCES, HANNOVERMESSE_PHRASES, hannovermesse_address, hannovermesse_company, load_hannovermesse_phrases
  title='HANNOVER MESSE Aussteller 2026: Patel Brass Turnomatics'
  profile='https://www.hannovermesse.de/aussteller/patel-brass-turnomatics/N1607477'
  self.assertEqual(hannovermesse_company(title, profile), ('Patel Brass Turnomatics', profile))
  self.assertIsNone(hannovermesse_company('Zylinderrollen', 'https://www.hannovermesse.de/produkt/zylinderrollen/515011/N1605504'))
  self.assertEqual(hannovermesse_address('Mergenthalerstr. 24 48268 Greven Deutschland'), 'Mergenthalerstr. 24, 48268 Greven, Germany')
  rows=load_hannovermesse_phrases()
  self.assertGreaterEqual(len(rows),40)
  self.assertIn('Gesenkschmiedeteile', HANNOVERMESSE_PHRASES)
  calls=[]
  def search(query, config):
   calls.append(query)
   if 'Gesenkschmiedeteile' in query:
    return [dict(title=title,url=profile,body='Patel Brass Turnomatics Mergenthalerstr. 24 48268 Greven Deutschland')]
   return []
  with tempfile.TemporaryDirectory() as tmp, patch('core.europages_search', side_effect=search), patch('geolocation.Locator.locate', return_value=None):
   r=Research(tmp);r.start(dict(categories=[],countries=['DE'],sources=['hannovermesse'],enrich_web=False));r.worker.join(15)
  self.assertEqual(DIRECT_SOURCES['hannovermesse'][0], 'Hannover Messe')
  self.assertEqual(len(calls), len(HANNOVERMESSE_PHRASES))
  self.assertEqual(len(r.records), 1)
  self.assertEqual(r.records[0]['name'], 'Patel Brass Turnomatics')
  self.assertIn('Greven', r.records[0]['address'])
  self.assertEqual(r.records[0]['website'], '')
  self.assertTrue(all('site:hannovermesse.de/aussteller' in query for query in calls))
 def test_een_catalog_keeps_the_publication_date(self):
  from catalogs import een_cards, een_company_name, een_legal_name, een_matches_countries, een_page_count, een_published, load_een_phrases, selected_een_phrases
  self.assertEqual(een_published('BOLT20261005005'), '2026-10-05')
  self.assertEqual(een_published('RDRTR20261005025'), '2026-10-05')
  self.assertEqual(een_published('brak'), '')
  phrases=[row['phrase'] for row in load_een_phrases()]
  self.assertEqual(phrases[0], 'shaft')
  self.assertEqual(phrases[-1], 'machining')
  self.assertEqual(set(phrases), {'forging','forged','machining','shaft','axle','gearbox','transmission','hydraulic','railway','agricultural machinery','construction machinery'})
  self.assertEqual([row['phrase'] for row in selected_een_phrases(['valves'])], phrases)
  card='''<h2>Partnering opportunities (2)</h2>
  <article class="ecl-card"><ul><li class="ecl-content-block__primary-meta-item">Business Offer</li><li class="ecl-content-block__primary-meta-item">BOLT20261005005</li></ul>
  <div class="ecl-content-block__title"><a href="/partnering-opportunities/lithuanian-forging">Lithuanian forging offer</a></div>
  <div class="ecl-content-block__description"><p>Closed-die forgings for axles.</p></div>
  <ul><li class="ecl-content-block__secondary-meta-item">2 days ago</li><li class="ecl-content-block__secondary-meta-item">Lithuania</li></ul></article>
  <li class="ecl-pagination__item--last"><a href="?page=1">2</a></li>'''
  second='''<article class="ecl-card"><ul><li class="ecl-content-block__primary-meta-item">Technology request</li><li class="ecl-content-block__primary-meta-item">TRDE20260315002</li></ul>
  <div class="ecl-content-block__title"><a href="/partnering-opportunities/german-gearbox">German gearbox request</a></div>
  <div class="ecl-content-block__description"><p>Gearbox machining partner.</p></div>
  <ul><li class="ecl-content-block__secondary-meta-item">1 month ago</li><li class="ecl-content-block__secondary-meta-item">Germany</li></ul></article>'''
  parsed=een_cards(card)
  self.assertEqual(parsed[0]['published_at'], '2026-10-05')
  self.assertEqual(parsed[0]['address'], 'Lithuania')
  self.assertEqual(parsed[0]['announcement'], 'Lithuanian forging offer')
  self.assertEqual(parsed[0]['name'], '')
  self.assertEqual(parsed[0]['source'], 'https://een.ec.europa.eu/partnering-opportunities/lithuanian-forging')
  self.assertIn('Closed-die', parsed[0]['text'])
  self.assertEqual(een_page_count(card), 2)
  self.assertEqual(een_legal_name('A Lithuanian company offers forging.'), '')
  self.assertEqual(een_company_name({'Full Description': 'Vilniaus Kalvė UAB forges axles in Vilnius.'}), 'Vilniaus Kalvė UAB')
  self.assertTrue(een_matches_countries('Poland', ['PL']))
  self.assertTrue(een_matches_countries('Germany', ['DE']))
  self.assertTrue(een_matches_countries('Czechia', ['CZ']))
  self.assertTrue(een_matches_countries('Česko', ['PL', 'CZ']))
  self.assertFalse(een_matches_countries('Lithuania', ['PL', 'DE', 'CZ']))
  self.assertFalse(een_matches_countries('Germany', []))
  calls=[]
  def fake_fetch(fetch, url):
   calls.append(url)
   return (second if 'page=1' in url else card).encode()
  with tempfile.TemporaryDirectory() as tmp, patch('catalogs.een_fetch', side_effect=fake_fetch), patch('core.selected_een_phrases', return_value=[dict(phrase='forging')]), patch('geolocation.Locator.locate', return_value=None):
   research=Research(tmp)
   research.start(dict(categories=[], countries=['DE'], sources=['een'], enrich_web=False, max_firms=10))
   research.worker.join(15)
  self.assertFalse(research.running)
  self.assertEqual(calls, [
   'https://een.ec.europa.eu/partnering-opportunities?f%5B0%5D=k%3Aforging',
   'https://een.ec.europa.eu/partnering-opportunities?f%5B0%5D=k%3Aforging&page=1'])
  self.assertEqual(len(research.records), 1)
  self.assertEqual(research.records[0]['catalog'], 'Enterprise Europe Network')
  self.assertEqual(research.records[0]['name'], '')
  self.assertIn('German gearbox request', research.records[0]['osm_text'])
  self.assertNotIn('Lithuanian', research.records[0]['osm_text'])
  self.assertEqual(research.records[0]['published_at'], '2026-03-15')
  self.assertEqual(research.records[0]['address'], 'Germany')
  self.assertEqual(research.records[0]['source'], 'https://een.ec.europa.eu/partnering-opportunities/german-gearbox')
  self.assertIsNone(research.records[0]['lat'])
  self.assertIn('een:DE:forging', research.progress['search_resume']['phrases'])
 def test_een_timeout_continues_with_the_next_phrase(self):
  import requests
  german='''<article class="ecl-card"><ul><li class="ecl-content-block__primary-meta-item">Technology request</li><li class="ecl-content-block__primary-meta-item">TRDE20260315002</li></ul>
  <div class="ecl-content-block__title"><a href="/partnering-opportunities/german-gearbox">German gearbox request</a></div>
  <div class="ecl-content-block__description"><p>Gearbox machining partner.</p></div>
  <ul><li class="ecl-content-block__secondary-meta-item">1 month ago</li><li class="ecl-content-block__secondary-meta-item">Germany</li></ul></article>'''
  def fake_fetch(fetch, url):
   if 'k%3Aslow' in url or 'k:slow' in url:
    raise requests.ReadTimeout('timed out')
   return german.encode()
  with tempfile.TemporaryDirectory() as tmp, patch('catalogs.een_fetch', side_effect=fake_fetch), patch('core.selected_een_phrases', return_value=[dict(phrase='slow'), dict(phrase='forging')]), patch('geolocation.Locator.locate', return_value=None):
   research=Research(tmp)
   research.start(dict(categories=[], countries=['DE'], sources=['een'], enrich_web=False, max_firms=10))
   research.worker.join(15)
  self.assertFalse(research.running)
  self.assertEqual([record['source'] for record in research.records], ['https://een.ec.europa.eu/partnering-opportunities/german-gearbox'])
  phrases=research.progress['search_resume']['phrases']
  self.assertIn('een:DE:forging', phrases)
  self.assertNotIn('een:DE:slow', phrases)
  self.assertNotIn('een', research.progress['search_resume']['completed_catalogs'])
  self.assertTrue(any('slow' in line and 'ReadTimeout' in line for line in research.logs))
 def test_techpilot_keeps_requests_matching_the_csv_technologies(self):
  from datetime import date
  from catalogs import load_announcement_phrases, techpilot_cards, techpilot_deadline, techpilot_pages
  phrases=[row['phrase'] for row in load_announcement_phrases('techpilot')]
  self.assertEqual(phrases[0], 'Gesenkschmieden')
  self.assertIn('Fräsen', phrases)
  self.assertIn('Verzahnen', phrases)
  self.assertEqual(techpilot_deadline('noch 22 Tage', date(2026,10,6)), '2026-10-28')
  self.assertEqual(techpilot_deadline('noch 15 Stunden', date(2026,10,6)), '2026-10-06')
  self.assertEqual(techpilot_deadline('bez terminu'), '')
  sitemap='<urlset><url><loc>https://www.techpilot.com/de/auftrag/fraesen</loc></url><url><loc>https://www.techpilot.com/de/auftrag/sitemap.xml</loc></url></urlset>'
  self.assertEqual(techpilot_pages(sitemap), ['https://www.techpilot.com/de/auftrag/fraesen'])
  page='''<div class="border-t-2"><p>Technologien: Gesenkschmieden, Fräsen</p><p>noch 1 Tag</p><p>18 Stk.</p></div>
  <div class="border-t-2"><p>Technologien: Siebdruck</p><p>noch 3 Tage</p><p>5 Stk.</p></div>'''
  cards=techpilot_cards(page, 'https://www.techpilot.com/de/auftrag/fraesen', date(2026,10,6))
  self.assertEqual(len(cards), 2)
  self.assertEqual(cards[0]['announcement'], 'Gesenkschmieden, Fräsen')
  self.assertEqual(cards[0]['name'], '')
  self.assertEqual(cards[0]['published_at'], '2026-10-07')
  self.assertIn('18 Stk.', cards[0]['text'])
  self.assertTrue(cards[0]['source'].startswith('https://www.techpilot.com/de/auftrag/fraesen#anfrage-'))
  self.assertNotEqual(cards[0]['source'], cards[1]['source'])
  def fake_page(self, url):
   return ((sitemap if url.endswith('sitemap.xml') else page).encode(), 'text/html', url)
  with tempfile.TemporaryDirectory() as tmp, patch('core.Fetcher.page', fake_page), patch('geolocation.Locator.locate', return_value=None):
   research=Research(tmp)
   research.start(dict(categories=[], countries=['PL'], sources=['techpilot'], enrich_web=False, max_firms=10))
   research.worker.join(15)
  self.assertFalse(research.running)
  self.assertEqual(len(research.records), 1)
  self.assertEqual(research.records[0]['catalog'], 'Techpilot — zapytania ofertowe')
  self.assertIn('Gesenkschmieden', research.records[0]['osm_text'])
  self.assertNotIn('techpilot', research.progress['search_resume']['completed_catalogs'])
 def test_gated_services_turn_csv_keywords_into_leads(self):
  from catalogs import load_announcement_phrases
  ariba=[row['phrase'] for row in load_announcement_phrases('ariba')]
  supplyon=[row['phrase'] for row in load_announcement_phrases('supplyon')]
  self.assertEqual(ariba[0], 'Forgings')
  self.assertEqual(supplyon[0], 'Forged components')
  self.assertIn('Railway axles', ariba)
  self.assertIn('Gearbox components', supplyon)
  with tempfile.TemporaryDirectory() as tmp, patch('geolocation.Locator.locate', return_value=None):
   research=Research(tmp)
   research.start(dict(categories=[], countries=['PL'], sources=['ariba'], enrich_web=False, max_firms=10))
   research.worker.join(15)
  self.assertFalse(research.running)
  self.assertEqual(research.records, [])
  leads=[item for item in research.discoveries if item['category']=='Hasło do wyszukania w serwisie']
  self.assertEqual(len(leads), len(ariba))
  self.assertEqual(leads[0]['url'], 'https://discovery.ariba.com/')
  self.assertIn('zaloguj się', leads[0]['status'])
  self.assertTrue(any('robots.txt' in line for line in research.logs))
 def test_filtered_pdf_only_ids(self):
  records=[dict(id='1',name='Kuźnia Śląska',groups=['Konkurencja'],contacts=[],lat=None),dict(id='2',name='NIEEKSPORTOWANA',groups=[],contacts=[],lat=None)]
  body=export_pdf(dict(records=records,run_id='test'),['1'],{'group':'Konkurencja'})
  text=''.join(p.extract_text() for p in PdfReader(io.BytesIO(body)).pages)
  self.assertIn('Kuźnia Śląska',text);self.assertNotIn('NIEEKSPORTOWANA',text)
