"""Página de la red de vídeo en t = 0: qué Interests tiene programados cada casa y qué
contenidos anuncia cada productor, más los primeros paquetes del log de cada nodo.

Rellena plantilla.html con los datos de una simulación de red_video.py o con los logs
de logs_casas.py. Estos no tienen escenario.json (no simulan la red): la topología se
toma del de una simulación, que se pasa como tercer argumento, y el log de cada casa
es su captura (interests/<casa>.csv).

Ejecutar (desde PrimerExperimento/):
    python3 proyecto/informes/red_t0/datos.py proyecto/resultados/video_v19 red_t0.html
    python3 proyecto/informes/red_t0/datos.py proyecto/resultados/logs_v1 red_t0.html \
        proyecto/resultados/video_v19/escenario.json
"""

import csv
import json
import math
import os
import sys
from collections import defaultdict

DIR = os.path.dirname(os.path.abspath(__file__))
PROYECTO = os.path.dirname(os.path.dirname(DIR))
sys.path.insert(0, os.path.join(PROYECTO, "video"))
import catalogo as cat  # noqa: E402
from humanos import INSTANTE  # noqa: E402

SEGMENTO = 4.0
PRIMEROS = 40  # paquetes del log por nodo
GEN = {"Drama": "Drama", "Comedy": "Comedia", "Crime": "Crimen", "Thriller": "Thriller", "Action": "Acción",
       "Adventure": "Aventura", "Sci-Fi": "Ciencia ficción", "Fantasy": "Fantasía", "Romance": "Romance",
       "Horror": "Terror", "Mystery": "Misterio", "Animation": "Animación", "Documentary": "Documental",
       "Biography": "Biografía", "History": "Historia", "Family": "Familia", "War": "Bélica", "Music": "Música",
       "Musical": "Musical", "Sport": "Deporte", "Western": "Western", "Reality-TV": "Telerrealidad",
       "Talk-Show": "Entrevistas", "Game-Show": "Concurso", "News": "Noticias", "Short": "Corto"}

carpeta, salida = sys.argv[1], sys.argv[2]
logs = os.path.isdir(os.path.join(carpeta, "capturas"))
e = json.load(open(sys.argv[3] if len(sys.argv) > 3 else os.path.join(carpeta, "escenario.json"), encoding="utf-8"))
imdb = {t["slug"]: t for t in json.load(open(os.path.join(PROYECTO, "video", "catalogo_imdb.json"), encoding="utf-8"))}
yt = json.load(open(os.path.join(PROYECTO, "video", "catalogo_youtube.json"), encoding="utf-8"))
nombre_canal = {c["slug"]: c["nombre"] for c in yt["canales"]}

# Lo que anuncia cada productor
titulos = {t.prefijo(): t for t in cat.TITULOS}
canales = {f"/yt/{c.slug}": c for c in cat.canales()}
prod = defaultdict(list)
for a in e["aplicaciones"]:
    p, nodo = a["prefijo"], a["nodo"]
    partes = p.split("/")
    if partes[1] in ("vod", "rtve") and partes[2] != "directo":
        temporada = partes[-1] if partes[-1][1:].isdigit() and partes[-1][0] == "t" else None
        t = titulos["/".join(partes[:-1]) if temporada else p]
        r = {"p": p, "n": t.nombre, "c": t.temporadas[int(temporada[1:]) - 1] if temporada else 1,
             "f": "episodios" if temporada else "vídeo", "m": t.minutos,
             "g": [GEN.get(g, g) for g in t.generos]}
        i = imdb.get(t.slug) if t.plataforma == "vod" else None
        if i:
            r.update(i=i["imdb"], v=i["votos"], a=i["anio"])
        if t.espanola:
            r["es"] = 1
        if temporada and t.estreno.get(int(temporada[1:])):
            r["h"] = t.estreno[int(temporada[1:])]
    elif partes[2] == "directo":
        r = {"p": p, "n": f"Canal en directo {partes[3]}", "f": "emisión continua"}
    elif p == "/yt/shorts":
        r = {"p": p, "n": "Shorts", "c": len(cat.SHORTS), "f": "vídeos cortos", "t": cat.SHORTS}
    else:
        c = canales[p]
        r = {"p": p, "n": nombre_canal[c.antiguo or c.slug], "g": [c.tematica], "c": len(c.videos),
             "f": "vídeos", "m": c.minutos, "t": c.videos}
        if c.antiguo:
            r["ant"] = c.antiguo
    prod[nodo].append(r)

