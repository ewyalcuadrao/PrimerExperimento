"""Red NDN de 13 nodos inspirada en el testbed NDN real.

Topología (proyecto/topologias/testbed.json, generada con topologia_testbed.py):
5 hubs reales del testbed con sus enlaces reales, 3 clientes y 5 productores.

              ucla-media                       wu-server
                  │                               │
    cli-ucla ── UCLA ──────────26ms──────────── WU ────────┐
                  │ 7ms                       ╱  │ 4ms      │
  az-weather ── ARIZONA ─────20ms────────────╱   │          │ 79ms
                  │  ╲                           │          │
           cli-arizona ╲──────20ms────────── MEMPHIS ── yoursunny-iah
                                                 │ 76ms     │
                                                UFBA ───────┘
                                               ╱    ╲
                                         cli-ufba   iot-gw

El catálogo de contenidos (CATALOGO) mezcla servicios reales del testbed (ping,
NLSR, certificados, weekly-call-doc, autoconf) con servicios inventados con
nombres al estilo del testbed. Cada familia tiene un patrón de acceso propio:

    unico       nombres que no se repiten (ping, LSA): la caché no ayuda
    zipf        catálogo con popularidad Zipf: unos pocos contenidos muy pedidos
    secuencial  sesiones que recorren segmentos 0..N (vídeo, descargas): quien
                llega después encuentra los segmentos en caché
    directo     todos los clientes piden la misma lectura a la vez: agregación PIT

Hay temas parecidos con nombres distintos (clases de UCLA y grabaciones de WU;
sensores de UFBA y meteorología de Arizona; el mismo paquete de software en el
repositorio de Memphis y en la réplica de yoursunny) y nombres casi correctos que
no existen (erratas, singular/plural, cursos inexistentes), que acaban en
Nack NoRoute en el nodo que se queda sin ruta.

Salida (en proyecto/resultados/testbed/ o la carpeta indicada con --salida):
    paquetes.csv      log de todos los nodos (ver ndnutils.packet_log)
    routers.csv       el log de todos los hubs (routers) en un solo fichero
    routers/<HUB>.csv el mismo log separado por hub
    escenario.json    topología, catálogo, tráfico y parámetros de la ejecución
    app-delays.txt    retardo de cada Interest en los consumidores
    cs.txt            aciertos/fallos de caché por nodo y segundo

Ejecutar (desde PrimerExperimento/):
    ./ndn.sh python3 proyecto/red_testbed.py
    ./ndn.sh python3 proyecto/red_testbed.py --tiempo 1800 --seed 3
"""

import argparse
import csv
import json
import os
import random
import sys
from collections import Counter, defaultdict

from ns.core import *
from ns.network import *
from ns.point_to_point import *
from ns.ndnSIM import *

import ndnutils

DIR = os.path.dirname(os.path.abspath(__file__))
TOPOLOGIA = os.path.join(DIR, "topologias", "testbed.json")

# ---------- Argumentos ----------

parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
parser.add_argument("--tiempo", type=float, default=600.0, help="segundos simulados")
parser.add_argument("--seed", type=int, default=1, help="ejecución del generador aleatorio")
parser.add_argument("--salida", default=os.path.join(DIR, "resultados", "testbed"))
parser.add_argument("--vis", action="store_true", help="abrir el visualizador")
parser.add_argument("--modelo", help="carpeta de embeddings (proyecto/modelos/...): activa el "
                    "reenvío por similitud cuando no hay ruta en la FIB")
parser.add_argument("--umbral", type=float, default=0.9, help="similitud mínima para reenviar")
args, ns3_args = parser.parse_known_args()
if args.vis:
    ns3_args.append("--SimulatorImplementationType=ns3::VisualSimulatorImpl")

cmd = CommandLine()
cmd.Parse([sys.argv[0]] + ns3_args)
RngSeedManager.SetRun(args.seed)
random.seed(args.seed)
T = args.tiempo

# ---------- Parámetros ----------

CS_HUB = 150            # tamaño de la caché de cada hub (paquetes)
LIFETIME = "4s"         # InterestLifetime habitual en el testbed
MTU = 9000              # el testbed usa UDP con paquetes NDN de hasta ~8800 B
IOT_CAIDA = 2 / 3       # fracción de la simulación tras la que se cae la pasarela IoT

