"""Compara dos ejecuciones de red_testbed.py: NoRoute evitados frente a falsos positivos.

Sigue cada Interest que generan las aplicaciones de los clientes y lo clasifica:

    Data          llega Data por una ruta normal de la FIB
    acierto       no había ruta, la estrategia por similitud lo reenvió hacia un
                  nombre que era el que el cliente quería (ver INTENCION en
                  red_testbed.py) y llegó Data
    falso positivo  no había ruta, se reenvió hacia otro contenido y llegó Data:
                  el cliente recibe algo que no pidió
    NoRoute       Nack NoRoute
    otro Nack     Nack por otro motivo (Duplicate, Congestion...)
    sin respuesta ni Data ni Nack (no cuenta lo pedido en el último InterestLifetime)

Un Interest "sin ruta" es el que pide un nombre que no se puede servir: las
peticiones con resultado esperado NoRoute y las de sensores tras la caída de iot-gw.

Salida en proyecto/resultados/comparacion/ (o --salida): comparacion.csv con una
fila por iteración y prefijo, y el resumen por pantalla.

Ejecutar (desde PrimerExperimento/):
    ./ndn.sh python3 proyecto/comparar_iteraciones.py \\
        proyecto/resultados/iteracion1 proyecto/resultados/iteracion2
"""

import argparse
import csv
import json
import os
from collections import Counter, defaultdict, deque

from ndnemb import normalizar

DIR = os.path.dirname(os.path.abspath(__file__))

parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
parser.add_argument("ejecuciones", nargs="+", help="carpetas de resultados a comparar")
parser.add_argument("--salida", default=os.path.join(DIR, "resultados", "comparacion"))
args = parser.parse_args()

CLASES = ["Data", "acierto", "falso positivo", "NoRoute", "otro Nack", "sin respuesta"]


def analizar(carpeta):
    esc = json.load(open(os.path.join(carpeta, "escenario.json")))
    clientes = {n["id"] for n in esc["topologia"]["nodes"] if n["rol"] == "consumer"}
    trafico = {t["prefijo"]: t for t in esc["trafico"] if t["nodo"] in clientes}
    caidas = {c["prefijo"]: c["tiempo"] for c in esc["caidas"]}
    fin = esc["params"]["tiempo"] - float(esc["params"]["lifetime"].rstrip("s"))

    pendientes = defaultdict(deque)  # (cliente, nombre) -> Interests sin resolver
    por_nonce = {}
    interests = []
    with open(os.path.join(carpeta, "paquetes.csv"), newline="") as f:
        for r in csv.DictReader(f):
            node, tipo, d = r["node"], r["tipo"], r["dir"]
            rec = por_nonce.get(r["nonce"])
            if rec:  # recorrido del Interest por la red (mismo nonce en todos los saltos)
                rec["ultimo"] = float(r["time"])
                if d == "in" and tipo == "Interest":
                    rec["visitas"][node] += 1
                elif d == "in" and tipo == "Nack" and r["motivo"] == "Duplicate":
                    rec["duplicate"] += 1
            if tipo == "Redirect":
                if rec:
                    rec["redirecciones"] += 1
                    if rec["redirigido"] is None:
                        rec["redirigido"] = normalizar(r["motivo"].rsplit("@", 1)[0])
                continue
            if node not in clientes:
                continue
            clave = (node, r["nombre"])
            if d == "in" and tipo == "Interest" and r["src"] == "app":
                prefijo = normalizar(r["nombre"])
                t = float(r["time"])
                entrada = trafico.get(prefijo, {})
                sin_ruta = entrada.get("esperado") == "NoRoute" or t >= caidas.get(prefijo, float("inf"))
                rec = {"cliente": node, "prefijo": prefijo, "t": t, "sin_ruta": sin_ruta,
                       "familia": entrada.get("familia", "otros"),
                       "intencion": entrada.get("intencion") or [],
                       "redirigido": None, "resultado": None, "redirecciones": 0,
                       "visitas": Counter(), "duplicate": 0, "ultimo": t}
                interests.append(rec)
                pendientes[clave].append(rec)
                por_nonce[r["nonce"]] = rec
                rec["visitas"][node] += 1
            elif d == "out" and r["dst"] == "app" and tipo in ("Data", "Nack") and pendientes[clave]:
                rec = pendientes[clave].popleft()
                rec["resultado"] = tipo if tipo == "Data" else r["motivo"]

    for rec in interests:
        res, red = rec["resultado"], rec["redirigido"]
        if res == "Data" and red:
            rec["clase"] = "acierto" if red in rec["intencion"] else "falso positivo"
        elif res == "Data":
            rec["clase"] = "Data"
        elif res == "NoRoute":
            rec["clase"] = "NoRoute"
        elif res:
            rec["clase"] = "otro Nack"
        else:
            rec["clase"] = "sin respuesta"
    return [r for r in interests if r["t"] < fin], esc


