"""Direct public directories. No search engine, credentials or provider fallback."""
import csv
import hashlib
import io
import json
import re
import time
import unicodedata
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urljoin, urlparse, urlencode
from bs4 import BeautifulSoup

DIRECT_SOURCES = {
 'pgm':('PGM — członkowie','https://pgm.org.pl/czlonkowie/'),
 'bvv':('BVV / MSV — kategoria 11','https://tikatalog.bvv.cz/'),
 'vdma':('VDMA — lista członków','https://www.vdma.eu/pl/mitglieder'),
 'cetop':('CETOP — katalog PDF','https://www.cetop.org/directory/'),
 'ares':('ARES — czeski rejestr firm','https://ares.gov.cz/ekonomicke-subjekty'),
 'europages':('Europages','https://www.europages.co.uk/'),
 'wlw':('WLW / Wer liefert was','https://www.wlw.de/'),
 'industrystock':('IndustryStock','https://www.industrystock.com/'),
 'hannovermesse':('Hannover Messe','https://www.hannovermesse.de/'),
 'kompass':('Kompass — załaduj CSV z firmami','https://pl.kompass.com/'),
 'een':('Enterprise Europe Network','https://een.ec.europa.eu/partnering-opportunities'),
 'techpilot':('Techpilot — zapytania ofertowe','https://www.techpilot.com/de/auftrag'),
 'ariba':('SAP Business Network Discovery','https://discovery.ariba.com/'),
 'supplyon':('SupplyOn','https://www.supplyon.com/en/for-suppliers/')}


def entry(name,website,source,fragment='',address='',country='',province=''):
 from core import extract
 parsed=extract(fragment,source)
 return dict(name=name.strip(),website=website if website.startswith(('http://','https://')) else '',source=source,address=address or parsed['address'],country=country,province=province,contacts=parsed['contacts'],text=parsed['text'],lat=parsed['lat'],lon=parsed['lon'])


def parse_html(key,body,url):
 from core import company_list,domain,DENIED,is_domain
 from local_osm import PROVINCES
 soup=BeautifulSoup(body,'html.parser');rows=[]
 if key=='pgm':
  active=False
  for p in soup.select('.wpb_wrapper > p'):
   t=p.get_text(' ',strip=True)
   if 'A. Producenci części' in t:active=True;continue
   if active and re.search(r'B\.\s*Jednostki',t):break
   if not active or not p.select_one('em'):continue
   a=p.select_one('a[href]')
   if not a or not a.get_text(strip=True):continue
   address=p.select_one('em').get_text(' ',strip=True)
   desc=p.find_next_sibling('p');fragment=str(p)+(str(desc) if desc and 'padding-left' in desc.get('style','') else '')
   regions=[x for x in PROVINCES if re.search(r'(?<![\w-])'+re.escape(x)+r'(?![\w-])',address)]
   province=regions[0] if len(regions)==1 else ''
   website=a['href'] if not is_domain(domain(a['href']),DENIED) else ''
   rows.append(entry(a.get_text(' ',strip=True),website,url+'#'+re.sub(r'\W+','-',a.get_text(strip=True)),fragment,address,'Polska' if province else '',province))
 elif key=='vdma':
  for card in soup.select('.association-member'):
   name=card.select_one('.association-member__title')
   if not name:continue
   links=[a['href'] for a in card.select('a[href]') if a['href'].startswith(('http://','https://'))]
   ad=card.select('.association-member__info-address li');address=', '.join(a.get_text(' ',strip=True) for a in ad)
   rows.append(entry(name.get_text(' ',strip=True),links[0] if links else '',url+'#'+re.sub(r'\W+','-',name.get_text(strip=True)),str(card),address,ad[-1].get_text(strip=True) if ad else ''))
 elif key=='bvv':
  seen=set()
  for a in soup.select('a[href*="_fdc"]'):
   u=urljoin(url,a['href']);name=a.get_text(' ',strip=True)
   if not name or u in seen:continue
   seen.add(u)
   rows.append(entry(name,'',u,str(a.parent or a)))
 else:
  for r in company_list(body,url):rows.append(entry(r['name'],r['target'] if domain(r['target'])!=domain(url) else '',r['target'],address=r['parsed']['address']))
  # Public firm detail links only; navigation and advertisements are not firms.
  pattern=r'/c/[^/]+/[a-z]{2}\d+' if key=='kompass' else r'/[^/]+/(?:000000|\d{6})'
  seen={r['source'] for r in rows}
  for a in soup.select('a[href]'):
   u=urljoin(url,a['href']);name=a.get_text(' ',strip=True)
   if u not in seen and re.search(pattern,u) and 3<len(name)<180:
    seen.add(u);rows.append(entry(name,'',u,str(a.parent)))
 return rows


def parse_cetop(body,url,check=lambda:None):
 from pypdf import PdfReader
 rows=[];seen=set();country=''
 for number,page in enumerate(PdfReader(io.BytesIO(body)).pages,1):
  check();text=page.extract_text() or ''
  if 'DIRECTORY BY COMPANIES AND PRODUCTS' not in text:continue
  for line in text.splitlines():
   header=re.match(r'^([A-Z][A-Z ]+)\s*[·•]\s*',line)
   if header:country=header.group(1).strip();continue
   m=re.match(r'^(.{2,140}?)\s+((?:www\.)?[a-zA-Z0-9][\w.-]+\.[a-zA-Z]{2,}(?:/\S*)?)\s+[•●]',line)
   if not m:continue
   name,web=m.groups();ident=(country,web.lower().removeprefix('www.'))
   if ident in seen:continue
   seen.add(ident);r=entry(name,'https://'+web,url+'#page='+str(number),country=country)
   r['text']='Katalog hydrauliki i pneumatyki CETOP: '+name;r['catalog_page']=number;rows.append(r)
 return rows


def bvv_prefix(url):
 path=urlparse(url).path
 mark=path.find('/_f')
 return path[:mark] if mark>=0 else path.rstrip('/')


def bvv_category_url(soup, base):
 for a in soup.select('a[href]'):
  text=a.get_text(' ',strip=True)
  href=urljoin(base,a['href']).split('#')[0]
  if re.match(r'^11\s', text) and '_fexc' in href:return href
 return ''


def bvv_editions(soup, base):
 """MSV catalogs already published, newest year first."""
 from datetime import date
 limit=date.today().year
 found=[];seen=set()
 for a in soup.select('a[href]'):
  href=urljoin(base,a['href']).split('#')[0]
  text=a.get_text(' ',strip=True)
  if not text or href in seen or 'msv' not in href.lower() or '_fexc' not in href or 'nom=0' not in href:continue
  years=[int(year) for year in re.findall(r'20\d{2}', text)]
  if not years or max(years)>limit:continue
  seen.add(href);found.append((max(years),href))
 found.sort(key=lambda item: item[0], reverse=True)
 return found


def bvv_firm_id(url):
 match=re.search(r'fir=(\d+)', url or '')
 return match.group(1) if match else (url or '').split('#')[0]


