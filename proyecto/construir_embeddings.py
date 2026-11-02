"""Construye embeddings de nombres y de secuencias de eventos a partir de routers.csv.

Entrada: la carpeta de una ejecución (p. ej. proyecto/resultados/iteracion1), de la
que se lee routers.csv (log de todos los hubs, ver ndnutils.packet_log).

Qué se construye:

1. Nombres de contenido. Cada nombre se normaliza quitando el número de secuencia
   final (seq=N), que es el segmento o la lectura y no dice nada del contenido:
       /ndn/edu/ucla/lectures/cs118/seq=42  ->  /ndn/edu/ucla/lectures/cs118
   - forma: FastText sobre las componentes de los nombres. Usa n-gramas de
     caracteres, así que nombres con erratas (wheather, lecture) quedan cerca del
     nombre correcto y se puede calcular el vector de un nombre nunca visto.
     El vector del nombre es la media de sus componentes ponderada por IDF: las
     que aparecen en casi todos los nombres (ndn, edu) pesan poco.
   - comportamiento: media de los vectores de los eventos de ese nombre (punto 2),
     ponderada por frecuencia. Dos nombres que los routers tratan igual (mismos
     vecinos, mismos resultados) quedan cerca aunque se escriban distinto.

2. Eventos. Cada fila del log se convierte en un token que resume qué pasó, con
   quién y con qué contenido:
       in:Interest<cli-ucla|/ndn/edu/ucla/lectures/cs118
       CsHit|/ndn/edu/ucla/lectures/cs118
       out:Nack>cli-ucla:NoRoute|/ndn/edu/ucla/lecture/cs118
   Word2Vec aprende un vector por token a partir del flujo de eventos de cada
   router (los eventos que ocurren cerca en el tiempo son el contexto).

3. Episodios. Un episodio es todo lo que hace un router con un nombre exacto
   (con su seq) desde que entra el Interest hasta que deja de haber actividad
   durante un InterestLifetime. Se guarda su secuencia de tokens, su resultado
   (CsHit, Data, Nack:<motivo>, Timeout) y su embedding: la media de los vectores
   de sus eventos. Para la segunda iteración, la parte útil a la hora de decidir
   es el inicio del episodio (lo que el router sabe cuando llega el Interest):
   el embedding completo incluye el resultado y sirve para analizar, no para
   predecirlo.

Salida (en proyecto/modelos/<ejecución>/ o la carpeta indicada con --salida),
que se carga con ndnemb.Modelo:
    nombres.ft                 modelo FastText (vector de cualquier nombre)
    eventos.w2v                modelo Word2Vec (vector de cada token de evento)
    nombres.csv                un nombre normalizado por fila, con sus estadísticas
    nombres.npz                arrays forma y comportamiento (en el orden de nombres.csv) y pesos IDF
    episodios.csv              un episodio por fila: router, nombre, tokens, resultado
    episodios.npy              embedding de cada episodio, en el orden de episodios.csv
    vecinos.txt                comprobación: nombres sin ruta y su nombre válido más cercano

Ejecutar (desde PrimerExperimento/):
    ./ndn.sh python3 proyecto/construir_embeddings.py proyecto/resultados/iteracion1
"""

import argparse
import csv
import os
from collections import Counter, defaultdict

import numpy as np
from gensim.models import FastText, Word2Vec

from ndnemb import calcular_idf, componentes, normalizar, vector_forma

DIR = os.path.dirname(os.path.abspath(__file__))

parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
parser.add_argument("ejecucion", help="carpeta de resultados con routers.csv")
parser.add_argument("--salida", help="carpeta de salida (por defecto proyecto/modelos/<ejecución>)")
parser.add_argument("--dim", type=int, default=32, help="dimensión de los embeddings")
parser.add_argument("--epocas", type=int, default=10)
parser.add_argument("--ventana", type=int, default=5, help="contexto de Word2Vec en eventos")
parser.add_argument("--lifetime", type=float, default=4.0, help="segundos sin actividad que cierran un episodio")
parser.add_argument("--seed", type=int, default=1)
parser.add_argument("--repeticiones", type=int, default=200,
                    help="veces que, como mucho, aparece cada nombre en el corpus de FastText")
parser.add_argument("--sin-episodios", action="store_true",
                    help="no construir episodios (para logs muy grandes, como el de la red de vídeo)")