# ---------- Topología ----------

topo = json.load(open(TOPOLOGIA))
info = {n["id"]: n for n in topo["nodes"]}
HUBS = [n for n, i in info.items() if i["rol"] == "router"]
CLIENTES = [n for n, i in info.items() if i["rol"] == "consumer"]
PRODUCTORES = [n for n, i in info.items() if i["rol"] == "producer"]
VECINOS = [(l["source"], l["target"]) for l in topo["links"]
           if l["source"] in HUBS and l["target"] in HUBS]


def sitio(hub):
    return info[hub]["prefijo"]


def lsa(hub):
    """Prefijo NLSR de las LSA de un hub: /localhop/ndn/nlsr/LSA/<sitio sin /ndn>/%C1.Router/<router>"""
    router = {"UCLA": "suns"}.get(hub, hub.lower())
    return f"/localhop/ndn/nlsr/LSA{sitio(hub)[len('/ndn'):]}/%C1.Router/{router}"


# ---------- Catálogo de contenidos ----------
#
# Producción: (nodo, prefijo de la aplicación, familia, bytes de contenido)
# Orígenes: prefijos que cada nodo anuncia en el enrutamiento (como NLSR).
# Un nodo no tiene ruta hacia lo que anuncia salvo lo que sirven sus
# aplicaciones: un nombre bajo su prefijo que nadie sirve recibe NoRoute ahí.

PRODUCCION = []
ORIGENES = defaultdict(list)
for hub in HUBS:
    PRODUCCION += [(hub, sitio(hub) + "/ping", "ping", 64),
                   (hub, sitio(hub) + "/KEY", "certificados", 400),
                   (hub, lsa(hub), "NLSR", 1100)]
    ORIGENES[hub].append(sitio(hub))

SOFTWARE = "ndn-cxx-0.8.1.tar.gz"
PRODUCCION += [
    ("MEMPHIS", f"/ndn/edu/memphis/repo/{SOFTWARE}", "software", 4096),
    ("wu-server", "/ndn/weekly-call-doc", "documento", 1024),
    ("wu-server", "/ndn/edu/wustl/courses/recordings", "vídeo bajo demanda", 4000),
    ("yoursunny-iah", "/yoursunny/_/iah/ping", "ping", 64),
    ("yoursunny-iah", f"/yoursunny/_/iah/mirror/{SOFTWARE}", "software", 4096),
    ("ucla-media", "/ndn/edu/ucla/lectures/cs118", "vídeo en directo", 4000),
    ("iot-gw", "/ndn/br/ufba/iot/sensors", "sensores", 32),
    ("az-weather", "/ndn/edu/arizona/weather/tucson", "meteorología", 48),
]
ORIGENES["wu-server"] += ["/ndn/weekly-call-doc", "/ndn/edu/wustl/courses"]
ORIGENES["yoursunny-iah"] += ["/yoursunny/_/iah"]
ORIGENES["ucla-media"] += ["/ndn/edu/ucla/lectures"]
ORIGENES["iot-gw"] += ["/ndn/br/ufba/iot"]
ORIGENES["az-weather"] += ["/ndn/edu/arizona/weather"]

# Productores que dejan de funcionar: (nodo, prefijo, instante de parada)
CAIDAS = [("iot-gw", "/ndn/br/ufba/iot/sensors", T * IOT_CAIDA)]

# Peticiones de cada cliente:
# (familia, prefijo, Interests/s, patrón, parámetros del patrón, resultado esperado)
#   zipf: (contenidos, s)   secuencial: (segmentos, sesiones)   unico/directo: None