def bvv_list_span(soup):
 """How many exhibitor pages the category list declares, and the total count."""
 text=soup.get_text(' ',strip=True)
 visible=len({bvv_firm_id(a['href']) for a in soup.select('a[href*="_fdc"]') if 'fir=' in (a.get('href') or '')})
 match=re.search(r'(\d+)\s*-\s*(\d+)\s*z\s*(\d+)', text)
 if not match:
  return 1, visible
 start,end,total=(int(match.group(i)) for i in (1,2,3))
 size=max(1, end-start+1)
 return max(1, (total+size-1)//size), total


def bvv_with_page(url, number):
 base=re.sub(r'&pg=\d+', '', url.split('#')[0])
 if number<=1:return base
 return base+'&pg='+str(number)


def bvv_firm(fetch, url):
 body,kind,final=fetch.page(url)
 soup=BeautifulSoup(body,'html.parser')
 name_el=soup.select_one('.company-detail-name')
 name=name_el.get_text(' ',strip=True) if name_el else ''
 if not name:return None
 fields={}
 for item in soup.select('.profile-item'):
  label=item.select_one('.profile-label');value=item.select_one('.profile-value')
  if not label or not value:continue
  fields[label.get_text(' ',strip=True).rstrip(':').casefold()]=value
 address=''
 if 'adresa' in fields:
  lines=[line.strip() for line in fields['adresa'].get_text('\n').splitlines() if line.strip()]
  address=', '.join(lines)
 website=''
 if 'www' in fields:
  link=fields['www'].select_one('a[href]')
  website=link['href'] if link and link.get('href') else fields['www'].get_text(' ',strip=True)
  if website and not website.startswith(('http://','https://')):website='https://'+website.lstrip('/')
 email=fields['e-mail'].get_text(' ',strip=True).lower() if 'e-mail' in fields else ''
 phone=fields['telefon'].get_text(' ',strip=True) if 'telefon' in fields else ''
 profile=''
 for block in soup.select('.company-block-without-padding'):
  text=block.get_text(' ',strip=True)
  if text.casefold().startswith('profil'):
   profile=re.sub(r'(?i)^profil\s*','',text).strip();break
 country=address.split(',')[-1].strip() if address else ''
 row=entry(name,website,final,profile,address,country)
 if email or phone:
  row['contacts']=[dict(person='',role='',email=email,phone=phone,source=final,status='Kontakt z katalogu MSV')]
 row['text']=' '.join(part for part in (profile,address) if part)
 return row


def bvv_collect(fetch, log, max_pages=None):
 """Every exhibitor in MSV category 11, list page after list page, then each card."""
 from core import Cancelled
 home=DIRECT_SOURCES['bvv'][1]
 fetch.check()
 body,kind,final=fetch.page(home)
 used=1
 soup=BeautifulSoup(body,'html.parser')
 editions=bvv_editions(soup,final)
 if not editions:
  log('MSV: nie znaleziono aktualnego katalogu.')
  return
 chosen=None;warned=False
 for year,fair in editions:
  if max_pages is not None and used>=max_pages:
   log('MSV: osiągnięto limit stron katalogu.');return
  fetch.check()
  body,kind,final=fetch.page(fair);used+=1
  soup=BeautifulSoup(body,'html.parser')
  if not warned and 'TESTOVACÍ VERZE' in soup.get_text():
   log('BVV oznacza tę stronę jako wersję testową — wpisy wymagają weryfikacji.');warned=True
  category=bvv_category_url(soup,final)
  if not category:continue
  if max_pages is not None and used>=max_pages:
   log('MSV: osiągnięto limit stron katalogu.');return
  fetch.check()
  body,kind,final=fetch.page(category);used+=1
  soup=BeautifulSoup(body,'html.parser')
  page_count,total=bvv_list_span(soup)
  if chosen is None or total>chosen['total'] or (total==chosen['total'] and year>chosen['year']):
   chosen=dict(year=year,category=final,soup=soup,pages=page_count,total=total)
  if total>=30:break
 if not chosen:
  log('MSV: na stronie targów nie ma kategorii 11.')
  return
 newer=editions[0][0]
 if chosen['year']!=newer:
  log(f'MSV {newer}: kategoria 11 ma krótką listę — czytam pełną kategorię 11 z katalogu {chosen["year"]} ({chosen["total"]} wystawców).')
 else:
  log(f'MSV {chosen["year"]}: kategoria 11, {chosen["total"]} wystawców, strony 1–{min(chosen["pages"],60)}.')
 if chosen['pages']>60:
  log('MSV: lista ma ponad 60 stron — odczytuję pierwsze 60.');chosen['pages']=60
 host=urlparse(chosen['category']).hostname
 seen=set()

 def firms_on(page_soup, page_url):
  found=[]
  for a in page_soup.select('a[href*="_fdc"]'):
   href=urljoin(page_url,a['href']).split('#')[0]
   if urlparse(href).hostname!=host or 'fir=' not in href:continue
   ident=bvv_firm_id(href)
   if ident in seen:continue
   seen.add(ident);found.append(href)
  return found

 def emit(found):
  nonlocal used
  for href in found:
   if max_pages is not None and used>=max_pages:
    log('MSV: osiągnięto limit stron katalogu.');return True
   fetch.check()
   try:
    row=bvv_firm(fetch,href);used+=1
   except Cancelled:
    raise
   except Exception:
    log('MSV: pomijam wizytówkę, strona niedostępna — '+href);continue
   if row:yield row
  return False

 yield from emit(firms_on(chosen['soup'], chosen['category']))
 for number in range(2, chosen['pages']+1):
  if max_pages is not None and used>=max_pages:
   log('MSV: osiągnięto limit stron katalogu.');return
  url=bvv_with_page(chosen['category'], number)
  fetch.check()
  log(f'MSV kategoria 11: strona {number} z {chosen["pages"]}.')
  body,kind,final=fetch.page(url);used+=1
  yield from emit(firms_on(BeautifulSoup(body,'html.parser'), final))
  if max_pages is not None and used>=max_pages:
   return
 if chosen['pages']>1:
  return
 prefix=bvv_prefix(chosen['category'])
 pending=[];visited={chosen['category']}
 for a in chosen['soup'].select('a[href]'):
  href=urljoin(chosen['category'],a['href']).split('#')[0]
  head=bvv_prefix(href)
  if urlparse(href).hostname==host and '_fexc' in href and (head==prefix or head.startswith(prefix+'/')) and href not in visited:
   pending.append(href)
 while pending:
  if max_pages is not None and used>=max_pages:
   log('MSV: osiągnięto limit stron katalogu.');return
  if len(visited)>=400:
   log('MSV: zbyt wiele podstron kategorii — kończę odczyt list.');return
  fetch.check();url=pending.pop(0)
  if url in visited:continue
  visited.add(url)
  log('MSV kategoria 11: '+url)
  body,kind,final=fetch.page(url);used+=1
  soup=BeautifulSoup(body,'html.parser')
  for a in soup.select('a[href]'):
   href=urljoin(final,a['href']).split('#')[0]
   head=bvv_prefix(href)
   if urlparse(href).hostname==host and '_fexc' in href and (head==prefix or head.startswith(prefix+'/')) and href not in visited and href not in pending:
    pending.append(href)
  yield from emit(firms_on(soup, final))


def vdma_address(item):
 street=str(item.get('address') or '').strip()
 city=' '.join(part for part in (str(item.get('plz') or '').strip(), str(item.get('city') or '').strip()) if part)
 country=str(item.get('country') or '').strip()
 return ', '.join(part for part in (street, city, country) if part)


def vdma_more(body, final, fetch, log, max_pages, pages, start_page=2):
 """Read the remaining pages of the public VDMA member list."""
 raw=body.decode('utf-8','replace') if isinstance(body,(bytes,bytearray)) else body
 endpoint=re.search(r"url:\s*'(https://[^']+p_p_resource_id=getPage[^']*)'", raw)
 count=re.search(r'pageCount:\s*(\d+)', raw)
 namespace=re.search(r"'([^']+_page)'\s*:\s*currentPage", raw)
 if not (endpoint and count and namespace):
  log('VDMA: na stronie nie ma kolejnych stron listy — zostają widoczne firmy.')
  return
 last=int(count.group(1))
 if last<=1:
  return
 stop=last if max_pages is None else min(last, max_pages-pages+1)
 if stop<2:
  return
 begin=max(2, int(start_page or 2))
 if begin>stop:
  return
 log(f'VDMA: lista członków, strony {begin}–{stop} z {last}.')
 for page in range(begin, stop+1):
  fetch.check()
  url=endpoint.group(1)+'&'+urlencode({namespace.group(1): page})
  data,kind,page_url=fetch.page(url)
  payload=json.loads(data.decode('utf-8','replace') if isinstance(data,(bytes,bytearray)) else data)
  listed=payload.get('publicUserList',{})
  if isinstance(listed,str):listed=json.loads(listed)
  for item in listed.get('content',[]):
   web=str(item.get('webAddr') or '').strip()
   web=web if '://' in web else 'https://'+web if web else ''
   address=vdma_address(item)
   row=entry(str(item.get('companyName') or ''), web, final+'#member-'+str(item.get('id') or ''), address=address, country=str(item.get('country') or ''))
   email=str(item.get('email') or '').strip().lower()
   phone=str(item.get('phoneNum') or '').strip()
   if email or phone:
    row['contacts']=[dict(person='',role='',email=email,phone=phone,source=row['source'],status='Kontakt firmy z katalogu VDMA')]
   row['text']=' '.join(part for part in (row['name'], address) if part)
   row['vdma_page']=page
   yield row
  if page==2 or page==stop or page%25==0:
   log(f'VDMA: strona {page} z {last}.')
 if max_pages is not None and last>stop:
  log('VDMA: osiągnięto limit stron katalogu; import częściowy.')


_EUROPAGES_FALLBACK = (
    dict(phrase='gearbox manufacturers', category='transmissions', priority=96, forging_probability='high', negative_keywords=()),
    dict(phrase='drive shafts', category='transmissions', priority=100, forging_probability='high', negative_keywords=()),
    dict(phrase='axle components', category='automotive', priority=97, forging_probability='high', negative_keywords=()),
    dict(phrase='transmission parts', category='transmissions', priority=98, forging_probability='high', negative_keywords=()),
    dict(phrase='hydraulic components', category='hydraulics', priority=93, forging_probability='high', negative_keywords=()),
)
_WLW_FALLBACK = (
    dict(phrase='Getriebehersteller', category='transmissions', priority=96, forging_probability='high', negative_keywords=()),
    dict(phrase='Gelenkwellen', category='transmissions', priority=99, forging_probability='high', negative_keywords=()),
    dict(phrase='Achskomponenten', category='automotive', priority=97, forging_probability='high', negative_keywords=()),
    dict(phrase='Antriebstechnik', category='transmissions', priority=78, forging_probability='medium', negative_keywords=()),
    dict(phrase='Hydraulikkomponenten', category='hydraulics', priority=93, forging_probability='high', negative_keywords=()),
)
_WLW_CATEGORY_MAP = {
    'automotive': {'automotive', 'railway'},
    'agriculture': {'agriculture'},
    'valves': {'hydraulics'},
    'drives': {'transmissions'},
    'construction': {'mining', 'railway'},
}
PHRASE_CSV_FILES = {
    'wlw': Path(__file__).parent / 'csv' / 'wlw_100_phrases.csv',
    'europages': Path(__file__).parent / 'csv' / 'europages_100_phrases.csv',
    'industrystock': Path(__file__).parent / 'csv' / 'industrystock_phrases.csv',
    'hannovermesse': Path(__file__).parent / 'csv' / 'hannovermesse_phrases.csv',
    'ares': Path(__file__).parent / 'csv' / 'ares_phrases.csv',
    'een': Path(__file__).parent / 'csv' / 'een_phrases.csv',
    'techpilot': Path(__file__).parent / 'csv' / 'techpilot_phrases.csv',
    'ariba': Path(__file__).parent / 'csv' / 'ariba_phrases.csv',
    'supplyon': Path(__file__).parent / 'csv' / 'supplyon_phrases.csv',
    'analysis_competition': Path(__file__).parent / 'csv' / 'analysis_competition.csv',
    'analysis_cooperation': Path(__file__).parent / 'csv' / 'analysis_cooperation.csv',
    'analysis_clients': Path(__file__).parent / 'csv' / 'analysis_clients.csv',
    'web': Path(__file__).parent / 'csv' / 'internet_phrases.csv',
    'osm': Path(__file__).parent / 'csv' / 'osm_phrases.csv',
}
ANALYSIS_CSV = {
    'competition': 'analysis_competition',
    'cooperation': 'analysis_cooperation',
    'clients': 'analysis_clients',
}
ANNOUNCEMENT_SOURCES = ('een', 'techpilot', 'ariba', 'supplyon')
PHRASE_CSV_FIELDS = ('phrase', 'category', 'priority', 'forging_probability', 'negative_keywords')
PHRASE_CSV_CATEGORIES = {
    'automotive', 'transmissions', 'agriculture', 'mining', 'railway', 'hydraulics', 'construction',
    'valves', 'drives', 'forgings', 'finished', 'iron', 'forged',
}


def phrase_csv_content(source):
    path = PHRASE_CSV_FILES.get(source)
    if path is None:
        raise ValueError('Nieznany plik fraz')
    return path.read_text(encoding='utf-8')


def phrase_csv_rows(source):
    return list(csv.DictReader(io.StringIO(phrase_csv_content(source))))


def match_csv_phrases(source, text):
    """Phrases from one CSV that appear in the text, minus negative words."""
    if source not in PHRASE_CSV_FILES:
        return []
    body = str(text or '').casefold()
    hits = []
    for row in phrase_csv_rows(source):
        phrase = re.sub(r'\s+', ' ', str(row.get('phrase') or '')).strip()
        if not phrase or phrase.casefold() not in body:
            continue
        negatives = re.split(r'[;,]', str(row.get('negative_keywords') or ''))
        if any(item.strip() and item.strip().casefold() in body for item in negatives):
            continue
        hits.append(phrase)
    return hits


def match_analysis_phrases(kind, text):
    """Phrases from the analysis CSV that appear in the firm text, minus negative words."""
    return match_csv_phrases(ANALYSIS_CSV.get(kind), text)


def save_phrase_csv(source, content):
    """Validate and atomically save one of the two editable phrase lists."""
    path = PHRASE_CSV_FILES.get(source)
    if path is None:
        raise ValueError('Nieznany plik fraz')
    if not isinstance(content, str) or len(content.encode('utf-8')) > 250_000:
        raise ValueError('Plik CSV jest zbyt duży')
    try:
        reader = csv.DictReader(io.StringIO(content))
        if tuple(reader.fieldnames or ()) != PHRASE_CSV_FIELDS:
            raise ValueError('Nagłówki CSV muszą pozostać bez zmian')
        rows, seen = 0, set()
        for row in reader:
            phrase = str(row.get('phrase') or '').strip()
            category = str(row.get('category') or '').strip()
            probability = str(row.get('forging_probability') or '').strip().casefold()
            if not phrase or phrase.casefold() in seen:
                raise ValueError('Frazy nie mogą być puste ani powtórzone')
            seen.add(phrase.casefold())
            if category not in PHRASE_CSV_CATEGORIES:
                raise ValueError(f'Nieznana kategoria przy frazie „{phrase}”')
            if probability not in {'low', 'medium', 'high'}:
                raise ValueError(f'Nieprawidłowe prawdopodobieństwo przy frazie „{phrase}”')
            priority = int(row.get('priority') or -1)
            if not 0 <= priority <= 100:
                raise ValueError(f'Priorytet poza zakresem przy frazie „{phrase}”')
            rows += 1
        if not 1 <= rows <= 500:
            raise ValueError('Plik musi zawierać od 1 do 500 fraz')
    except csv.Error as exc:
        raise ValueError('Nieprawidłowy format CSV') from exc
    normalized = content.replace('\r\n', '\n').replace('\r', '\n')
    if not normalized.endswith('\n'):
        normalized += '\n'
    temporary = path.with_suffix('.tmp')
    temporary.write_text(normalized, encoding='utf-8')
    temporary.replace(path)
    return rows


def save_phrase_rows(source, rows):
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError('Nieprawidłowe wiersze tabeli')
    output = io.StringIO(newline='')
    writer = csv.DictWriter(output, fieldnames=PHRASE_CSV_FIELDS, lineterminator='\n')
    writer.writeheader()
    for row in rows:
        writer.writerow({field: str(row.get(field) or '').strip() for field in PHRASE_CSV_FIELDS})
    return save_phrase_csv(source, output.getvalue())


def _load_ranked_phrases(path, fallback):
    rows = []
    try:
        with path.open(encoding='utf-8-sig', newline='') as handle:
            for position, row in enumerate(csv.DictReader(handle)):
                phrase = re.sub(r'\s+', ' ', str(row.get('phrase') or '')).strip()
                category = re.sub(r'[^a-z_]', '', str(row.get('category') or '').casefold())
                probability = str(row.get('forging_probability') or '').strip().casefold()
                try:
                    priority = int(row.get('priority') or 0)
                except (TypeError, ValueError):
                    continue
                if not phrase or not category or not 0 <= priority <= 100:
                    continue
                negatives = tuple(dict.fromkeys(
                    item.strip() for item in str(row.get('negative_keywords') or '').split(';') if item.strip()
                ))
                rows.append(dict(
                    phrase=phrase, category=category, priority=priority,
                    forging_probability=probability, negative_keywords=negatives,
                    position=position,
                ))
    except OSError:
        return list(fallback)
    rows.sort(key=lambda item: (-item['priority'], item['position']))
    return rows or list(fallback)


def load_wlw_phrases(path=None):
    """Load and validate ranked WLW search phrases from the project CSV."""
    path = Path(path) if path else PHRASE_CSV_FILES['wlw']
    return _load_ranked_phrases(path, _WLW_FALLBACK)


def load_europages_phrases(path=None):
    """Load and validate ranked Europages search phrases from the project CSV."""
    path = Path(path) if path else PHRASE_CSV_FILES['europages']
    return _load_ranked_phrases(path, _EUROPAGES_FALLBACK)


def load_industrystock_phrases(path=None):
    """Load IndustryStock product phrases from the project CSV."""
    path = Path(path) if path else PHRASE_CSV_FILES['industrystock']
    return _load_ranked_phrases(path, ())


def load_hannovermesse_phrases(path=None):
    """Load Hannover Messe exhibitor phrases from the project CSV."""
    path = Path(path) if path else PHRASE_CSV_FILES['hannovermesse']
    return _load_ranked_phrases(path, ())


def load_ares_phrases(path=None):
    """Load Czech business-name phrases for the official ARES API."""
    path = Path(path) if path else PHRASE_CSV_FILES['ares']
    return _load_ranked_phrases(path, _EUROPAGES_FALLBACK)


WLW_SEARCH_ROWS = load_wlw_phrases()
WLW_PHRASES = tuple(item['phrase'] for item in WLW_SEARCH_ROWS)
EUROPAGES_SEARCH_ROWS = load_europages_phrases()
EUROPAGES_PHRASES = tuple(item['phrase'] for item in EUROPAGES_SEARCH_ROWS)
INDUSTRYSTOCK_SEARCH_ROWS = load_industrystock_phrases()
INDUSTRYSTOCK_PHRASES = tuple(item['phrase'] for item in INDUSTRYSTOCK_SEARCH_ROWS)
HANNOVERMESSE_SEARCH_ROWS = load_hannovermesse_phrases()
HANNOVERMESSE_PHRASES = tuple(item['phrase'] for item in HANNOVERMESSE_SEARCH_ROWS)
ARES_SEARCH_ROWS = load_ares_phrases()
ARES_PHRASES = tuple(item['phrase'] for item in ARES_SEARCH_ROWS)


def selected_wlw_phrases(categories=None):
    categories = [item for item in categories or [] if item in _WLW_CATEGORY_MAP]
    if not categories:
        return load_wlw_phrases()
    allowed = set().union(*(_WLW_CATEGORY_MAP[item] for item in categories))
    return [row for row in load_wlw_phrases() if row['category'] in allowed]


def selected_europages_phrases(categories=None):
    categories = [item for item in categories or [] if item in _WLW_CATEGORY_MAP]
    if not categories:
        return load_europages_phrases()
    allowed = set().union(*(_WLW_CATEGORY_MAP[item] for item in categories))
    return [row for row in load_europages_phrases() if row['category'] in allowed]


def selected_industrystock_phrases(categories=None):
    categories = [item for item in categories or [] if item in _WLW_CATEGORY_MAP]
    if not categories:
        return load_industrystock_phrases()
    allowed = set().union(*(_WLW_CATEGORY_MAP[item] for item in categories))
    return [row for row in load_industrystock_phrases() if row['category'] in allowed]


def selected_hannovermesse_phrases(categories=None):
    categories = [item for item in categories or [] if item in _WLW_CATEGORY_MAP]
    if not categories:
        return load_hannovermesse_phrases()
    allowed = set().union(*(_WLW_CATEGORY_MAP[item] for item in categories))
    return [row for row in load_hannovermesse_phrases() if row['category'] in allowed]


def selected_ares_phrases(categories=None):
    categories = [item for item in categories or [] if item in _WLW_CATEGORY_MAP]
    if not categories:
        return load_ares_phrases()
    allowed = set().union(*(_WLW_CATEGORY_MAP[item] for item in categories))
    return [row for row in load_ares_phrases() if row['category'] in allowed]


def load_announcement_phrases(key, path=None):
    """Load the keyword list of one announcement service. Every row is searched."""
    if key not in ANNOUNCEMENT_SOURCES:
        raise ValueError('Nieznany serwis ogłoszeń')
    path = Path(path) if path else PHRASE_CSV_FILES[key]
    return _load_ranked_phrases(path, ())


def selected_announcement_phrases(key, categories=None):
    """Announcement services use the whole CSV. Producer checkboxes do not drop keywords."""
    return load_announcement_phrases(key)


def load_een_phrases(path=None):
    """Load the Enterprise Europe Network keyword list. Every row is searched."""
    return load_announcement_phrases('een', path)


def selected_een_phrases(categories=None):
    """The EEN catalog uses the whole CSV. Producer checkboxes do not drop keywords."""
    return load_een_phrases()


def load_internet_phrases(path=None):
    """DuckDuckGo / Brave phrases formerly taken from producer and recipient checkboxes."""
    path = Path(path) if path else PHRASE_CSV_FILES['web']
    return _load_ranked_phrases(path, ())


def load_osm_phrases(path=None):
    """Offline OSM import phrases; starts as a copy of the Internet list."""
    path = Path(path) if path else PHRASE_CSV_FILES['osm']
    return _load_ranked_phrases(path, ())


_EEN_BROWSER = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15'
_EEN_LIST = 'https://een.ec.europa.eu/partnering-opportunities'


def een_published(reference):
    """Publication day encoded in the POD reference, for example BOLT20261005005."""
    match = re.search(r'(20\d{2})(\d{2})(\d{2})', str(reference or ''))
    if not match:
        return ''
    year, month, day = (int(part) for part in match.groups())
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return ''
    return f'{year:04d}-{month:02d}-{day:02d}'


def een_cards(body):
    """Read opportunity cards from one public listing page."""
    html = body.decode('utf-8', 'replace') if isinstance(body, (bytes, bytearray)) else body
    soup = BeautifulSoup(html, 'html.parser')
    found = []
    for card in soup.select('article.ecl-card'):
        link = card.select_one('.ecl-content-block__title a[href]')
        if link is None:
            continue
        metas = [item.get_text(' ', strip=True) for item in card.select('.ecl-content-block__primary-meta-item')]
        summary = card.select_one('.ecl-content-block__description')
        secondary = [item.get_text(' ', strip=True) for item in card.select('.ecl-content-block__secondary-meta-item')]
        country = next((item for item in secondary if item and not re.search(r'\b(?:ago|hours?|minutes?|days?|weeks?|months?|years?)\b', item, re.I)), '')
        profile = metas[0] if metas else ''
        reference = metas[1] if len(metas) > 1 else ''
        text = summary.get_text(' ', strip=True) if summary else ''
        title = link.get_text(' ', strip=True)
        found.append(dict(
            name='',
            announcement=title,
            website='',
            source=urljoin(_EEN_LIST + '/', link['href']).split('?')[0],
            address=country,
            country=country,
            province='',
            contacts=[],
            text=' · '.join(part for part in (title, profile, text) if part),
            lat=None,
            lon=None,
            published_at=een_published(reference),
            reference=reference,
        ))
    return found


_EEN_NAME_LABELS = ('company name', 'organisation name', 'organization name', 'name of the company', 'enterprise name', 'partner organisation', 'partner organization')
_EEN_LEGAL = r'(?:GmbH(?:\s*&\s*Co\.\s*KG)?|AG|Ltd|Limited|S\.r\.o\.|s\.r\.o\.|Sp\.\s*z\s*o\.o\.|UAB|Oy|Inc|SAS|SARL|B\.V\.|BV|S\.A\.|SA|AB|ApS|kft|Srl|PLC|a\.s\.|OÜ|SIA)'


def een_fields(body):
    """Labelled facts from one public opportunity page."""
    html = body.decode('utf-8', 'replace') if isinstance(body, (bytes, bytearray)) else body
    soup = BeautifulSoup(html, 'html.parser')
    fields = {}
    for term in soup.select('dt.ecl-description-list__term'):
        label = term.get_text(' ', strip=True)
        definition = term.find_next_sibling('dd')
        fields[label] = definition.get_text(' ', strip=True) if definition else ''
    heading = soup.select_one('h1')
    title = heading.get_text(' ', strip=True) if heading else ''
    return title, fields


def een_legal_name(text):
    """A named company with a legal form. Generic phrases such as 'a Lithuanian company' are not names."""
    match = re.search(r'\b([A-ZŁŚŻŹĆŃ][\w&.\'’-]{1,40}(?:\s+[A-ZŁŚŻŹĆŃ][\w&.\'’-]{1,40}){0,4}\s+' + _EEN_LEGAL + r')\b', str(text or ''))
    return re.sub(r'\s+', ' ', match.group(1)).strip() if match else ''


def een_company_name(fields):
    """Company name published on the opportunity page, apart from the offer headline."""
    folded = {str(key).casefold(): str(value or '').strip() for key, value in (fields or {}).items()}
    for label in _EEN_NAME_LABELS:
        value = folded.get(label, '')
        if value and len(value) <= 180 and not re.search(r'\b(agreement|countries|sought|university)\b', value, re.I):
            return value
    prose = ' '.join(folded.get(key, '') for key in ('full description', 'short summary', 'advantages and innovations'))
    return een_legal_name(prose)


def een_page_count(body):
    html = body.decode('utf-8', 'replace') if isinstance(body, (bytes, bytearray)) else body
    soup = BeautifulSoup(html, 'html.parser')
    last = soup.select_one('.ecl-pagination__item--last a[href]')
    match = re.search(r'(?:^|[?&])page=(\d+)', last['href']) if last is not None else None
    return int(match.group(1)) + 1 if match else 1


def een_result_count(body):
    html = body.decode('utf-8', 'replace') if isinstance(body, (bytes, bytearray)) else body
    match = re.search(r'Partnering opportunities \((\d+)\)', BeautifulSoup(html, 'html.parser').get_text(' ', strip=True))
    return int(match.group(1)) if match else 0


def een_fetch(fetch, url):
    """Read a public EEN page. CloudFront rejects the research user agent, so the browser one is sent."""
    from core import Cancelled
    from urllib.robotparser import RobotFileParser
    fetch.check()
    parser = getattr(fetch, '_een_robots', None)
    if parser is None:
        rules = een_fetch_body(fetch, 'https://een.ec.europa.eu/robots.txt')
        parser = RobotFileParser()
        parser.parse(rules.decode('utf-8', 'replace').splitlines())
        fetch._een_robots = parser
    if not parser.can_fetch('*', url):
        raise ValueError('robots.txt nie pozwala na pobranie Enterprise Europe Network')
    return een_fetch_body(fetch, url)


def een_fetch_body(fetch, url):
    """Read one EEN page. A slow response is tried again instead of stopping the catalog."""
    import requests
    from core import Cancelled
    last_error = None
    for attempt in range(3):
        fetch.check()
        host = urlparse(url).hostname or ''
        wait = max(0, 1.1 - (time.monotonic() - fetch.last.get(host, 0)))
        if attempt:
            wait = max(wait, 3.0 * attempt)
        if fetch.stop.wait(wait):
            raise Cancelled()
        fetch.last[host] = time.monotonic()
        try:
            response = fetch.session.get(url, headers={'User-Agent': _EEN_BROWSER, 'Accept': 'text/html,text/plain'}, timeout=(8, 60))
        except (requests.Timeout, requests.ConnectionError) as exc:
            last_error = exc
            fetch.log(f'EEN: {type(exc).__name__}, próba {attempt + 1}/3.')
            continue
        try:
            response.raise_for_status()
            return response.content
        finally:
            response.close()
    raise last_error


_EEN_COUNTRY_NAMES = {
    'PL': ('poland', 'polska', 'polsko', 'pl'),
    'DE': ('germany', 'deutschland', 'niemcy', 'de'),
    'CZ': ('czechia', 'czech republic', 'cesko', 'česko', 'czechy', 'cz'),
}


def een_country_code(label):
    """Map a listing country name to PL, DE or CZ. Other countries return an empty code."""
    raw = str(label or '').strip().casefold()
    folded = unicodedata.normalize('NFKD', raw).encode('ascii', 'ignore').decode()
    for code, names in _EEN_COUNTRY_NAMES.items():
        if raw in names or folded in names:
            return code
    return ''


def een_matches_countries(label, countries):
    """True when the opportunity country is one of the checked panel countries."""
    code = een_country_code(label)
    return bool(code) and code in set(countries or [])


def een_opportunities(fetch, log, phrases=None, countries=None):
    """Search the public opportunity list and keep offers from the checked countries only."""
    import requests
    from core import Cancelled
    rows = phrases if phrases is not None else load_een_phrases()
    selected = [code for code in ('PL', 'DE', 'CZ') if code in set(countries or [])]
    if not selected:
        log('EEN: nie zaznaczono kraju — nie zbieram ogłoszeń.')
        return
    labels = {'PL': 'Polska', 'DE': 'Niemcy', 'CZ': 'Czechy'}
    scope = ','.join(selected)
    log('EEN: zbieram tylko z krajów: ' + ', '.join(labels[code] for code in selected) + '.')
    seen = set()
    kept = 0
    incomplete = False
    for row in rows:
        fetch.check()
        phrase = row['phrase'] if isinstance(row, dict) else str(row)
        query = f'een:{scope}:{phrase}'
        try:
            first = een_fetch(fetch, _EEN_LIST + '?' + urlencode([('f[0]', 'k:' + phrase)]))
        except Cancelled:
            raise
        except requests.RequestException as exc:
            incomplete = True
            log(f'EEN, fraza „{phrase}”: {type(exc).__name__} — pomijam tę frazę i czytam dalej. Następne uruchomienie wróci do niej.')
            yield dict(query=query, resume_ok=False, een_skipped=True, name='', source='', announcement='', country='', address='', text='', contacts=[])
            continue
        pages = min(40, een_page_count(first))
        log(f'EEN, fraza „{phrase}”: {een_result_count(first)} ogłoszeń, strony 1–{pages}.')
        batch = []
        skipped = 0
        phrase_ok = True
        for page in range(pages):
            try:
                body = first if page == 0 else een_fetch(fetch, _EEN_LIST + '?' + urlencode([('f[0]', 'k:' + phrase), ('page', page)]))
            except Cancelled:
                raise
            except requests.RequestException as exc:
                phrase_ok = False
                incomplete = True
                log(f'EEN, fraza „{phrase}”, strona {page + 1}: {type(exc).__name__} — zapisuję już odczytane ogłoszenia i przechodzę do następnej frazy.')
                break
            cards = een_cards(body)
            if not cards:
                break
            for card in cards:
                if card['source'] in seen:
                    continue
                seen.add(card['source'])
                if not een_matches_countries(card.get('country'), selected):
                    skipped += 1
                    continue
                card['query'] = query
                card['name'] = ''
                card['resume_ok'] = phrase_ok
                batch.append(card)
            if page == 39 and een_page_count(first) > 40:
                log(f'EEN, fraza „{phrase}”: limit 40 stron, dalsze ogłoszenia pozostają na kolejny przebieg.')
        if not phrase_ok:
            for card in batch:
                card['resume_ok'] = False
            if not batch:
                yield dict(query=query, resume_ok=False, een_skipped=True, name='', source='', announcement='', country='', address='', text='', contacts=[])
        for card in batch:
            yield card
            kept += 1
        log(f'EEN, fraza „{phrase}”: dodano {len(batch)} ogłoszeń z wybranych krajów, pominięto {skipped} z innych krajów.')
    if incomplete:
        log('EEN: część fraz nie doszła przez przerwę połączenia. Następne uruchomienie je dokończy.')
    log(f'Enterprise Europe Network: {kept} ogłoszeń z wybranych krajów.')
_TECHPILOT_LIST = 'https://www.techpilot.com/de/auftrag'


def techpilot_pages(body):
    """Public order pages listed in the Techpilot sitemap."""
    html = body.decode('utf-8', 'replace') if isinstance(body, (bytes, bytearray)) else body
    found = re.findall(r'<loc>\s*([^<\s]+)\s*</loc>', html)
    return [url for url in dict.fromkeys(found) if '/auftrag/' in url and not url.endswith('.xml')]


def techpilot_deadline(text, today=None):
    """Closing day of a request published as remaining time, for example 'noch 22 Tage'."""
    match = re.search(r'noch\s+(\d+)\s*(Minute|Stunde|Tag|Woche|Monat)', str(text or ''), re.I)
    if not match:
        return ''
    amount, unit = int(match.group(1)), match.group(2).casefold()
    days = {'minute': 0, 'stunde': 0, 'tag': amount, 'woche': amount * 7, 'monat': amount * 30}[unit]
    base = today or date.today()
    return (base + timedelta(days=days)).isoformat()


def techpilot_cards(body, url, today=None):
    """Anonymous requests for quotation from one public Techpilot order page."""
    html = body.decode('utf-8', 'replace') if isinstance(body, (bytes, bytearray)) else body
    soup = BeautifulSoup(html, 'html.parser')
    found = []
    for heading in soup.select('p'):
        label = heading.get_text(' ', strip=True)
        if not label.lower().startswith('technologien:'):
            continue
        card = heading.parent
        lines = [item.get_text(' ', strip=True) for item in card.select('p') if item is not heading]
        technologies = re.sub(r'\s+', ' ', label.split(':', 1)[1]).strip()
        remaining = next((line for line in lines if re.search(r'\bnoch\b', line, re.I)), '')
        quantity = next((line for line in lines if re.search(r'\bStk\b', line, re.I)), '')
        if not technologies:
            continue
        marker = hashlib.sha1('|'.join((technologies, quantity, remaining)).encode('utf-8')).hexdigest()[:10]
        found.append(dict(
            name='',
            announcement=technologies,
            website='',
            source=f'{url}#anfrage-{marker}',
            address='',
            country='',
            province='',
            contacts=[],
            text=' · '.join(part for part in (technologies, quantity, remaining) if part),
            lat=None,
            lon=None,
            published_at=techpilot_deadline(remaining, today),
            quantity=quantity,
        ))
    return found


def techpilot_requests(fetch, log, phrases=None, today=None):
    """Read the public Techpilot order pages and keep requests matching the CSV technologies."""
    rows = phrases if phrases is not None else load_announcement_phrases('techpilot')
    wanted = [row['phrase'] if isinstance(row, dict) else str(row) for row in rows]
    if not wanted:
        log('Techpilot: plik csv/techpilot_phrases.csv jest pusty — nie zbieram zapytań.')
        return
    body, _kind, final = fetch.page(_TECHPILOT_LIST + '/sitemap.xml')
    pages = techpilot_pages(body) or [_TECHPILOT_LIST + '/lohnfertigung']
    log(f'Techpilot: {len(pages)} stron z zapytaniami, {len(wanted)} technologii z pliku CSV.')
    seen = set()
    kept = 0
    for url in pages:
        fetch.check()
        body, _kind, final = fetch.page(url)
        cards = techpilot_cards(body, url, today)
        added = 0
        skipped = 0
        for card in cards:
            if card['source'] in seen:
                continue
            seen.add(card['source'])
            folded = card['announcement'].casefold()
            phrase = next((item for item in wanted if item.casefold() in folded), '')
            if not phrase:
                skipped += 1
                continue
            card['query'] = 'techpilot:' + phrase
            yield card
            added += 1
            kept += 1
        log(f'Techpilot, strona {url.rsplit("/", 1)[-1]}: {len(cards)} zapytań, dodano {added}, pominięto {skipped} spoza listy technologii.')
    log(f'Techpilot: {kept} zapytań ofertowych z publicznych stron.')


GATED_SERVICES = {
    'ariba': dict(
        home='https://discovery.ariba.com/',
        note='ogłoszenia są widoczne po zalogowaniu, a robots.txt serwisu nie pozwala na automatyczny odczyt',
    ),
    'supplyon': dict(
        home='https://www.supplyon.com/en/for-suppliers/',
        note='zapytania trafiają tylko do zarejestrowanych dostawców, serwis nie publikuje listy ogłoszeń',
    ),
}


def gated_announcements(key, log, phrases=None):
    """Services that keep postings behind a login. Each keyword becomes a lead to open by hand."""
    service = GATED_SERVICES[key]
    label = DIRECT_SOURCES[key][0]
    rows = phrases if phrases is not None else load_announcement_phrases(key)
    log(f'{label}: {service["note"]}.')
    log(f'{label}: przygotowuję {len(rows)} haseł z pliku CSV do wyszukania po zalogowaniu.')
    for row in rows:
        phrase = row['phrase'] if isinstance(row, dict) else str(row)
        yield dict(
            lead_only=True,
            lead_category='Hasło do wyszukania w serwisie',
            lead_status=f'{label} — zaloguj się i wyszukaj „{phrase}”',
            name=phrase,
            source=service['home'],
            query=f'{key}:{phrase}',
        )


EUROPAGES_LEAD = 'Trop — nazwa i URL profilu; dane firmy przy uzupełnianiu'
_COMPANY_MARKERS = {'company', 'firma', 'entreprise', 'azienda', 'empresa', 'bedrijf', 'virksomhed', 'yritys', 'foretag', 'spolecnost'}
_LEGAL_FORMS = {'gmbh': 'GmbH', 'kg': 'KG', 'ag': 'AG', 'sa': 'SA', 'ltd': 'Ltd', 'bv': 'BV', 'srl': 'Srl', 'plc': 'PLC', 'inc': 'Inc', 'co': 'Co', 'ug': 'UG', 'oy': 'Oy', 'kft': 'Kft', 'sas': 'SAS', 'sarl': 'SARL', 'ab': 'AB'}


_ONLY_FORM = set(_LEGAL_FORMS) | {'sp', 'z', 'o', 'oo', 's', 'r', 'se', 'kgaa'}
_GLUED_COUNTRY = ('United Kingdom', 'Czech Republic', 'Germany', 'Poland', 'France', 'Italy', 'Spain', 'Austria', 'Netherlands', 'Belgium', 'Switzerland', 'Sweden', 'Denmark', 'Hungary', 'Portugal', 'Slovakia', 'Finland', 'Norway', 'Ireland', 'Turkey', 'Romania', 'Greece', 'Croatia')


def _slug_name(slug):
    words = re.sub(r'-\d{5,}$', '', slug).replace('-', ' ').split()
    if not words:
        return ''
    if any(char.isupper() for char in slug):
        return ' '.join(words)
    return ' '.join(_LEGAL_FORMS.get(word, word.capitalize()) for word in words)


def _title_name(title):
    cleaned = re.sub(r'\s+', ' ', title or '').strip()
    cleaned = re.split(r'\s+[|\-–]\s+', cleaned)[0].strip()
    cleaned = re.sub(r'\s+on europages\b.*$', '', cleaned, flags=re.I).strip()
    match = re.match(r'(.+?)\s+in\s+\S+$', cleaned)
    if match:
        cleaned = match.group(1).strip()
    if len(cleaned) < 3 or 'europages' in cleaned.lower():
        return ''
    return cleaned


def _real_company(name):
    tokens = [re.sub(r'[^a-z]', '', word.lower()) for word in name.split()]
    return any(token and token not in _ONLY_FORM for token in tokens)


def europages_company(title, url):
    """Company name and profile URL from a public search hit. Listing pages are not companies."""
    parsed = urlparse(str(url or '').split('#')[0])
    host = (parsed.hostname or '').lower().removeprefix('www.')
    if parsed.scheme not in ('http', 'https') or not host.startswith('europages.'):
        return None
    parts = [part for part in parsed.path.split('/') if part]
    if any(part in ('companies', 'showroom', 'showroom-cate', 'search') for part in parts):
        return None
    slug, profile = '', ''
    for index, part in enumerate(parts):
        if part in _COMPANY_MARKERS and index + 1 < len(parts):
            slug = parts[index + 1]
            profile = f'{parsed.scheme}://{parsed.netloc}/' + '/'.join(parts[:index + 2])
            break
    if not profile and len(parts) == 2 and re.fullmatch(r'\d{6,}(?:-\d+)?', parts[1].split('.')[0]):
        slug, profile = parts[0], f'{parsed.scheme}://{parsed.netloc}/' + '/'.join(parts)
    if not slug or not profile:
        return None
    name = _slug_name(slug) if 'products' in parts else (_title_name(title) or _slug_name(slug))
    if not name or not _real_company(name):
        return None
    return name, profile


def wlw_company(title, url):
    """Company name and canonical WLW profile from a public search result."""
    parsed = urlparse(str(url or '').split('#')[0])
    host = (parsed.hostname or '').lower().removeprefix('www.')
    parts = [part for part in parsed.path.split('/') if part]
    if parsed.scheme not in ('http', 'https') or host != 'wlw.de' or len(parts) < 3 or parts[0:2] != ['de', 'firma']:
        return None
    slug = parts[2]
    if not re.search(r'-\d+$', slug):
        return None
    name = _title_name(title) or _slug_name(slug)
    name = re.sub(r'\s+(?:auf|bei)\s+wlw\b.*$', '', name, flags=re.I).strip()
    if not name or not _real_company(name):
        return None
    return name, f'{parsed.scheme}://{parsed.netloc}/de/firma/{slug}'


def wlw_address(text):
    """Address printed in the public index, including WLW's city-before-postcode form."""
    from geolocation import find_address
    body = re.sub(r'\s+', ' ', str(text or ''))
    found = find_address(body)
    if found:
        return found
    match = re.search(
        r'\b((?:(?:Am|An|Im|Zum|Zur)\s+[A-ZÄÖÜ][\wÄÖÜäöüß.\-]*(?:\s+[A-ZÄÖÜ][\wÄÖÜäöüß.\-]*){0,2}'
        r'|[A-ZÄÖÜ][\wÄÖÜäöüß.\-]*?(?:strasse|straße|str\.|Str\.|weg|gasse|allee|platz|ring))'
        r'\s+\d{1,4}[A-Za-z]?)'
        r'\s*,\s*([A-ZÄÖÜ][\wÄÖÜäöüß.\-]*(?:\s+[A-ZÄÖÜ][\wÄÖÜäöüß.\-]*){0,2})\s+(\d{5})\b',
        body)
    return f'{match.group(1)}, {match.group(3)} {match.group(2)}, Germany' if match else ''


def europages_address(text):
    """Street address already printed by the public index. A city name alone is not an address."""
    from geolocation import find_address
    body = re.sub(r'\s+', ' ', str(text or ''))
    polish = find_address(body)
    if polish:
        return polish
    match = re.search(r'Location\.\s*(.+?)(?:\bContact\b|$)', body, re.I)
    if not match:
        return ''
    raw = match.group(1).strip(' .,')
    country = ''
    for name in _GLUED_COUNTRY:
        if raw.lower().startswith(name.lower()) and len(raw) > len(name):
            country = name
            raw = raw[len(name):].lstrip(' ,')
            break
    if not re.search(r'\d', raw):
        return ''
    raw = re.sub(r'\s+', ' ', raw).strip(' ,')
    return f'{raw}, {country}' if country else raw


def europages_locality(title):
    """City printed next to the company name, for example 'in Alfeld'."""
    cleaned = re.sub(r'\s+(?:on|auf|su|sur|a)\s+europages\b.*$', '', str(title or ''), flags=re.I)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    match = re.search(r'\bin\s+([A-ZŁŚŻŹĆŃÓÄÖÜ][\w.\-]+(?:\s+[A-ZŁŚŻŹĆŃÓÄÖÜ][\w.\-]+){0,2})$', cleaned)
    return match.group(1) if match else ''


_SITE_SKIP = (
    'youtube.com', 'bing.com', 'duckduckgo.com', 'cylex.de', 'cylex.com',
    'paginebianche.it', 'firmania.it', 'wikipedia.org', 'yahoo.com',
    'facebook.com', 'instagram.com', 'linkedin.com', 'wlw.de', 'wlw.com',
    'kompass.com', 'x.com', 'twitter.com', 'dasoertliche.de',
    'rekvizitai.vz.lt', 'netetrade.com', 'tradewheel.com', 'emis.com',
)
_SITE_NOISE = ('machineryline', 'listofcompany', 'einforma', 'grokipedia')
_ARES_SITE_SKIP = (
    'ares.gov.cz', 'justice.cz', 'firmy.cz', 'search.seznam.cz', 'podnikatel.cz', 'fakturujzdarma.cz',
    'kurzy.cz', 'finmag.cz', 'detail.cz', 'rejstriky.finance.cz', 'mesec.cz', 'euro.cz',
    'chytryrejstrik.cz', 'firma.cz', 'firmyvkraji.cz', 'zivefirmy.cz', 'penize.cz',
    'firmyvdosahu.cz', 'coface.com', 'edb.cz', 'najisto.centrum.cz', 'hotfrog.cz',
    'dnb.com', 'info-cechy.cz', 'ceskefirmy.online', 'idatabaze.cz', 'jenfirmy.cz',
    'ifirmy.cz', 'azfirma.cz', 'firmy-brno.cz', 'firma.sluzby.cz', 'obchodiste.cz',
    'rejstr.cz', 'ekatalog.cz', 'expanzo.com', 'mapy.com', 'seznam.cz', 'o-seznam.cz',
    'jenprace.cz', 'ceginformacio.hu', 'mojedatovaschranka.cz', 'hlidacstatu.cz',
    'edb.eu', 'indexforwp.com', 'financni-web.cz', 'firmablizko.cz', 'epoptavka.cz',
    'netkatalog.cz', 'vhodne-uverejneni.cz', 'smlouvy.gov.cz', 'pracomat.cz',
    'cylex.cz', 'ares.cz',
)


def europages_website(name, locality='', exclude=()):
    """Official company site from the public index. Europages profile pages are not opened."""
    from core import DENIED, DIRECTORIES, SearchBlocked, domain, is_domain
    query = ' '.join(part for part in (re.sub(r'\s+', ' ', name or '').strip(), locality or '') if part)
    if len(query) < 3:
        return ''
    excluded = {domain(item) if '://' in str(item) else str(item).lower().removeprefix('www.') for item in exclude}
    rows = []
    for backend in ('bing', 'auto'):
        try:
            found = _europages_rows(query, backend)
        except SearchBlocked:
            raise
        if found:
            rows = found
            break
    for row in rows:
        url = str(row.get('url') or '').split('#')[0]
        host = domain(url)
        if not url.startswith(('http://', 'https://')) or not host:
            continue
        if host in excluded or 'europages.' in host or is_domain(host, DENIED) or is_domain(host, DIRECTORIES) or is_domain(host, _SITE_SKIP) or any(part in host for part in _SITE_NOISE):
            continue
        return url
    return ''


def ares_website(name, ico):
    """Return only a plausible official Czech company site, never a registry card."""
    from core import DENIED, DIRECTORIES, domain, is_domain
    query=' '.join(part for part in (f'"{name}"',str(ico or '')) if part)
    folded_name=re.findall(r'[a-z0-9]+',''.join(c for c in unicodedata.normalize('NFKD',name.casefold()) if not unicodedata.combining(c)))
    legal={'s','r','o','spol','as','a','akciova','spolecnost','firma','druzstvo'}
    significant=[token for token in folded_name if len(token)>1 and token not in legal]
    rows=_europages_rows(query,'seznam',12,timeout=8) or []
    for row in rows:
        url=str(row.get('url') or '').split('#')[0];site=domain(url)
        if not url.startswith(('http://','https://')) or not site or is_domain(site,_ARES_SITE_SKIP):
            continue
        if is_domain(site,DENIED) or is_domain(site,DIRECTORIES) or is_domain(site,_SITE_SKIP) or any(part in site for part in _SITE_NOISE):
            continue
        raw=' '.join((str(row.get('title') or ''),str(row.get('body') or ''),site))
        folded=set(re.findall(r'[a-z0-9]+',''.join(c for c in unicodedata.normalize('NFKD',raw.casefold()) if not unicodedata.combining(c))))
        name_match=len(significant)>=2 and all(token in folded for token in significant)
        if (ico and re.sub(r'\D','',str(ico)) in re.sub(r'\D','',raw)) or name_match:
            return url.split('?',1)[0]
    if len(significant) >= 2:
        import requests
        candidate='https://www.'+''.join(significant)+'.cz/'
        try:
            response=requests.get(candidate,headers={'User-Agent':'Mozilla/5.0'},timeout=(3,5))
            response.raise_for_status()
            final=response.url
            host=domain(final)
            text=BeautifulSoup(response.content,'html.parser').get_text(' ',strip=True)
            folded=set(re.findall(r'[a-z0-9]+',''.join(c for c in unicodedata.normalize('NFKD',text.casefold()) if not unicodedata.combining(c))))
            if host and not is_domain(host,_ARES_SITE_SKIP) and (all(token in folded for token in significant) or (ico and str(ico) in re.sub(r'\D','',text))):
                return final
        except requests.RequestException:
            pass
    return ''


def europages_contacts(text, source):
    from core import EMAIL
    contacts = []
    for email in sorted({item.lower() for item in EMAIL.findall(text or '')}):
        contacts.append(dict(person='', role='', email=email, phone='', source=source, status='Kontakt z indeksu Europages — do sprawdzenia'))
    for phone in re.findall(r'(?:tel(?:efon)?\.?|phone)\s*[:.]?\s*(\+[\d][\d\s()./-]{6,}\d)', text or '', re.I):
        phone = re.sub(r'\s+', ' ', phone).strip()
        if phone and not any(item.get('phone') == phone for item in contacts):
            contacts.append(dict(person='', role='', email='', phone=phone, source=source, status='Kontakt z indeksu Europages — do sprawdzenia'))
    return contacts


def _europages_rows(query, backend, limit=10, timeout=15):
    if backend == 'seznam':
        import requests
        try:
            response = requests.get(
                'https://search.seznam.cz/',
                params={'q': query},
                headers={'User-Agent': 'Mozilla/5.0'},
                timeout=(4, timeout),
            )
            response.raise_for_status()
        except requests.RequestException:
            return None
        rows, seen_domains = [], set()
        for anchor in BeautifulSoup(response.content, 'html.parser').select('a[href]'):
            url = str(anchor.get('href') or '').strip()
            host = urlparse(url).netloc.lower().removeprefix('www.')
            if not url.startswith(('http://', 'https://')) or not host:
                continue
            if host in {'seznam.cz', 'search.seznam.cz', 'o-seznam.cz'} or host in seen_domains:
                continue
            seen_domains.add(host)
            parent = anchor.find_parent(['article', 'section', 'div'])
            rows.append({
                'title': anchor.get_text(' ', strip=True),
                'url': url,
                'body': (parent.get_text(' ', strip=True) if parent else '')[:1200],
            })
            if len(rows) >= limit:
                break
        return rows
    from ddgs import DDGS
    from ddgs.exceptions import DDGSException, RatelimitException, TimeoutException
    from core import SearchBlocked
    try:
        rows = DDGS(timeout=timeout).text(query, region='wt-wt', safesearch='moderate', max_results=limit, backend=backend)
    except RatelimitException:
        raise SearchBlocked('DuckDuckGo ograniczył zapytania — zatrzymuję wyszukiwanie Europages') from None
    except TimeoutException:
        return None
    except DDGSException as exc:
        message = str(exc).strip().lower()
        if 'no results found' in message:
            return []
        if re.search(r'captcha|ratelimit|rate.limit|too many requests|\b(403|429)\b', message):
            raise SearchBlocked('DuckDuckGo odmówił dostępu lub ograniczył zapytania') from None
        return None
    return [{'title': row.get('title', ''), 'url': row.get('href', row.get('url', '')), 'body': row.get('body') or ''} for row in rows or []]


def europages_search(query, config):
    """Public index of Europages profiles. The catalog pages themselves stay unread."""
    if config.get('engine', 'duckduckgo') == 'brave':
        import requests
        key = str(config.get('brave_key') or '').strip()
        if not key:
            raise ValueError('Wybrany katalog przez Brave wymaga klucza API')
        wlw = 'site:wlw.' in query or 'site:industrystock.' in query or 'site:hannovermesse.' in query
        from core import live_session
        session = live_session()
        try:
            response = session.get('https://api.search.brave.com/res/v1/web/search', params={'q': query, 'count': 20, 'country': 'DE' if wlw else 'PL', 'search_lang': 'de' if wlw else 'pl'}, headers={'X-Subscription-Token': key, 'Accept': 'application/json'}, timeout=(8, 20))
            response.raise_for_status()
            return [{'title': row.get('title', ''), 'url': row.get('url', ''), 'body': row.get('description') or ''} for row in response.json().get('web', {}).get('results', [])]
        finally:
            try:
                session.close()
            except Exception:
                pass
    catalog = 'site:europages.' in query or 'site:wlw.' in query or 'site:industrystock.' in query or 'site:hannovermesse.' in query
    found = []
    for backend in (('bing', 'duckduckgo', 'auto') if catalog else ('bing', 'auto')):
        rows = _europages_rows(query, backend, 30 if catalog else 8)
        if rows:
            return rows
        if rows is None and backend == 'duckduckgo':
            continue
        found = rows or found
    return found or []


def europages_leads(log, search, phrases=None):
    """Phrase search. Company rows come from the index; the catalog site stays unread."""
    from core import SearchBlocked
    log('Europages: robots.txt zamyka strony katalogu. Profil daje nazwę, a adres i kontakty biorę ze strony firmy.')
    seen = set()
    rows = phrases if phrases is not None else EUROPAGES_SEARCH_ROWS
    for row in rows:
        phrase = row['phrase'] if isinstance(row, dict) else str(row)
        negatives = row.get('negative_keywords', ()) if isinstance(row, dict) else ()
        priority = row.get('priority', 0) if isinstance(row, dict) else 0
        category = row.get('category', '') if isinstance(row, dict) else ''
        exclusions = ' '.join('-"' + item.replace('"', '') + '"' for item in negatives)
        query = ' '.join(part for part in (f'site:europages.co.uk/en/company "{phrase}"', exclusions) if part)
        log(f'Europages, fraza: {phrase} · {category} · priorytet {priority}')
        try:
            hits = search(query) or []
        except SearchBlocked:
            raise
        except Exception as exc:
            log(f'Europages, fraza „{phrase}”: {exc}')
            continue
        added = 0
        for hit in hits:
            found = europages_company(hit.get('title') or '', hit.get('url') or '')
            if not found or found[1] in seen:
                continue
            seen.add(found[1])
            body = hit.get('body') or ''
            yield dict(name=found[0], website='', source=found[1], address=europages_address(body), country='', province='', locality=europages_locality(hit.get('title') or ''), contacts=europages_contacts(body, found[1]), text=body, lat=None, lon=None, query=phrase, europages_category=category, europages_priority=priority, forging_probability=row.get('forging_probability', '') if isinstance(row, dict) else '')
            added += 1
        log(f'Europages, fraza „{phrase}”: {added} firm.')
    log(f'Europages: {len(seen)} firm z indeksu. Stronę firmy czytam poza katalogiem.')


def wlw_leads(log, search, phrases=None):
    """WLW profiles from the public index; wlw.de itself is not fetched."""
    from core import SearchBlocked
    log('WLW: robots.txt zamyka katalog dla programu. Nazwę i adres biorę z publicznego indeksu, a kontakty z oficjalnej strony firmy.')
    seen = set()
    rows = phrases if phrases is not None else WLW_SEARCH_ROWS
    for row in rows:
        phrase = row['phrase'] if isinstance(row, dict) else str(row)
        negatives = row.get('negative_keywords', ()) if isinstance(row, dict) else ()
        priority = row.get('priority', 0) if isinstance(row, dict) else 0
        category = row.get('category', '') if isinstance(row, dict) else ''
        exclusions = ' '.join('-"' + item.replace('"', '') + '"' for item in negatives)
        query = ' '.join(part for part in (f'site:wlw.de/de/firma "{phrase}"', exclusions) if part)
        log(f'WLW, fraza: {phrase} · {category} · priorytet {priority}')
        try:
            hits = search(query) or []
        except SearchBlocked:
            raise
        except Exception as exc:
            log(f'WLW, fraza „{phrase}”: {exc}')
            continue
        added = 0
        for hit in hits:
            found = wlw_company(hit.get('title') or '', hit.get('url') or '')
            if not found or found[1] in seen:
                continue
            seen.add(found[1])
            body = hit.get('body') or ''
            yield dict(
                name=found[0], website='', source=found[1], address=wlw_address(body),
                country='Germany', province='', locality='', contacts=europages_contacts(body, found[1]),
                text=body, lat=None, lon=None, query=phrase, wlw_category=category,
                wlw_priority=priority, forging_probability=row.get('forging_probability', '') if isinstance(row, dict) else '')
            added += 1
        log(f'WLW, fraza „{phrase}”: {added} firm.')
    log(f'WLW: {len(seen)} firm z indeksu. Oficjalne strony czytam poza katalogiem.')


def industrystock_company(title, url):
    """Company name and canonical IndustryStock profile from a public search result."""
    parsed = urlparse(str(url or '').split('#')[0])
    host = (parsed.hostname or '').lower().removeprefix('www.')
    parts = [part for part in parsed.path.split('/') if part]
    if parsed.scheme not in ('http', 'https') or host not in ('industrystock.com', 'industrystock.de', 'industrystock.pl'):
        return None
    if len(parts) < 5 or parts[1] not in ('company', 'firma') or parts[2] not in ('profile', 'profil'):
        return None
    slug, ident = parts[3], parts[4]
    if not slug or not re.fullmatch(r'\d+', ident):
        return None
    profile = f'{parsed.scheme}://{parsed.netloc}/{"/".join(parts[:5])}'
    name = re.sub(r'^(?:products and services of|produkte und dienstleistungen von)\s+', '', str(title or ''), flags=re.I)
    name = _title_name(name) or _slug_name(slug)
    name = re.sub(r'\s+(?:on|auf|bei)\s+industrystock\b.*$', '', name, flags=re.I).strip()
    if not name or not _real_company(name):
        return None
    return name, profile


def industrystock_address(text):
    """Street or city printed in the public IndustryStock snippet."""
    from geolocation import find_address
    body = re.sub(r'\s+', ' ', str(text or ''))
    found = find_address(body)
    if found:
        return found
    glued = re.search(
        r'\b((?:[A-ZÄÖÜ][\wÄÖÜäöüß.\-]*\s+){0,3}(?:Straße|Strasse|Str\.|str\.)\s+\d{1,4})(\d{5})\s+([A-ZÄÖÜ][\wÄÖÜäöüß.\-]+)',
        body)
    if glued:
        return f'{glued.group(1)}, {glued.group(2)} {glued.group(3)}, Germany'
    city = re.search(r'\b(\d{5})\s+([A-ZÄÖÜ][\wÄÖÜäöüß.\-]+),\s*(Germany|Deutschland)\b', body)
    return f'{city.group(2)}, {city.group(1)}, Germany' if city else ''


def industrystock_leads(log, search, phrases=None):
    """IndustryStock profiles from the public index; catalog pages stay unread."""
    from core import SearchBlocked
    log('IndustryStock: nazwy i adresy biorę z publicznego indeksu, stronę katalogu pomijam.')
    seen = set()
    rows = phrases if phrases is not None else INDUSTRYSTOCK_SEARCH_ROWS
    for row in rows:
        phrase = row['phrase'] if isinstance(row, dict) else str(row)
        negatives = row.get('negative_keywords', ()) if isinstance(row, dict) else ()
        priority = row.get('priority', 0) if isinstance(row, dict) else 0
        category = row.get('category', '') if isinstance(row, dict) else ''
        exclusions = ' '.join('-"' + item.replace('"', '') + '"' for item in negatives)
        query = ' '.join(part for part in (f'site:industrystock.com/en/company/profile "{phrase}"', exclusions) if part)
        log(f'IndustryStock, fraza: {phrase} · {category} · priorytet {priority}')
        try:
            hits = search(query) or []
        except SearchBlocked:
            raise
        except Exception as exc:
            log(f'IndustryStock, fraza „{phrase}”: {exc}')
            continue
        added = 0
        for hit in hits:
            found = industrystock_company(hit.get('title') or '', hit.get('url') or '')
            if not found or found[1] in seen:
                continue
            seen.add(found[1])
            body = hit.get('body') or ''
            yield dict(
                name=found[0], website='', source=found[1], address=industrystock_address(body),
                country='', province='', locality='', contacts=europages_contacts(body, found[1]),
                text=body, lat=None, lon=None, query=phrase, industrystock_category=category,
                industrystock_priority=priority, forging_probability=row.get('forging_probability', '') if isinstance(row, dict) else '')
            added += 1
        log(f'IndustryStock, fraza „{phrase}”: {added} firm.')
    log(f'IndustryStock: {len(seen)} firm z indeksu. Oficjalne strony czytam poza katalogiem.')


def hannovermesse_company(title, url):
    """Company name and canonical exhibitor profile from a public search result."""
    parsed = urlparse(str(url or '').split('#')[0])
    host = (parsed.hostname or '').lower().removeprefix('www.')
    parts = [part for part in parsed.path.split('/') if part]
    if parsed.scheme not in ('http', 'https') or host != 'hannovermesse.de':
        return None
    if len(parts) < 3 or parts[0] != 'aussteller' or not re.fullmatch(r'N\d+', parts[2]):
        return None
    slug, ident = parts[1], parts[2]
    if not slug:
        return None
    profile = f'{parsed.scheme}://{parsed.netloc}/aussteller/{slug}/{ident}'
    name = re.sub(r'^hannover messe\s+(?:aussteller|exhibitor)\s+\d{4}:\s*', '', str(title or ''), flags=re.I)
    name = _title_name(name) or _slug_name(slug)
    name = re.sub(r'\s+(?:auf|bei)\s+(?:der\s+)?hannover messe\b.*$', '', name, flags=re.I).strip()
    if not name or not _real_company(name):
        return None
    return name, profile


def hannovermesse_address(text):
    """Street printed in the public Hannover Messe snippet."""
    from geolocation import find_address
    body = re.sub(r'\s+', ' ', str(text or ''))
    found = find_address(body)
    if found:
        return found
    match = re.search(
        r'\b((?:[A-ZÄÖÜ][\wÄÖÜäöüß.\-]*\s+){0,3}(?:Straße|Strasse|Str\.|str\.)\s+\d{1,4}[A-Za-z]?)\s+(\d{5})\s+([A-ZÄÖÜ][\wÄÖÜäöüß.\-]+)',
        body)
    if match:
        country = 'Germany' if re.search(r'\b(?:Deutschland|Germany)\b', body) else ''
        return f'{match.group(1)}, {match.group(2)} {match.group(3)}' + (f', {country}' if country else '')
    return ''


def hannovermesse_leads(log, search, phrases=None):
    """Hannover Messe exhibitor profiles from the public index; catalog pages stay unread."""
    from core import SearchBlocked
    log('Hannover Messe: nazwy i adresy biorę z publicznego indeksu wystawców, stronę katalogu pomijam.')
    seen = set()
    rows = phrases if phrases is not None else HANNOVERMESSE_SEARCH_ROWS
    for row in rows:
        phrase = row['phrase'] if isinstance(row, dict) else str(row)
        negatives = row.get('negative_keywords', ()) if isinstance(row, dict) else ()
        priority = row.get('priority', 0) if isinstance(row, dict) else 0
        category = row.get('category', '') if isinstance(row, dict) else ''
        exclusions = ' '.join('-"' + item.replace('"', '') + '"' for item in negatives)
        query = ' '.join(part for part in (f'site:hannovermesse.de/aussteller "{phrase}"', exclusions) if part)
        log(f'Hannover Messe, fraza: {phrase} · {category} · priorytet {priority}')
        try:
            hits = search(query) or []
        except SearchBlocked:
            raise
        except Exception as exc:
            log(f'Hannover Messe, fraza „{phrase}”: {exc}')
            continue
        added = 0
        for hit in hits:
            found = hannovermesse_company(hit.get('title') or '', hit.get('url') or '')
            if not found or found[1] in seen:
                continue
            seen.add(found[1])
            body = hit.get('body') or ''
            yield dict(
                name=found[0], website='', source=found[1], address=hannovermesse_address(body),
                country='', province='', locality='', contacts=europages_contacts(body, found[1]),
                text=body, lat=None, lon=None, query=phrase, hannovermesse_category=category,
                hannovermesse_priority=priority, forging_probability=row.get('forging_probability', '') if isinstance(row, dict) else '')
            added += 1
        log(f'Hannover Messe, fraza „{phrase}”: {added} firm.')
    log(f'Hannover Messe: {len(seen)} wystawców z indeksu. Oficjalne strony czytam poza katalogiem.')


ARES_API = 'https://ares.gov.cz/ekonomicke-subjekty-v-be/rest'


def _ares_json(fetch, path, payload=None):
    """Call the public API below its documented 500 requests/minute limit."""
    from core import Cancelled
    url = ARES_API + path
    host = urlparse(url).hostname or ''
    wait = max(0, .14 - (time.monotonic() - fetch.last.get(host, 0)))
    if fetch.stop.wait(wait):
        raise Cancelled()
    fetch.check()
    fetch.last[host] = time.monotonic()
    response = fetch.session.request('POST' if payload is not None else 'GET', url, json=payload, headers={'Accept':'application/json'}, timeout=(8, 25))
    try:
        response.raise_for_status()
        return response.json()
    finally:
        response.close()


def _ares_address(value):
    value = value or {}
    text = str(value.get('textovaAdresa') or '').strip()
    country = str(value.get('nazevStatu') or '').strip()
    parts = [text] if text else []
    if country and country.casefold() not in text.casefold():
        parts.append(country)
    return ', '.join(parts)


def _ares_rzp(fetch, ico):
    """Return active RŽP establishments and trade descriptions for one IČO."""
    from core import Cancelled
    try:
        payload = _ares_json(fetch, '/ekonomicke-subjekty-rzp/' + ico)
    except Cancelled:
        raise
    except Exception:
        return [], []
    establishments, trades, seen = [], [], set()
    records = payload.get('zaznamy') or []
    primary = next((item for item in records if item.get('primarniZaznam')), records[0] if records else {})
    for trade in primary.get('zivnosti') or []:
        subject = str(trade.get('predmetPodnikani') or '').strip()
        if subject and subject not in trades:
            trades.append(subject)
        activities = [str(item.get('oborNazev') or '').strip() for item in trade.get('oboryCinnosti') or [] if item.get('oborNazev')]
        for activity in activities:
            if activity not in trades:
                trades.append(activity)
        for site in trade.get('provozovny') or []:
            if site.get('platnostDo'):
                continue
            address = _ares_address(site.get('sidloProvozovny'))
            ident = str(site.get('icp') or '') or address
            if not ident or ident in seen:
                continue
            seen.add(ident)
            establishments.append(dict(icp=str(site.get('icp') or ''), name=str(site.get('nazev') or '').strip(), address=address, activities=activities))
    return establishments, trades


def ares_companies(fetch, log, phrases=None):
    """Search public ARES by Czech business-name phrases and enrich from RŽP."""
    rows = phrases if phrases is not None else ARES_SEARCH_ROWS
    seen = set()
    for row in rows:
        phrase = row['phrase'] if isinstance(row, dict) else str(row)
        negatives = tuple(item.casefold() for item in (row.get('negative_keywords', ()) if isinstance(row, dict) else ()))
        log(f'ARES API, fraza nazwy firmy: {phrase}')
        try:
            payload = _ares_json(fetch, '/ekonomicke-subjekty/vyhledat', {'obchodniJmeno':phrase,'start':0,'pocet':100})
        except Exception as exc:
            log(f'ARES, fraza „{phrase}”: {exc}')
            continue
        added = 0
        for company in payload.get('ekonomickeSubjekty') or []:
            fetch.check()
            ico = str(company.get('ico') or company.get('icoId') or '').strip()
            name = str(company.get('obchodniJmeno') or '').strip()
            if not ico or not name or ico in seen or any(word in name.casefold() for word in negatives):
                continue
            seen.add(ico)
            registrations = company.get('seznamRegistraci') or {}
            establishments, trades = _ares_rzp(fetch, ico) if registrations.get('stavZdrojeRzp') == 'AKTIVNI' else ([], [])
            nace = list(dict.fromkeys(str(code) for code in (company.get('czNace2008') or company.get('czNace') or []) if code))
            dic = str(company.get('dic') or '').strip()
            source = f'https://ares.gov.cz/ekonomicke-subjekty?ico={ico}'
            text = ' · '.join(part for part in (name, 'CZ-NACE: '+', '.join(nace) if nace else '', *trades) if part)
            office=company.get('sidlo') or {}
            yield dict(name=name,website='',source=source,address=_ares_address(office),country='CZ',province='',locality=str(office.get('nazevObce') or ''),address_code=str(office.get('kodAdresnihoMista') or ''),contacts=[],text=text,lat=None,lon=None,query=phrase,establishments=establishments,business_ids={'nace':nace,'pkd':[],'nip':[],'regon':[],'krs':[],'ico':[ico],'dic':[dic] if dic else []})
            added += 1
        log(f'ARES, fraza „{phrase}”: {added} nowych firm.')
    log(f'ARES API: {len(seen)} unikalnych firm.')


def refresh_ares_addresses(records, fetch, log):
    """Backfill RÚIAN address codes for saved ARES rows created before this field existed."""
    from core import Cancelled
    pending=[record for record in records if 'ares.gov.cz' in str(record.get('source') or '') and not record.get('address_code') and ((record.get('business_ids') or {}).get('ico') or [])]
    if not pending:
        return
    log(f'ARES API: uzupełniam kody adresowe RÚIAN dla {len(pending)} firm.')
    done=0
    for record in pending:
        fetch.check()
        ico=str(record['business_ids']['ico'][0])
        try:
            company=_ares_json(fetch,'/ekonomicke-subjekty/'+ico)
        except Cancelled:
            raise
        except Exception as exc:
            log(f'ARES {ico}: nie udało się pobrać kodu adresu — {type(exc).__name__}.')
            continue
        office=company.get('sidlo') or {}
        record['address_code']=str(office.get('kodAdresnihoMista') or '')
        record['locality']=record.get('locality') or str(office.get('nazevObce') or '')
        done+=bool(record['address_code'])
    log(f'ARES API: pobrano {done} kodów adresowych RÚIAN.')


def collect(key,fetch,log,max_pages=None,search=None,wlw_phrases=None,europages_phrases=None,industrystock_phrases=None,hannovermesse_phrases=None,ares_phrases=None,een_phrases=None,een_countries=None,announcement_phrases=None,vdma_page=1):
 """Yield firm records from every published catalog page. max_pages stops early when set."""
 if key=='bvv':
  for row in bvv_collect(fetch,log,max_pages):
   row['catalog']=DIRECT_SOURCES['bvv'][0];yield row
  return
 if key=='europages':
  if search is None:
   log('Europages: pominięto odczyt stron katalogu, bo robots.txt na to nie pozwala.')
   return
  yield from europages_leads(log, search, europages_phrases)
  return
 if key=='wlw':
  if search is None:
   log('WLW: pominięto odczyt katalogu, bo robots.txt na to nie pozwala.')
   return
  yield from wlw_leads(log, search, wlw_phrases)
  return
 if key=='industrystock':
  if search is None:
   log('IndustryStock: pominięto odczyt stron katalogu — biorę profile z publicznego indeksu.')
   return
  yield from industrystock_leads(log, search, industrystock_phrases)
  return
 if key=='hannovermesse':
  if search is None:
   log('Hannover Messe: pominięto odczyt stron katalogu — biorę profile wystawców z publicznego indeksu.')
   return
  yield from hannovermesse_leads(log, search, hannovermesse_phrases)
  return
 if key=='ares':
  yield from ares_companies(fetch, log, ares_phrases)
  return
 if key=='een':
  yield from een_opportunities(fetch, log, een_phrases, een_countries)
  return
 if key=='techpilot':
  yield from techpilot_requests(fetch, log, announcement_phrases)
  return
 if key in GATED_SERVICES:
  yield from gated_announcements(key, log, announcement_phrases)
  return
 url=DIRECT_SOURCES[key][1];pending=[url];visited=set();emitted=set();pages=0
 while pending and (max_pages is None or pages<max_pages):
  fetch.check();url=pending.pop(0)
  if url in visited:continue
  visited.add(url);log('Katalog '+DIRECT_SOURCES[key][0]+': '+url)
  body,kind,final=fetch.page(url);pages+=1
  if key=='cetop' and ('pdf' in kind or final.lower().endswith('.pdf')):
   rows=parse_cetop(body,final,fetch.check)
  else:
   soup=BeautifulSoup(body,'html.parser');rows=parse_html(key,body,final)
   for a in soup.select('a[href]'):
    h=urljoin(final,a['href']);t=a.get_text(' ',strip=True).lower();allow=False
    if key=='cetop':allow=h.lower().endswith('.pdf') and 'directory' in h.lower()
    if a.get('rel')==['next'] and urlparse(h).hostname==urlparse(final).hostname:allow=True
    if allow and h not in visited and h not in pending:pending.append(h)
  for row in rows:
   fetch.check();ident=(row['name'].casefold(),row['website'] or row['source'])
   if ident in emitted:continue
   emitted.add(ident);row['catalog']=DIRECT_SOURCES[key][0]
   if key=='vdma' and vdma_page>1:continue
   if key=='vdma':row['vdma_page']=1
   yield row
  if key=='vdma':
   for row in vdma_more(body, final, fetch, log, max_pages, pages, max(2, vdma_page) if vdma_page>1 else 2):
    fetch.check();ident=(row['name'].casefold(),row['website'] or row['source'])
    if ident in emitted:continue
    emitted.add(ident);row['catalog']=DIRECT_SOURCES[key][0];yield row
 if pending and max_pages is not None:log('Osiągnięto limit stron katalogu — część list może pozostać nieodczytana.')
 if not emitted:log('Nie rozpoznano wpisów firm w tym katalogu; źródło pozostaje w Tropach do ręcznego sprawdzenia.')
