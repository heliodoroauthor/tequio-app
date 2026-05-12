#!/usr/bin/env python3
"""
scrape_to_supabase.py
"""
import os
import re
import time
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin

GEMINI_API_KEY = os.environ['GEMINI_API_KEY']
SUPABASE_URL = os.environ['SUPABASE_URL'].rstrip('/')
SERVICE_KEY = os.environ['SUPABASE_SERVICE_ROLE_KEY']
FUENTE = os.environ.get('FUENTE', 'all').lower()
MAX_PAGINAS = int(os.environ.get('MAX_PAGINAS', '5'))

HEADERS = {'User-Agent': 'TequioLegalBot/1.0 (+https://tequio.app; investigacion educativa)'}
DELAY = 2
EMBED_DELAY = 7


def sanitize(s):
    return re.sub(r'\\s+', ' ', (s or '')).strip()


def get_embedding(text):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/text-embedding-004:embedContent?key={GEMINI_API_KEY}"
    r = requests.post(url, json={
        'model': 'models/text-embedding-004',
        'content': {'parts': [{'text': text[:2000]}]},
        'taskType': 'RETRIEVAL_DOCUMENT',
    }, timeout=30)
    r.raise_for_status()
    return r.json()['embedding']['values']


def supabase_upsert(table, row, on_conflict='nombre'):
    url = f"{SUPABASE_URL}/rest/v1/{table}?on_conflict={on_conflict}"
    r = requests.post(url, json=row, headers={
        'Content-Type': 'application/json',
        'apikey': SERVICE_KEY,
        'Authorization': f'Bearer {SERVICE_KEY}',
        'Prefer': 'resolution=merge-duplicates,return=minimal',
    }, timeout=30)
    if not r.ok:
        print(f"  ⚠️ Supabase error {r.status_code}: {r.text[:200]}")
        return False
    return True


def scrape_diputados_leyes():
    print("\\n🏛️ DIPUTADOS — Leyes vigentes...")
    url_base = "https://www.diputados.gob.mx/LeyesBiblio/index.htm"
    count = 0
    try:
        r = requests.get(url_base, headers=HEADERS, timeout=30)
        soup = BeautifulSoup(r.text, 'lhml')
        for link in soup.select('a[href]'):
            nombre = sanitize(link.get_text())
            href = link.get('href', '')
            if not nombre.lower().startswith('ley') or len(nombre) < 15:
                continue
            if count >= MAX_PAGINAS * 5:
                break
            url_completa = urljoin(url_base, href)
            try:
                texto = ''
                if href.endswith('.htm') or href.endswith('.html'):
                    r2 = requests.get(url_completa, headers=HEADERS, timeout=30)
                    soup2 = BeautifulSoup(r2.text, 'lxml')
                    texto = sanitize(soup2.get_text(separator=' '))[:5000]
                if not texto:
                    texto = nombre
                emb = get_embedding(nombre + '\\n' + texto)
                ok = supabase_upsert('leyes', {
                    'nombre': nombre[:500],
                    'fuente': 'Cámara de Diputados',
                    'url': url_completa,
                    'texto': texto,
                    'embedding': emb,
                })
                if ok:
                    print(f"  ✅ {nombre[:80]}")
                    count += 1
                time.sleep(EMBED_DELAY)
            except Exception as e:
                print(f"  ⚠️ {nombre[:60]}: {e}")
    except Exception as e:
        print(f"  ❌ {e}")
    print(f"  → {count} leyes procesadas")


def scrape_scjn_jurisprudencia():
    print("\\n⚾️ SCJN — Jurisprudencia reciente...")
    count = 0
    api_url = "https://sjf2.scjn.gob.mx/api/busquedas/tesis"
    for offset in range(0, MAX_PAGINAS * 20, 20):
        try:
            r = requests.get(api_url, headers=HEADERS, params={
                'tipoTesis': 'Jurisprudencia',
                'offset': offset,
                'limit': 20,
                'ordenarPor': 'fechaPublicacion desc',
            }, timeout=30)
            if not r.ok:
                continue
            data = r.json()
            for tesis in data.get('resultados', []):
                rubro = sanitize(tesis.get('rubro', ''))
                texto = sanitize(tesis.get('texto', ''))
                if not rubro:
                    continue
                try:
                    emb = get_embedding(rubro + '\\n' + texto)
                    ok = supabase_upsert('jurisprudencia', {
                        'rubro': rubro[:500],
                        'instancia': sanitize(tesis.get('instancia', '')),
                        'epoca': sanitize(tesis.get('epoca', '')),
                        'fecha_publicacion': sanitize(tesis.get('fechaPublicacion', '')),
                        'texto': texto[:5000],
                        'embedding': emb,
                    }, on_conflict='rubro')
                    if ok:
                        print(f"  ✅ {rubro[:80]}")
                        count += 1
                    time.sleep(EMBED_DELAY)
                except Exception as e:
                    print(f"  ⚠️ {rubro[:60]}: {e}")
            time.sleep(DELAY)
        except Exception as e:
            print(f"  ❌ Offset {offset}: {e}")
    print(f"  → {count} jurisprudencias procesadas")


def main():
    print(f"🚀 Tequio Scraper — fuente={FUENTE} max_pag={MAX_PAGINAS}")
    print(f"   Supabase: {SUPABASE_URL}")
    if FUENTE in ('all', 'diputados'):
        scrape_diputados_leyes()
    if FUENTE in ('all', 'scjn'):
        scrape_scjn_jurisprudencia()
    print("\\n✅ Scrape completo.")


if __name__ == '__main__':
    main()
