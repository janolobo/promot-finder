"""PDF of an explicitly selected snapshot, never the unfiltered database."""
import io
from pathlib import Path
from xml.sax.saxutils import escape


def export_pdf(snapshot, ids, filters):
    from reportlab.pdfgen import canvas
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, KeepTogether
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.lib import colors
    fonts=[Path(__file__).parent/'fonts'/'DejaVuSans.ttf',Path('/System/Library/Fonts/Supplemental/Arial.ttf'),Path('C:/Windows/Fonts/arial.ttf'),Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')]
    font=next((p for p in fonts if p.exists()),None)
    if font is None:raise ValueError('Brak czcionki Unicode do PDF. Zainstaluj Arial albo DejaVu Sans.')
    if 'PromotUnicode' not in pdfmetrics.getRegisteredFontNames():pdfmetrics.registerFont(TTFont('PromotUnicode',str(font)))
    wanted=set(ids);rows=[r for r in snapshot['records'] if r['id'] in wanted]
    if not rows:raise ValueError('Brak firm do eksportu')
    out=io.BytesIO();styles=getSampleStyleSheet()
    for style in styles.byName.values():style.fontName='PromotUnicode'
    styles['Normal'].fontSize=9;styles['Normal'].leading=13
    story=[Paragraph('PROMOT — przefiltrowane firmy',styles['Title']),Paragraph(escape(f"Liczba firm: {len(rows)} · Sesja: {snapshot['run_id']}"),styles['Normal'])]
    text=' · '.join(str(filters.get(k,'')) for k in ('group','scope','filter') if filters.get(k)) or 'Wszystkie widoczne wyniki'
    story.extend([Paragraph('Filtry: '+escape(text),styles['Normal']),Spacer(1,14)])
    def paragraph(text):return Paragraph(escape(str(text or '')),styles['Normal'])
    for i,r in enumerate(rows,1):
        story.append(KeepTogether([Paragraph(escape(f"{i}. {r['name']}"),styles['Heading2']),paragraph(' · '.join(r.get('groups',[])))]))
        for label,key in [('Województwo','province'),('Adres','address'),('Katalog','catalog'),('Dokładność','geo_precision'),('Weryfikacja OSM','osm_check')]:
            if r.get(key):story.append(paragraph(label+': '+str(r[key])))
        if r.get('lat') is not None:story.append(paragraph(f"Współrzędne: {r['lat']}, {r['lon']}"))
        for label,key in [('Strona firmy','website'),('Źródło firmy','source')]:
            url=r.get(key,'')
            if url.startswith(('https://','http://')):story.append(Paragraph(f'<link href="{escape(url, {chr(34): "&quot;"})}" color="#087f87">{label}: {escape(url)}</link>',styles['Normal']))
        for c in r.get('contacts',[]):
            story.append(paragraph(' · '.join(str(c.get(k,'') or '') for k in ['person','role','email','phone'] if c.get(k))))
        story.append(Spacer(1,10))
    story.append(paragraph('Kandydaci do kwalifikacji handlowej. Dane i lokalizacje wymagają sprawdzenia. © OpenStreetMap contributors, ODbL.'))
    def footer(c,doc):
        c.setFont('PromotUnicode',8);c.setFillColor(colors.grey);c.drawRightString(A4[0]-36,22,f'Strona {doc.page}')
    SimpleDocTemplate(out,pagesize=A4,rightMargin=36,leftMargin=36,topMargin=36,bottomMargin=36,title='PROMOT — przefiltrowane firmy').build(story,onFirstPage=footer,onLaterPages=footer)
    return out.getvalue()
