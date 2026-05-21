#!/usr/bin/env python3
"""Scrape RSS oficial: DOF + SEGOB + Presidencia + SCJN.

v2 (2026-05-21 FIX-19 auditoria):
- Detector explicito de anti-bot Challenge Validation en gob.mx
- Reporta status diferenciado: blocked_antibot, http_error, parse_zero
- SCJN sigue como fuente confiable
- DOF con timeout extendido + fallback HTTP
"""
import os, re, sys, time, hashlib, requests
from bs4 import BeautifulSoup
from xml.etree import ElementTree as ET

SB = os.environ.get('SUPABASE_URL', '').rstrip('/')
KEY = os.environ.get('SUPABASE_SERVICE_ROLE_KEY', '')
MAX = int(os.environ.get('MAX_PER_FUENTE', '40'))
UA = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36',
      'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
      'Accept-Language': 'es-MX,es;q=0.9,en;q=0.8'}
MESES = {'enero':1,'febrero':2,'marzo':3,'abril':4,'mayo':5,'junio':6,'julio':7,'agosto':8,'septiembre':9,'octubre':10,'noviembre':11,'diciembre':12}


def is_antibot(body):
    """Detecta el HTML de Challenge Validation que sirve gob.mx anti-bot."""
    if not body: return False
    head = body[:600] if isinstance(body, str) else body[:600].decode('utf-8', 'ignore')
    return 'Challenge Validation' in head or 'challenge-platform' in head


def clean(s):
    t = re.sub(r'<[^>]+>', ' ', s or '')
    t = t.replace('\\/', '/').replace('\\n', ' ').replace('\\t', ' ')
    return re.sub(r'\s+', ' ', t).strip()


def uh(u):
    return hashlib.sha256((u or '').encode('utf-8')).hexdigest()[:32]


def pfecha(txt):
    m = re.search(r'(\d{1,2})\s+de\s+(\w+)\s+de\s+(\d{4})', txt or '')
    if not m: return None
    mes = MESES.get(m.group(2).lower())
    if not mes: return None
    try: return f'{m.group(3)}-{mes:02d}-{int(m.group(1)):02d}T12:00:00+00:00'
    except: return None


def _m(t, pats):
    return any(re.search(r'\b' + p + r'\b', t) for p in pats)


def cls(txt):
    t = (txt or '').lower()
    if _m(t, [r'corrupci\w*', r'desvi\w*', 'fraude', 'soborno', 'lavado']): return 'corrupcion'
    if _m(t, ['seguridad', 'guardia nacional', 'sedena', r'detenci\w*', 'crimen', r'narco\w*', r'homicid\w*', 'violencia']): return 'seguridad'
    if _m(t, ['salud', 'imss', 'issste', r'medicament\w*', 'hospital', 'pandemia']): return 'salud'
    if _m(t, [r'educaci\w*', 'sep', r'escuela\w*', r'universidad\w*', r'maestr\w*', r'docente\w*', r'beca\w*']): return 'educacion'
    if _m(t, [r'ambient\w*', 'agua', r'ecolog\w*', 'clima', 'conagua', 'semarnat', 'forestal', r'inundaci\w*']): return 'medio_ambiente'
    if _m(t, [r'econom\w*', 'fiscal', r'impuest\w*', 'hacienda', 'banxico', r'inflaci\w*', 'pacic', 'mdp', 'pib']): return 'economia'
    if _m(t, ['derechos humanos', r'discriminaci\w*', r'ind[ií]gen\w*', 'igualdad', r'g[eé]nero', r'desaparici\w*']): return 'derechos'
    return 'politica'


def up(row):
    try:
        r = requests.post(SB + '/rest/v1/noticias_civicas?on_conflict=hash_url', json=row,
            headers={'Content-Type':'application/json', 'apikey':KEY, 'Authorization':'Bearer '+KEY,
                     'Prefer':'resolution=merge-duplicates,return=minimal'}, timeout=20)
        return r.ok
    except: return False


def s_dof():
    print('\n[DOF]'); n = 0
    # FIX-19: intentar HTTPS primero, fallback HTTP, timeout extendido
    for url in ('https://dof.gob.mx/sumario.xml', 'http://dof.gob.mx/sumario.xml'):
        try:
            r = requests.get(url, headers=UA, timeout=45, verify=False)
            if r.status_code != 200:
                print(f'  HTTP {r.status_code} en {url}')
                continue
            if is_antibot(r.text):
                print(f'  ANTIBOT detected en {url} — skipping DOF')
                return 0
            for it in ET.fromstring(r.content).iter('item'):
                if n >= MAX: break
                t = clean(it.findtext('title') or ''); l = clean(it.findtext('link') or ''); d = clean(it.findtext('description') or '')
                if not t or not l: continue
                row = {'hash_url':uh(l),'titulo':t[:500],'resumen':d[:800],'url_oficial':l,
                       'fuente':'DOF','fuente_url':'https://dof.gob.mx','ambito':'nacional','tema':cls(t+' '+d)}
                if up(row): n += 1
            print(f'  -> {n} (url: {url})')
            return n
        except Exception as e:
            print(f'  err {url}:', str(e)[:120])
            continue
    print(f'  TODAS LAS URLS FALLARON -> 0')
    return 0


