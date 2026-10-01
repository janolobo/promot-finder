"""Direct public directories. No search engine, credentials or provider fallback."""
import io
import json
import re
from urllib.parse import urljoin, urlparse, urlencode
from bs4 import BeautifulSoup

DIRECT_SOURCES = {
 'pgm':('PGM — członkowie','https://pgm.org.pl/czlonkowie/'),
 'bvv':('BVV / MSV — wystawcy','https://tikatalog.bvv.cz/'),
 'agrotech':('AGROTECH — wystawcy','https://www.targikielce.pl/pl/agrotech'),
 'vdma':('VDMA — firmy członkowskie','https://www.vdma.org/service-firmensuche-produktsuche'),
 'cetop':('CETOP — katalog PDF','https://www.cetop.org/directory/'),
 'europages':('Europages — firmy w Polsce','https://www.europages.pl/przedsiebiorstwa/polska.html'),
 'kompass':('Kompass — firmy w Polsce','https://pl.kompass.com/')}


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
 elif key in ('agrotech','bvv'):
  selector='.main-title a[href]' if key=='agrotech' else 'a[href*="_fdc"]'
  seen=set()
  for a in soup.select(selector):
   u=urljoin(url,a['href']);name=a.get_text(' ',strip=True)
   if not name or u in seen:continue
   seen.add(u);card=a.find_parent('tr') if key=='agrotech' else a.parent
   country=''
   if key=='agrotech' and card:
    cells=card.find_all('td',recursive=False)
    if len(cells)>2:country=cells[2].get_text(' ',strip=True)
   rows.append(entry(name,'',u,str(card or a),country=country))
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


def collect(key,fetch,log,max_pages=None):
 """Yield firm records from every published catalog page. max_pages stops early when set."""
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
   if key=='bvv' and 'TESTOVACÍ VERZE' in soup.get_text():log('BVV oznacza tę stronę jako wersję testową — wpisy wymagają weryfikacji.')
   for a in soup.select('a[href]'):
    h=urljoin(final,a['href']);t=a.get_text(' ',strip=True).lower();allow=False
    if key=='cetop':allow=h.lower().endswith('.pdf') and 'directory' in h.lower()
    if key=='vdma':allow=urlparse(h).path.endswith('/mitglieder')
    if key=='agrotech':allow=('agrotech' in h and ('lista-wystawcow' in h and not re.search(r',\d+',h) or t=='poprzednia edycja'))
    if key=='bvv':allow='msv' in h and '_fexc' in h and 'nom=0' in h and '2029' not in t
    if a.get('rel')==['next'] and urlparse(h).hostname==urlparse(final).hostname:allow=True
    if allow and h not in visited and h not in pending:pending.append(h)
   if key=='vdma' and rows:
    # Same public pagination URL and field used by the page's own loadPage().
    raw=body.decode('utf-8','replace') if isinstance(body,bytes) else body
    endpoint=re.search(r"url:\s*'(https://[^']+p_p_resource_id=getPage[^']*)'",raw)
    count=re.search(r'pageCount:\s*(\d+)',raw)
    if endpoint and count:
     namespace=re.search(r"'([^']+_page)'\s*:\s*currentPage",raw)
     if namespace:
      last=int(count.group(1)) if max_pages is None else min(int(count.group(1)),max_pages-pages+1)
      for page in range(2,last+1):
       fetch.check();u=endpoint.group(1)+'&'+urlencode({namespace.group(1):page})
       b,k,f=fetch.page(u);pages+=1
       payload=json.loads(b);data=payload.get('publicUserList',{})
       if isinstance(data,str):data=json.loads(data)
       for item in data.get('content',[]):
        web=item.get('webAddr','') or '';web=web if '://' in web else 'https://'+web if web else ''
        r=entry(item.get('companyName',''),web,final+'#member-'+str(item.get('id','')),address=', '.join(str(item.get(x) or '') for x in ['address','plz','city','country']),country=item.get('country',''))
        r['contacts']=[dict(person='',role='',email=item.get('email') or '',phone=item.get('phoneNum') or '',source=r['source'],status='Kontakt firmy z katalogu VDMA')];rows.append(r)
      if max_pages is not None and int(count.group(1))>max_pages:log('VDMA: osiągnięto limit stron katalogu; import częściowy.')
   if key=='agrotech' and rows:log('AGROTECH: odczyt publicznego HTML; dynamiczna dalsza część listy może wymagać dodatkowych adresów.')
  for row in rows:
   fetch.check();ident=(row['name'].casefold(),row['website'] or row['source'])
   if ident in emitted:continue
   emitted.add(ident);row['catalog']=DIRECT_SOURCES[key][0];yield row
 if pending and max_pages is not None:log('Osiągnięto limit stron katalogu — część list może pozostać nieodczytana.')
 if not emitted:log('Nie rozpoznano wpisów firm w tym katalogu; źródło pozostaje w Tropach do ręcznego sprawdzenia.')
