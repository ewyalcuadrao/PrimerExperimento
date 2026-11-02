"""Construye el catálogo de /vod a partir de los datasets públicos de IMDb.

IMDb publica sus datos para uso personal y no comercial en
https://datasets.imdbws.com/ (descargados en proyecto/datos/imdb/). De ahí sale:
    title.basics    tipo (película, serie, miniserie), géneros, año, duración
    title.ratings   número de votos: la popularidad de cada título
    title.episode   temporadas y episodios reales de cada serie
    title.akas      títulos por país: el título en España da el nombre NDN y el
                    original, el inglés y los otros títulos españoles son alias reales
                    (lo que la gente escribe cuando no sabe el nombre exacto)

Selección: los títulos más votados de cada tipo, con título en España y sin contenido
para adultos, y aparte las producciones en castellano más votadas (como el catálogo
nacional de una plataforma española). Los documentales son los del género Documentary.

Salida: proyecto/video/catalogo_imdb.json, que lee catalogo.py.

Ejecutar (desde PrimerExperimento/; con Python del sistema, solo usa la biblioteca estándar):
    python3 proyecto/video/importar_imdb.py
"""

import argparse
import csv
import gzip
import json
import os
import re
import sys
import unicodedata
from collections import defaultdict

DIR = os.path.dirname(os.path.abspath(__file__))
DATOS = os.path.join(os.path.dirname(DIR), "datos", "imdb")

parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
parser.add_argument("--series", type=int, default=300)
parser.add_argument("--pelis", type=int, default=600)
parser.add_argument("--docs", type=int, default=80)
parser.add_argument("--espanolas", type=int, default=200,
                    help="producciones en castellano añadidas aparte (las más votadas en IMDb son de EE. UU.)")
parser.add_argument("--min-votos", type=int, default=15000)
parser.add_argument("--min-votos-espanolas", type=int, default=1500)
parser.add_argument("--max-temporadas", type=int, default=15)
parser.add_argument("--max-episodios", type=int, default=30, help="episodios por temporada, como mucho")
args = parser.parse_args()

csv.field_size_limit(sys.maxsize)


def leer(nombre):
    with gzip.open(os.path.join(DATOS, f"{nombre}.tsv.gz"), "rt", encoding="utf-8", newline="") as f:
        yield from csv.DictReader(f, delimiter="\t", quoting=csv.QUOTE_NONE)


def slug(texto):
    """Nombre de un título como componente NDN: minúsculas, sin acentos, palabras con guiones."""
    texto = unicodedata.normalize("NFKD", texto.lower().replace("&", " y "))
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "-", texto).strip("-")


print("Votos...")
votos = {r["tconst"]: int(r["numVotes"]) for r in leer("title.ratings")
         if int(r["numVotes"]) >= min(args.min_votos, args.min_votos_espanolas)}

print("Títulos...")
titulos = {}
for r in leer("title.basics"):
    if r["tconst"] not in votos or r["isAdult"] == "1":
        continue
    if r["titleType"] not in ("movie", "tvSeries", "tvMiniSeries"):
        continue
    generos = [] if r["genres"] == r"\N" else r["genres"].split(",")
    titulos[r["tconst"]] = {
        "id": r["tconst"], "serie": r["titleType"] != "movie", "generos": generos,
        "doc": "Documentary" in generos, "primario": r["primaryTitle"], "original": r["originalTitle"],
        "anio": None if r["startYear"] == r"\N" else int(r["startYear"]),
        "minutos": None if r["runtimeMinutes"] == r"\N" else int(r["runtimeMinutes"]),
        "votos": votos[r["tconst"]], "es": [], "en": set(), "con_original": set(),
    }
print(f"  {len(titulos)} títulos con al menos {min(args.min_votos, args.min_votos_espanolas)} votos")

HISPANOS = {"ES", "MX", "AR", "CO", "CL", "PE", "VE", "UY", "EC", "BO", "PY", "CR", "CU", "DO", "GT", "PA"}
for t in titulos.values():
    t["slug_original"] = slug(t["original"])

print("Títulos en España (title.akas, tarda unos minutos)...")
for r in leer("title.akas"):
    t = titulos.get(r["titleId"])
    if t is None:
        continue
    if r["region"] not in (r"\N", "XWW") and slug(r["title"]) == t["slug_original"]:
        t["con_original"].add(r["region"])  # países donde se estrenó con el título original
    if r["region"] == "ES":
        t["es"].append((r["title"], r["types"], r["language"]))
    elif r["region"] in ("US", "GB"):
        t["en"].add(slug(r["title"]))

