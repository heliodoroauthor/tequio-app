#!/usr/bin/env python3
"""
🦎 Tequio · sync_kpis_fallback.py

Sincroniza fallbacks HTML hardcoded en index.html con cache kpis_globales_cache.
Resuelve el gap de Cero Invención para crawlers (Google/Twitter/FB/OG) y no-JS.

Uso:
    python scripts/sync_kpis_fallback.py
    python scripts/sync_kpis_fallback.py --check
"""
import argparse, json, os, re, sys, urllib.error, urllib.request

SUPABASE_URL = "https://mhsuihwjgtzxflesbnxv.supabase.co"
ANON_KEY = "sb_publishable_grJAWIMoSr5b8YfaB7HVlw_OesNipBz"
INDEX_FILE = "index.html"


def fmt_num(n):
    if n is None: return None
    try: return f"{int(n):,}"
    except (TypeError, ValueError): return str(n)


KPI_MAP = {
    "leyes": lambda k: fmt_num(k.get("leyes")),
    "leyes-estatal": lambda k: fmt_num(k.get("leyes_estatal")),
    "leyes-municipal": lambda k: fmt_num(k.get("leyes_municipal")),
    "leyes-federal": lambda k: fmt_num(k.get("leyes_federal")),
    "leyes-calidad": lambda k: fmt_num(k.get("leyes_con_contenido_calidad")),
    "leyes-federal-estatal": lambda k: fmt_num(k.get("leyes_federal_estatal")),
    "leyes-municipal-reglamento": lambda k: fmt_num(k.get("leyes_municipal_reglamento")),
    "leyes-municipal-gaceta": lambda k: fmt_num(k.get("leyes_municipal_gaceta")),
    "contratos": lambda k: fmt_num(k.get("contratos_publicos")),
    "inah-zonas": lambda k: fmt_num(k.get("inah_zonas")),
    "inah-museos": lambda k: fmt_num(k.get("inah_museos")),
    "inah-patrimonio": lambda k: f'{k.get("inah_zonas",0)}+{k.get("inah_museos",0)}',
    "sre-embajadas": lambda k: fmt_num(k.get("sre_embajadas")),
    "sre-consulados": lambda k: fmt_num(k.get("sre_consulados")),
    "inali-lenguas": lambda k: fmt_num(k.get("inali_lenguas")),
    "unesco-patrimonio": lambda k: fmt_num(k.get("unesco_patrimonio")),
    "diputados": lambda k: fmt_num(k.get("politicos_diputados")),
    "senadores": lambda k: fmt_num(k.get("politicos_senadores")),
    "actores-poder": lambda k: fmt_num(k.get("actores_poder")),
    "vinculos-poder": lambda k: fmt_num(k.get("vinculos_poder")),
    "sat-69": lambda k: fmt_num(k.get("sat_69")),
    "sat-69b": lambda k: fmt_num(k.get("sat_69b")),
    "sat-total": lambda k: fmt_num((k.get("sat_69",0) or 0) + (k.get("sat_69b",0) or 0)),
    "gasolina": lambda k: fmt_num(k.get("gasolina_estaciones")),
    "profeco-precios": lambda k: fmt_num(k.get("profeco_precios")),
    "municipios": lambda k: fmt_num(k.get("municipios")),
    "votos-individuales": lambda k: fmt_num(k.get("votos_individuales")),
    "chunks-total": lambda k: fmt_num(k.get("chunks_total")),
    "chunks-embedded": lambda k: fmt_num(k.get("chunks_embedded")),
    "costo_financiero_pib_pct": lambda k: f'{k.get("costo_financiero_pib_pct","")}',
}


def fetch_cache():
    req = urllib.request.Request(
        f"{SUPABASE_URL}/rest/v1/rpc/dashboard_kpis_globales",
        data=b"{}",
        headers={
            "Content-Type": "application/json",
            "apikey": ANON_KEY,
            "Authorization": f"Bearer {ANON_KEY}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def update_kpi(html, kpi_id, new_value):
    pattern = re.compile(
        r'(data-kpi="' + re.escape(kpi_id) + r'"[^>]*>)([^<]*)(</[a-zA-Z]+>)',
        re.DOTALL,
    )
    changed = 0
    def replace(match):
        nonlocal changed
        prefix, old, suffix = match.group(1), match.group(2), match.group(3)
        if "'" in old or "+" in prefix.split(">")[0]:
            return match.group(0)
        if old.strip() == new_value:
            return match.group(0)
        changed += 1
        return f"{prefix}{new_value}{suffix}"
    return pattern.sub(replace, html), changed


def update_meta_tags(html, cache):
    leyes = cache.get("leyes")
    if not leyes: return html, 0
    leyes_fmt = fmt_num(leyes)
    changed = 0
    pattern = re.compile(
        r'(<meta[^>]+content="[^"]*?)(\d{1,3}(?:,\d{3})*) leyes',
    )
    def replace(m):
        nonlocal changed
        prefix, old_num = m.group(1), m.group(2)
        if old_num == leyes_fmt:
            return m.group(0)
        changed += 1
        return f"{prefix}{leyes_fmt} leyes"
    return pattern.sub(replace, html), changed


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--index", default=INDEX_FILE)
    args = parser.parse_args()

    if not os.path.exists(args.index):
        print(f"X No existe {args.index}", file=sys.stderr)
        return 1

    print("Tequio sync_kpis_fallback")
    print("  Fetching RPC dashboard_kpis_globales...")
    try:
        cache = fetch_cache()
    except urllib.error.URLError as e:
        print(f"X Error RPC: {e}", file=sys.stderr)
        return 2

    print(f"  Cache actualizado: {cache.get('actualizado','?')}")
    print(f"  leyes={cache.get('leyes')} inali={cache.get('inali_lenguas')} unesco={cache.get('unesco_patrimonio')} sre={cache.get('sre_embajadas')}")
    print()

    with open(args.index, "r", encoding="utf-8") as f:
        html = f.read()

    total = 0
    for kpi_id, getter in KPI_MAP.items():
        v = getter(cache)
        if v is None or v == "": continue
        html, n = update_kpi(html, kpi_id, v)
        if n > 0:
            print(f"  data-kpi=\"{kpi_id}\" -> {v} ({n} elementos)")
            total += n

    html, n = update_meta_tags(html, cache)
    if n > 0:
        print(f"  meta tags actualizados ({n} reemplazos)")
        total += n

    print()
    if total == 0:
        print("OK Sin cambios - fallbacks ya sincronizados")
        return 0
    if args.check:
        print(f"NOTE {total} cambios detectados (modo --check, no se escribe)")
        return 0
    with open(args.index, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"OK {total} cambios escritos a {args.index}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
