"""Red NDN de un operador español con una plataforma de vídeo real (24 h).

    origen-vod (Dublín)   origen-yt (Fráncfort)   origen-rtve (Madrid)
              \\                  |                  /
                \\                |                /
                         ix-madrid (punto neutro)
                          /                \\
                   core-mad ─────────── core-bcn
                  /  |   |  \\          /    |
          core-bio  core-vlc  core-svq      |      núcleo en malla
                  \\_________________________/
         cada core ── 2 routers de agregación ── 1 barrio cada uno (hogares)

- Contenidos (video/catalogo.py): series, películas y documentales reales en /vod,
  RTVE Play en /rtve (con canales en directo) y vídeos de usuarios en /yt.
- Usuarios (video/humanos.py): ~600 personas repartidas por barrios según la
  población de cada ciudad, con su edad, dispositivo, horario y gustos; ven series,
  películas, YouTube, shorts y directos a lo largo del día.
- Reproductor (ndnutils/video-player.cpp): calidad adaptativa, búfer, paradas y
  reintentos, en tiempo de simulación.
- Escala: bitrates y anchos de banda a 1:1000 de los reales. Las proporciones
  (vídeo frente a capacidad) son las reales; los bytes absolutos no.

Salida (en proyecto/resultados/video/ o la carpeta indicada con --salida):
    sesiones.csv      lo que cada usuario quiere ver (con la causa de los errores
                      previstos y lo que quería de verdad)
    usuarios.csv      perfil de cada usuario
    qoe.csv           resultado de cada sesión en el reproductor (arranque, paradas, calidad)
    paquetes.csv      log de todos los nodos (ver ndnutils.packet_log)
    routers.csv       log de todos los routers (punto neutro, núcleo y agregación)
    routers/<R>.csv   el mismo log separado por router
    cs.txt            aciertos/fallos de caché por nodo y segundo
    escenario.json    topología, catálogo de aplicaciones y parámetros

Ejecutar (desde PrimerExperimento/):
    ./ndn.sh python3 proyecto/red_video.py
    ./ndn.sh python3 proyecto/red_video.py --horas 2 --escala 0.2   # prueba rápida
"""

import argparse
import csv
import json
import math
import os
import sys
from collections import Counter, defaultdict

from ns.core import *
from ns.network import *
from ns.point_to_point import *
from ns.ndnSIM import *

import logs
import ndnutils
from video import catalogo as cat
from video.humanos import Generador

DIR = os.path.dirname(os.path.abspath(__file__))

parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
parser.add_argument("--horas", type=float, default=24.0, help="horas simuladas desde las 00:00")
parser.add_argument("--escala", type=float, default=1.0, help="factor sobre el número de usuarios")
parser.add_argument("--seed", type=int, default=1)
parser.add_argument("--salida", default=os.path.join(DIR, "resultados", "video"))
parser.add_argument("--modelo", help="carpeta de modelos: activa el reenvío por similitud")
parser.add_argument("--umbral", type=float, default=0.9, help="similitud mínima para reenviar")
args, ns3_args = parser.parse_known_args()

cmd = CommandLine()
cmd.Parse([sys.argv[0]] + ns3_args)
RngSeedManager.SetRun(args.seed)
T = args.horas * 3600

# ---------- Topología ----------

CIUDADES = {"madrid": (40.4168, -3.7038), "barcelona": (41.3874, 2.1686),
            "valencia": (39.4699, -0.3763), "sevilla": (37.3891, -5.9845),
            "bilbao": (43.2630, -2.9350), "dublin": (53.3498, -6.2603),
            "frankfurt": (50.1109, 8.6821)}

# nodo: (rol, ciudad, tamaño de caché en paquetes)
NODOS = {
    "origen-vod": ("producer", "dublin", 1),
    "origen-yt": ("producer", "frankfurt", 1),
    "origen-rtve": ("producer", "madrid", 1),
    "ix-madrid": ("router", "madrid", 3000),
}
CORES = {"core-mad": "madrid", "core-bcn": "barcelona", "core-vlc": "valencia",
         "core-svq": "sevilla", "core-bio": "bilbao"}
# Usuarios por barrio (proporcional a la población de cada área urbana)
BARRIOS = {"barrio-mad-1": 80, "barrio-mad-2": 80, "barrio-bcn-1": 70, "barrio-bcn-2": 70,
           "barrio-vlc-1": 50, "barrio-vlc-2": 50, "barrio-svq-1": 50, "barrio-svq-2": 40,
           "barrio-bio-1": 45, "barrio-bio-2": 45}
RURAL = {"barrio-svq-2"}  # zona rural: salida por radioenlace, mucha menos capacidad

for core, ciudad in CORES.items():
    NODOS[core] = ("router", ciudad, 3000)
for barrio in BARRIOS:
    core = "core-" + barrio.split("-")[1]
    NODOS["agg-" + barrio[len("barrio-"):]] = ("router", CORES[core], 1500)
    NODOS[barrio] = ("consumer", CORES[core], 300)

