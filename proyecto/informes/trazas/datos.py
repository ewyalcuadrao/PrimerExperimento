"""Página que explica cómo es una traza NDN según las especificaciones y cómo son los
logs de las casas: un intercambio visto desde la tarjeta de red de la casa, los paquetes
abiertos byte a byte y de dónde sale cada columna del CSV.

Los Interest son tramas reales de las capturas de logs_casas.py. El Data y el Nack no
están en esas capturas (solo se guardan los Interest que salen de la casa): se
construyen aquí según NDN Packet Format v0.3 y NDNLPv2, con lo que respondería el
productor de ndnutils (manifiesto de 20 bytes a escala 1:1000, frescura de 1 h).

Ejecutar (desde PrimerExperimento/):
    python3 proyecto/informes/trazas/datos.py proyecto/resultados/logs_v1 trazas_ndn.html
"""

import csv
import hashlib
import json
import os
import sys

DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(DIR)))
import ndntlv as t  # noqa: E402

carpeta, salida = sys.argv[1], sys.argv[2]
CASA = "barrio-mad-1"

# ---------- Dos sesiones de la casa: una sin ruta y la primera que funciona ----------
with open(os.path.join(carpeta, "etiquetas.csv"), newline="") as f:
    etq = [r for r in csv.DictReader(f) if r["maquina"] == CASA]
tramas = {f"{tt:.6f}": b for tt, b in t.leer_pcapng(os.path.join(carpeta, "capturas", f"{CASA}.pcapng"))}
sin_ruta = [r for r in etq if r["sesion"] == etq[0]["sesion"]]
con_ruta = next([r for r in etq if r["sesion"] == s] for s in dict.fromkeys(r["sesion"] for r in etq)
                if all(r["resultado"] == "Data" for r in etq if r["sesion"] == s))
assert sin_ruta[0]["resultado"] == "Nack:NoRoute"

MAC_CASA, MAC_ROUTER = tramas[sin_ruta[0]["time"]][6:12], tramas[sin_ruta[0]["time"]][0:6]


def data(uri, contenido, frescura_ms):
    """Data firmado con DigestSha256: la firma es el SHA-256 de Name..SignatureInfo."""
    firmado = (t.nombre(uri) + t.tlv(0x14, t.tlv(0x19, t.entero(frescura_ms))) + t.tlv(0x15, contenido)
               + t.tlv(0x16, t.tlv(0x1B, t.entero(0))))
    return t.tlv(0x06, firmado + t.tlv(0x17, hashlib.sha256(firmado).digest()))


def nack(interest, motivo):
    """Nack de NDNLPv2: LpPacket con el campo Nack (y su NackReason) y el Interest rechazado
    como Fragment."""
    return t.tlv(t.LP_PACKET, t.tlv(0x320, t.tlv(0x321, t.entero(motivo))) + t.tlv(t.LP_FRAGMENT, interest))


# ---------- Paquetes para el inspector ----------
i_nr = tramas[sin_ruta[0]["time"]]
i_man, i_seg = tramas[con_ruta[0]["time"]], tramas[con_ruta[1]["time"]]
d_man = t.trama_ethernet(MAC_ROUTER, MAC_CASA, data(con_ruta[0]["nombre"], bytes(20), 3_600_000))
n_nr = t.trama_ethernet(MAC_ROUTER, MAC_CASA, nack(i_nr[14:14 + t.leer_interest(i_nr[14:])["bytes"]], 150))

NOMBRES = {0x64: "LpPacket", 0x50: "Fragment", 0x320: "Nack", 0x321: "NackReason", 0x05: "Interest",
           0x06: "Data", 0x07: "Name", 0x08: "GenericNameComponent", 0x32: "SegmentNameComponent",
           0x0a: "Nonce", 0x0c: "InterestLifetime", 0x14: "MetaInfo", 0x19: "FreshnessPeriod",
           0x15: "Content", 0x16: "SignatureInfo", 0x1b: "SignatureType", 0x17: "SignatureValue"}
