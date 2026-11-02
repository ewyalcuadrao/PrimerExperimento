"""Prueba sin simulación: corregir la componente que falla en lugar de buscar un nombre visto.

Lo que sabe el router: los prefijos de su FIB (escenario.json) y los nombres válidos del
log de la iteración 1 (nombres.csv del modelo). Para un nombre sin ruta:
  1. Prueba cada prefijo de la FIB p como "lo que quería decir" la parte inicial del
     nombre: puntuación = producto de la similitud de cada componente distinta.
  2. Al resto del nombre (episodio, id de vídeo, miniatura) le corrige las componentes que
     no conoce, solo en posiciones de vocabulario cerrado (e1..eN sí, ids de vídeo no).
  3. Devuelve el mejor nombre reconstruido y su puntuación.

Uso: python3 corrector.py <resultados v6> <modelo v6> [medida ...]
"""

import csv
import difflib
import json
import os
import sys
import unicodedata
from collections import Counter, defaultdict
from urllib.parse import unquote

sys.path.insert(0, "/work/proyecto")
from ndnemb import coseno, componentes  # noqa: E402

RES, MOD = sys.argv[1], sys.argv[2]
MEDIDAS = sys.argv[3:] or ["edicion", "trigramas"]


def plano(c):
    """Lo que normaliza cualquier app: % del navegador, mayúsculas y acentos."""
    c = unicodedata.normalize("NFKD", unquote(c).lower())
    return "".join(x for x in c if not unicodedata.combining(x))


def tri(c):
    s = f"<{c}>"
    return Counter(s[i:i + 3] for i in range(len(s) - 2))


_cache = {}


def sim(medida, a, b):
    if a == b:
        return 1.0
    clave = (medida, a, b)
    if clave not in _cache:
        x, y = plano(a), plano(b)
        if medida == "edicion":
            v = difflib.SequenceMatcher(None, x, y).ratio()
        elif medida == "trigramas":
            v = coseno(tri(x), tri(y))
        else:
            v = EXTRA[medida](x, y)
        _cache[clave] = v
    return _cache[clave]


import builtins
EXTRA = getattr(builtins, "MEDIDAS_EXTRA", {})  # medidas añadidas desde fuera (embeddings)

prefijos = [componentes(a["prefijo"]) for a in json.load(open(os.path.join(RES, "escenario.json")))["aplicaciones"]]
nombres = list(csv.DictReader(open(os.path.join(MOD, "nombres.csv"))))
validos = [componentes(n["nombre"]) for n in nombres if int(n["data"]) + int(n["cshit"]) > 0]

# Vocabulario del resto del nombre por (tipo de prefijo, posición): e1..eN, miniatura, ids
vocab = defaultdict(set)
por_longitud = defaultdict(list)
for p in prefijos:
    por_longitud[len(p)].append(p)
largos = sorted(por_longitud, reverse=True)

# IDF de cada componente en la FIB: vod o serie están en miles de prefijos y equivocarse en
# ellas dice poco; un título está en uno o pocos. Con idf, la similitud de una componente se
# eleva a su peso (0..1): s ** peso, así una diferencia en una componente común pesa poco.
import math
df = Counter(c for p in prefijos for c in set(p))
idf = {c: math.log(len(prefijos) / d) for c, d in df.items()}
IDF_MAX = max(idf.values())
USAR_IDF = os.environ.get("IDF") == "1"


POTENCIA = float(os.environ.get("POTENCIA", "1"))
MINIMO = float(os.environ.get("MINIMO", "0"))      # similitud mínima de una componente
VISTO = float(os.environ.get("VISTO", "1"))        # puntuación ** VISTO si el nombre ya se vio
vistos = {"/" + "/".join(n) for n in validos}


def peso(c):
    return (idf.get(c, IDF_MAX) / IDF_MAX) ** POTENCIA if USAR_IDF else 1.0


def tipo(p):
    return tuple(p[:2]) if p[0] != "yt" else ("yt",)


for n in validos:
    for L in largos:
        if any(n[:L] == p for p in por_longitud[L]):
            for i, c in enumerate(n[L:]):
                vocab[(tipo(n), L + i)].add(c)
            break
CERRADO = 200  # una posición con más componentes distintas que esto es "abierta" (ids)


def corregir(q, medida):
    mejor = (0.0, None)
    for L in largos:
        if L > len(q):
            continue
        for p in por_longitud[L]:
            s = 1.0
            for a, b in zip(q[:L], p):
                if a != b:
                    s *= max(sim(medida, a, b), MINIMO) ** peso(b)
                    if s <= mejor[0]:
                        break
            if s <= mejor[0]:
                continue
            resto = []
            for i, c in enumerate(q[L:]):
                v = vocab.get((tipo(p), L + i), set())
                if c in v or len(v) > CERRADO or not v:
                    resto.append(c)
                else:
                    r = max(v, key=lambda x: sim(medida, c, x))
                    s *= sim(medida, c, r)
                    resto.append(r)
            nombre = "/" + "/".join(p + resto)
            if nombre in vistos:
                s = s ** VISTO
            if s > mejor[0]:
                mejor = (s, nombre)
    return mejor


def titulo(b):
    partes = b.split("/")
    return "/".join(partes[:3] if partes[1] == "yt" else partes[:4])


errores = [s for s in csv.DictReader(open(os.path.join(RES, "sesiones.csv"))) if s["causa"]]
# Nombres que sí tienen ruta, para ver si el corrector "corrige" lo que está bien (no debería
# llamarse nunca con ellos, pero dice qué puntuación da un nombre bueno contra otros)
for medida in MEDIDAS:
    clases, sims = Counter(), defaultdict(list)
    por_causa = defaultdict(Counter)
    for s in errores:
        v, n = corregir(componentes(s["base"]), medida)
        c = "acierto" if n == s["intencion"] else "mismo_titulo" if n and titulo(n) == titulo(s["intencion"]) else "falso_positivo"
        clases[c] += 1
        sims[c].append(v)
        por_causa[s["causa"]][c] += 1
    total = len(errores)
    print(f"\n== {medida}: acierto {clases['acierto']} ({100 * clases['acierto'] / total:.0f} %), "
          f"mismo título {clases['mismo_titulo']}, falso positivo {clases['falso_positivo']}")
    for c in ("acierto", "mismo_titulo", "falso_positivo"):
        if sims[c]:
            x = sorted(sims[c])
            print(f"   {c:<15} puntuación media {sum(x) / len(x):.2f}  (p10 {x[len(x) // 10]:.2f}, p90 {x[9 * len(x) // 10]:.2f})")
    for u in (0.5, 0.6, 0.7, 0.8, 0.9):
        a = sum(v >= u for v in sims["acierto"])
        f = sum(v >= u for c in ("mismo_titulo", "falso_positivo") for v in sims[c])
        print(f"   umbral {u}: redirige {a + f}, acierto {a}, otro {f}")
    for causa, k in sorted(por_causa.items(), key=lambda x: -sum(x[1].values())):
        print(f"   {causa:<24} " + "  ".join(f"{c} {k[c]}" for c in ("acierto", "mismo_titulo", "falso_positivo")))