print("Episodios...")
episodios = defaultdict(lambda: defaultdict(int))
for r in leer("title.episode"):
    if r["parentTconst"] in titulos and r["seasonNumber"] != r"\N":
        episodios[r["parentTconst"]][int(r["seasonNumber"])] += 1


def titulo_es(t):
    """Título con el que se conoce en España, en castellano (la región ES de IMDb incluye
    títulos en catalán, euskera y gallego): el de la plataforma (imdbDisplay) o el primero."""
    castellano = [(x, tipos) for x, tipos, lengua in t["es"] if lengua in ("es", r"\N")]
    if not castellano:
        return None
    preferidos = [x for x, tipos in castellano if "imdbDisplay" in tipos] + \
                 [x for x, tipos in castellano if tipos in (r"\N", "")]
    return (preferidos or [x for x, _ in castellano])[0]


def temporadas(t):
    eps = episodios.get(t["id"], {})
    numeradas = [eps.get(n, 0) for n in range(1, args.max_temporadas + 1)]
    # Temporadas seguidas desde la 1 (IMDb tiene huecos y temporadas "0" de especiales)
    seguidas = []
    for n in numeradas:
        if n == 0:
            break
        seguidas.append(min(n, args.max_episodios))
    return seguidas


def espanola(t):
    """Producción en castellano: el título original es el de España, en EE. UU. y Reino
    Unido se conoce con otro, y los países que lo dejan sin traducir son sobre todo
    hispanohablantes (así no entran películas de otros idiomas que en España se
    estrenan con su título original)."""
    nombre = titulo_es(t)
    if not nombre or t["slug_original"] != slug(nombre) or not t["en"] or slug(nombre) in t["en"]:
        return False
    hispanos = len(t["con_original"] & HISPANOS)
    return hispanos >= 2 and hispanos > len(t["con_original"] - HISPANOS)


def tipo(t):
    return "doc" if t["doc"] else "serie" if t["serie"] else "peli"


def valido(t):
    return t["es"] and (not t["serie"] or temporadas(t))


def elegir(filtro, n, min_votos=args.min_votos):
    candidatos = [t for t in titulos.values() if t["votos"] >= min_votos and valido(t) and filtro(t)]
    return sorted(candidatos, key=lambda t: -t["votos"])[:n]


elegidos = (elegir(lambda t: tipo(t) == "serie", args.series)
            + elegir(lambda t: tipo(t) == "peli", args.pelis)
            + elegir(lambda t: tipo(t) == "doc", args.docs))
ya = {t["id"] for t in elegidos}
elegidos += elegir(lambda t: t["id"] not in ya and espanola(t), args.espanolas, args.min_votos_espanolas)

salida, usados = [], set()
for t in elegidos:
    nombre = titulo_es(t)
    s = slug(nombre) if nombre else ""
    if not s:
        continue
    if s in usados:
        s = f"{s}-{t['anio']}"  # remakes y títulos repetidos: con el año, como en las plataformas
    if s in usados:
        continue
    usados.add(s)
    alias = [t["original"], t["primario"]] + [x for x, _, _ in t["es"]]
    salida.append({
        "imdb": t["id"], "tipo": tipo(t), "slug": s, "nombre": nombre,
        "alias": list(dict.fromkeys(slug(a) for a in alias if slug(a) and slug(a) != s)),
        "temporadas": temporadas(t) if t["serie"] else [],
        "minutos": t["minutos"], "anio": t["anio"], "votos": t["votos"], "generos": t["generos"],
        "espanola": espanola(t),
    })

# Un alias no puede ser el nombre real de otro título: entonces no sería un error
for t in salida:
    t["alias"] = [a for a in t["alias"] if a not in usados][:4]

ruta = os.path.join(DIR, "catalogo_imdb.json")
with open(ruta, "w", encoding="utf-8") as f:
    json.dump(salida, f, ensure_ascii=False, indent=1)
tipos = defaultdict(int)
for t in salida:
    tipos[t["tipo"]] += 1
print(f"{len(salida)} títulos ({dict(tipos)}), "
      f"{sum(len(t['temporadas']) for t in salida)} temporadas, "
      f"{sum(sum(t['temporadas']) for t in salida)} episodios -> {ruta}")
