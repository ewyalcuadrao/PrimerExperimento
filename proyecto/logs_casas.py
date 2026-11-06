"""Logs de los Interest de cada casa sin simular la red, como capturas reales.

Genera los usuarios y sus sesiones con video/humanos.py (solo episodios, cada uno de
INSTANTE = 1 ms, así que se pueden generar muchos días), elige una casa por barrio
igual que red_video.py y reproduce lo que haría el reproductor de ndnutils
(video-player.cpp) con cada sesión:
    - pide <episodio>/manifest; si el nombre no tiene ruta recibe Nack NoRoute, lo
      reintenta una vez y la sesión falla; la siguiente con condicion=si_falla (la
      persona busca el nombre correcto) solo se ejecuta si la anterior falló;
    - si hay ruta, pide el único segmento, <episodio>/<calidad>/seg=0, con la calidad
      inicial del reproductor (480p, o menos si el dispositivo no llega).
Si un nombre tiene ruta se decide con los prefijos que anuncian los servidores en
red_video.py; la red no cambia nada más: con un solo segmento la calidad no depende
del caudal, y no hay pérdidas.

Formato, según las especificaciones de NDN (ver ndntlv.py): cada Interest es un TLV
del NDN Packet Format v0.3 (Name, Nonce, InterestLifetime), con los segmentos como
SegmentNameComponent (Naming Conventions rev3). Viaja suelto, sin LpPacket, porque
cabe en la trama y no lleva campos de enlace (NDNLPv2), en una trama Ethernet con el
ethertype de NDN (0x8624) de la casa a su router de acceso: NDN directamente sobre
Ethernet, sin IP ni UDP.

Salida, en la carpeta indicada:
    capturas/<barrio>.pcapng   lo que se capturaría en la tarjeta de red de la casa
    interests/<barrio>.csv     esas capturas decodificadas: time, src, dst (MAC),
                               tipo, nombre, bytes (del paquete NDN), nonce, lifetime_ms
    etiquetas.csv              qué pasó con cada Interest (Data o Nack NoRoute) y de qué
                               sesión viene: no está en la captura, sirve para evaluar
    sesiones.csv, usuarios.csv del generador

Ejecutar (desde PrimerExperimento/; solo usa la biblioteca estándar):
    python3 proyecto/logs_casas.py --salida proyecto/resultados/logs_v1
"""

import argparse
import ast
import csv
import os
import random
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(DIR, "video"))
sys.path.insert(0, DIR)
import catalogo as cat  # noqa: E402
import ndntlv  # noqa: E402
from humanos import INSTANTE, Generador  # noqa: E402

parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
parser.add_argument("--dias", type=int, default=90)
parser.add_argument("--seed", type=int, default=3)
parser.add_argument("--inicio", default="2026-01-05", help="fecha del primer día (hora de Madrid, 00:00)")
parser.add_argument("--salida", default=os.path.join(DIR, "resultados", "logs"))
args = parser.parse_args()

CALIDADES = ["240p", "480p", "720p", "1080p", "2160p"]
CALIDAD_INICIAL = 1   # 480p, como CALIDAD_INICIAL en video-player.cpp
LIFETIME_MS = 2000    # LIFETIME de video-player.cpp

# Los barrios y su población salen de red_video.py (no se importa: arranca ns-3)
with open(os.path.join(DIR, "red_video.py"), encoding="utf-8") as f:
    arbol = ast.parse(f.read())
BARRIOS = next(ast.literal_eval(n.value) for n in arbol.body
               if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "BARRIOS")

# Prefijos con ruta: los que anuncian los servidores de red_video.py
prefijos = set()
for t in cat.TITULOS:
    for temporada in range(1, len(t.temporadas) + 1) if t.temporadas else [None]:
        prefijos.add(tuple(t.prefijo(temporada).strip("/").split("/")))
for c in cat.DIRECTOS:
    prefijos.add(("rtve", "directo", c))
for canal in cat.canales():
    prefijos.update(("yt", canal.slug, v) for v in canal.videos)
prefijos.update(("yt", "shorts", v) for v in cat.SHORTS)


def tiene_ruta(uri):
    comps = tuple(uri.strip("/").split("/"))
    return any(comps[:k] in prefijos for k in range(2, len(comps) + 1))


# ---------- Usuarios y sesiones ----------
gen = Generador(BARRIOS, seed=args.seed, horas=24 * args.dias, solo_episodios=True, instantaneo=True)
gen.generar()
gen.una_casa_por_barrio()
os.makedirs(os.path.join(args.salida, "capturas"), exist_ok=True)
os.makedirs(os.path.join(args.salida, "interests"), exist_ok=True)
gen.guardar(args.salida)
print(gen.resumen())