def trafico_cliente(cli):
    hub = info[cli]["hub"]
    t = [("ping", sitio(h) + "/ping", 1.0, "unico", None, "Data") for h in HUBS]
    t += [
        ("ping", "/yoursunny/_/iah/ping", 1.0, "unico", None, "Data"),
        ("certificados", sitio(hub) + "/KEY", 1.0, "zipf", (20, 0.8), "Data"),
        ("certificados", "/ndn/edu/wustl/KEY", 1.0, "zipf", (20, 0.8), "Data"),
        ("documento", "/ndn/weekly-call-doc", 6.0, "zipf", (200, 0.8), "Data"),
        ("vídeo bajo demanda", "/ndn/edu/wustl/courses/recordings", 4.0, "zipf", (300, 1.0), "Data"),
        ("vídeo en directo", "/ndn/edu/ucla/lectures/cs118", 10.0, "secuencial", (600, 3), "Data"),
        ("sensores", "/ndn/br/ufba/iot/sensors", 4.0, "zipf", (30, 1.2), "Data, NoRoute tras la caída"),
        ("meteorología", "/ndn/edu/arizona/weather/tucson", 2.0, "directo", None, "Data"),
        ("software", f"/ndn/edu/memphis/repo/{SOFTWARE}", 30.0, "secuencial", (300, 2), "Data"),
        ("software", f"/yoursunny/_/iah/mirror/{SOFTWARE}", 30.0, "secuencial", (300, 1), "Data"),
        # Nombres sin ruta, como en el testbed
        ("autoconf", "/localhop/ndn-autoconf/hub", 0.3, "unico", None, "NoRoute"),
        ("ping caído", "/ndn/ch/unibe/ping", 0.3, "unico", None, "NoRoute"),
        ("ping caído", "/ndn/es/udc/ping", 0.3, "unico", None, "NoRoute"),
        ("ping caído", "/ndn/uk/ac/qub/ping", 0.3, "unico", None, "NoRoute"),
        ("ping caído", "/ndn/my/edu/uum/ping", 0.3, "unico", None, "NoRoute"),
        ("ping caído", "/ndn/ma/univh2c/ping", 0.3, "unico", None, "NoRoute"),
        ("ping caído", "/yoursunny/_/bom/ping", 0.3, "unico", None, "NoRoute"),
        ("certificado sin productor", "/ndn/br/ufba/lasid/maria/KEY", 0.2, "unico", None, "NoRoute"),
        # Nombres casi correctos que no existen
        ("errata", "/ndn/edu/ucla/lecture/cs118", 0.3, "unico", None, "NoRoute"),
        ("errata", "/ndn/edu/ucla/lectures/cs188", 0.3, "unico", None, "NoRoute"),
        ("errata", "/ndn/br/ufba/iot/sensor", 0.3, "unico", None, "NoRoute"),
        ("errata", "/ndn/edu/arizona/wheather/tucson", 0.3, "unico", None, "NoRoute"),
        ("errata", f"/ndn/edu/memphis/repository/{SOFTWARE}", 0.2, "unico", None, "NoRoute"),
        ("errata", "/ndn/edu/wustl/course/recordings", 0.2, "unico", None, "NoRoute"),
    ]
    if hub != "UFBA":  # usuarios de UCLA que piden su certificado desde UCLA y Arizona
        t.append(("certificado sin productor", "/ndn/edu/ucla/cs/tianyuan/KEY", 0.2, "unico",
                  None, "NoRoute"))
    return t


TRAFICO = [(cli, *t) for cli in CLIENTES for t in trafico_cliente(cli)]

# Qué contenido quería de verdad quien pide un nombre sin ruta. Si el reenvío por
# similitud entrega Data de uno de estos nombres es un acierto; si entrega Data de
# otro (o el nombre no tiene intención), es un falso positivo. Los nombres sin ruta
# que no aparecen aquí no tienen ningún contenido correcto en la red.
INTENCION = {
    "/ndn/edu/ucla/lecture/cs118": ["/ndn/edu/ucla/lectures/cs118"],
    "/ndn/br/ufba/iot/sensor": ["/ndn/br/ufba/iot/sensors"],
    "/ndn/edu/arizona/wheather/tucson": ["/ndn/edu/arizona/weather/tucson"],
    f"/ndn/edu/memphis/repository/{SOFTWARE}": [f"/ndn/edu/memphis/repo/{SOFTWARE}",
                                                f"/yoursunny/_/iah/mirror/{SOFTWARE}"],
    "/ndn/edu/wustl/course/recordings": ["/ndn/edu/wustl/courses/recordings"],
    # Tras la caída de iot-gw los sensores no están en ningún otro sitio
    "/ndn/br/ufba/iot/sensors": ["/ndn/br/ufba/iot/sensors"],
}
TRAFICO += [(a, "NLSR", lsa(b), 0.5, "unico", None, "Data") for a, b in VECINOS]
TRAFICO += [(b, "NLSR", lsa(a), 0.5, "unico", None, "Data") for a, b in VECINOS]

