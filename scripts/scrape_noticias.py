#!/usr/bin/env python3
"""Scrape RSS oficial: DOF + SEGOB + Presidencia + SCJN."""
import os, re, sys, time, hashlib, requests
from bs4 import BeautifulSoup
from xml.etree import ElementTree as ET

SB_URL = os.environ.get('SUPABASE_URL', '').rstrip('/')
SB_KEY = os.environ.get('SUPABASE_SERVICE_ROLE_KEY', '')
MAX_PER_FUENTE = int(os.environ.get('MAX_PER_FUENTE', '40'))
UA = {'User-Agent': 'TequioNewsBot/1.0 (+https://tequio.app)'}

MESES = {'enero':1,'febrero':2,'marzo':3,'abril':4,'mayo':5,'junio':6,
         'julio':7,'agosto':8,'septiembre':9,'octubre':10,'noviembre':11,'diciembre':12}


def clean(s):
    return re.sub(r'\s+', ' ', (s or '')).strip()


def url_hash(u):
    return hashlib.sha256((u or '').encode('utf-8')).hexdigest()[:32]


def parse_fecha_es(txt):
    m = re.search(r'(\d{1,2})\s+de\s+(\w+)\s+de\s+(\d{4})', txt or '')
    if not m: return None
    mes = MESES.get(m.group(2).lower())
    if not mes: return None
    try:
        return f'{m.group(3)}-{mes:02d}-{int(m.group(1)):02d}T12:00:00+00:00'
    except Exception:
        return None


def clasificar_tema(txt):
    t = (txt or '').lower()
    if any(k in t for k in ['corrupc','desvio','fraude','soborno','lavado']): return 'corrupcion'
    if any(k in t for k in ['seguridad','guardia nacional','sedena','detencion','crimen','narco']): return 'seguridad'
    if any(k in t for k in ['salud','imss','issste','medicamento','hospital','pandemia']): return 'salud'
    if any(k in t for k in ['educacion','sep','escuela','universidad','maestro']): return 'educacion'
    if any(k in t for k in ['ambient','agua','ecolog','clima','conagua','semarnat']): return 'medio_ambiente'
    if any(k in t for k in ['econom','fiscal','impuest','hacienda','banxico','inflacion']): return 'economia'
    if any(k in t for k in ['derechos humanos','discriminaci','indigen','igualdad','genero']): return 'derechos'
    return 'politica'


def upsert(row):
    try:
        r = requests.post(SB_URL + '/rest/v1/noticias_civicas?on_conflict=hash_url',
            json=row, headers={
                'Content-Type': 'application/json',
                'apikey': SB_KEY,
                'Authorization': 'Bearer ' + SB_KEY,
                'Prefer': 'resolution=merge-duplicates,return=minimal',
            }, timeout=20)
        return r.ok
    except Exception:
        return False


def scrape_dof():
    print('\n[DOF] sumario.xml...')
    n = 0
    try:
        r = requests.get('https://dof.gob.mx/sumario.xml', headers=UA, timeout=30, verify=False)
        root = ET.fromstring(r.content)
        for item in root.iter('item'):
            if n >= MAX_PER_FUENTE: break
            tit = clean(item.findtext('title') or '')
            lnk = clean(item.findtext('link') or '')
            desc = clean(item.findtext('description') or '')
            if not tit or not lnk: continue
            row = {
                'hash_url': url_hash(lnk),
                'titulo': tit[:500], 'resumen': desc[:800],
                'url_oficial': lnk, 'fuente': 'DOF',
                'fuente_url': 'https://dof.gob.mx',
                'ambito': 'nacional', 'tema': clasificar_tema(tit + ' ' + desc),
            }
            if upsert(row): n += 1
    except Exception as e:
        print('  err:', str(e)[:120])
    print('  ->', n, 'items')
    return n


def scrape_gob_mx(slug, label):
    """gob.mx archivo prensa. Estructura: <article><h2>titulo</h2><a href>/prensa/</a></article>."""
    print(f'\n[{label}] prensa...')
    n = 0
    base = f'https://www.gob.mx/{slug}/archivo/prensa'
    paginas = [base, base + '?order=DESC&page=1', base + '?order=DESC&page=2']
    for url_pag in paginas:
        if n >= MAX_PER_FUENTE: break
        try:
            r = requests.get(url_pag, headers=UA, timeout=30)
            soup = BeautifulSoup(r.text, 'lxml')
        except Exception:
            continue
        for art in soup.find_all('article'):
            if n >= MAX_PER_FUENTE: break
            h = art.find(['h2', 'h3'])
            tit = clean(h.get_text()) if h else ''
            href = ''
            for a in art.find_all('a', href=True):
                hh = a.get('href', '')
                if '/prensa/' in hh:
                    href = hh
                    break
            if not tit or not href or len(tit) < 15: continue
            if href.startswith('/'): href = 'https://www.gob.mx' + href
            txt = clean(art.get_text())
            row = {
                'hash_url': url_hash(href),
                'titulo': tit[:500],
                'resumen': txt[:800].replace('Continuar leyendo', '').strip(),
                'url_oficial': href, 'fuente': label,
                'fuente_url': base,
                'ambito': 'nacional', 'tema': clasificar_tema(tit + ' ' + txt),
            }
            f = parse_fecha_es(txt)
            if f: row['fecha_publicacion'] = f
            if upsert(row): n += 1
        time.sleep(1)
    print('  ->', n, 'items')
    return n


def scrape_scjn():
    print('\n[SCJN] comunicados...')
    n = 0
    try:
        r = requests.get('https://www.scjn.gob.mx/multimedia/comunicados',
            headers=UA, timeout=30, verify=False)
        soup = BeautifulSoup(r.text, 'lxml')
        for a in soup.find_all('a', href=True):
            if n >= MAX_PER_FUENTE: break
            tit = clean(a.get_text())
            href = a.get('href', '')
            if not tit or len(tit) < 25: continue
            if 'comunicado' not in href.lower() and 'noticia' not in href.lower(): continue
            if href.startswith('/'): href = 'https://www.scjn.gob.mx' + href
            elif not href.startswith('http'): continue
            row = {
                'hash_url': url_hash(href),
                'titulo': tit[:500], 'resumen': tit[:800],
                'url_oficial': href, 'fuente': 'SCJN',
                'fuente_url': 'https://www.scjn.gob.mx/multimedia/comunicados',
                'ambito': 'nacional', 'tema': clasificar_tema(tit),
            }
            if upsert(row): n += 1
    except Exception as e:
        print('  err:', str(e)[:120])
    print('  ->', n, 'items')
    return n


if __name__ == '__main__':
    print('Tequio Scraper Noticias')
    if not (SB_URL and SB_KEY):
        print('ERR: faltan vars Supabase')
        sys.exit(1)
    total = scrape_dof()
    total += scrape_gob_mx('segob', 'SEGOB')
    total += scrape_gob_mx('presidencia', 'Presidencia')
    total += scrape_scjn()
    print(f'\nTotal: {total}')
