"""Build a reusable, offline industrial-company index and province polygons from PBF."""
import json
import re
import unicodedata
from pathlib import Path
from urllib.parse import urlparse

PROVINCES = ['dolnośląskie','kujawsko-pomorskie','lubelskie','lubuskie','łódzkie','małopolskie','mazowieckie','opolskie','podkarpackie','podlaskie','pomorskie','śląskie','świętokrzyskie','warmińsko-mazurskie','wielkopolskie','zachodniopomorskie']
GROUPS = ['Odbiorcy odkuwek','Odbiorcy części gotowych','Kooperacja CNC','Konkurencja','Współpraca','Klienci','Do weryfikacji']
LOCAL_OSM = {
    'PL': ('poland-latest.osm.pbf', 'osm_index.json', 'Polska'),
    'DE': ('germany-latest.osm.pbf', 'osm_index_de.json', 'Niemcy'),
    'CZ': ('czech-republic-latest.osm.pbf', 'osm_index_cz.json', 'Czechy'),
}

def norm(s):
    return ''.join(c for c in unicodedata.normalize('NFKD', str(s).lower().replace('ł','l')) if not unicodedata.combining(c))

def host(s):
    return (urlparse(s if '://' in s else 'https://' + s).hostname or '').removeprefix('www.')

def qualify(text, industry=False):
    t=norm(text)
    forging = re.search(r'\bkuznia\b|gesenkschmiede|forging company|produkcj\w* odkuwek|producent\w* odkuwek|kucie matrycowe',t)
    cnc = re.search(r'cnc|obrobk\w* skrawaniem|toczeni\w*|frezowani\w*',t)
    product = re.search(r'producent|produkcj|manufactur',t)
    target = re.search(r'maszyn|przekladn|zawor|armatur|hydraul|osi\b|podzespol|motoryzac|silownik',t)
    if forging:
        return ['Konkurencja'], 'Sygnał produkcji odkuwek: ' + forging.group()
    groups=[]
    if cnc: groups.append('Kooperacja CNC')
    if product and target: groups.extend(GROUPS[:2])
    return groups or ['Do weryfikacji'], 'Słowa w opisie; potencjalna rola, bez potwierdzenia zakupów' if groups else 'Brak wystarczających danych do określenia roli'