# Anchos de banda a 1:1000 de los reales (100G troncal, 10G agregación, GPON 2,5G)
INTERNACIONAL, TRONCAL, AGREGACION, ACCESO, RURAL_BW = "100Mbps", "100Mbps", "10Mbps", "2500Kbps", "120Kbps"
KM_POR_MS = 100  # fibra con el doble de recorrido que la línea recta

ENLACES = [("origen-vod", "ix-madrid", INTERNACIONAL), ("origen-yt", "ix-madrid", INTERNACIONAL),
           ("origen-rtve", "ix-madrid", INTERNACIONAL),
           ("ix-madrid", "core-mad", TRONCAL), ("ix-madrid", "core-bcn", TRONCAL),
           ("core-mad", "core-bcn", TRONCAL), ("core-mad", "core-vlc", TRONCAL),
           ("core-mad", "core-svq", TRONCAL), ("core-mad", "core-bio", TRONCAL),
           ("core-bcn", "core-vlc", TRONCAL), ("core-vlc", "core-svq", TRONCAL),
           ("core-bio", "core-bcn", TRONCAL)]
for barrio in BARRIOS:
    agg = "agg-" + barrio[len("barrio-"):]
    ENLACES += [("core-" + barrio.split("-")[1], agg, AGREGACION),
                (agg, barrio, RURAL_BW if barrio in RURAL else ACCESO)]


def km(a, b):
    (la1, lo1), (la2, lo2) = [map(math.radians, CIUDADES[c]) for c in (a, b)]
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(h))


def retardo(a, b):
    ca, cb = NODOS[a][1], NODOS[b][1]
    if ca != cb:
        return f"{max(1, round(km(ca, cb) / KM_POR_MS))}ms"
    if a.startswith("barrio") or b.startswith("barrio"):
        return "2ms"  # última milla
    return "1ms"


ROUTERS = [n for n, (rol, *_) in NODOS.items() if rol == "router"]
CLIENTES = list(BARRIOS)

nodes = {}
for name in NODOS:
    nodes[name] = Node()
    Names.Add(name, nodes[name])

for a, b, bw in ENLACES:
    p2p = PointToPointHelper()
    p2p.SetDeviceAttribute("DataRate", StringValue(bw))
    p2p.SetDeviceAttribute("Mtu", UintegerValue(9000))
    p2p.SetChannelAttribute("Delay", StringValue(retardo(a, b)))
    p2p.SetQueue("ns3::DropTailQueue<Packet>", "MaxSize", StringValue("100p"))
    p2p.Install(nodes[a], nodes[b])

for name, (_, _, cs) in NODOS.items():
    stack = ndn.StackHelper()
    stack.setCsSize(cs)
    c = NodeContainer()
    c.Add(nodes[name])
    stack.Install(c)

# ---------- Reenvío y rutas ----------

if args.modelo:
    from ndnemb import Modelo, normalizar

    modelo = Modelo(args.modelo)
    cache_parecidos = {}

    def parecidos(nodo, nombre):
        clave = normalizar(nombre)
        if clave not in cache_parecidos:
            cache_parecidos[clave] = [(n, s) for n, s in modelo.parecidos(clave, k=5) if s >= args.umbral]
        return cache_parecidos[clave]

    ndnutils.similarity(parecidos)
    ndn.StrategyChoiceHelper.InstallAll("/", "/localhost/nfd/strategy/similarity")
else:
    ndn.StrategyChoiceHelper.InstallAll("/", "/localhost/nfd/strategy/best-route")

ORIGENES = {"origen-vod": "/vod", "origen-yt": "/yt", "origen-rtve": "/rtve"}
routing = ndn.GlobalRoutingHelper()
routing.InstallAll()
for node, prefix in ORIGENES.items():
    c = NodeContainer()
    c.Add(nodes[node])
    routing.AddOrigins(prefix, c)
routing.CalculateRoutes()  # camino más corto, como el IGP de un operador

for barrio in BARRIOS:  # ruta por defecto de los hogares hacia su router de agregación
    ndn.FibHelper.AddRoute(nodes[barrio], ndn.Name("/"), nodes["agg-" + barrio[len("barrio-"):]], 1)

# ---------- Servidores de origen ----------
# Una aplicación por temporada, película, canal en directo o canal de vídeos. Lo que
# no tiene aplicación (retirado, temporada inexistente, canal borrado) da NoRoute.

APLICACIONES = []  # (nodo, prefijo, directo, hora de inicio)
for t in cat.TITULOS:
    if t.retirado:
        continue
    origen = f"origen-{t.plataforma}"
    if t.temporadas:
        for temporada in range(1, len(t.temporadas) + 1):
            APLICACIONES.append((origen, t.prefijo(temporada), False, t.estreno.get(temporada, 0)))
    else:
        APLICACIONES.append((origen, t.prefijo(), False, 0))
for canal in cat.DIRECTOS:
    APLICACIONES.append(("origen-rtve", f"/rtve/directo/{canal}", True, 0))
for canal in cat.canales():
    if not canal.borrado:
        APLICACIONES.append(("origen-yt", f"/yt/{canal.slug}", False, 0))
