"""item2vec: embeddings de nombres a partir de las sesiones que ven los routers.

Idea (Barkan y Koenigstein, 2016): una sesión es una frase y cada nombre pedido en ella es
una palabra. Word2Vec (skip-gram) acerca los nombres que se piden en las mismas sesiones,
así que nombres del mismo tema o que la gente ve seguidos quedan cerca aunque no se
parezcan al escribirlos.

Sesiones (lo que puede ver un router, sin saber qué es un usuario):
- Se usan los routers de acceso: los nodos barrio-*, donde cada reproductor llega por su
  propia cara. Más arriba (agg-*, núcleo) una cara mezcla a todo un barrio.
- Dentro de cada cara, los Interests en orden de tiempo se cortan en sesiones cuando hay
  un hueco mayor que --hueco segundos. Por defecto se elige solo: el valle del histograma
  de huecos (en escala logarítmica) entre la joroba de huecos cortos (dentro de una
  visita) y la de huecos largos.
- Entra todo lo que pide la cara (vídeos, miniaturas, también los nombres que reciben
  NoRoute), sin filtrar nada según lo que sabemos de la simulación.

Cada router puede entrenar solo con sus sesiones (--router), para comparar después con
aprendizaje federado; sin --router se entrena con las de todos (centralizado).

Evaluación (lo único que usa lo que sabemos de la simulación, y solo para medir): para
cada nombre de vídeo, ¿sus 10 vecinos más cercanos son del mismo título (serie, película o
canal) y del mismo tema (género de IMDb, temática del canal)? Se compara con lo que daría
elegir vecinos al azar.

Salida (proyecto/modelos/item2vec_<ejecución>[_<router>]/):
    item2vec.w2v     modelo Word2Vec (vector de cada nombre)
    validos.txt      nombres que recibieron Data en los routers de acceso (los únicos a
                     los que se puede redirigir, ver ndnemb.Modelo)
    sesiones.txt     una sesión por línea: router, cara y nombres separados por espacios

Ejecutar (desde PrimerExperimento/):
    ./ndn.sh python3 proyecto/item2vec.py proyecto/resultados/video_v19
    ./ndn.sh python3 proyecto/item2vec.py proyecto/resultados/video_v19 --router barrio-mad-1
"""

import argparse
import csv
import math
import os
import random
from collections import Counter, defaultdict

from gensim.models import Word2Vec

from ndnemb import normalizar

DIR = os.path.dirname(os.path.abspath(__file__))

parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
parser.add_argument("ejecucion", help="carpeta de resultados con paquetes.csv")
parser.add_argument("--router", help="entrenar solo con las sesiones de este router de acceso")
parser.add_argument("--hueco", type=float, help="segundos sin Interests que cortan una sesión (por defecto, automático)")
parser.add_argument("--dim", type=int, default=32)
parser.add_argument("--ventana", type=int, default=5)
parser.add_argument("--epocas", type=int, default=20)
parser.add_argument("--seed", type=int, default=1)
parser.add_argument("--salida")
args = parser.parse_args()


# ---------- Sesiones ----------

def leer(carpeta):
    """Interests que entran a un router de acceso desde un reproductor, como
    (router, cara, tiempo, nombre), y nombres a los que ese router devolvió Data."""
    entradas, con_data = [], Counter()
    with open(os.path.join(carpeta, "paquetes.csv"), newline="") as f:
        for r in csv.DictReader(f):
            if not r["node"].startswith("barrio-") or r["nombre"].startswith("/localhost"):
                continue
            if r["dir"] == "in" and r["tipo"] == "Interest" and r["src"] == "app":
                entradas.append((r["node"], r["face"], float(r["time"]), normalizar(r["nombre"])))
            elif r["dir"] == "out" and r["tipo"] == "Data" and r["dst"] == "app":
                con_data[(r["node"], normalizar(r["nombre"]))] += 1
    return entradas, con_data


def valle(huecos):
    """Hueco que separa sesiones: el mínimo del histograma (4 cajas por década) entre la
    joroba más alta por debajo de 100 s y la más alta por encima."""
    h = Counter(int(math.floor(math.log10(g) * 4)) for g in huecos if g > 0)
    corte = 8  # 100 s
    corta = max((b for b in h if b < corte), key=lambda b: h[b])
    larga = max((b for b in h if b >= corte), key=lambda b: h[b])
    fondo = min(range(corta + 1, larga), key=lambda b: h[b])
    return 10 ** ((fondo + 1) / 4)  # borde superior de la caja del valle


entradas, con_data = leer(args.ejecucion)
por_cara = defaultdict(list)
for router, cara, t, nombre in entradas:
    por_cara[(router, cara)].append((t, nombre))
for v in por_cara.values():
    v.sort()

huecos = [b[0] - a[0] for v in por_cara.values() for a, b in zip(v, v[1:])]
hueco = args.hueco or valle(huecos)

