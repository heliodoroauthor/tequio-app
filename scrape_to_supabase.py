#!/usr/bin/env python3
"""Scrape leyes federales de diputados.gob.mx/LeyesBiblio -> Supabase."""
import os, re, sys, time, requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin

GEMINI = os.environ.get('GEMINI_API_KEY', '')
SB_URL = os.environ.get('SUPABASE_URL', '').rstrip('/')
SB_KEY = os.environ.get('SUPABASE_SERVICE_ROLE_KEY', '')
MAX_LEYES = int(os.environ.get('MAX_LEYES', '500'))
EMBED_DELAY = float(os.environ.get('EMBED_DELAY', '0.3'))
SKIP_EMB = os.environ.get('SKIP_EMBEDDING', 'false').lower() == 'true'

UA = {'User-Agent': 'TequioLegalBot/1.0 (+https://tequio.app)'}


def clean(s):
    return re.sub(r'\s+', ' ', (s or '')).strip()


def emb(text):
    if SKIP_EMB or not GEMINI:
        return None
    try:
        url = 'https://generativelanguage.googleapis.com/v1beta/models/gemini-embedding-001:embedContent?key=' + GEMINI
        r = requests.post(url, json={
            'model': 'models/gemini-embedding-001',
            'content': {'parts': [{'text': text[:6000]}]},
            'outputDimensionality': 768,
            'taskType': 'RETRIEVAL_DOCUMENT',
        }, timeout=30)
        if r.status_code == 429:
            time.sleep(8)
            return None
        r.raise_for_status()
        v = r.json().get('embedding', {}).get('values')
        return v if v and len(v) == 768 else None
    except Exception:
        return None


def upsert(table, row, conflict='nombre'):
    url = SB_URL + '/rest/v1/' + table + '?on_conflict=' + conflict
    r = requests.post(url, json=row, headers={
        'Content-Type': 'application/json',
        'apikey': SB_KEY,
        'Authorization': 'Bearer ' + SB_KEY,
        'Prefer': 'resolution=merge-duplicates,return=minimal',
    }, timeout=30)
    if not r.ok:
        print('  [sb ' + str(r.status_code) + '] ' + r.text[:150])
        return False
    return True


def scrape_leyes():
    print('\n[diputados] LeyesBiblio...')
    base = 'https://www.diputados.gob.mx/LeyesBiblio/index.htm'
    n = 0
    try:
        r = requests.get(base, headers=UA, timeout=30)
        soup = BeautifulSoup(r.text, 'lxml')
        for a in soup.select('a[href]'):
            nombre = clean(a.get_text())
            href = a.get('href', '')
            nlow = nombre.lower()
            ok_pref = (
                nlow.startswith('ley') or
                nlow.startswith('codigo') or
                nlow.startswith('reglamento') or
                nlow.startswith('estatuto') or
                'codigo' in nlow[:8]
            )
            if not ok_pref or len(nombre) < 15:
                continue
            if n >= MAX_LEYES:
                break
            url_l = urljoin(base, href)
            try:
                txt = ''
                if href.endswith('.htm') or href.endswith('.html'):
                    r2 = requests.get(url_l, headers=UA, timeout=30)
                    s2 = BeautifulSoup(r2.text, 'lxml')
                    txt = clean(s2.get_text(separator=' '))[:5000]
                if not txt:
                    txt = nombre
                e = emb(nombre + ' ' + txt[:1500])
                row = {'nombre': nombre[:500], 'fuente': 'Camara de Diputados', 'url': url_l, 'texto': txt}
                if e:
                    row['embedding'] = e
                if upsert('leyes', row):
                    print('  + ' + nombre[:80])
                    n += 1
                time.sleep(EMBED_DELAY)
            except Exception as ex:
                print('  - ' + nombre[:50] + ': ' + str(ex)[:60])
    except Exception as ex:
        print('  FATAL: ' + str(ex)[:150])
    print('  -> ' + str(n) + ' leyes')


if __name__ == '__main__':
    print('Tequio Scraper Leyes')
    if not (SB_URL and SB_KEY):
        print('ERR: faltan vars Supabase')
        sys.exit(1)
    scrape_leyes()
    print('done')