madrid = timezone(timedelta(hours=1))
t0 = datetime.fromisoformat(args.inicio).replace(tzinfo=madrid).timestamp()
rng = random.Random(args.seed)
casa = {u["usuario"]: u["barrio"] for u in gen.usuarios}
indice = {b: i + 1 for i, b in enumerate(BARRIOS)}

# ---------- Lo que hace el reproductor de cada casa ----------
por_casa = defaultdict(list)
for s in gen.filas:
    por_casa[s["usuario"]].append(s)

etiquetas = []
resultado_sesion = Counter()
for usuario, sesiones in por_casa.items():
    barrio = casa[usuario]
    i = indice[barrio]
    # Direcciones MAC administradas localmente: casa ...:02 y su router de acceso ...:01
    mac_casa, mac_router = [2, 0, 0, 0, i, 2], [2, 0, 0, 0, i, 1]
    captura = ndntlv.Pcapng(os.path.join(args.salida, "capturas", f"{barrio}.pcapng"))
    reloj, fallo_anterior = 0.0, False

    def enviar(t, uri, s, resultado):
        nonce = rng.getrandbits(32)
        captura.escribir(t0 + t, ndntlv.trama_ethernet(mac_casa, mac_router, ndntlv.interest(uri, nonce, LIFETIME_MS)))
        etiquetas.append({"maquina": barrio, "usuario": usuario, "time": f"{t0 + t:.6f}", "nonce": f"{nonce:08x}",
                          "nombre": uri, "resultado": resultado, "sesion": s["sesion"], "causa": s["causa"],
                          "intencion": s["intencion"]})

    for s in sesiones:
        inicio = float(s["inicio"])
        reloj = (inicio if inicio >= 0 else reloj) + float(s["pausa"])
        if s["condicion"] == "si_falla" and not fallo_anterior:
            continue  # la anterior funcionó: no hace falta buscar el nombre correcto
        manifiesto = s["base"] + "/manifest"
        if not tiene_ruta(manifiesto):
            enviar(reloj, manifiesto, s, "Nack:NoRoute")
            enviar(reloj + INSTANTE / 2, manifiesto, s, "Nack:NoRoute")  # "reintentar"
            fallo_anterior = True
            resultado_sesion["Nack:NoRoute"] += 1
        else:
            calidad = min(CALIDAD_INICIAL, CALIDADES.index(s["calidad_max"]))
            enviar(reloj, manifiesto, s, "Data")
            enviar(reloj + INSTANTE / 2, f"{s['base']}/{CALIDADES[calidad]}/seg=0", s, "Data")
            fallo_anterior = False
            resultado_sesion["completa"] += 1
        reloj += INSTANTE
    captura.close()

with open(os.path.join(args.salida, "etiquetas.csv"), "w", newline="") as f:
    w = csv.DictWriter(f, list(etiquetas[0]))
    w.writeheader()
    w.writerows(etiquetas)

# ---------- Decodificar las capturas, como se haría con una traza real ----------
total = 0
for barrio in BARRIOS:
    ruta = os.path.join(args.salida, "capturas", f"{barrio}.pcapng")
    if not os.path.exists(ruta):
        continue
    with open(os.path.join(args.salida, "interests", f"{barrio}.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["time", "src", "dst", "tipo", "nombre", "bytes", "nonce", "lifetime_ms"])
        for t, trama in ndntlv.leer_pcapng(ruta):
            mac = lambda b: ":".join(f"{x:02x}" for x in b)
            assert int.from_bytes(trama[12:14], "big") == ndntlv.ETHERTYPE_NDN
            p = ndntlv.leer_interest(trama[14:])
            w.writerow([f"{t:.6f}", mac(trama[6:12]), mac(trama[0:6]), "Interest",
                        p["nombre"], p["bytes"], p["nonce"], p["lifetime_ms"]])
            total += 1

sesiones = sum(resultado_sesion.values())
print(f"\n{args.dias} días desde el {args.inicio}: {total} Interests en {len(gen.usuarios)} casas")
print(f"Sesiones reproducidas: {sesiones}; " + ", ".join(
    f"{k} {v} ({100 * v / sesiones:.1f} %)" for k, v in resultado_sesion.most_common()))
print("Interests por casa:", dict(Counter(e["maquina"] for e in etiquetas)))
print(f"Resultados en {args.salida}")