# ---------- Red ----------

nodes = {}
for name in info:
    nodes[name] = Node()
    Names.Add(name, nodes[name])

for link in topo["links"]:
    p2p = PointToPointHelper()
    p2p.SetDeviceAttribute("DataRate", StringValue(link["DataRate"]))
    p2p.SetDeviceAttribute("Mtu", UintegerValue(MTU))
    p2p.SetChannelAttribute("Delay", StringValue(link["Delay"]))
    p2p.Install(nodes[link["source"]], nodes[link["target"]])


def container(names):
    c = NodeContainer()
    for n in names:
        c.Add(nodes[n])
    return c


stack = ndn.StackHelper()
stack.setCsSize(CS_HUB)
stack.Install(container(HUBS))

stack = ndn.StackHelper()
stack.setCsSize(1)  # los extremos prácticamente no cachean
stack.Install(container(CLIENTES + PRODUCTORES))

if args.modelo:
    # Segunda iteración: si no hay ruta, reenviar hacia el nombre conocido más parecido
    from ndnemb import Modelo, normalizar

    modelo = Modelo(args.modelo)
    cache_parecidos = {}

    def parecidos(nodo, nombre):
        clave = normalizar(nombre)
        if clave not in cache_parecidos:
            cache_parecidos[clave] = [(n, s) for n, s in modelo.parecidos(clave, k=5)
                                      if s >= args.umbral]
        return cache_parecidos[clave]

    ndnutils.similarity(parecidos)
    ndn.StrategyChoiceHelper.InstallAll("/", "/localhost/nfd/strategy/similarity")
else:
    ndn.StrategyChoiceHelper.InstallAll("/", "/localhost/nfd/strategy/best-route")
ndn.StrategyChoiceHelper.InstallAll("/localhop/ndn/nlsr", "/localhost/nfd/strategy/multicast")

routing = ndn.GlobalRoutingHelper()
routing.InstallAll()
for node, prefixes in ORIGENES.items():
    for prefix in prefixes:
        routing.AddOrigins(prefix, container([node]))
routing.CalculateAllPossibleRoutes()

for a, b in VECINOS:  # adyacencias NLSR: cada hub alcanza las LSA de su vecino
    ndn.FibHelper.AddRoute(nodes[a], ndn.Name(lsa(b)), nodes[b], 1)
    ndn.FibHelper.AddRoute(nodes[b], ndn.Name(lsa(a)), nodes[a], 1)

for cli in CLIENTES:  # ruta por defecto del cliente hacia su hub, como un host del testbed
    ndn.FibHelper.AddRoute(nodes[cli], ndn.Name("/"), nodes[info[cli]["hub"]], 1)

# ---------- Aplicaciones ----------

# Sin retransmisiones, como ndnping y ndn-autoconf. Si no, cada Interest con
# Nack se reenviaría sin fin y los NoRoute crecerían con el tiempo.
Config.SetDefault("ns3::ndn::Consumer::RetxTimer", StringValue("100000s"))
Config.SetDefault("ns3::ndn::Consumer::LifeTime", StringValue(LIFETIME))

paradas = {(n, p): t for n, p, t in CAIDAS}
for node, prefix, familia, payload in PRODUCCION:
    app = ndn.AppHelper("ns3::ndn::Producer")
    app.SetPrefix(prefix)
    app.SetAttribute("PayloadSize", StringValue(str(payload)))
    apps = app.Install(nodes[node])
    if (node, prefix) in paradas:
        apps.Stop(Seconds(paradas[(node, prefix)]))

DIRECTO_INICIO = 1.0  # todos los clientes empiezan a leer la misma lectura a la vez
SESIONES = []         # (nodo, prefijo, inicio) de cada sesión secuencial

