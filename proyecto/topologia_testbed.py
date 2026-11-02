"""Genera la topología de la simulación a partir del testbed NDN real.

Toma de testbed-nodes.json (proyecto miNDNsim) los hubs elegidos, sus prefijos
de sitio, sus coordenadas y los enlaces reales entre ellos. A cada hub le cuelga
los clientes y productores de la simulación. El retardo de cada enlace se estima
a partir de la distancia geográfica (~1 ms por cada 100 km, que equivale a fibra
con el doble de recorrido que la línea recta, algo típico de Internet).

Se ejecuta en el host (no en el contenedor):
    python3 proyecto/topologia_testbed.py [ruta/testbed-nodes.json]
Escribe proyecto/topologias/testbed.json, que lee red_testbed.py.
"""

import json
import math
import os
import sys

TESTBED = os.path.expanduser("~/Documentos/miNDNsim/Topologia/testbed-nodes.json")
SALIDA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "topologias", "testbed.json")

# Hubs del testbed (activos, ndn-up) que forman la malla de la simulación
HUBS = ["UCLA", "ARIZONA", "WU", "MEMPHIS", "UFBA"]

# Coordenadas reales cuando el JSON solo trae la posición de dibujo
REAL_POSITION = {"UFBA": [-12.9997, -38.5076]}  # Salvador de Bahía

# Nodos de la simulación que no son hubs: (id, rol, hub al que se conectan, descripción)
EXTREMOS = [
    ("cli-ucla", "consumer", "UCLA", "Cliente en la red de UCLA (como 131.179.196.7 en la traza)"),
    ("cli-arizona", "consumer", "ARIZONA", "Cliente en la red de Arizona"),
    ("cli-ufba", "consumer", "UFBA", "Cliente en la red de UFBA"),
    ("wu-server", "producer", "WU", "Servidor de WU: documento weekly-call-doc y grabaciones de cursos"),
    ("yoursunny-iah", "producer", "MEMPHIS", "Nodo yoursunny en Houston: ping y réplica de software"),
    ("ucla-media", "producer", "UCLA", "Emisión de clases de UCLA (inventado)"),
    ("iot-gw", "producer", "UFBA", "Pasarela de sensores IoT de UFBA (inventado)"),
    ("az-weather", "producer", "ARIZONA", "Estaciones meteorológicas de Arizona (inventado)"),
]

ACCESO = {"DataRate": "100Mbps", "Delay": "2ms"}
TRONCAL_RATE = "100Mbps"
KM_POR_MS = 100


def km(a, b):
    (la1, lo1), (la2, lo2) = map(lambda p: map(math.radians, p), (a, b))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(h))


def main():
    testbed = json.load(open(sys.argv[1] if len(sys.argv) > 1 else TESTBED))

    nodes, pos = [], {}
    for hub in HUBS:
        t = testbed[hub]
        pos[hub] = REAL_POSITION.get(hub) or t.get("_real_position") or t["position"]
        nodes.append({"id": hub, "rol": "router", "nombre": t["name"],
                      "prefijo": t["prefix"].replace("ndn:", "", 1), "posicion": pos[hub]})
    for node, rol, hub, desc in EXTREMOS:
        nodes.append({"id": node, "rol": rol, "nombre": desc, "hub": hub})

    links = []
    for i, a in enumerate(HUBS):
        for b in HUBS[i + 1:]:
            if b in testbed[a].get("neighbors", []) or a in testbed[b].get("neighbors", []):
                d = km(pos[a], pos[b])
                links.append({"source": a, "target": b, "DataRate": TRONCAL_RATE,
                              "Delay": f"{max(1, round(d / KM_POR_MS))}ms", "km": round(d)})
    for node, _, hub, _ in EXTREMOS:
        links.append({"source": node, "target": hub, **ACCESO})

    os.makedirs(os.path.dirname(SALIDA), exist_ok=True)
    with open(SALIDA, "w") as f:
        json.dump({"fuente": os.path.basename(TESTBED), "nodes": nodes, "links": links}, f,
                  indent=2, ensure_ascii=False)

    print(f"{len(nodes)} nodos, {len(links)} enlaces -> {SALIDA}")
    for l in links:
        print(f"  {l['source']:<16}{l['target']:<9}{l['Delay']:>6}  {l.get('km', '')}")


if __name__ == "__main__":
    main()