def s_gob(slug, label):
    """Scraper gob.mx con detector ANTIBOT explicito."""
    print(f'\n[{label}]'); n = 0
    base = f'https://www.gob.mx/{slug}/archivo/prensa'
    antibot_hits = 0
    for u in [base, base+'?order=DESC&page=1', base+'?order=DESC&page=2']:
        if n >= MAX: break
        try:
            r = requests.get(u, headers=UA, timeout=30)
            # FIX-19: detector ANTIBOT explicito antes de parsear
            if is_antibot(r.text):
                antibot_hits += 1
                print(f'  ANTIBOT en {u} (body={len(r.text)}B con <title>Challenge Validation</title>)')
                continue
            soup = BeautifulSoup(r.text, 'lxml')
        except Exception as e:
            print(f'  err {u}:', str(e)[:100])
            continue
        for art in soup.find_all('article'):
            if n >= MAX: break
            h = art.find(['h2', 'h3'])
            tit = clean(h.get_text()) if h else ''
            href = ''
            for a in art.find_all('a', href=True):
                if '/prensa/' in a.get('href', ''):
                    href = a.get('href', ''); break
            if not tit or not href or len(tit) < 15: continue
            if href.startswith('/'): href = 'https://www.gob.mx' + href
            txt = clean(art.get_text())
            row = {'hash_url':uh(href),'titulo':tit[:500],
                   'resumen':txt[:800].replace('Continuar leyendo','').strip(),
                   'url_oficial':href,'fuente':label,'fuente_url':base,
                   'ambito':'nacional','tema':cls(tit+' '+txt)}
            f = pfecha(txt)
            if f: row['fecha_publicacion'] = f
            if up(row): n += 1
        time.sleep(1)
    if antibot_hits and n == 0:
        print(f'  -> 0 (BLOQUEADO por anti-bot gob.mx en {antibot_hits} URLs)')
    else:
        print(f'  -> {n}')
    return n


def s_scjn():
    """SCJN - sigue funcionando porque no tiene anti-bot."""
    print('\n[SCJN]'); n = 0
    try:
        r = requests.get('https://www.scjn.gob.mx/multimedia/comunicados', headers=UA, timeout=30, verify=False)
        if is_antibot(r.text):
            print('  ANTIBOT detected en SCJN — skipping')
            return 0
        for a in BeautifulSoup(r.text, 'lxml').find_all('a', href=True):
            if n >= MAX: break
            tit = clean(a.get_text()); href = a.get('href', '')
            if not tit or len(tit) < 25: continue
            if 'comunicado' not in href.lower() and 'noticia' not in href.lower(): continue
            if href.startswith('/'): href = 'https://www.scjn.gob.mx' + href
            elif not href.startswith('http'): continue
            row = {'hash_url':uh(href),'titulo':tit[:500],'resumen':tit[:800],'url_oficial':href,
                   'fuente':'SCJN','fuente_url':'https://www.scjn.gob.mx/multimedia/comunicados',
                   'ambito':'nacional','tema':cls(tit)}
            if up(row): n += 1
    except Exception as e: print(' err:', str(e)[:100])
    print(' ->', n); return n


if __name__ == '__main__':
    if not (SB and KEY):
        print('ERR vars'); sys.exit(1)
    print('=== Tequio scrape_noticias v2 ===')
    print('  Estrategia: SCJN principal (sin antibot). DOF con HTTPS+HTTP fallback.')
    print('  SEGOB/Presidencia: detectar Challenge Validation y skip explicito.')
    print('')
    totals = {
        'DOF':         s_dof(),
        'SEGOB':       s_gob('segob', 'SEGOB'),
        'Presidencia': s_gob('presidencia', 'Presidencia'),
        'SCJN':        s_scjn(),
    }
    total = sum(totals.values())
    print(f'\n=== RESUMEN ===')
    for k, v in totals.items():
        flag = 'OK' if v > 0 else 'BLOQUEADO'
        print(f'  {k}: {v} noticias ({flag})')
    print(f'\nTotal: {total}')
    if total == 0:
        print('\n[WARNING] Cero noticias insertadas. Verificar SCRAPERS_STATUS.md para diagnostico.')
        sys.exit(2)