for node, familia, prefix, rate, patron, params, _ in TRAFICO:
    if patron == "zipf":
        contenidos, s = params
        app = ndn.AppHelper("ns3::ndn::ConsumerZipfMandelbrot")
        app.SetAttribute("NumberOfContents", StringValue(str(contenidos)))
        app.SetAttribute("s", StringValue(str(s)))
        app.SetAttribute("Randomize", StringValue("exponential"))
        app.SetAttribute("Frequency", StringValue(str(rate)))
        app.SetPrefix(prefix)
        app.Install(nodes[node]).Start(Seconds(random.uniform(0, 1 / rate)))
    elif patron == "secuencial":
        segmentos, sesiones = params
        duracion = segmentos / rate
        for _ in range(sesiones):
            inicio = random.uniform(0, max(T - duracion, 0))
            app = ndn.AppHelper("ns3::ndn::ConsumerCbr")
            app.SetAttribute("Frequency", StringValue(str(rate)))
            app.SetAttribute("MaxSeq", IntegerValue(segmentos))
            app.SetPrefix(prefix)
            app.Install(nodes[node]).Start(Seconds(inicio))
            SESIONES.append((node, prefix, round(inicio, 3)))
    else:
        app = ndn.AppHelper("ns3::ndn::ConsumerCbr")
        app.SetAttribute("Frequency", StringValue(str(rate)))
        if patron == "unico":
            # Como ndnping: secuencia que empieza en un número aleatorio, para que
            # los nombres de distintos clientes no coincidan
            app.SetAttribute("StartSeq", IntegerValue(random.randrange(2 ** 30)))
            inicio = random.uniform(0, 1 / rate)
        else:
            inicio = DIRECTO_INICIO
        app.SetPrefix(prefix)
        app.Install(nodes[node]).Start(Seconds(inicio))

# ---------- Logs y métricas ----------

out = args.salida
ndnutils.packet_log(os.path.join(out, "paquetes.csv"))
ndnutils.app_delay(os.path.join(out, "app-delays.txt"))
ndnutils.cs(os.path.join(out, "cs.txt"))

Simulator.Stop(Seconds(T))
Simulator.Run()
ndnutils.close()
Simulator.Destroy()

# ---------- Post-proceso ----------

with open(os.path.join(out, "escenario.json"), "w") as f:
    json.dump({
        "topologia": topo,
        "origenes": ORIGENES,
        "produccion": [{"nodo": n, "prefijo": p, "familia": fa, "payload": b}
                       for n, p, fa, b in PRODUCCION],
        "caidas": [{"nodo": n, "prefijo": p, "tiempo": t} for n, p, t in CAIDAS],
        "trafico": [{"nodo": n, "familia": fa, "prefijo": p, "rate": r, "patron": pa,
                     "params": pr, "esperado": e, "intencion": INTENCION.get(p)}
                    for n, fa, p, r, pa, pr, e in TRAFICO],
        "sesiones": [{"nodo": n, "prefijo": p, "inicio": t} for n, p, t in SESIONES],
        "params": {"tiempo": T, "seed": args.seed, "cs_hub": CS_HUB, "lifetime": LIFETIME,
                   "mtu": MTU, "modelo": args.modelo, "umbral": args.umbral if args.modelo else None},
    }, f, indent=2, ensure_ascii=False)

# Las filas ya están en orden de tiempo, pero ndnSIM emite la entrada de un Interest
# después de que el forwarder lo procese. Dentro de cada instante se reordena a:
# entra -> caché -> sale -> PIT. Se hace en streaming para no cargar el log en memoria.
ORDEN = {("in", "Interest"): 0, ("in", "Data"): 0, ("in", "Nack"): 0,
         ("", "CsHit"): 1, ("", "CsMiss"): 1, ("", "Timeout"): 3}
PREFIJOS = sorted({(p, fa) for _, fa, p, *_ in TRAFICO}, key=lambda x: -len(x[0]))


def familia(nombre):
    return next((fa for p, fa in PREFIJOS if nombre.startswith(p + "/")), "otros")