APLICACIONES.append(("origen-yt", "/yt/shorts", False, 0))

for node, prefix, directo, hora in APLICACIONES:
    app = ndn.AppHelper("ns3::ndn::VideoProducer")
    app.SetPrefix(prefix)
    app.SetAttribute("Directo", BooleanValue(directo))
    app.Install(nodes[node]).Start(Seconds(hora * 3600))

# ---------- Usuarios ----------

out = args.salida
os.makedirs(out, exist_ok=True)
usuarios_por_barrio = {b: max(1, round(n * args.escala)) for b, n in BARRIOS.items()}
gen = Generador(usuarios_por_barrio, seed=args.seed, horas=args.horas)
gen.generar()
ruta_sesiones = gen.guardar(out)
print(gen.resumen())

for u in gen.usuarios:
    app = ndn.AppHelper("ns3::ndn::VideoPlayer")
    app.SetAttribute("Sesiones", StringValue(ruta_sesiones))
    app.SetAttribute("Usuario", StringValue(u["usuario"]))
    app.Install(nodes[u["barrio"]])

# ---------- Simulación ----------

ndnutils.packet_log(os.path.join(out, "paquetes.csv"))
ndnutils.qoe_log(os.path.join(out, "qoe.csv"))
ndnutils.cs(os.path.join(out, "cs.txt"), period=60.0)

print(f"Simulando {args.horas:g} h con {len(gen.usuarios)} usuarios y {len(APLICACIONES)} aplicaciones de origen")
Simulator.Stop(Seconds(T))
Simulator.Run()
ndnutils.close()
Simulator.Destroy()

# ---------- Post-proceso ----------

with open(os.path.join(out, "escenario.json"), "w") as f:
    json.dump({
        "nodos": {n: {"rol": r, "ciudad": c, "cs": cs} for n, (r, c, cs) in NODOS.items()},
        "enlaces": [{"source": a, "target": b, "DataRate": bw, "Delay": retardo(a, b)} for a, b, bw in ENLACES],
        "origenes": ORIGENES,
        "aplicaciones": [{"nodo": n, "prefijo": p, "directo": d, "inicio_h": h} for n, p, d, h in APLICACIONES],
        "params": {"horas": args.horas, "escala": args.escala, "seed": args.seed,
                   "modelo": args.modelo, "umbral": args.umbral if args.modelo else None},
    }, f, indent=2, ensure_ascii=False)

stats = defaultdict(Counter)
interests_por_hora = Counter()


def contar(r, col):
    node, d, tipo = r[col["node"]], r[col["dir"]], r[col["tipo"]]
    s = stats[node]
    s[(d, tipo, r[col["motivo"]])] += 1
    if d == "in" and tipo == "Interest" and r[col["src"]] == "app":
        interests_por_hora[int(float(r[col["time"]]) // 3600)] += 1


print("Reordenando el log y separando los routers...")
logs.reordenar(out, ROUTERS, contar)

filas = sum(sum(s.values()) for s in stats.values())
total = sum(interests_por_hora.values())
print(f"\n{filas} filas de log, {total} Interests de los reproductores")
print("Interests por hora: " + " ".join(f"{h}h:{interests_por_hora[h]}" for h in range(int(args.horas))))

print(f"\n{'router':<16}{'Int in':>9}{'CsHit':>9}{'hit %':>7}{'NoRoute':>9}")
for n in ROUTERS + list(ORIGENES):
    s = stats[n]
    hit, miss = s[("", "CsHit", "")], s[("", "CsMiss", "")]
    print(f"{n:<16}{s[('in', 'Interest', '')]:>9}{hit:>9}{100 * hit / max(hit + miss, 1):>6.1f}%"
          f"{s[('out', 'Nack', 'NoRoute')]:>9}")

# Calidad de experiencia por sesión, con la causa prevista de los errores
causas = {r["sesion"]: r["causa"] for r in csv.DictReader(open(ruta_sesiones))}
qoe = list(csv.DictReader(open(os.path.join(out, "qoe.csv"))))
video = [q for q in qoe if q["tipo"] != "miniatura"]
resultado = Counter(q["resultado"] for q in video)
print(f"\nSesiones de vídeo reproducidas: {len(video)}")
for k, v in resultado.most_common():
    print(f"  {k:<16}{v:>7}  {100 * v / len(video):5.1f}%")
nr = Counter(causas.get(q["sesion"], "") or "(sin causa prevista)" for q in video if q["resultado"] == "Nack:NoRoute")
print("NoRoute por causa: " + ", ".join(f"{k} {v}" for k, v in nr.most_common()))
ok = [q for q in video if q["resultado"] == "completa"]
if ok:
    media = lambda campo: sum(float(q[campo]) for q in ok) / len(ok)
    con_paradas = sum(int(q["paradas"]) > 0 for q in ok)
    print(f"Sesiones completas: arranque medio {media('arranque_s'):.2f} s, "
          f"{100 * con_paradas / len(ok):.1f} % con paradas, bitrate medio {media('bitrate_medio'):.0f} bit/s (escala 1:1000)")
print(f"\nResultados en {out}/")