CONTENEDORES = {0x64, 0x50, 0x320, 0x05, 0x06, 0x07, 0x14, 0x16}


def valor(tipo, v):
    if tipo == 0x08:
        return v.decode("ascii", "replace")
    if tipo == 0x32:
        return f"seg={int.from_bytes(v, 'big')}"
    if tipo in (0x0c, 0x19):
        return f"{int.from_bytes(v, 'big')} ms"
    if tipo == 0x321:
        return f"{int.from_bytes(v, 'big')} = NoRoute"
    if tipo == 0x1b:
        return f"{int.from_bytes(v, 'big')} = DigestSha256"
    if tipo == 0x0a:
        return v.hex()
    if tipo in (0x15, 0x17):
        return f"{len(v)} bytes"
    return None


def arbol(b, i, fin, nivel):
    out = []
    while i < fin:
        ini = i
        tipo, i = t.leer_varnum(b, i)
        n, i = t.leer_varnum(b, i)
        c = {"t": tipo, "n": NOMBRES.get(tipo, f"tipo {tipo}"), "ini": ini, "cab": i, "fin": i + n, "nivel": nivel}
        if tipo in CONTENEDORES and n:
            c["hijos"] = arbol(b, i, i + n, nivel + 1)
        else:
            c["v"] = valor(tipo, b[i:i + n])
        out.append(c)
        i += n
    return out


def mac(b):
    return ":".join(f"{x:02x}" for x in b)


def inspeccion(trama):
    """Campos de la trama Ethernet y, dentro, del paquete NDN. Los de Ethernet no son TLV:
    "cab" = "ini" para que no se atenúe ningún byte."""
    _, fin = t.leer_varnum(trama, 14)
    n, fin = t.leer_varnum(trama, fin)
    fin += n
    campos = [{"t": -1, "n": "Destino", "ini": 0, "cab": 0, "fin": 6, "nivel": 0, "v": mac(trama[0:6]), "eth": 1},
              {"t": -1, "n": "Origen", "ini": 6, "cab": 6, "fin": 12, "nivel": 0, "v": mac(trama[6:12]), "eth": 1},
              {"t": -1, "n": "EtherType", "ini": 12, "cab": 12, "fin": 14, "nivel": 0, "v": "0x8624 = NDN", "eth": 1}]
    campos += arbol(trama, 14, fin, 0)
    if fin < len(trama):
        campos.append({"t": -1, "n": "Relleno", "ini": fin, "cab": fin, "fin": len(trama), "nivel": 0,
                       "v": f"{len(trama) - fin} bytes a cero", "eth": 1})
    return {"hex": trama.hex(), "campos": campos, "ndn": fin - 14}


paquetes = {"interest": inspeccion(i_man), "seg": inspeccion(i_seg), "data": inspeccion(d_man),
            "nack": inspeccion(n_nr)}


# ---------- Los dos intercambios, paso a paso ----------
def fila(trama, r=None):
    """Fila de interests/<casa>.csv para una trama; las que no se guardan, sin hora."""
    ndn = trama[14:]
    tipo = {0x05: "Interest", 0x06: "Data", 0x64: "Nack"}[ndn[0]]
    nombre = r["nombre"] if r else None
    if tipo == "Interest":
        p = t.leer_interest(ndn)
        return [r["time"], mac(trama[6:12]), mac(trama[0:6]), tipo, p["nombre"], str(p["bytes"]), p["nonce"],
                str(p["lifetime_ms"])]
    _, i = t.leer_varnum(ndn, 0)
    n, i = t.leer_varnum(ndn, i)
    return ["—", mac(trama[6:12]), mac(trama[0:6]), tipo, nombre, str(i + n), "", ""]


