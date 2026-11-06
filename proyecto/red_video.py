"""Red NDN de un operador español con plataformas de vídeo reales (3 días).

  vod-series vod-cine vod-docs   yt-canales-a yt-canales-b yt-shorts   rtve-play rtve-directo
         \\     |     /                 \\      |      /                    \\    /
         vod-borde (Dublín)            yt-borde (Fráncfort)            rtve-borde (Madrid)
                  \\                    /             \\                   /
                   ix-madrid (Espanix) ──────────────── ix-barcelona (Catnix)
                          |                                   /      \\
                       core-mad ──────────────────────── core-bcn   core-vlc
                  núcleo en malla: core-mad, core-bcn, core-vlc, core-svq, core-bio
         cada core ── 2 routers de agregación ── 1 casa cada uno (nodos barrio-*)

- Cada plataforma tiene un router de borde y servidores separados por sección del
  catálogo; cada servidor anuncia un prefijo por temporada, película o canal.
- Rutas: el núcleo, los puntos neutros y los bordes conocen cada prefijo (camino más
  corto, como el IGP/BGP de un operador). Las agregaciones y los hogares solo tienen
  una ruta por defecto hacia arriba. Un nombre que no es de ningún contenido llega al
  núcleo de la ciudad y allí no hay ruta: Nack NoRoute (o redirección, con --modelo).

- Contenidos (video/catalogo.py): series, películas y documentales reales en /vod,
  RTVE Play en /rtve (con canales en directo) y vídeos de usuarios en /yt.
- Usuarios (video/humanos.py): se generan ~600 personas repartidas por barrios según
  la población de cada ciudad, con su edad, dispositivo, horario y gustos, y cada
  nodo barrio-* es la casa de una de ellas: la que ve más títulos distintos. Así hay
  10 consumidores, cada uno con su log, como las máquinas con las que se entrenará
  el modelo.
- Reproductor (ndnutils/video-player.cpp): como un cliente DASH, pide el manifiesto
  y luego los segmentos de 4 s, eligiendo la calidad de cada uno según el caudal
  medido (240p a 2160p). Reintenta tras un Nack y si el Interest caduca.
- Escala: anchos de banda y tamaños de los Data a 1:1000 de los reales
  (ndnutils/video-common.hpp), así que el vídeo sí carga la red.

Con --modelo, los routers usan la estrategia de similitud en modo redirección: si no
hay ruta, contestan con un Data de tipo Link al nombre válido más parecido (con
FastText, item2vec o el coseno de trigramas, --similitud) si llega al --umbral, y el
reproductor pide ese contenido.

Salida (en proyecto/resultados/video/ o la carpeta indicada con --salida):
    sesiones.csv      lo que cada usuario quiere ver (con la causa de los errores
                      previstos y lo que quería de verdad)
    usuarios.csv      perfil de cada usuario
    qoe.csv           resultado de cada sesión en el reproductor (arranque, redirección)
    paquetes.csv      log de todos los nodos (ver ndnutils.packet_log)
    routers.csv       log de todos los routers (punto neutro, núcleo y agregación)
    routers/<R>.csv   el mismo log separado por router
    cs.txt            aciertos/fallos de caché por nodo y segundo
    escenario.json    topología, catálogo de aplicaciones y parámetros

Ejecutar (desde PrimerExperimento/):
    ./ndn.sh python3 proyecto/red_video.py
    ./ndn.sh python3 proyecto/red_video.py --horas 2 --escala 0.2   # prueba rápida
    ./ndn.sh python3 proyecto/red_video.py --modelo proyecto/modelos/video --umbral 0.85 \\
        --salida /work/proyecto/resultados/video_ft085
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
parser.add_argument("--horas", type=float, default=72.0, help="horas simuladas desde las 00:00 (3 días)")
parser.add_argument("--escala", type=float, default=1.0, help="factor sobre el número de usuarios")
parser.add_argument("--seed", type=int, default=3)  # con la 3, ~20 % de NoRoute en las casas
parser.add_argument("--salida", default=os.path.join(DIR, "resultados", "video"))
parser.add_argument("--modelo", help="carpeta de modelos: activa el reenvío por similitud")
parser.add_argument("--umbral", type=float, default=0.9, help="similitud mínima para redirigir")
parser.add_argument("--peso-comunes", type=float, default=0.0,
                    help="con FastText, peso de las componentes que comparten los dos nombres (0 = no cuentan)")
parser.add_argument("--similitud", choices=["fasttext", "coseno", "item2vec"], default="fasttext",
                    help="medida de similitud entre nombres (ver ndnemb.py)")
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

# Servidores de cada plataforma: nodo -> (ciudad, qué sirve). La sección se decide con
# servidor_de(prefijo).
SERVIDORES = {
    "vod-series": "dublin", "vod-cine": "dublin", "vod-docs": "dublin",
    "yt-canales-a": "frankfurt", "yt-canales-b": "frankfurt", "yt-shorts": "frankfurt",
    "rtve-play": "madrid", "rtve-directo": "madrid",
}
BORDES = {"vod-borde": "dublin", "yt-borde": "frankfurt", "rtve-borde": "madrid"}
YT_CANALES_A = {"entretenimiento", "musica", "humor", "cine", "gaming"}  # el resto, en yt-canales-b


def servidor_de(prefijo, tematica=None):
    plataforma, seccion = prefijo.split("/")[1:3]
    if plataforma == "vod":
        return {"serie": "vod-series", "peli": "vod-cine", "doc": "vod-docs"}[seccion]
    if plataforma == "rtve":
        return "rtve-directo" if seccion == "directo" else "rtve-play"
    if seccion == "shorts":
        return "yt-shorts"
    return "yt-canales-a" if tematica in YT_CANALES_A else "yt-canales-b"


# nodo: (rol, ciudad, tamaño de caché en paquetes)
NODOS = {s: ("producer", c, 1) for s, c in SERVIDORES.items()}
NODOS.update({b: ("router", c, 1500) for b, c in BORDES.items()})
NODOS.update({"ix-madrid": ("router", "madrid", 3000), "ix-barcelona": ("router", "barcelona", 3000)})
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
    NODOS[barrio] = ("consumer", CORES[core], 100)  # la casa: caché del dispositivo

# Anchos de banda a 1:1000 de los reales (100G troncal, 10G agregación, fibra de 1G
# en cada casa; en la zona rural, 100M por radioenlace)
INTERNACIONAL, TRONCAL, AGREGACION, ACCESO, RURAL_BW = "100Mbps", "100Mbps", "10Mbps", "1Mbps", "100Kbps"
KM_POR_MS = 100  # fibra con el doble de recorrido que la línea recta

ENLACES = [(s, s.split("-")[0] + "-borde", INTERNACIONAL) for s in SERVIDORES]  # centro de datos
ENLACES += [("vod-borde", "ix-madrid", INTERNACIONAL), ("yt-borde", "ix-madrid", INTERNACIONAL),
            ("yt-borde", "ix-barcelona", INTERNACIONAL), ("rtve-borde", "ix-madrid", INTERNACIONAL),
            ("ix-madrid", "ix-barcelona", TRONCAL),
            ("ix-madrid", "core-mad", TRONCAL), ("ix-barcelona", "core-bcn", TRONCAL),
            ("ix-barcelona", "core-vlc", TRONCAL), ("core-mad", "core-bcn", TRONCAL), ("core-mad", "core-vlc", TRONCAL),
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

# ---------- Servidores ----------
# Una aplicación por temporada, película, canal en directo o canal de vídeos, en el
# servidor de su sección. Un nombre que no corresponde a ninguna (mal escrito,
# plataforma equivocada...) no tiene ruta en el núcleo y da NoRoute. Los servidores de
# /yt anuncian, bajo el prefijo del canal, solo los títulos de sus vídeos.

APLICACIONES = []  # (nodo, prefijo, directo, hora de inicio, nombres bajo el prefijo)
for t in cat.TITULOS:
    servidor = servidor_de(t.prefijo())
    if t.temporadas:
        for temporada in range(1, len(t.temporadas) + 1):
            APLICACIONES.append((servidor, t.prefijo(temporada), False, t.estreno.get(temporada, 0), ""))
    else:
        APLICACIONES.append((servidor, t.prefijo(), False, 0, ""))
for canal in cat.DIRECTOS:
    APLICACIONES.append(("rtve-directo", f"/rtve/directo/{canal}", True, 0, ""))
for canal in cat.canales():
    APLICACIONES.append((servidor_de(f"/yt/{canal.slug}", canal.tematica), f"/yt/{canal.slug}", False, 0,
                         " ".join(canal.videos)))
APLICACIONES.append(("yt-shorts", "/yt/shorts", False, 0, " ".join(cat.SHORTS)))

# ---------- Reenvío y rutas ----------

if args.modelo:
    from ndnemb import Modelo, normalizar

    modelo = Modelo(args.modelo, args.similitud, args.peso_comunes)
    cache_parecidos = {}

    def parecidos(nodo, nombre):
        # Una miniatura se compara con miniaturas y un vídeo con vídeos
        clave = normalizar(nombre)
        if clave not in cache_parecidos:
            miniatura = clave.rsplit("/", 1)[-1] == "miniatura"
            cache_parecidos[clave] = [(n, s) for n, s in modelo.parecidos(clave, k=5, miniaturas=miniatura)
                                      if s >= args.umbral]
        return cache_parecidos[clave]

    ndnutils.similarity(parecidos, redirigir=True)
    ndn.StrategyChoiceHelper.InstallAll("/", "/localhost/nfd/strategy/similarity")
else:
    ndn.StrategyChoiceHelper.InstallAll("/", "/localhost/nfd/strategy/best-route")

routing = ndn.GlobalRoutingHelper()
routing.InstallAll()
for node, prefix, _, _, _ in APLICACIONES:
    c = NodeContainer()
    c.Add(nodes[node])
    routing.AddOrigins(prefix, c)
routing.CalculateRoutes()  # camino más corto, como el IGP de un operador

# Agregaciones y hogares: solo una ruta por defecto hacia arriba
for barrio in BARRIOS:
    agg = "agg-" + barrio[len("barrio-"):]
    ndnutils.ruta_por_defecto(agg, "core-" + barrio.split("-")[1])
    ndnutils.ruta_por_defecto(barrio, agg)

for node, prefix, directo, hora, nombres in APLICACIONES:
    app = ndn.AppHelper("ns3::ndn::VideoProducer")
    app.SetPrefix(prefix)
    app.SetAttribute("Directo", BooleanValue(directo))
    app.SetAttribute("Nombres", StringValue(nombres))
    app.Install(nodes[node]).Start(Seconds(hora * 3600))

# ---------- Usuarios ----------

out = args.salida
os.makedirs(out, exist_ok=True)
usuarios_por_barrio = {b: max(1, round(n * args.escala)) for b, n in BARRIOS.items()}
gen = Generador(usuarios_por_barrio, seed=args.seed, horas=args.horas, solo_episodios=True)
gen.generar()

# Una casa por barrio, que es la máquina de un solo usuario (ver una_casa_por_barrio)
gen.una_casa_por_barrio()
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
        "servidores": {s: sorted({p for n, p, _, _, _ in APLICACIONES if n == s}) for s in SERVIDORES},
        "aplicaciones": [{"nodo": n, "prefijo": p, "directo": d, "inicio_h": h} for n, p, d, h, _ in APLICACIONES],
        "params": {"horas": args.horas, "escala": args.escala, "seed": args.seed,
                   "modelo": args.modelo, "umbral": args.umbral if args.modelo else None,
                   "similitud": args.similitud if args.modelo else None,
                   "peso_comunes": args.peso_comunes if args.modelo else None},
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
for n in ROUTERS + list(SERVIDORES):
    s = stats[n]
    hit, miss = s[("", "CsHit", "")], s[("", "CsMiss", "")]
    print(f"{n:<16}{s[('in', 'Interest', '')]:>9}{hit:>9}{100 * hit / max(hit + miss, 1):>6.1f}%"
          f"{s[('out', 'Nack', 'NoRoute')]:>9}")

# Calidad de experiencia por sesión, con la causa prevista de los errores
sesiones = {r["sesion"]: r for r in csv.DictReader(open(ruta_sesiones))}
causas = {k: r["causa"] for k, r in sesiones.items()}
qoe = list(csv.DictReader(open(os.path.join(out, "qoe.csv"))))
video = [q for q in qoe if q["tipo"] != "miniatura"]
resultado = Counter(q["resultado"] for q in video)
print(f"\nSesiones de vídeo reproducidas: {len(video)}")
for k, v in resultado.most_common():
    print(f"  {k:<16}{v:>7}  {100 * v / len(video):5.1f}%")
nr = Counter(causas.get(q["sesion"], "") or "(sin causa prevista)" for q in video if q["resultado"] == "Nack:NoRoute")
print("NoRoute por causa: " + ", ".join(f"{k} {v}" for k, v in nr.most_common()))
redirigidas = [q for q in video if q["redirigido_a"]]
if redirigidas:
    aciertos = sum(q["redirigido_a"] == sesiones[q["sesion"]]["intencion"] for q in redirigidas)
    print(f"Redirigidas: {len(redirigidas)}, al contenido que quería el usuario {aciertos} "
          f"({100 * aciertos / len(redirigidas):.1f} %), a otro {len(redirigidas) - aciertos}")
ok = [q for q in video if q["resultado"] == "completa"]
if ok:
    print(f"Sesiones completas: arranque medio {sum(float(q['arranque_s']) for q in ok) / len(ok):.3f} s")
    vod = [q for q in ok if int(q["segmentos"])]
    if vod:
        print(f"  con paradas {sum(int(q['paradas']) > 0 for q in vod)} de {len(vod)}, "
              f"{sum(float(q['parado_s']) for q in vod):.0f} s parados en total, "
              f"calidad media {sum(float(q['kbps_medio']) for q in vod) / len(vod):.0f} kb/s")
print(f"\nResultados en {out}/")
