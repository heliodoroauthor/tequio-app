#!/usr/bin/env python3
"""
Tequio - Scraper SRE (Embajadas + Consulados de Mexico en el Exterior) v6
FIX-32 2026-05-21: cambio a modo SEMILLA. portales.sre.gob.mx tiene anti-bot
Imperva+perfdrive no bypaseable desde CI sin browser real. La fuente pasa a ser
una lista mantenida manualmente en este archivo. Para refrescar titulares,
direcciones o emails, editar SEED_CONSULADOS / SEED_EMBAJADAS y commitear.
"""
import json
import os
import sys
from datetime import date, datetime, timezone
import requests

SB_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SB_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
GH_RUN_ID = os.environ.get("GITHUB_RUN_ID", "")

if not SB_URL or not SB_KEY:
    print("[sre] Faltan SUPABASE_URL o SUPABASE_SERVICE_ROLE_KEY", file=sys.stderr)
    sys.exit(1)

SCRAPER_SLUG = "sre_directorio"
FUENTE_URL = "https://portales.sre.gob.mx/directorio/  (semilla local; upstream bloqueado por perfdrive)"
HOY = date.today().isoformat()

# ============================================================================
# SEMILLA: Consulados de Mexico en el Exterior (74 entries)
# ============================================================================
# Estructura: (nombre, ciudad, nombre_slug, sitio_web_url)
SEED_CONSULADOS = [
    ("ALBUQUERQUE", "Albuquerque", "albuquerque", "http://consulmex.sre.gob.mx/albuquerque"),
    ("ATLANTA", "Atlanta", "atlanta", "http://consulmex.sre.gob.mx/atlanta"),
    ("AUSTIN", "Austin", "austin", "http://consulmex.sre.gob.mx/austin"),
    ("BARCELONA", "Barcelona", "barcelona", "http://consulmex.sre.gob.mx/barcelona"),
    ("BOISE", "Boise", "boise", "http://consulmex.sre.gob.mx/boise"),
    ("BOSTON", "Boston", "boston", "http://consulmex.sre.gob.mx/boston"),
    ("BROWNSVILLE", "Brownsville", "brownsville", "http://consulmex.sre.gob.mx/brownsville"),
    ("CALEXICO", "Calexico", "calexico", "http://consulmex.sre.gob.mx/calexico"),
    ("CALGARY", "Calgary", "calgary", "http://consulmex.sre.gob.mx/calgary"),
    ("CHICAGO", "Chicago", "chicago", "http://consulmex.sre.gob.mx/chicago"),
    ("CHONGQING", "Chongqing", "chongqing", None),
    ("DALLAS", "Dallas", "dallas", "http://consulmex.sre.gob.mx/dallas"),
    ("DEL RIO", "Del Rio", "del-rio", "http://consulmex.sre.gob.mx/delrio"),
    ("DENVER", "Denver", "denver", "http://consulmex.sre.gob.mx/denver"),
    ("DETROIT", "Detroit", "detroit", "http://consulmex.sre.gob.mx/detroit"),
    ("DOUGLAS", "Douglas", "douglas", "http://consulmex.sre.gob.mx/douglas"),
    ("EAGLE PASS", "Eagle Pass", "eagle-pass", "http://consulmex.sre.gob.mx/eaglepass"),
    ("EL PASO", "El Paso", "el-paso", "http://consulmex.sre.gob.mx/elpaso"),
    ("ESTAMBUL", "Estambul", "estambul", "http://consulmex.sre.gob.mx/estambul"),
    ("FILADELFIA", "Filadelfia", "filadelfia", "http://consulmex.sre.gob.mx/filadelfia"),
    ("FRANKFURT", "Frankfurt", "frankfurt", "http://consulmex.sre.gob.mx/frankfurt"),
    ("FRESNO", "Fresno", "fresno", "http://consulmex.sre.gob.mx/fresno"),
    ("GUANGZHOU", "Guangzhou", "guangzhou", "http://consulmex.sre.gob.mx/guangzhou"),
    ("HONG KONG", "Hong Kong", "hong-kong", "http://consulmex.sre.gob.mx/hongkong"),
    ("HOUSTON", "Houston", "houston", "http://consulmex.sre.gob.mx/houston"),
    ("INDIANAPOLIS", "Indianapolis", "indianapolis", "http://consulmex.sre.gob.mx/indianapolis"),
    ("KANSAS CITY", "Kansas City", "kansas-city", "http://consulmex.sre.gob.mx/kansascity"),
    ("LA HABANA", "La Habana", "la-habana", None),
    ("LAREDO", "Laredo", "laredo", "http://consulmex.sre.gob.mx/laredo"),
    ("LAS VEGAS", "Las Vegas", "las-vegas", "http://consulmex.sre.gob.mx/lasvegas"),
    ("LEAMINGTON", "Leamington", "leamington", "http://consulmex.sre.gob.mx/leamington"),
    ("LITTLE ROCK", "Little Rock", "little-rock", "http://consulmex.sre.gob.mx/littlerock"),
    ("LOS ANGELES", "Los Angeles", "los-angeles", "http://consulmex.sre.gob.mx/losangeles"),
    ("MCALLEN", "Mcallen", "mcallen", "http://consulmex.sre.gob.mx/mcallen"),
    ("MIAMI", "Miami", "miami", "http://consulmex.sre.gob.mx/miami"),
    ("MILAN", "Milan", "milan", "http://consulmex.sre.gob.mx/milan"),
    ("MILWAUKEE", "Milwaukee", "milwaukee", "https://consulmex.sre.gob.mx/milwaukee/"),
    ("MONTREAL", "Montreal", "montreal", "http://consulmex.sre.gob.mx/montreal"),
    ("MUMBAI", "Mumbai", "mumbai", "http://consulmex.sre.gob.mx/mumbai"),
    ("NEW BRUNSWICK", "New Brunswick", "new-brunswick", "http://consulmex.sre.gob.mx/new-brunswick"),
    ("NOGALES", "Nogales", "nogales", "http://consulmex.sre.gob.mx/nogales"),
    ("NUEVA ORLEANS", "Nueva Orleans", "nueva-orleans", "http://consulmex.sre.gob.mx/nuevaorleans"),
    ("NUEVA YORK", "Nueva York", "nueva-york", "http://consulmex.sre.gob.mx/nuevayork"),
    ("OKLAHOMA", "Oklahoma", "oklahoma", "https://consulmex.sre.gob.mx/oklahoma/index.php"),
    ("OMAHA", "Omaha", "omaha", "http://consulmex.sre.gob.mx/omaha"),
    ("ORLANDO", "Orlando", "orlando", "http://consulmex.sre.gob.mx/orlando"),
    ("OXNARD", "Oxnard", "oxnard", "http://consulmex.sre.gob.mx/oxnard"),
    ("PETEN", "Peten", "peten", "http://consulmex.sre.gob.mx/peten"),
    ("PHOENIX", "Phoenix", "phoenix", "http://consulmex.sre.gob.mx/phoenix"),
    ("PORTLAND", "Portland", "portland", "http://consulmex.sre.gob.mx/portland"),
    ("PRESIDIO", "Presidio", "presidio", "http://consulmex.sre.gob.mx/presidio"),
    ("QUETZALTENANGO", "Quetzaltenango", "quetzaltenango", "http://consulmex.sre.gob.mx/quetzaltenango"),
    ("RALEIGH", "Raleigh", "raleigh", "http://consulmex.sre.gob.mx/raleigh"),
    ("RIO DE JANEIRO", "Rio De Janeiro", "rio-de-janeiro", "http://consulmex.sre.gob.mx/riodejaneiro"),
    ("SACRAMENTO", "Sacramento", "sacramento", "http://consulmex.sre.gob.mx/sacramento"),
    ("SAINT PAUL", "Saint Paul", "saint-paul", "http://consulmex.sre.gob.mx/saintpaul"),
    ("SALT LAKE CITY", "Salt Lake City", "salt-lake-city", "http://consulmex.sre.gob.mx/saltlakecity"),
    ("SAN ANTONIO", "San Antonio", "san-antonio", "http://consulmex.sre.gob.mx/sanantonio"),
    ("SAN BERNARDINO", "San Bernardino", "san-bernardino", "http://consulmex.sre.gob.mx/sanbernardino"),
    ("SAN DIEGO", "San Diego", "san-diego", "http://consulmex.sre.gob.mx/sandiego"),
    ("SAN FRANCISCO", "San Francisco", "san-francisco", "http://consulmex.sre.gob.mx/sanfrancisco"),
    ("SAN JOSE", "San Jose", "san-jose", "http://consulmex.sre.gob.mx/sanjose"),
    ("SAN JUAN", "San Juan", "san-juan", "http://consulmex.sre.gob.mx/sanjuan"),
    ("SAN PEDRO SULA", "San Pedro Sula", "san-pedro-sula", "http://consulmex.sre.gob.mx/sanpedrosula"),
    ("SANTA ANA", "Santa Ana", "santa-ana", "http://consulmex.sre.gob.mx/santaana"),
    ("SAO PAULO", "Sao Paulo", "sao-paulo", "http://consulmex.sre.gob.mx/saopaulo"),
    ("SEATTLE", "Seattle", "seattle", "http://consulmex.sre.gob.mx/seattle"),
    ("SHANGHAI", "Shanghai", "shanghai", "http://consulmex.sre.gob.mx/shanghai/"),
    ("TECUN UMAN", "Tecun Uman", "tecun-uman", "http://consulmex.sre.gob.mx/tecunuman"),
    ("TORONTO", "Toronto", "toronto", "http://consulmex.sre.gob.mx/toronto"),
    ("TUCSON", "Tucson", "tucson", "http://consulmex.sre.gob.mx/tucson"),
    ("VANCOUVER", "Vancouver", "vancouver", "http://consulmex.sre.gob.mx/vancouver"),
    ("WASHINGTON", "Washington", "washington", "http://consulmex.sre.gob.mx/washington"),
    ("YUMA", "Yuma", "yuma", "http://consulmex.sre.gob.mx/yuma"),
]