AQ = "<span class='mono'>"
pasos_nr = [
    {"de": "app", "a": "nfd", "exp": f"El reproductor pide {AQ}{sin_ruta[0]['nombre']}</span>, con un nombre mal escrito ({sin_ruta[0]['causa'].replace('_', ' ')}). Se lo da al NFD de la propia máquina por un socket local: eso no pasa por la tarjeta y no se captura."},
    {"de": "nfd", "a": "router", "fila": fila(i_nr, sin_ruta[0]), "exp": "NFD no lo tiene en caché y su FIB manda todo hacia el router de acceso. Sale por la tarjeta: <b>primera trama de la captura</b>. Es un Interest suelto dentro de una trama Ethernet con ethertype 0x8624."},
    {"en": "router", "exp": "El router hace lo que describe la guía de NFD: busca en su caché (no está), crea la entrada en la PIT y busca en la FIB el prefijo más largo que coincida. <b>No hay ninguno</b>: nadie anuncia ese nombre."},
    {"de": "router", "a": "nfd", "fila": fila(n_nr, sin_ruta[0]), "nuestro": False, "exp": "El router devuelve el Interest dentro de un <b>Nack con motivo NoRoute (150)</b>. Un Nack no es un paquete de red, es un campo de NDNLPv2: el Interest va en el Fragment de un LpPacket. Esta trama entra en la casa, pero <b>no la guardamos</b>: el resultado está en etiquetas.csv."},
    {"de": "nfd", "a": "router", "fila": fila(tramas[sin_ruta[1]["time"]], sin_ruta[1]), "exp": "El usuario pulsa «reintentar»: el mismo nombre con <b>otro Nonce</b>, porque es un Interest nuevo. Segunda trama de la captura."},
    {"de": "router", "a": "nfd", "fila": fila(n_nr, sin_ruta[1]), "nuestro": False, "exp": "Otro Nack NoRoute. La sesión falla, y la siguiente sesión de la casa será la búsqueda con el nombre correcto."},
]
pasos_ok = [
    {"de": "app", "a": "nfd", "exp": f"El reproductor pide el manifiesto de {AQ}{con_ruta[0]['nombre'].rsplit('/', 1)[0]}</span>."},
    {"de": "nfd", "a": "router", "fila": fila(i_man, con_ruta[0]), "exp": "Sale por la tarjeta: se captura la trama."},
    {"en": "router", "exp": "Ahora la FIB sí tiene un prefijo que coincide: el que anuncia la plataforma. El router lo apunta en la PIT y lo reenvía hacia el productor."},
    {"de": "router", "a": "prod", "exp": "Lo que pasa más allá del router de acceso no se ve desde la casa. Cada router hace lo mismo hasta llegar al productor o a una caché que tenga el Data."},
    {"de": "prod", "a": "router", "exp": "Vuelve el <b>Data</b> por el camino inverso, siguiendo las entradas de la PIT."},
    {"de": "router", "a": "nfd", "fila": fila(d_man, con_ruta[0]), "nuestro": False, "exp": "El Data entra en la casa: nombre, frescura de 1 h, 20 bytes de contenido y la firma. Tampoco lo guardamos: sin simular la red no sabemos cuándo llegaría."},
    {"de": "nfd", "a": "router", "fila": fila(i_seg, con_ruta[1]), "exp": "Con el manifiesto, el reproductor pide el único segmento, en 480p. Su último componente es un <b>SegmentNameComponent</b> (tipo 50): se escribe seg=0."},
    {"de": "router", "a": "nfd", "fila": fila(d_man, con_ruta[1])[:4] + [con_ruta[1]["nombre"], "—", "", ""], "nuestro": False, "exp": "Llega el segmento. Fin de la sesión."},
]

datos = {"paquetes": paquetes, "nr": pasos_nr, "ok": pasos_ok, "casa": CASA}
html = open(os.path.join(DIR, "plantilla.html"), encoding="utf-8").read()
html = html.replace("__ESCENARIO__", os.path.basename(os.path.normpath(carpeta))).replace("__CASA__", CASA)
html = html.replace("__DATOS__", json.dumps(datos, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/"))
open(salida, "w", encoding="utf-8").write(html)
print(f"{salida}: {len(html) // 1024} KB; bytes NDN", {k: p["ndn"] for k, p in paquetes.items()})