args = parser.parse_args()

entrada = os.path.join(args.ejecucion, "routers.csv")
salida = args.salida or os.path.join(DIR, "modelos", os.path.basename(os.path.normpath(args.ejecucion)))
os.makedirs(salida, exist_ok=True)

FRASE = 64  # eventos por "frase" de Word2Vec: el flujo de cada router se trocea


# ---------- Tokens ----------

def token(r, prefijo):
    d, tipo = r["dir"], r["tipo"]
    if d == "in":
        t = f"in:{tipo}<{r['src']}"
    elif d == "out":
        t = f"out:{tipo}>{r['dst']}"
    else:
        t = tipo  # CsHit, CsMiss, Timeout: eventos internos del router
    if r["motivo"]:
        t += ":" + r["motivo"]
    return f"{t}|{prefijo}"


RESULTADO = ["CsHit", "Data", "Nack", "Timeout"]  # de mayor a menor prioridad


def resultado(ep):
    for clave in RESULTADO:
        if clave in ep["marcas"]:
            return f"Nack:{ep['marcas']['Nack']}" if clave == "Nack" else clave
    return "pendiente"


# ---------- Lectura del log ----------

print(f"Leyendo {entrada}")
flujos = defaultdict(list)            # router -> tokens en orden de tiempo
stats = defaultdict(Counter)          # nombre normalizado -> contadores
routers_de = defaultdict(set)         # nombre normalizado -> routers que lo ven
abiertos = {}                         # (router, nombre exacto) -> episodio en curso
episodios = []


def cerrar(clave):
    ep = abiertos.pop(clave)
    ep["resultado"] = resultado(ep)
    episodios.append(ep)


with open(entrada, newline="") as f:
    for i, r in enumerate(csv.DictReader(f)):
        router, nombre, tipo, t = r["node"], r["nombre"], r["tipo"], float(r["time"])
        if nombre.startswith("/localhost"):
            continue  # gestión interna de NFD, no es contenido
        prefijo = normalizar(nombre)
        tok = token(r, prefijo)
        flujos[router].append(tok)

        s = stats[prefijo]
        s["eventos"] += 1
        routers_de[prefijo].add(router)
        if r["dir"] == "in" and tipo == "Interest":
            s["interests"] += 1
        elif tipo == "CsHit":
            s["cshit"] += 1
        elif r["dir"] == "out" and tipo == "Data":
            s["data"] += 1
        elif r["dir"] == "out" and tipo == "Nack":
            s["nack:" + r["motivo"]] += 1
        elif tipo == "Timeout":
            s["timeout"] += 1

        if args.sin_episodios:
            continue
        clave = (router, nombre)
        ep = abiertos.get(clave)
        if ep and t - ep["fin"] > args.lifetime:
            cerrar(clave)
            ep = None
        if ep is None:
            ep = abiertos[clave] = {"router": router, "nombre": nombre, "prefijo": prefijo,
                                    "inicio": t, "fin": t, "tokens": [], "marcas": {}}
        ep["fin"] = t
        ep["tokens"].append(tok)
        if tipo == "CsHit":
            ep["marcas"]["CsHit"] = True
        elif tipo == "Data" and r["dir"] == "in":
            ep["marcas"]["Data"] = True
        elif tipo == "Nack" and r["dir"] == "out":
            ep["marcas"]["Nack"] = r["motivo"]
        elif tipo == "Timeout":
            ep["marcas"]["Timeout"] = True

for clave in list(abiertos):
    cerrar(clave)
episodios.sort(key=lambda e: e["inicio"])
print(f"  {i + 1} eventos, {len(stats)} nombres normalizados, {len(episodios)} episodios")

# ---------- Embeddings de eventos (Word2Vec) ----------

frases = [flujo[k:k + FRASE] for flujo in flujos.values() for k in range(0, len(flujo), FRASE)]
print(f"Entrenando Word2Vec con {len(frases)} frases de eventos")
w2v = Word2Vec(frases, vector_size=args.dim, window=args.ventana, min_count=1, sg=1,
               epochs=args.epocas, seed=args.seed, workers=4)
w2v.save(os.path.join(salida, "eventos.w2v"))
print(f"  {len(w2v.wv)} tokens de evento distintos")

# ---------- Embeddings de nombres (FastText) ----------