stats = defaultdict(Counter)
mezcla = Counter()
noroute = Counter()    # (nodo, familia, "origina" | "reenvía")
nack_recibido = set()  # (nodo, nombre) de Nacks que llegan de un vecino (por nombre:
                       # con agregación en la PIT el Nack vuelve con otro nonce)

raw = os.path.join(out, "paquetes.csv")
tmp = raw + ".tmp"
os.makedirs(os.path.join(out, "routers"), exist_ok=True)
with open(raw, newline="") as fin, open(tmp, "w", newline="") as fout:
    reader = csv.reader(fin)
    header = next(reader)
    col = {c: i for i, c in enumerate(header)}
    TIME, NODE, DIR_, TIPO, SRC, NOMBRE, MOTIVO = (
        col[c] for c in ("time", "node", "dir", "tipo", "src", "nombre", "motivo"))
    writer = csv.writer(fout)
    writer.writerow(header)
    router_files = {h: open(os.path.join(out, "routers", h + ".csv"), "w", newline="") for h in HUBS}
    router_w = {h: csv.writer(f) for h, f in router_files.items()}
    todos_files = open(os.path.join(out, "routers.csv"), "w", newline="")
    todos_w = csv.writer(todos_files)
    for w in [*router_w.values(), todos_w]:
        w.writerow(header)

    def flush(group):
        group.sort(key=lambda r: ORDEN.get((r[DIR_], r[TIPO]), 2))
        for r in group:
            node, tipo, d = r[NODE], r[TIPO], r[DIR_]
            stats[node][(d, tipo, r[MOTIVO])] += 1
            if d == "in" and tipo == "Interest" and r[SRC] == "app":
                mezcla[familia(r[NOMBRE])] += 1
            if tipo == "Nack" and d == "in":
                nack_recibido.add((node, r[NOMBRE]))
            if tipo == "Nack" and d == "out" and r[MOTIVO] == "NoRoute" and r[SRC] != "app":
                papel = "reenvía" if (node, r[NOMBRE]) in nack_recibido else "origina"
                noroute[(node, familia(r[NOMBRE]), papel)] += 1
            writer.writerow(r)
            if node in router_w:
                router_w[node].writerow(r)
                todos_w.writerow(r)

    group, current = [], None
    for r in reader:
        if r[TIME] != current:
            flush(group)
            group, current = [], r[TIME]
        group.append(r)
    flush(group)
    for f in [*router_files.values(), todos_files]:
        f.close()
os.replace(tmp, raw)

total = sum(mezcla.values())
filas = sum(sum(s.values()) for s in stats.values())
print(f"\n{filas} filas de log. Interests generados por familia ({total}):")
for fa, n in mezcla.most_common():
    print(f"  {fa:<27}{n:>8}  {100 * n / total:5.1f}%")

print(f"\n{'nodo':<15}{'Int in':>8}{'Int out':>9}{'CsHit':>8}{'hit %':>7}{'NoRoute':>9}")
for n in HUBS + PRODUCTORES:
    s = stats[n]
    hit, miss = s[("", "CsHit", "")], s[("", "CsMiss", "")]
    origina = sum(v for (h, _, p), v in noroute.items() if h == n and p == "origina")
    print(f"{n:<15}{s[('in', 'Interest', '')]:>8}{s[('out', 'Interest', '')]:>9}{hit:>8}"
          f"{100 * hit / max(hit + miss, 1):>6.1f}%{origina:>9}")

print("\nNoRoute originados por nodo y familia:")
for (node, fa, papel), n in sorted(noroute.items()):
    if papel == "origina":
        print(f"  {node:<15}{fa:<27}{n:>7}")

print(f"\n{'cliente':<13}{'Int':>8}{'Data':>8}{'Nack':>7}  satisfechos")
for n in CLIENTES:
    s = stats[n]
    sent = s[("in", "Interest", "")]   # Interests que entran desde las apps
    data = s[("out", "Data", "")]      # Data entregados a las apps
    nack = sum(v for (d, t, _), v in s.items() if d == "out" and t == "Nack")
    print(f"{n:<13}{sent:>8}{data:>8}{nack:>7}  {100 * data / max(sent, 1):.1f}%")

print(f"\nResultados en {out}/")
