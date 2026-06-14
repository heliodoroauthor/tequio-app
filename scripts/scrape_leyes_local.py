#!/usr/bin/env python3
"""
scrape_leyes_local.py - corre desde TU máquina (Houston OK).
Scrapea compendios oficiales que bloquean datacenter IPs y genera SQL listo
para pegar en Supabase Studio.

REQUISITOS (uno-time install):
    pip install playwright requests beautifulsoup4
    playwright install chromium

USO:
    python3 scrape_leyes_local.py                  # todos los estados disponibles
    python3 scrape_leyes_local.py --state=Sinaloa  # solo uno
    python3 scrape_leyes_local.py --vpn-on         # incluye Veracruz (requiere VPN MX)

OUTPUT:
    leyes_urls_updates.sql   ← pegas esto en Supabase Studio

Estados implementados:
    - Sinaloa     (~35 leyes pendientes)
    - Coahuila    (~47 leyes pendientes)
    - Zacatecas   (~39 leyes pendientes)
    - Veracruz    (~63, requiere VPN México)
    - Guerrero    (~79, requiere VPN México por Cloudflare)
    - Baja California (~35)
"""
import asyncio, re, unicodedata, json, sys, requests
from playwright.async_api import async_playwright

SB_URL  = 'https://mhsuihwjgtzxflesbnxv.supabase.co'
SB_ANON = 'sb_publishable_grJAWIMoSr5b8YfaB7HVlw_OesNipBz'

VPN_ON = '--vpn-on' in sys.argv
ONLY_STATE = None
for a in sys.argv[1:]:
    if a.startswith('--state='):
        ONLY_STATE = a.split('=',1)[1]


def norm(s):
    if not s: return ''
    s = unicodedata.normalize('NFKD', s)
    s = ''.join(c for c in s if not unicodedata.combining(c))
    return re.sub(r'\s+', ' ', s.lower()).strip()


async def scrape_sinaloa(page):
    """Sinaloa: iframe de gaceta.congresosinaloa con tarjetas accordion."""
    await page.goto('https://gaceta.congresosinaloa.gob.mx/#/leyes', wait_until='domcontentloaded', timeout=30000)
    await page.wait_for_timeout(5000)
    for _ in range(8):
        await page.evaluate('window.scrollBy(0, 1500)')
        await page.wait_for_timeout(700)
    return await page.evaluate('''() => {
        const out = [];
        document.querySelectorAll('a[href*=".pdf"]').forEach(a => {
            let parent = a.closest('article, .card, li, div');
            let nombre = '';
            while (parent && !nombre) {
                const h = parent.querySelector('h1, h2, h3, h4, .title, strong');
                if (h && h.textContent.trim().length > 15) { nombre = h.textContent.trim(); break; }
                parent = parent.parentElement;
                if (!parent || parent.tagName === 'BODY') break;
            }
            if (!nombre) nombre = a.textContent.trim();
            if (nombre) out.push({nombre, url: a.href});
        });
        return out;
    }''')


async def scrape_coahuila(page):
    """Coahuila: menu LEYES ESTATALES."""
    await page.goto('https://www.congresocoahuila.gob.mx/portal/', wait_until='domcontentloaded', timeout=30000)
    await page.wait_for_timeout(3000)
    # Click "Leyes Estatales" menu link
    leyes_link = await page.query_selector('a:has-text("LEYES ESTATALES"), a:has-text("Leyes Estatales")')
    if leyes_link:
        await leyes_link.click()
        await page.wait_for_timeout(4000)
    for _ in range(5):
        await page.evaluate('window.scrollBy(0, 1500)')
        await page.wait_for_timeout(700)
    return await page.evaluate('''() => {
        const out = [];
        document.querySelectorAll('a[href*=".pdf"]').forEach(a => {
            const text = a.textContent.trim() || a.title || '';
            if (text && (text.toLowerCase().includes('ley') || text.toLowerCase().includes('codigo') || text.toLowerCase().includes('código'))) {
                out.push({nombre: text, url: a.href});
            }
        });
        return out;
    }''')