nombres = sorted(stats, key=lambda n: -stats[n]["eventos"])
# Cada nombre aparece en el corpus tantas veces como eventos tiene (hasta un tope),
# para que la frecuencia cuente sin que los nombres muy frecuentes lo dominen todo
corpus = [componentes(n) for n in nombres for _ in range(min(stats[n]["eventos"], args.repeticiones))]
print(f"Entrenando FastText con {len(corpus)} nombres")
# bucket: huecos para los n-gramas de caracteres; con pocas decenas de nombres
# sobran 100.000 (el valor por defecto, 2 millones, ocupa 256 MB)
ft = FastText(corpus, vector_size=args.dim, window=3, min_count=1, min_n=3, max_n=5,
              bucket=100_000, epochs=args.epocas, seed=args.seed, workers=4)
ft.save(os.path.join(salida, "nombres.ft"))

idf = calcular_idf(nombres)
idf_desconocida = max(idf.values())


def forma(nombre):
    return vector_forma(ft.wv, idf, nombre, idf_desconocida)


# Comportamiento: media ponderada de los tokens de evento de cada nombre
suma = defaultdict(lambda: np.zeros(args.dim))
peso = Counter()
for tok, idx in w2v.wv.key_to_index.items():
    prefijo = tok.split("|", 1)[1]
    n = w2v.wv.get_vecattr(tok, "count")
    suma[prefijo] += n * w2v.wv.vectors[idx]
    peso[prefijo] += n

F = np.array([forma(n) for n in nombres])
C = np.array([suma[n] / peso[n] for n in nombres])
np.savez(os.path.join(salida, "nombres.npz"), nombres=np.array(nombres), forma=F, comportamiento=C,
         idf_componentes=np.array(list(idf)), idf_pesos=np.array(list(idf.values())))

CAMPOS = ["interests", "data", "cshit", "nack:NoRoute", "nack:Duplicate", "timeout", "eventos"]
with open(os.path.join(salida, "nombres.csv"), "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["nombre"] + CAMPOS + ["routers"])
    for n in nombres:
        w.writerow([n] + [stats[n][c] for c in CAMPOS] + [" ".join(sorted(routers_de[n]))])

# ---------- Episodios ----------

E = np.zeros((0, args.dim)) if args.sin_episodios else np.array([np.mean([w2v.wv[t] for t in ep["tokens"]], axis=0) for ep in episodios])
np.save(os.path.join(salida, "episodios.npy"), E)
with open(os.path.join(salida, "episodios.csv"), "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["id", "router", "nombre", "prefijo", "inicio", "duracion", "n_eventos",
                "resultado", "tokens"])
    for k, ep in enumerate(episodios):
        w.writerow([k, ep["router"], ep["nombre"], ep["prefijo"], ep["inicio"],
                    round(ep["fin"] - ep["inicio"], 6), len(ep["tokens"]), ep["resultado"],
                    " ".join(ep["tokens"])])

# ---------- Comprobación ----------


def cos(a, b):
    return (a @ b.T) / (np.linalg.norm(a, axis=-1)[:, None] * np.linalg.norm(b, axis=-1)[None, :])


validos = [k for k, n in enumerate(nombres) if stats[n]["data"] + stats[n]["cshit"] > 0]
sin_ruta = [k for k, n in enumerate(nombres)
            if stats[n]["nack:NoRoute"] > 0 and stats[n]["data"] + stats[n]["cshit"] == 0]
lineas = ["Nombres que solo reciben NoRoute y el nombre válido más parecido",
          "(forma = FastText sobre el nombre; comportamiento = Word2Vec sobre los eventos)", ""]
for vista, M in (("forma", F), ("comportamiento", C)):
    sim = cos(M[sin_ruta], M[validos])
    lineas.append(f"== {vista}")
    for fila, k in enumerate(sin_ruta):
        j = sim[fila].argmax()
        lineas.append(f"  {nombres[k]:<44} -> {nombres[validos[j]]:<44} {sim[fila, j]:.2f}")
    lineas.append("")
with open(os.path.join(salida, "vecinos.txt"), "w") as f:
    f.write("\n".join(lineas))

print("\n" + "\n".join(lineas))
print(Counter(ep["resultado"] for ep in episodios).most_common())
print(f"\nModelos y datos en {salida}/")