# Sesiones programadas de cada casa, con su hora prevista
with open(os.path.join(carpeta, "usuarios.csv"), newline="") as f:
    casa = {r["usuario"]: r["barrio"] for r in csv.DictReader(f)}
ints = defaultdict(list)
reloj = 0.0
with open(os.path.join(carpeta, "sesiones.csv"), newline="") as f:
    for r in csv.DictReader(f):
        inicio = float(r["inicio"])
        reloj = (inicio if inicio >= 0 else reloj) + float(r["pausa"])
        fila = [round(reloj, 3), r["usuario"], r["base"], r["categoria"], "", ""]
        if r["tipo"] != "miniatura":
            fila += [int(r["seg_inicio"]), int(r["n_segs"]), r["calidad_max"], r["condicion"]]
        if r["causa"]:
            fila[4:6] = [r["causa"], r["intencion"]]
        ints[casa[r["usuario"]]].append(fila)
        if logs:
            reloj += INSTANTE
        elif r["tipo"] != "miniatura":
            reloj += int(r["n_segs"]) * SEGMENTO

# Primeros paquetes de contenido de cada nodo; con logs, solo hay los de las casas
log, enviados = defaultdict(list), {}
if logs:
    for n in casa.values():
        with open(os.path.join(carpeta, "interests", f"{n}.csv"), newline="") as f:
            filas = list(csv.DictReader(f))
        enviados[n] = len(filas)
        t0 = (math.floor((float(filas[0]["time"]) + 3600) / 86400)) * 86400 - 3600  # 00:00 en Madrid (+01:00)
        log[n] = [[round(float(r["time"]) - t0, 4), "out", r["tipo"], r["src"], r["dst"], r["nombre"], int(r["bytes"])]
                  for r in filas[:PRIMEROS]]
    horas = 24 * math.ceil(max(f[0] for v in ints.values() for f in v) / 86400)
else:
    with open(os.path.join(carpeta, "paquetes.csv"), newline="") as f:
        for r in csv.DictReader(f):
            n = r["node"]
            if r["tipo"] not in ("Interest", "Data") or r["nombre"].startswith("/localhost") or len(log[n]) >= PRIMEROS:
                continue
            log[n].append([float(r["time"]), r["dir"], r["tipo"], r["src"], r["dst"], r["nombre"], int(r["bytes"])])

datos = {"escenario": os.path.basename(os.path.normpath(carpeta)), "nodos": e["nodos"],
         "enlaces": [[l["source"], l["target"], l["DataRate"], l["Delay"]] for l in e["enlaces"]],
         "prod": prod, "ints": ints, "log": log, "logs": logs, "enviados": enviados}
s = json.dumps(datos, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
html = open(os.path.join(DIR, "plantilla.html"), encoding="utf-8").read()
if not logs:
    horas = e["params"]["horas"]
periodo = f"{horas / 24:g} días" if horas > 24 else f"las {horas:g} h"
html = html.replace("__ESCENARIO__", datos["escenario"]).replace("__PERIODO__", periodo).replace("__DATOS__", s)
open(salida, "w", encoding="utf-8").write(html)
print(f"{salida}: {len(html) // 1024} KB; sesiones por casa", {k: len(v) for k, v in sorted(ints.items())})