sesiones = []  # (router, cara, [nombres])
for (router, cara), v in sorted(por_cara.items()):
    actual = [v[0][1]]
    for (t0, _), (t1, nombre) in zip(v, v[1:]):
        if t1 - t0 > hueco:
            sesiones.append((router, cara, actual))
            actual = []
        actual.append(nombre)
    sesiones.append((router, cara, actual))

routers = sorted({r for r, _, _ in sesiones})
if args.router:
    if args.router not in routers:
        raise SystemExit(f"{args.router} no es un router de acceso de esta ejecución: {routers}")
    sesiones = [s for s in sesiones if s[0] == args.router]
largos = [len(s) for _, _, s in sesiones]
print(f"Hueco que corta sesiones: {hueco:.0f} s{' (automático)' if not args.hueco else ''}")
print(f"{len(sesiones)} sesiones en {len({(r, c) for r, c, _ in sesiones})} caras de "
      f"{len({r for r, _, _ in sesiones})} routers; nombres por sesión: mediana "
      f"{sorted(largos)[len(largos) // 2]}, media {sum(largos) / len(largos):.1f}; "
      f"{sum(x == 1 for x in largos)} sesiones de un solo nombre")

# ---------- Entrenamiento ----------

frases = [s for _, _, s in sesiones]
modelo = Word2Vec(frases, vector_size=args.dim, window=args.ventana, min_count=1, sg=1,
                  epochs=args.epocas, seed=args.seed, workers=4)
nombre_ejec = os.path.basename(os.path.normpath(args.ejecucion))
salida = args.salida or os.path.join(
    DIR, "modelos", f"item2vec_{nombre_ejec}" + (f"_{args.router}" if args.router else ""))
os.makedirs(salida, exist_ok=True)
modelo.save(os.path.join(salida, "item2vec.w2v"))
with open(os.path.join(salida, "sesiones.txt"), "w") as f:
    for router, cara, s in sesiones:
        f.write(f"{router} {cara} {' '.join(s)}\n")
validos = sorted({n for (r, n) in con_data if not args.router or r == args.router})
with open(os.path.join(salida, "validos.txt"), "w") as f:
    f.write("\n".join(validos) + "\n")
print(f"{len(modelo.wv)} nombres con vector, {len(validos)} válidos (recibieron Data) -> {salida}/")

# ---------- Evaluación con lo que sabemos de la simulación ----------

import sys  # noqa: E402

sys.path.insert(0, os.path.join(DIR, "video"))
import catalogo as cat  # noqa: E402

temas = {}
for t in cat.TITULOS:
    temas[t.prefijo()] = set(t.generos) or {t.tipo}  # RTVE no tiene géneros: su tipo
for c in cat.canales():
    temas[f"/yt/{c.slug}"] = {c.tematica}
temas["/yt/shorts"] = {"shorts"}
for canal in cat.DIRECTOS:
    temas[f"/rtve/directo/{canal}"] = {"directo"}


def titulo(nombre):
    p = nombre.split("/")
    return "/".join(p[:3] if p[1] == "yt" else p[:4])


def es_video(n):
    return not n.endswith("/miniatura") and titulo(n) in temas


frecuencia = Counter(n for s in frases for n in s)
evaluados = [n for n in modelo.wv.index_to_key if es_video(n) and frecuencia[n] >= 3]
videos = [n for n in modelo.wv.index_to_key if es_video(n)]
K = 10
mismo_titulo = mismo_tema = 0
azar_titulo = azar_tema = 0
rng = random.Random(args.seed)
for n in evaluados:
    vecinos = [v for v, _ in modelo.wv.most_similar(n, topn=50) if es_video(v)][:K]
    azar = rng.sample([v for v in videos if v != n], min(K, len(videos) - 1))
    for grupo, cuenta in ((vecinos, "modelo"), (azar, "azar")):
        t = sum(titulo(v) == titulo(n) for v in grupo) / max(len(grupo), 1)
        m = sum(bool(temas[titulo(v)] & temas[titulo(n)]) for v in grupo) / max(len(grupo), 1)
        if cuenta == "modelo":
            mismo_titulo += t
            mismo_tema += m
        else:
            azar_titulo += t
            azar_tema += m
if evaluados:
    e = len(evaluados)
    print(f"\nEvaluación: {e} nombres de vídeo pedidos al menos 3 veces, sus {K} vecinos más cercanos")
    print(f"  del mismo título:  {100 * mismo_titulo / e:5.1f} %   (al azar {100 * azar_titulo / e:4.1f} %)")
    print(f"  del mismo tema:    {100 * mismo_tema / e:5.1f} %   (al azar {100 * azar_tema / e:4.1f} %)")
    ejemplo = max(evaluados, key=lambda n: frecuencia[n] * (n.startswith("/vod/serie")))
    print(f"\nEjemplo, vecinos de {ejemplo}:")
    for v, s in modelo.wv.most_similar(ejemplo, topn=6):
        print(f"  {s:.2f}  {v}")
