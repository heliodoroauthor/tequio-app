#!/usr/bin/env python3
"""Scrape RSS oficial: DOF (sumario.xml) + SCJN/SEGOB/Presidencia/Diputados (HTML)."""
import os, re, sys, time, hashlib, requests
from bs4 import BeautifulSoup
from datetime import datetime
from xml.etree import ElementTree as ET

SB_URL = os.environ.get('SUPABASE_URL', '').rstrip('/')
SB_KEY = os.environ.get('SUPABASE_SERVICE_ROLE_KEY', '')
MAX_PER_FUENTE = int(os.environ.get('MAX_PER_FUENTE', '30'))

UA = {'User-Agent': 'TequioNewsBot/1.0 (+https://tequio.app)'}


def clean(s):
    return re.sub(r'\s+', ' ', (s or '')).strip()


def url_hash(u):
    return hashlib.sha256((u or '').encode('utf-8')).hexdigest()[:32]


def clasificar_tema(titulo):
    t = (titulo or '').lower()
    if any(k in t for k in ['corrupc', 'desvio', 'fraude', 'soborno', 'lavado']):
        return 'corrupcion'
    if any(k in t for k in ['seguridad', 'guardia nacional', 'sedena', 'detencion', 'crimen', 'narco']):
        return 'seguridad'
    if any(k in t for k in ['salud', 'imss', 'issste', 'medicamento', 'hospital', 'pandemia']):
        return 'salud'
    if any(k in t for k in ['educacion', 'sep', 'escuela', 'universidad', 'maestro']):
        return 'educacion'
    if any(k in t for k in ['ambient', 'agua', 'ecolog', 'clima', 'conagua', 'semarnat']):
        return 'medio_ambiente'
    if any(k in t for k in ['econom', 'fiscal', 'impuest', 'hacienda', 'banxico', 'isr', 'iva', 'inflacion']):
        return 'economia'
    if any(k in t for k in ['derechos humanos', 'discriminaci', 'indigen', 'igualdad', 'genero']):
        return 'derechos'
    return 'politica'


def upsert(row):
    url = SB_URL + '/rest/v1/noticias_civicas?on_conflict=hash_url'
    try:
        r = requests.post(url, json=row, headers={
            'Content-Type': 'application/json',
            'apikey': SB_KEY,
            'Authorization': 'Bearer ' + SB_KEY,
            'Prefer': 'resolution=merge-duplicates,return=minimal',
        }, timeout=20)
        return r.ok
    except Exception:
        return False


def scrape_dof():
    print('\n[DOF] Sumario diario...')
    n = 0
    try:
        r = requests.get('https://dof.gob.mx/sumario.xml', headers=UA, timeout=30, verify=False)
        root = ET.fromstring(r.content)
        # RSS items
        for item in root.iter('item'):
            if n >= MAX_PER_FUENTE:
                break
            tit = clean((item.find('title').text if item.find('title') is not None else '') or '')
            lnk = clean((item.find('link').text if item.find('link') is not None else '') or '')
            desc = clean((item.find('description').text if item.find('description') is not None else '') or '')
            fecha = clean((item.find('pubDate').text if item.find('pubDate') is not None else '') or '')
            if not tit or not lnk:
                continue
            row = {
                'hash_url': url_hash(lnk),
                'titulo': tit[:500],
                'resumen': desc[:800],
                'url_oficial': lnk,
                'fuente': 'DOF',
                'fuente_url': 'https://dof.gob.mx',
                'ambito': 'nacional',
                'tema': clasificar_tema(tit + ' ' + desc),
            }
            try:
                row['fecha_publicacion'] = datetime.strptime(fecha[:25], '%a, %d %b %Y %H:%M:%S').isoformat()
            except Exception:
                pass
            if upsert(row):
                n += 1
    except Exception as e:
        print('  err DOF:', str(e)[:120])
    print('  ->', n, 'items DOF')
    return n


def scrape_gob_mx_prensa(slug, fuente_label):
    """Scrape archivo de prensa de gob.mx (SEGOB, Presidencia, etc.)."""
    print(f'\n[{fuente_label}] Archivo prensa...')
    n = 0
    base = f'https://www.gob.mx/{slug}/archivo/prensa'
    try:
        r = requests.get(base, headers=UA, timeout=30)
        soup = BeautifulSoup(r.text, 'lxml')
        # Cada noticia es típicamente un <article> con <h3> y enlace
        for art in soup.select('article, .archive-list-item, .views-row, h3')[:MAX_PER_FUENTE * 2]:
            if n >= MAX_PER_FUENTE:
                break
            a = art.find('a', href=True) if hasattr(art, 'find') else None
            if not a:
                continue
            tit = clean(a.get_text())
            href = a.get('href', '')
            if not tit or len(tit) < 15:
                continue
            if href.startswith('/'):
                href = 'https://www.gob.mx' + href
            elif not href.startswith('http'):
                continue
            row = {
                'hash_url': url_hash(href),
                'titulo': tit[:500],
                'resumen': tit[:800],
                'url_oficial': href,
                'fuente': fuente_label,
                'fuente_url': base,
                'ambito': 'nacional',
                'tema': clasificar_tema(tit),
            }
            if upsert(row):
                n += 1
    except Exception as e:
        print('  err:', str(e)[:120])
    print('  ->', n, 'items', fuente_label)
    return n


def scrape_scjn():
    print('\n[SCJN] Comunicados...')
    n = 0
    try:
        r = requests.get('https://www.scjn.gob.mx/multimedia/comunicados', headers=UA, timeout=30, verify=False)
        soup = BeautifulSoup(r.text, 'lxml')
        for a in soup.find_all('a', href=True):
            if n >= MAX_PER_FUENTE:
                break
            tit = clean(a.get_text())
            href = a.get('href', '')
            if not tit or len(tit) < 25:
                continue
            if 'comunicado' not in href.lower() and 'noticia' not in href.lower():
                continue
            if href.startswith('/'):
                href = 'https://www.scjn.gob.mx' + href
            elif not href.startswith('http'):
                continue
            row = {
                'hash_url': url_hash(href),
                'titulo': tit[:500],
                'resumen': tit[:800],
                'url_oficial': href,
                'fuente': 'SCJN',
                'fuente_url': 'https://www.scjn.gob.mx/multimedia/comunicados',
                'ambito': 'nacional',
                'tema': 'politica',
            }
            if upsert(row):
                n += 1
    except Exception as e:
        print('  err SCJN:', str(e)[:120])
    print('  ->', n, 'items SCJN')
    return n


if __name__ == '__main__':
    print('Tequio Scraper Noticias')
    if not (SB_URL and SB_KEY):
        print('ERR: faltan vars Supabase')
        sys.exit(1)
    total = 0
    total += scrape_dof()
    total += scrape_gob_mx_prensa('segob', 'SEGOB')
    total += scrape_gob_mx_prensa('presidencia', 'Presidencia')
    total += scrape_scjn()
    print(f'\nTotal items: {total}')