def pct(n, total):
    return f"{100 * n / total:5.1f}%" if total else "    -"


resultados = {}
for carpeta in args.ejecuciones:
    nombre = os.path.basename(os.path.normpath(carpeta))
    print(f"Analizando {nombre}")
    resultados[nombre], esc = analizar(carpeta)
    modelo = esc["params"].get("modelo")
    estrategia = f"similitud ≥ {esc['params']['umbral']}" if modelo else "best-route"
    resultados[nombre] = (resultados[nombre], estrategia)

# ---------- Resumen global ----------

print(f"\n{'':<26}" + "".join(f"{n:>22}" for n in resultados))
print(f"{'estrategia':<26}" + "".join(f"{e:>22}" for _, e in resultados.values()))
for titulo, filtro in (("Todos los Interests", lambda r: True),
                       ("Interests sin ruta", lambda r: r["sin_ruta"]),
                       ("Interests con ruta", lambda r: not r["sin_ruta"])):
    print(f"\n{titulo}")
    cuentas = {n: Counter(r["clase"] for r in recs if filtro(r)) for n, (recs, _) in resultados.items()}
    totales = {n: sum(c.values()) for n, c in cuentas.items()}
    print(f"  {'total':<24}" + "".join(f"{totales[n]:>22}" for n in resultados))
    for clase in CLASES:
        if any(c[clase] for c in cuentas.values()):
            print(f"  {clase:<24}" + "".join(f"{c[clase]:>14} {pct(c[clase], totales[n])}"
                                             for n, c in cuentas.items()))

print("\nReenvío por similitud")
for n, (recs, _) in resultados.items():
    c = Counter(r["clase"] for r in recs)
    tp, fp = c["acierto"], c["falso positivo"]
    datos = c["Data"] + tp + fp
    if tp + fp:
        print(f"  {n}: precisión {pct(tp, tp + fp).strip()} ({tp} aciertos, {fp} falsos positivos); "
              f"el {pct(fp, datos).strip()} de los Data que reciben los clientes es contenido equivocado")

# ---------- Interests redirigidos: ¿recibe el usuario lo que pidió? ----------

print("\nInterests sin ruta que la estrategia por similitud reenvió")
for n, (recs, _) in resultados.items():
    red = [r for r in recs if r["redirigido"]]
    if not red:
        continue
    c = Counter(r["clase"] for r in red)
    total = len(red)
    print(f"  {n}: {total} reenviados")
    print(f"    {'recibe lo que pidió':<34}{c['acierto']:>7}  {pct(c['acierto'], total)}")
    print(f"    {'recibe otro contenido (FP)':<34}{c['falso positivo']:>7}  {pct(c['falso positivo'], total)}")
    for clase in ("NoRoute", "otro Nack", "sin respuesta", "Data"):
        if c[clase]:
            etiqueta = {"NoRoute": "no recibe nada: NoRoute más adelante",
                        "otro Nack": "no recibe nada: otro Nack",
                        "sin respuesta": "no recibe nada: sin respuesta",
                        "Data": "Data sin redirección (caché)"}[clase]
            print(f"    {etiqueta:<34}{c[clase]:>7}  {pct(c[clase], total)}")

    print("    por prefijo:")
    grupos = defaultdict(list)
    for r in red:
        grupos[r["prefijo"]].append(r)
    for prefijo, rs in sorted(grupos.items()):
        cp = Counter(r["clase"] for r in rs)
        print(f"      {prefijo:<48} {len(rs):>5} reenviados  lo pedido {pct(cp['acierto'], len(rs))}"
              f"  otro {pct(cp['falso positivo'], len(rs))}  nada {pct(len(rs) - cp['acierto'] - cp['falso positivo'], len(rs))}")