class LocalIndex:
    def __init__(self, root, country='PL'):
        if country not in LOCAL_OSM: raise ValueError('Brak konfiguracji lokalnego OSM dla kraju: '+str(country))
        self.root=Path(root);self.country=country
        pbf,cache,self.label=LOCAL_OSM[country]
        self.pbf=self.root/pbf;self.cache=self.root/cache;self.data=None;self.polygons=[]
    def ensure(self, stop, log):
        if not self.pbf.exists(): raise ValueError('Brak '+self.pbf.name+' w katalogu programu: '+str(self.root))
        st=self.pbf.stat()
        fingerprint=[st.st_size,st.st_mtime_ns,1] if self.country=='PL' else [st.st_size,st.st_mtime_ns,1,self.country]
        if self.cache.exists():
            try:
                d=json.loads(self.cache.read_text())
                if d.get('fingerprint')==fingerprint:
                    self.data=d;self.load_shapes();log(f'OSM lokalnie: gotowy indeks {len(d["companies"])} obiektów.');return
            except (OSError,ValueError,KeyError): pass
        import osmium
        from shapely.geometry import LineString, Polygon, mapping
        from shapely.ops import polygonize, unary_union
        def check():
            if stop.is_set(): raise InterruptedError('Przerwano budowanie indeksu OSM')
        ways={}; nodes={}; business=[]; boundaries=[]; needed_ways=set()
        log(f'OSM lokalnie ({self.label}), etap 1/3: odczyt zakładów. Pierwszy odczyt dużego pliku może potrwać kilka minut.')
        for o in osmium.FileProcessor(str(self.pbf)).with_filter(osmium.filter.KeyFilter('name')):
            check();t=dict(o.tags)
            if self.country=='PL' and o.is_relation() and t.get('admin_level')=='4' and t.get('ISO3166-2','').startswith('PL-'):
                name=t['name'].removeprefix('województwo ')
                if name in PROVINCES:
                    members=[(m.ref,m.role) for m in o.members if m.type=='w'];boundaries.append((name,members));needed_ways.update(w for w,r in members)
            relevant=t.get('man_made')=='works' or 'industrial' in t or t.get('craft') in ('metal_construction','metal_working','machining','toolmaker','agricultural_engines','blacksmith')
            if not relevant: continue
            item={'osm_type':o.type_str(),'osm_id':o.id,'tags':t}
            if o.is_node() and o.location.valid(): item['point']=[o.lon,o.lat]
            elif o.is_way():
                ways[o.id]=[n.ref for n in o.nodes];item['ways']=[(o.id,'outer')]
            elif o.is_relation():
                item['ways']=[(m.ref,m.role) for m in o.members if m.type=='w'];needed_ways.update(w for w,r in item['ways'])
            else: continue
            business.append(item)
        log(f'OSM lokalnie ({self.label}), etap 2/3: geometrie {len(business)} obiektów.')
        for o in osmium.FileProcessor(str(self.pbf),osmium.osm.WAY).with_filter(osmium.filter.IdFilter(needed_ways)):
            check();ways[o.id]=[n.ref for n in o.nodes]
        needed_nodes={n for w in ways.values() for n in w}
        log(f'OSM lokalnie, etap 3/3: współrzędne {len(needed_nodes)} węzłów; późniejsze uruchomienia użyją indeksu.')
        for o in osmium.FileProcessor(str(self.pbf),osmium.osm.NODE).with_filter(osmium.filter.IdFilter(needed_nodes)):
            check()
            if o.location.valid(): nodes[o.id]=(o.lon,o.lat)
        def geometry(members):
            outer=[];inner=[]
            for wid,role in members:
                refs=ways.get(wid,[])
                if len(refs)<2 or any(n not in nodes for n in refs): return None
                (inner if role=='inner' else outer).append(LineString([nodes[n] for n in refs]))
            try:
                polys=list(polygonize(outer))
                if not polys:return None
                g=unary_union(polys)
                holes=list(polygonize(inner))
                if holes:
                    g=g.difference(unary_union(holes))
            except Exception:
                return None
            if g is None or g.is_empty:
                return None
            return g
        shapes=[]
        for name,members in boundaries:
            check();g=geometry(members)
            if g is None or g.is_empty or not g.is_valid: continue
            shapes.append((name,g))
        self.polygons=shapes
        companies=[]
        for item in business:
            check()
            xy=item.get('point')
            if xy is None:
                g=geometry(item.get('ways',[]))
                if g is None: continue
                p=g.representative_point();xy=[p.x,p.y]
            province=self.province(xy[1],xy[0]) if self.country=='PL' else ''
            t=item['tags'];name=t['name'];source='https://www.openstreetmap.org/'+{'n':'node','w':'way','r':'relation'}[item['osm_type']]+'/'+str(item['osm_id'])
            companies.append(dict(name=name,lat=xy[1],lon=xy[0],province=province,country=self.country,website=t.get('website',t.get('contact:website','')),address=', '.join(t[k] for k in ('addr:street','addr:housenumber','addr:postcode','addr:city') if t.get(k)),email=t.get('contact:email',t.get('email','')),phone=t.get('contact:phone',t.get('phone','')),source=source,text=' '.join([name]+[t.get(k,'') for k in ('description','product','industrial','craft','operator')]),geo_precision='Punkt obiektu OSM' if item['osm_type']=='n' else 'Punkt wewnątrz obszaru zakładu OSM'))
        self.data=dict(fingerprint=fingerprint,country=self.country,companies=companies,provinces=[{'name':n,'geometry':mapping(g)} for n,g in shapes])
        tmp=self.cache.with_suffix('.tmp');tmp.write_text(json.dumps(self.data,ensure_ascii=False),encoding='utf-8');tmp.replace(self.cache)
        log(f'OSM lokalnie ({self.label}): zapisano {len(companies)} obiektów przemysłowych z lokalizacją.')
    def load_shapes(self):
        from shapely.geometry import shape
        self.polygons=[(p['name'],shape(p['geometry'])) for p in self.data['provinces']]
        import shapely
        for name,g in self.polygons: shapely.prepare(g)
    def province(self,lat,lon):
        from shapely.geometry import Point
        if lat is None or lon is None:return ''
        p=Point(lon,lat)
        return next((n for n,g in self.polygons if g.covers(p)),'')
    def find(self,record):
        h=host(record.get('website',''));name=norm(record.get('name','')).strip()
        def clean_name(s):
            s=norm(s)
            s=re.sub(r'\bsp[.]?\s*z[.]?\s*o[.]?\s*o[.]?|\bs[.]?a[.]?\b|\bspolka z ograniczona odpowiedzialnoscia\b', '', s)
            return ' '.join(re.findall(r'[a-z0-9]+',s))
        candidates=[r for r in self.data['companies'] if (h and host(r['website'])==h) or (len(name)>4 and norm(r['name']).strip()==name)]
        if not candidates:
            n=clean_name(record.get('name',''))
            if len(n)>5:candidates=[r for r in self.data['companies'] if clean_name(r['name'])==n]
        if len(candidates)>1 and record.get('address'):
            address=norm(record['address']);candidates=[r for r in candidates if r['address'] and norm(r['address']) in address]
        if len(candidates)==1:return candidates[0]
        return None

    def locate_addresses(self, records, stop, log):
        """Resolve many exact addresses in one offline PBF pass."""
        if self.country == 'CZ':
            return self._locate_postal_houses(records, stop, log, countries={'cz', 'czechia', 'česká republika', 'ceska republika'}, postal=r'\b(\d{3})\s?(\d{2})\b', join_postal=True, label='Czechy')
        if self.country == 'DE':
            return self._locate_postal_houses(records, stop, log, countries={'de', 'deutschland', 'germany', 'niemcy'}, postal=r'\b(\d{5})\b', join_postal=False, label='Niemcy')
        return 0

    def _locate_postal_houses(self, records, stop, log, countries, postal, join_postal, label):
        wanted={};wanted_codes={}
        for record in records:
            if record.get('lat') is not None:
                continue
            country=str(record.get('country') or '').strip().casefold()
            if country and country not in countries:
                continue
            code=re.sub(r'\D','',str(record.get('address_code') or ''))
            if self.country=='CZ' and code:
                wanted_codes.setdefault(code,[]).append(record)
            address=' '.join(str(record.get('address') or '').split())
            match=re.search(postal,address)
            if not match:
                continue
            before=address[:match.start()]
            numbers=re.findall(r'\d+(?:/\d+)?[A-Za-z]?',before)
            if not numbers:
                continue
            digits=''.join(match.groups()) if join_postal else match.group(1)
            key=(digits,re.sub(r'\W','',numbers[-1]).casefold())
            wanted.setdefault(key,[]).append((record,norm(address)))
        if not wanted and not wanted_codes:
            return 0
        import osmium
        found={}
        log(f'OSM lokalnie ({label}): szukam {sum(map(len,wanted.values()))} dokładnych adresów firm.')
        for node in osmium.FileProcessor(str(self.pbf),osmium.osm.NODE).with_filter(osmium.filter.KeyFilter('addr:housenumber')):
            if stop.is_set(): raise InterruptedError('Przerwano lokalizowanie w lokalnym OSM')
            if not node.location.valid():continue
            tags=dict(node.tags)
            if self.country=='CZ':
                code=re.sub(r'\D','',tags.get('ref:ruian:addr',''))
                for record in wanted_codes.get(code,[]) if code else ():
                    place=', '.join(part for part in (tags.get('addr:street') or tags.get('addr:place',''),tags.get('addr:housenumber',''),tags.get('addr:postcode',''),tags.get('addr:city','')) if part)
                    found.setdefault(id(record),[]).append((100,node.lat,node.lon,place,node.id,record))
            postcode=re.sub(r'\D','',tags.get('addr:postcode',''))
            house=re.sub(r'\W','',tags.get('addr:housenumber','')).casefold()
            targets=wanted.get((postcode,house))
            if not targets:continue
            place=', '.join(part for part in (tags.get('addr:street') or tags.get('addr:place',''),tags.get('addr:housenumber',''),tags.get('addr:postcode',''),tags.get('addr:city','')) if part)
            candidate=set(re.findall(r'[a-z0-9]+',norm(place)))
            for record,address in targets:
                overlap=len(candidate&set(re.findall(r'[a-z0-9]+',address)))
                found.setdefault(id(record),[]).append((overlap,node.lat,node.lon,place,node.id,record))
        matched=0
        for candidates in found.values():
            candidates.sort(key=lambda item:item[0],reverse=True)
            best=candidates[0]
            if best[0]<2 or (len(candidates)>1 and candidates[1][0]==best[0] and (candidates[1][1],candidates[1][2])!=(best[1],best[2])):
                continue
            _score,lat,lon,place,node_id,record=best
            record.update(lat=lat,lon=lon,geo_source=f'https://www.openstreetmap.org/node/{node_id}',geo_precision=f'Dokładny adres z lokalnego OSM ({label})',geo_label=place,osm_check='Adres dopasowany w lokalnym OSM')
            matched+=1
        log(f'OSM lokalnie ({label}): dopasowano {matched} z {sum(map(len,wanted.values()))} adresów.')
        return matched