# ============================================================================
# SEMILLA: Embajadas de Mexico en el Exterior (80 entries)
# ============================================================================
# Estructura: (pais, pais_slug, sitio_web_url)
SEED_EMBAJADAS = [
    ("ALEMANIA", "alemania", "http://embamex.sre.gob.mx/alemania"),
    ("ARABIA SAUDITA", "arabia-saudita", "http://embamex.sre.gob.mx/arabiasaudita"),
    ("ARGELIA", "argelia", "http://embamex.sre.gob.mx/argelia"),
    ("ARGENTINA", "argentina", "http://embamex.sre.gob.mx/argentina"),
    ("AUSTRALIA", "australia", "http://embamex.sre.gob.mx/australia"),
    ("AUSTRIA", "austria", "http://embamex.sre.gob.mx/austria/"),
    ("AZERBAIYAN", "azerbaiyan", "https://embamex.sre.gob.mx/azerbaiyan/"),
    ("BELGICA", "belgica", "http://embamex.sre.gob.mx/belgica/"),
    ("BELIZE", "belize", "http://embamex.sre.gob.mx/belice"),
    ("BOLIVIA", "bolivia", "http://embamex.sre.gob.mx/bolivia"),
    ("BRASIL", "brasil", "http://embamex.sre.gob.mx/brasil"),
    ("CANADA", "canada", "http://embamex.sre.gob.mx/canada"),
    ("CHILE", "chile", "http://embamex.sre.gob.mx/chile"),
    ("CHINA", "china", "http://embamex.sre.gob.mx/china"),
    ("COLOMBIA", "colombia", "http://embamex.sre.gob.mx/colombia"),
    ("COREA", "corea", "http://embamex.sre.gob.mx/corea"),
    ("COSTA RICA", "costa-rica", "http://embamex.sre.gob.mx/costarica"),
    ("CUBA", "cuba", "http://embamex.sre.gob.mx/cuba"),
    ("DINAMARCA", "dinamarca", "http://embamex.sre.gob.mx/dinamarca"),
    ("ECUADOR", "ecuador", "http://embamex.sre.gob.mx/ecuador"),
    ("EGIPTO", "egipto", "http://embamex.sre.gob.mx/egipto"),
    ("EL SALVADOR", "el-salvador", "http://embamex.sre.gob.mx/elsalvador"),
    ("EMIRATOS ARABES", "emiratos-arabes", None),
    ("ESPANA", "espana", "http://embamex.sre.gob.mx/espana"),
    ("ESTADOS UNIDOS", "estados-unidos", "http://embamex.sre.gob.mx/eua"),
    ("ETIOPIA", "etiopia", "http://embamex.sre.gob.mx/etiopia"),
    ("FEDERACION RUSA", "federacion-rusa", "http://embamex.sre.gob.mx/rusia"),
    ("FILIPINAS", "filipinas", "http://embamex.sre.gob.mx/filipinas"),
    ("FINLANDIA", "finlandia", "http://embamex.sre.gob.mx/finlandia"),
    ("FRANCIA", "francia", "http://embamex.sre.gob.mx/francia"),
    ("GHANA", "ghana", "https://embamex.sre.gob.mx/ghana/"),
    ("GRECIA", "grecia", "http://embamex.sre.gob.mx/grecia"),
    ("GUATEMALA", "guatemala", "http://embamex.sre.gob.mx/guatemala"),
    ("GUYANA", "guyana", "http://embamex.sre.gob.mx/guyana"),
    ("HAITI", "haiti", "http://embamex.sre.gob.mx/haiti"),
    ("HONDURAS", "honduras", "http://embamex.sre.gob.mx/honduras"),
    ("HUNGRIA", "hungria", "http://embamex.sre.gob.mx/hungria"),
    ("INDIA", "india", "http://embamex.sre.gob.mx/india"),
    ("INDONESIA", "indonesia", "http://embamex.sre.gob.mx/indonesia"),
    ("IRAN", "iran", "http://embamex.sre.gob.mx/iran"),
    ("IRLANDA", "irlanda", "http://embamex.sre.gob.mx/irlanda"),
    ("ISRAEL", "israel", "http://embamex.sre.gob.mx/israel"),
    ("ITALIA", "italia", "http://embamex.sre.gob.mx/italia"),
    ("JAMAICA", "jamaica", "http://embamex.sre.gob.mx/jamaica"),
    ("JAPON", "japon", "http://embamex.sre.gob.mx/japon"),
    ("JORDANIA", "jordania", "http://embamex2.sre.gob.mx/jordania"),
    ("KENYA", "kenya", "http://embamex.sre.gob.mx/kenia"),
    ("KUWAIT", "kuwait", "http://embamex.sre.gob.mx/kuwait"),
    ("LIBANO", "libano", "http://embamex.sre.gob.mx/libano"),
    ("MALASIA", "malasia", "http://embamex.sre.gob.mx/malasia"),
    ("MARRUECOS", "marruecos", "http://embamex.sre.gob.mx/marruecos"),
    ("NICARAGUA", "nicaragua", "http://embamex.sre.gob.mx/nicaragua"),
    ("NIGERIA", "nigeria", "http://embamex.sre.gob.mx/nigeria"),
    ("NORUEGA", "noruega", "http://embamex.sre.gob.mx/noruega"),
    ("NUEVA ZELANDIA", "nueva-zelandia", "http://embamex.sre.gob.mx/nuevazelandia"),
    ("PAISES BAJOS", "paises-bajos", "http://embamex.sre.gob.mx/paisesbajos"),
    ("PANAMA", "panama", "http://embamex.sre.gob.mx/panama"),
    ("PARAGUAY", "paraguay", "http://embamex.sre.gob.mx/paraguay"),
    ("PERU", "peru", "http://embamex.sre.gob.mx/peru"),
    ("POLONIA", "polonia", "http://embamex.sre.gob.mx/polonia"),
    ("PORTUGAL", "portugal", "http://embamex.sre.gob.mx/portugal"),
    ("QATAR", "qatar", "http://embamex.sre.gob.mx/qatar"),
    ("REINO UNIDO", "reino-unido", "http://embamex.sre.gob.mx/reinounido"),
    ("REPUBLICA CHECA", "republica-checa", "http://embamex.sre.gob.mx/republicacheca"),
    ("REPUBLICA DOMINICANA", "republica-dominicana", "http://embamex.sre.gob.mx/republicadominicana"),
    ("RUMANIA", "rumania", "http://embamex.sre.gob.mx/rumania/"),
    ("SANTA LUCIA", "santa-lucia", "http://embamex.sre.gob.mx/santalucia"),
    ("SANTA SEDE", "santa-sede", "http://embamex.sre.gob.mx/vaticano"),
    ("SERBIA", "serbia", "http://embamex.sre.gob.mx/serbia"),
    ("SINGAPUR", "singapur", "http://embamex.sre.gob.mx/singapur"),
    ("SUDAFRICA", "sudafrica", "http://embamex.sre.gob.mx/sudafrica"),
    ("SUECIA", "suecia", "http://embamex.sre.gob.mx/suecia"),
    ("SUIZA", "suiza", "http://embamex.sre.gob.mx/suiza"),
    ("TAILANDIA", "tailandia", "http://embamex.sre.gob.mx/tailandia"),
    ("TRINIDAD Y TOBAGO", "trinidad-y-tobago", "http://embamex.sre.gob.mx/trinidadytobago"),
    ("TURKIYE", "turkiye", "http://embamex.sre.gob.mx/turquia"),
    ("UCRANIA", "ucrania", "http://embamex.sre.gob.mx/ucrania"),
    ("URUGUAY", "uruguay", "http://embamex.sre.gob.mx/uruguay"),
    ("VENEZUELA", "venezuela", "http://embamex.sre.gob.mx/venezuela"),
    ("VIET NAM", "viet-nam", "http://embamex.sre.gob.mx/vietnam"),
]