# ---------- Recorrido de los Interests redirigidos ----------

print("\nRecorrido de los Interests reenviados por similitud (¿alguno da vueltas?)")
for n, (recs, _) in resultados.items():
    red = [r for r in recs if r["redirigido"]]
    if not red:
        continue
    saltos = Counter(sum(r["visitas"].values()) for r in red)
    redirs = Counter(r["redirecciones"] for r in red)
    bucles = [r for r in red if max(r["visitas"].values()) > 1]
    duracion = max(r["ultimo"] - r["t"] for r in red)
    print(f"  {n}:")
    print(f"    nodos visitados (incluido el cliente): " +
          ", ".join(f"{k}: {v}" for k, v in sorted(saltos.items())))
    print(f"    redirecciones por Interest:           " +
          ", ".join(f"{k}: {v}" for k, v in sorted(redirs.items())))
    print(f"    pasan dos veces por el mismo nodo:    {len(bucles)}")
    print(f"    reciben Nack Duplicate (bucle cortado): {sum(r['duplicate'] > 0 for r in red)}")
    print(f"    sin respuesta (esperan al lifetime):  {sum(r['clase'] == 'sin respuesta' for r in red)}")
    print(f"    mayor tiempo en la red:               {duracion:.3f} s")

# ---------- Detalle por prefijo sin ruta ----------

nombres = list(resultados)
filas = []
for n, (recs, _) in resultados.items():
    grupos = defaultdict(list)
    for r in recs:
        if r["sin_ruta"]:
            grupos[r["prefijo"]].append(r)
    for prefijo, rs in grupos.items():
        c = Counter(r["clase"] for r in rs)
        destino = Counter(r["redirigido"] for r in rs if r["redirigido"]).most_common(1)
        filas.append({"iteracion": n, "prefijo": prefijo, "familia": rs[0]["familia"],
                      "interests": len(rs), **{k: c[k] for k in CLASES},
                      "redirigido_a": destino[0][0] if destino else "",
                      "intencion": " ".join(rs[0]["intencion"])})

os.makedirs(args.salida, exist_ok=True)
with open(os.path.join(args.salida, "comparacion.csv"), "w", newline="") as f:
    w = csv.DictWriter(f, list(filas[0]))
    w.writeheader()
    w.writerows(filas)

print("\nInterests sin ruta, por prefijo (NoRoute / acierto / falso positivo)")
por_prefijo = defaultdict(dict)
for fila in filas:
    por_prefijo[fila["prefijo"]][fila["iteracion"]] = fila
print(f"  {'prefijo':<48}" + "".join(f"{n:>24}" for n in nombres) + "   reenviado a")
for prefijo, it in sorted(por_prefijo.items(), key=lambda x: x[0]):
    celdas = []
    for n in nombres:
        fila = it.get(n)
        celdas.append(f"{fila['NoRoute']:>8}/{fila['acierto']:>6}/{fila['falso positivo']:>6}   "
                      if fila else f"{'-':>24}")
    destino = next((it[n]["redirigido_a"] for n in reversed(nombres) if n in it and it[n]["redirigido_a"]), "")
    print(f"  {prefijo:<48}" + "".join(celdas) + f"  {destino}")

print(f"\nDetalle en {args.salida}/comparacion.csv")