async def scrape_zacatecas(page):
    """Zacatecas /63/leyes y subpages."""
    await page.goto('https://www.congresozac.gob.mx/63/leyes', wait_until='domcontentloaded', timeout=20000)
    await page.wait_for_timeout(3000)
    return await page.evaluate('''() => {
        const out = [];
        document.querySelectorAll('a[href*=".pdf"]').forEach(a => {
            const text = a.textContent.trim();
            if (text && /ley|código|codigo/i.test(text) && text.length > 12) {
                out.push({nombre: text, url: a.href});
            }
        });
        return out;
    }''')


async def scrape_veracruz(page):
    """Veracruz - requiere VPN México."""
    await page.goto('https://www.legisver.gob.mx/?p=compendio', wait_until='domcontentloaded', timeout=30000)
    await page.wait_for_timeout(4000)
    return await page.evaluate('''() => {
        const out = [];
        document.querySelectorAll('a[href*=".pdf"], a[href*="leyEstatal"]').forEach(a => {
            const text = a.textContent.trim();
            if (text && text.length > 15 && /ley|código/i.test(text)) {
                out.push({nombre: text, url: a.href});
            }
        });
        return out;
    }''')


async def scrape_guerrero(page):
    """Guerrero - Cloudflare. Probablemente Playwright real lo pasa."""
    await page.goto('https://congresogro.gob.mx/63/inicio/marco_legal', wait_until='domcontentloaded', timeout=30000)
    await page.wait_for_timeout(5000)
    return await page.evaluate('''() => {
        const out = [];
        document.querySelectorAll('a[href*=".pdf"]').forEach(a => {
            const text = a.textContent.trim();
            if (text && /ley|código/i.test(text) && text.length > 12) out.push({nombre: text, url: a.href});
        });
        return out;
    }''')


SCRAPERS = {
    'Sinaloa': scrape_sinaloa,
    'Coahuila': scrape_coahuila,
    'Zacatecas': scrape_zacatecas,
}
VPN_REQUIRED = {
    'Veracruz': scrape_veracruz,
    'Guerrero': scrape_guerrero,
}


def fetch_pendientes(entidad):
    r = requests.get(
        f'{SB_URL}/rest/v1/leyes',
        params={'url':'is.null','entidad':f'eq.{entidad}','ambito':'eq.estatal','select':'id,nombre','limit':500},
        headers={'apikey':SB_ANON,'Authorization':f'Bearer {SB_ANON}'},
        timeout=30
    )
    return r.json() if r.ok else []


async def main():
    targets = dict(SCRAPERS)
    if VPN_ON:
        targets.update(VPN_REQUIRED)
    if ONLY_STATE:
        targets = {ONLY_STATE: targets.get(ONLY_STATE)} if ONLY_STATE in targets else {}
        if not targets:
            print(f'ERR: scraper para {ONLY_STATE} no implementado')
            return

    print(f'Estados a procesar: {list(targets.keys())}\n')

    sql_lines = ['-- Auto-generated by scrape_leyes_local.py']
    total_matched = 0

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(user_agent='Mozilla/5.0 (Windows NT 10.0) AppleWebKit/537.36 Chrome/120.0')
        page = await ctx.new_page()
        for state, scraper in targets.items():
            print(f'=== {state} ===')
            try:
                compendio = await scraper(page)
                print(f'  Compendio: {len(compendio)} leyes')
            except Exception as e:
                print(f'  SKIP error: {type(e).__name__}: {str(e)[:80]}')
                continue

            lookup = {norm(l['nombre']): l for l in compendio if l.get('nombre')}
            pend = fetch_pendientes(state)
            print(f'  DB pendientes: {len(pend)}')

            matched = 0
            for row in pend:
                nk = norm(row['nombre'])
                if nk in lookup:
                    url = lookup[nk]['url'].replace("'","''")
                    sql_lines.append(f"UPDATE leyes SET url='{url}' WHERE id={row['id']};")
                    matched += 1
            print(f'  Matched: {matched}')
            total_matched += matched
            sql_lines.append(f'-- {state}: {matched} matched\n')
        await browser.close()

    sql_lines.insert(1, f'-- Total: {total_matched} UPDATEs\n')
    out = '\n'.join(sql_lines)
    with open('leyes_urls_updates.sql', 'w') as f:
        f.write(out)
    print(f'\n✅ Total {total_matched} UPDATEs → leyes_urls_updates.sql')
    print(f'   Pega ese archivo en Supabase Studio → SQL Editor → Run')


if __name__ == '__main__':
    asyncio.run(main())
