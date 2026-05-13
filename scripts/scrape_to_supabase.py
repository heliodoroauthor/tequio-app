#!/usr/bin/env python3
"""
scrape_to_supabase.py (v2 - FIX)
=================================
Cambios respecto v1:
  - Diputados: selector mas flexible (cualquier link con 'Ley', 'Codigo', etc.)
  - SCJN: endpoint corregido a /services/sjftesismicroservice/api/public/tesis
  - Mejor manejo de embeddings (no bloquea si Gemini falla)
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

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (compatible; TequioLegalBot/2.0; +https://tequio.app)'
}
DELAY = 2
EMBED_DELAY = 7


def sanitize(s):
    return re.sub(r'\s+', ' ', (s or '')).strip()


def get_embedding(text):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/text-embedding-004:embedContent?key={GEMINI_API_KEY}"
    try:
        r = requests.post(url, json={
            'model': 'models/text-embedding-004',
            'content': {'parts': [{'text': text[:2000]}]},
            'taskType': 'RETRIEVAL_DOCUMENT',
        }, timeout=30)
        if not r.ok:
            print(f"   [WARN] Embed error {r.status_code}: {r.text[:120]}")
            return None
        return r.json()['embedding']['values']
    except Exception as e:
        print(f"   [WARN] Embed exception: {e}")
        return None


def supabase_upsert(table, row, on_conflict='nombre'):
    url = f"{SUPABASE_URL}/rest/v1/{table}?on_conflict={on_conflict}"
    r = requests.post(url, json=row, headers={
        'Content-Type': 'application/json',
        'apikey': SERVICE_KEY,
        'Authorization': f'Bearer {SERVICE_KEY}',
        'Prefer': 'resolution=merge-duplicates,return=minimal',
    }, timeout=30)
    if not r.ok:
        print(f"   [WARN] Supabase error {r.status_code}: {r.text[:200]}")
        return False
    return True


def scrape_diputados_leyes():
    print("\n[DIPUTADOS] Leyes vigentes...")
    url_base = "https://www.diputados.gob.mx/LeyesBiblio/index.htm"
    count = 0
    try:
        r = requests.get(url_base, headers=HEADERS, timeout=30)
        soup = BeautifulSoup(r.text, 'lxml')

        keywords = ('ley ', 'codigo', 'constitucion',
                    'reglamento', 'estatuto', 'ley general', 'ley federal')

        for link in soup.select('a[href]'):
            nombre = sanitize(link.get_text())
            href = link.get('href', '')
            nombre_low = nombre.lower()
            if not any(nombre_low.startswith(k) for k in keywords):
                continue
            if len(nombre) < 12 or len(nombre) > 300:
                continue
            if count >= MAX_PAGINAS * 5:
                break
            url_completa = urljoin(url_base, href)
            try:
                texto = ''
                if href.lower().endswith(('.htm', '.html')):
                    try:
                        r2 = requests.get(url_completa, headers=HEADERS, timeout=30)
                        soup2 = BeautifulSoup(r2.text, 'lxml')
                        texto = sanitize(soup2.get_text(separator=' '))[:5000]
                    except:
                        pass
                if not texto:
                    texto = nombre
                emb = get_embedding(nombre + '\n' + texto)
                row = {
                    'nombre': nombre[:500],
                    'fuente': 'Camara de Diputados',
                    'url': url_completa,
                    'texto': texto,
                }
                if emb:
                    row['embedding'] = emb
                ok = supabase_upsert('leyes', row)
                if ok:
                    print(f"   [OK] {nombre[:80]}")
                    count += 1
                time.sleep(EMBED_DELAY if emb else 1)
            except Exception as e:
                print(f"   [WARN] {nombre[:60]}: {e}")
    except Exception as e:
        print(f"   [ERR] {e}")
    print(f"   -> {count} leyes procesadas")


def scrape_scjn_jurisprudencia():
    print("\n[SCJN] Jurisprudencia reciente...")
    count = 0
    api_url = "https://sjf2.scjn.gob.mx/services/sjftesismicroservice/api/public/tesis"

    for offset in range(0, MAX_PAGINAS * 20, 20):
        try:
            payloads = [
                {'tipoTesis': 'Jurisprudencia', 'offset': offset, 'limit': 20,
                 'ordenarPor': 'fechaPublicacion desc'},
                {'TipoTesis': 'Jurisprudencia', 'desde': offset, 'hasta': offset + 20},
            ]
            data = None
            for params in payloads:
                try:
                    r = requests.get(api_url, headers={**HEADERS, 'Accept': 'application/json'},
                                     params=params, timeout=30)
                    if r.ok:
                        data = r.json()
                        break
                    r = requests.post(api_url, headers={**HEADERS,
                                                       'Content-Type': 'application/json',
                                                       'Accept': 'application/json'},
                                      json=params, timeout=30)
                    if r.ok:
                        data = r.json()
                        break
                except:
                    continue
            if not data:
                if offset == 0:
                    print(f"   [WARN] SCJN no respondio (puede requerir token o estructura distinta)")
                continue

            resultados = data.get('resultados') or data.get('listas') or data.get('items') or []
            if not resultados:
                break

            for tesis in resultados:
                rubro = sanitize(tesis.get('rubro') or tesis.get('Rubro', ''))
                texto = sanitize(tesis.get('texto') or tesis.get('Texto', ''))
                if not rubro:
                    continue
                try:
                    emb = get_embedding(rubro + '\n' + texto)
                    row = {
                        'rubro': rubro[:500],
                        'instancia': sanitize(tesis.get('instancia') or tesis.get('Instancia', '')),
                        'epoca': sanitize(tesis.get('epoca') or tesis.get('Epoca', '')),
                        'fecha_publicacion': sanitize(tesis.get('fechaPublicacion') or
                                                       tesis.get('FechaPublicacion', '')),
                        'texto': texto[:5000],
                    }
                    if emb:
                        row['embedding'] = emb
                    ok = supabase_upsert('jurisprudencia', row, on_conflict='rubro')
                    if ok:
                        print(f"   [OK] {rubro[:80]}")
                        count += 1
                    time.sleep(EMBED_DELAY if emb else 1)
                except Exception as e:
                    print(f"   [WARN] {rubro[:60]}: {e}")
            time.sleep(DELAY)
        except Exception as e:
            print(f"   [ERR] Offset {offset}: {e}")
    print(f"   -> {count} jurisprudencias procesadas")


def main():
    print(f"Tequio Legal Scraper v2 - fuente={FUENTE} max_pag={MAX_PAGINAS}")
    if FUENTE in ('all', 'diputados'):
        scrape_diputados_leyes()
    if FUENTE in ('all', 'scjn'):
        scrape_scjn_jurisprudencia()
    print("\nScrape completo.")


if __name__ == '__main__':
    main()