def sb_headers():
    return {
        "apikey": SB_KEY,
        "Authorization": f"Bearer {SB_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=minimal,resolution=merge-duplicates",
    }


def get_existing_slugs(tabla, slug_col):
    url = f"{SB_URL}/rest/v1/{tabla}?select={slug_col}"
    r = requests.get(url, headers={
        "apikey": SB_KEY,
        "Authorization": f"Bearer {SB_KEY}",
    }, timeout=30)
    r.raise_for_status()
    return {row[slug_col] for row in r.json() if row.get(slug_col)}


def upsert(tabla, payload, on_conflict):
    url = f"{SB_URL}/rest/v1/{tabla}?on_conflict={on_conflict}"
    r = requests.post(url, headers=sb_headers(), data=json.dumps(payload), timeout=30)
    if not r.ok:
        return False, r.text[:300]
    return True, ""


def main():
    print(f"[sre] FIX-32 modo SEMILLA. portales.sre.gob.mx bloqueado por perfdrive.")
    print(f"[sre] Seed: {len(SEED_CONSULADOS)} consulados + {len(SEED_EMBAJADAS)} embajadas")

    # Consulados
    existing_cons = get_existing_slugs("sre_consulados", "nombre_slug")
    print(f"[sre] Consulados ya en BD: {len(existing_cons)}")
    cons_inserted = cons_updated = cons_fail = 0
    for nombre, ciudad, slug, sitio in SEED_CONSULADOS:
        payload = {
            "nombre": nombre,
            "ciudad": ciudad,
            "nombre_slug": slug,
            "sitio_web_url": sitio,
            "directorio_url": f"https://portales.sre.gob.mx/directorio/index.php/consulados-de-mexico-en-el-exterior/{slug}" if slug != "chongqing" else None,
            "tipo": "consulado",
            "fecha_extraccion": HOY,
            "fuente_url": FUENTE_URL,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        ok, err = upsert("sre_consulados", payload, "nombre_slug")
        if not ok:
            cons_fail += 1
            if cons_fail <= 3:
                print(f"  [FAIL consulado={slug}] {err}")
            continue
        if slug in existing_cons:
            cons_updated += 1
        else:
            cons_inserted += 1

    # Embajadas
    existing_emb = get_existing_slugs("sre_embajadas", "pais_slug")
    print(f"[sre] Embajadas ya en BD: {len(existing_emb)}")
    emb_inserted = emb_updated = emb_fail = 0
    for pais, slug, sitio in SEED_EMBAJADAS:
        payload = {
            "pais": pais,
            "pais_slug": slug,
            "sitio_web_url": sitio,
            "directorio_url": f"https://portales.sre.gob.mx/directorio/index.php/embajadas-de-mexico-en-el-exterior/{slug}",
            "fecha_extraccion": HOY,
            "fuente_url": FUENTE_URL,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        ok, err = upsert("sre_embajadas", payload, "pais_slug")
        if not ok:
            emb_fail += 1
            if emb_fail <= 3:
                print(f"  [FAIL embajada={slug}] {err}")
            continue
        if slug in existing_emb:
            emb_updated += 1
        else:
            emb_inserted += 1

    total_inserted = cons_inserted + emb_inserted
    total_updated = cons_updated + emb_updated
    total_fail = cons_fail + emb_fail
    total_touched = total_inserted + total_updated

    print(f"\n[sre] Consulados: insert={cons_inserted} update={cons_updated} fail={cons_fail}")
    print(f"[sre] Embajadas:  insert={emb_inserted} update={emb_updated} fail={emb_fail}")
    print(f"[sre] Total touched: {total_touched} (insert={total_inserted}, update={total_updated})")
    # Marker para workflow extract step. Reportamos total_touched como rows_inserted
    # porque desde la perspectiva del scraper cada upsert es "fila escrita autoritativamente"
    # desde la semilla. La distincion insert vs update queda en stdout para auditoria.
    print(f"rows_inserted={total_touched}")
    print(f"rows_updated={total_updated}")

    if total_fail > 0:
        print(f"[sre] WARN: {total_fail} fallos en upsert", file=sys.stderr)


if __name__ == "__main__":
    main()
