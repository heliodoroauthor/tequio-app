#!/usr/bin/env python3
"""
🦎 Tequio · sync_kpis_fallback.py

Sincroniza fallbacks HTML hardcoded en index.html con cache kpis_globales_cache,
+ stamps ?v=<git_sha7> en /js/ /css/ propios para cache-bust el browser,
+ bumpea VERSION del service worker para invalidar SW cache.

Resuelve gap de Cero Invención para:
  - crawlers (Google/Twitter/FB/OG) — fallback HTML
  - usuarios sin JS — fallback HTML
  - usuarios con SW cache estancado — ?v=SHA + VERSION bump
  - browsers normales con CF cache 4h — ?v=SHA → URL diferente → bypass cache

Uso:
    python scripts/sync_kpis_fallback.py
    python scripts/sync_kpis_fallback.py --check
    python scripts/sync_kpis_fallback.py --git-sha be6069a  # override

Si --git-sha no se pasa: usa GITHUB_SHA env o git rev-parse --short HEAD.
"""
import argparse, json, os, re, subprocess, sys, urllib.error, urllib.request
from pathlib import Path

SUPABASE_URL = "https://mhsuihwjgtzxflesbnxv.supabase.co"
ANON_KEY = "sb_publishable_grJAWIMoSr5b8YfaB7HVlw_OesNipBz"
INDEX_FILE = "index.html"

# JS/CSS mutables propios — todos llevan cache-bust
ASSET_PATHS = [
    "/js/tequio-kpis.js",
    "/js/tequio-freshness-widget.js",
    "/js/anti-bot.js",
    "/js/verificacion-ine.js",
    "/css/main.css",
]

# Glob de HTMLs a estampar (top-level + panel/)
HTML_GLOBS = ["index.html", "*.html", "panel/*.html"]

# Service worker
SW_FILE = "sw.js"


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


def resolve_sha(override=None):
    if override:
        return override[:7]
    env_sha = os.environ.get("GITHUB_SHA")
    if env_sha:
        return env_sha[:7]
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--short=7", "HEAD"],
            stderr=subprocess.DEVNULL,
        )
        return out.decode().strip()
    except Exception:
        return None


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


def stamp_asset(html, asset_path, sha):
    """
    Stamps ?v=<sha> al final del src/href de un asset path.
    Si ya tiene ?v=X, lo reemplaza por ?v=<sha>.
    Soporta: src="/js/x.js" defer, href="/css/y.css" rel=...
    """
    # Patrón con o sin query string actual
    pattern = re.compile(
        r'((?:src|href)=")(' + re.escape(asset_path) + r')(\?v=[^"]*)?(")',
    )
    changed = 0
    target_qs = f"?v={sha}"
    def replace(m):
        nonlocal changed
        attr_open, path, old_qs, attr_close = m.group(1), m.group(2), m.group(3), m.group(4)
        if old_qs == target_qs:
            return m.group(0)
        changed += 1
        return f"{attr_open}{path}{target_qs}{attr_close}"
    return pattern.sub(replace, html), changed


def update_sw_version(sw_content, sha):
    """
    Bumpea VERSION del SW para terminar en -<sha>.
    'tequio-v2.0.3' o 'tequio-v2.0.3-OLDSHA' → 'tequio-v2.0.3-<sha>'
    """
    pattern = re.compile(
        r"(const\s+VERSION\s*=\s*')(tequio-v\d+\.\d+\.\d+)(-[a-f0-9]+)?(';)",
    )
    target_suffix = f"-{sha}"
    changed = 0
    def replace(m):
        nonlocal changed
        prefix, base, old_suffix, close = m.group(1), m.group(2), m.group(3), m.group(4)
        if old_suffix == target_suffix:
            return m.group(0)
        changed += 1
        return f"{prefix}{base}{target_suffix}{close}"
    return pattern.sub(replace, sw_content), changed


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--index", default=INDEX_FILE)
    parser.add_argument("--git-sha", default=None, help="Override SHA (default: env GITHUB_SHA or git HEAD)")
    args = parser.parse_args()

    sha = resolve_sha(args.git_sha)
    if not sha:
        print("WARN no se pudo resolver git SHA (cache-busting skipped)", file=sys.stderr)

    if not os.path.exists(args.index):
        print(f"X No existe {args.index}", file=sys.stderr)
        return 1

    print("Tequio sync_kpis_fallback")
    print(f"  Git SHA: {sha or '(skip)'}")
    print("  Fetching RPC dashboard_kpis_globales...")
    try:
        cache = fetch_cache()
    except urllib.error.URLError as e:
        print(f"X Error RPC: {e}", file=sys.stderr)
        return 2

    print(f"  Cache actualizado: {cache.get('actualizado','?')}")
    print(f"  leyes={cache.get('leyes')} inali={cache.get('inali_lenguas')} unesco={cache.get('unesco_patrimonio')} sre={cache.get('sre_embajadas')}")
    print()

    # 1) KPI fallbacks + meta tags en index.html
    total = 0
    with open(args.index, "r", encoding="utf-8") as f:
        html = f.read()

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

    files_to_write = {}
    if total > 0 and not args.check:
        files_to_write[args.index] = html

    # 2) Cache-bust ?v=<sha> en TODOS los HTMLs (raíz + panel/)
    if sha:
        html_files = []
        html_files.append(Path(args.index))
        html_files.extend(p for p in Path(".").glob("*.html") if p.name != args.index)
        html_files.extend(Path(".").glob("panel/*.html"))
        # Dedupe
        seen, dedup = set(), []
        for p in html_files:
            if p in seen: continue
            seen.add(p); dedup.append(p)

        for path in dedup:
            content = files_to_write.get(str(path))
            if content is None:
                content = path.read_text(encoding="utf-8")
            file_changes = 0
            for asset in ASSET_PATHS:
                content, n2 = stamp_asset(content, asset, sha)
                file_changes += n2
            if file_changes > 0:
                print(f"  ?v={sha} stamped en {path} ({file_changes} assets)")
                total += file_changes
                if not args.check:
                    files_to_write[str(path)] = content

    # 3) SW VERSION bump
    if sha and os.path.exists(SW_FILE):
        sw_content = Path(SW_FILE).read_text(encoding="utf-8")
        sw_new, n3 = update_sw_version(sw_content, sha)
        if n3 > 0:
            print(f"  SW VERSION bumped → -{sha}")
            total += n3
            if not args.check:
                files_to_write[SW_FILE] = sw_new

    print()
    if total == 0:
        print("OK Sin cambios - todo sincronizado")
        return 0
    if args.check:
        print(f"NOTE {total} cambios detectados (modo --check, no se escribe)")
        return 0

    for path, content in files_to_write.items():
        Path(path).write_text(content, encoding="utf-8")
    print(f"OK {total} cambios escritos en {len(files_to_write)} archivo(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
