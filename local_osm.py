"""Build a reusable, offline industrial-company index and province polygons from PBF."""
import json
import re
import unicodedata
from pathlib import Path
from urllib.parse import urlparse

PROVINCES = ['dolnośląskie','kujawsko-pomorskie','lubelskie','lubuskie','łódzkie','małopolskie','mazowieckie','opolskie','podkarpackie','podlaskie','pomorskie','śląskie','świętokrzyskie','warmińsko-mazurskie','wielkopolskie','zachodniopomorskie']
GROUPS = ['Odbiorcy odkuwek','Odbiorcy części gotowych','Kooperacja CNC','Konkurencja','Do weryfikacji']

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
    def __init__(self, root):
        self.root=Path(root); self.pbf=self.root/'poland-latest.osm.pbf'
        self.cache=self.root/'osm_index.json'; self.data=None;self.polygons=[]
    def ensure(self, stop, log):
        if not self.pbf.exists(): raise ValueError('Brak poland-latest.osm.pbf w katalogu programu: '+str(self.root))
        st=self.pbf.stat(); fingerprint=[st.st_size,st.st_mtime_ns,1]
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
        log('OSM lokalnie, etap 1/3: odczyt zakładów i granic województw. Pierwszy odczyt pliku 2 GB może potrwać kilka minut.')
        for o in osmium.FileProcessor(str(self.pbf)).with_filter(osmium.filter.KeyFilter('name')):
            check();t=dict(o.tags)
            if o.is_relation() and t.get('admin_level')=='4' and t.get('ISO3166-2','').startswith('PL-'):
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
        if len({x[0] for x in boundaries})!=16: raise ValueError('PBF nie zawiera wszystkich 16 granic województw — nie przypisuję ich na podstawie prostokątów')
        log(f'OSM lokalnie, etap 2/3: geometrie {len(business)} obiektów i 16 województw.')
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
            polys=list(polygonize(outer))
            if not polys:return None
            g=unary_union(polys)
            holes=list(polygonize(inner))
            return g.difference(unary_union(holes)) if holes else g
        shapes=[]
        for name,members in boundaries:
            check();g=geometry(members)
            if g is None or g.is_empty or not g.is_valid: raise ValueError('Niepełna granica województwa: '+name)
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
            province=self.province(xy[1],xy[0])
            if not province:continue
            t=item['tags'];name=t['name'];source='https://www.openstreetmap.org/'+{'n':'node','w':'way','r':'relation'}[item['osm_type']]+'/'+str(item['osm_id'])
            companies.append(dict(name=name,lat=xy[1],lon=xy[0],province=province,website=t.get('website',t.get('contact:website','')),address=', '.join(t[k] for k in ('addr:street','addr:housenumber','addr:postcode','addr:city') if t.get(k)),email=t.get('contact:email',t.get('email','')),phone=t.get('contact:phone',t.get('phone','')),source=source,text=' '.join([name]+[t.get(k,'') for k in ('description','product','industrial','craft','operator')]),geo_precision='Punkt obiektu OSM' if item['osm_type']=='n' else 'Punkt wewnątrz obszaru zakładu OSM'))
        self.data=dict(fingerprint=fingerprint,companies=companies,provinces=[{'name':n,'geometry':mapping(g)} for n,g in shapes])
        tmp=self.cache.with_suffix('.tmp');tmp.write_text(json.dumps(self.data,ensure_ascii=False),encoding='utf-8');tmp.replace(self.cache)
        log(f'OSM lokalnie: zapisano {len(companies)} obiektów przemysłowych z lokalizacją i województwem.')
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
