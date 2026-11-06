"""Construye el catálogo de /yt a partir del dataset público Trending YouTube Video Statistics.

El dataset (Kaggle, datasnaek/youtube-new, licencia CC0) recoge los vídeos en
tendencia de YouTube en varios países entre 2017 y 2018: título, canal, categoría,
fecha de publicación y visitas. Usamos el de México (MXvideos.csv y
MX_category_id.json), el que está en castellano, descargado en proyecto/datos/youtube/.

De ahí sale:
    canales   los que tienen al menos --min-videos vídeos; su nombre NDN es el nombre
              del canal y el de cada vídeo, su título (sin identificador)
    temática  la categoría de YouTube más frecuente entre sus vídeos
    shorts    los vídeos más vistos del resto de canales (en 2017 no había shorts:
              son vídeos sueltos de canales pequeños, con su título como nombre)

Salida: proyecto/video/catalogo_youtube.json, que lee catalogo.py.

Ejecutar (desde PrimerExperimento/; con Python del sistema, solo usa la biblioteca estándar):
    python3 proyecto/video/importar_youtube.py
"""

import argparse
import csv
import json
import os
import re
import sys
import unicodedata
from collections import Counter, defaultdict

DIR = os.path.dirname(os.path.abspath(__file__))
DATOS = os.path.join(os.path.dirname(DIR), "datos", "youtube")

parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
parser.add_argument("--min-videos", type=int, default=10, help="vídeos distintos para que un canal entre")
parser.add_argument("--shorts", type=int, default=3000)
parser.add_argument("--max-palabras", type=int, default=10, help="palabras del título en el nombre")
args = parser.parse_args()

csv.field_size_limit(sys.maxsize)

# Categorías de YouTube -> temáticas de catalogo.py (las que no están se descartan)
TEMATICA = {"Entertainment": "entretenimiento", "People & Blogs": "vlogs", "Sports": "deporte",
            "Music": "musica", "News & Politics": "noticias", "Howto & Style": "tutoriales",
            "Comedy": "humor", "Film & Animation": "cine", "Gaming": "gaming",
            "Education": "educacion", "Science & Technology": "tecnologia",
            "Autos & Vehicles": "motor", "Travel & Events": "viajes", "Pets & Animals": "mascotas"}


def slug(texto, max_palabras=None):
    """Nombre como componente NDN: minúsculas, sin acentos, palabras con guiones."""
    texto = unicodedata.normalize("NFKD", texto.lower().replace("&", " y "))
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    palabras = re.sub(r"[^a-z0-9]+", " ", texto).split()
    return "-".join(palabras[:max_palabras])


def latino(texto):
    """Si el texto está escrito casi todo en letras latinas (hay títulos con letras
    griegas o cirílicas usadas como adorno, que no tienen un nombre legible)."""
    letras = [c for c in unicodedata.normalize("NFKD", texto) if c.isalpha()]
    return len(letras) >= 3 and sum(c.isascii() for c in letras) >= 0.9 * len(letras)


with open(os.path.join(DATOS, "MX_category_id.json"), encoding="utf-8") as f:
    categorias = {c["id"]: c["snippet"]["title"] for c in json.load(f)["items"]}

# Un vídeo aparece un día por cada día en tendencia: nos quedamos con su máximo de visitas
videos = {}
with open(os.path.join(DATOS, "MXvideos.csv"), encoding="utf-8", errors="replace", newline="") as f:
    for r in csv.DictReader(f):
        if r["video_error_or_removed"] == "True" or r["title"] == "Deleted video":
            continue
        v = videos.setdefault(r["video_id"], {"titulo": r["title"], "canal": r["channel_title"],
                                              "categoria": categorias.get(r["category_id"]),
                                              "publicado": r["publish_time"], "visitas": 0})
        v["visitas"] = max(v["visitas"], int(r["views"]))
print(f"{len(videos)} vídeos distintos")

por_canal = defaultdict(list)
for v in videos.values():
    if latino(v["titulo"]) and latino(v["canal"]):
        por_canal[v["canal"]].append(v)

canales, pequenos, usados = [], [], set()
for nombre, vs in por_canal.items():
    s = slug(nombre)
    tematica = TEMATICA.get(Counter(v["categoria"] for v in vs).most_common(1)[0][0])
    if len(vs) < args.min_videos or not tematica or s in usados or s == "shorts":
        pequenos += vs
        continue
    # Del más reciente al más antiguo; títulos repetidos (o iguales al recortarlos), una vez
    nombres, vistos = [], set()
    for v in sorted(vs, key=lambda v: v["publicado"], reverse=True):
        n = slug(v["titulo"], args.max_palabras)
        if n and n != "miniatura" and n not in vistos:
            vistos.add(n)
            nombres.append(n)
    if len(nombres) < args.min_videos:
        pequenos += vs
        continue
    usados.add(s)
    canales.append({"slug": s, "nombre": nombre, "tematica": tematica,
                    "visitas": sum(v["visitas"] for v in vs), "videos": nombres})

shorts, vistos = [], set()
for v in sorted(pequenos, key=lambda v: -v["visitas"]):
    n = slug(v["titulo"], args.max_palabras)
    if n and n != "miniatura" and n not in vistos:
        vistos.add(n)
        shorts.append(n)
    if len(shorts) == args.shorts:
        break

ruta = os.path.join(DIR, "catalogo_youtube.json")
with open(ruta, "w", encoding="utf-8") as f:
    json.dump({"canales": canales, "shorts": shorts}, f, ensure_ascii=False, indent=0)
print(f"{len(canales)} canales, {sum(len(c['videos']) for c in canales)} vídeos, {len(shorts)} shorts -> {ruta}")
print("Canales por temática:", Counter(c["tematica"] for c in canales).most_common())
