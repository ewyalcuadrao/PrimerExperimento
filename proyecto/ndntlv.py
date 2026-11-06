"""Codificación TLV de NDN y escritura de capturas pcapng, según las especificaciones.

Solo lo que necesitan los logs de los consumidores: Interest con nombre, Nonce e
InterestLifetime, en una trama Ethernet con el ethertype de NDN; y la lectura de
vuelta para comprobarlos.

    NDN Packet Format v0.3 (https://docs.named-data.net/NDN-packet-spec/current/):
        Interest 0x05 = Name, CanBePrefix?, MustBeFresh?, ForwardingHint?, Nonce?,
        InterestLifetime?, HopLimit?, ... en ese orden. Name 0x07, GenericNameComponent
        0x08, Nonce 0x0a (4 bytes), InterestLifetime 0x0c (ms, 4000 si no está).
    NDN Naming Conventions rev3: SegmentNameComponent 0x32 (50), escrito seg=N.
    NDNLPv2 (https://redmine.named-data.net/projects/nfd/wiki/NDNLPv2): un paquete
        que cabe en la trama y no lleva campos de enlace (Nack, fragmentos...) puede
        ir suelto, sin LpPacket (0x64); el receptor lo trata como su Fragment (0x50).
    NDN sobre Ethernet: ethertype 0x8624, sin IP ni UDP.
"""

import re
import struct
from urllib.parse import unquote_to_bytes

INTEREST, NAME, GENERICO, SEGMENTO = 0x05, 0x07, 0x08, 0x32
NONCE, LIFETIME = 0x0A, 0x0C
LP_PACKET, LP_FRAGMENT = 0x64, 0x50
ETHERTYPE_NDN = 0x8624
NO_RESERVADOS = set(b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")


# ---------- Codificar ----------

def varnum(n):
    if n < 253:
        return bytes([n])
    if n <= 0xFFFF:
        return b"\xfd" + struct.pack(">H", n)
    if n <= 0xFFFFFFFF:
        return b"\xfe" + struct.pack(">I", n)
    return b"\xff" + struct.pack(">Q", n)


def tlv(tipo, valor):
    return varnum(tipo) + varnum(len(valor)) + valor


def entero(n):
    """NonNegativeInteger: 1, 2, 4 u 8 bytes, los mínimos."""
    for fmt, maximo in ((">B", 0xFF), (">H", 0xFFFF), (">I", 0xFFFFFFFF)):
        if n <= maximo:
            return struct.pack(fmt, n)
    return struct.pack(">Q", n)


def nombre(uri):
    """Name a partir de su URI: los componentes seg=N son SegmentNameComponent y el resto
    GenericNameComponent, con los %XX ya decodificados a bytes."""
    comps = b""
    for c in uri.strip("/").split("/"):
        m = re.fullmatch(r"seg=(\d+)", c)
        comps += tlv(SEGMENTO, entero(int(m.group(1)))) if m else tlv(GENERICO, unquote_to_bytes(c))
    return tlv(NAME, comps)


def interest(uri, nonce, lifetime_ms):
    """Interest como lo codifica ndn-cxx: Name, Nonce, InterestLifetime (CanBePrefix y
    MustBeFresh a falso no se codifican)."""
    return tlv(INTEREST, nombre(uri) + tlv(NONCE, struct.pack(">I", nonce)) + tlv(LIFETIME, entero(lifetime_ms)))


# ---------- Decodificar ----------

def leer_varnum(b, i):
    x = b[i]
    if x < 253:
        return x, i + 1
    n = {253: 2, 254: 4, 255: 8}[x]
    return int.from_bytes(b[i + 1:i + 1 + n], "big"), i + 1 + n


def campos(b):
    """Lista de (tipo, valor) de los TLV seguidos en b."""
    i, out = 0, []
    while i < len(b):
        t, i = leer_varnum(b, i)
        l, i = leer_varnum(b, i)
        out.append((t, b[i:i + l]))
        i += l
    return out


def uri(valor_nombre):
    partes = []
    for t, v in campos(valor_nombre):
        if t == SEGMENTO:
            partes.append(f"seg={int.from_bytes(v, 'big')}")
        else:
            partes.append("".join(chr(c) if c in NO_RESERVADOS else f"%{c:02X}" for c in v))
    return "/" + "/".join(partes)


def leer_interest(b):
    """De un Interest suelto o dentro de un LpPacket (NDNLPv2 admite los dos) saca
    nombre, nonce (hex) y lifetime en ms. b puede llevar relleno de Ethernet al final."""
    t, i = leer_varnum(b, 0)
    l, i = leer_varnum(b, i)
    v = b[i:i + l]
    if t == LP_PACKET:
        v = dict(campos(v))[LP_FRAGMENT]
        (t, v), = campos(v)
    assert t == INTEREST, f"no es un Interest (tipo {t})"
    c = dict(campos(v))
    return {"nombre": uri(c[NAME]), "nonce": c[NONCE].hex(), "bytes": i + l,
            "lifetime_ms": int.from_bytes(c[LIFETIME], "big") if LIFETIME in c else 4000}


# ---------- Tramas y pcapng ----------

def trama_ethernet(mac_src, mac_dst, paquete):
    """Trama Ethernet con un paquete NDN (ethertype 0x8624), rellenada al mínimo de 60
    bytes que exige Ethernet (sin contar el FCS)."""
    trama = bytes(mac_dst) + bytes(mac_src) + struct.pack(">H", ETHERTYPE_NDN) + paquete
    return trama + b"\x00" * max(0, 60 - len(trama))


def _bloque(tipo, cuerpo):
    cuerpo += b"\x00" * (-len(cuerpo) % 4)
    n = 12 + len(cuerpo)
    return struct.pack("<II", tipo, n) + cuerpo + struct.pack("<I", n)


class Pcapng:
    """Escritor mínimo de pcapng: una sección, una interfaz Ethernet y un Enhanced
    Packet Block por trama, con marcas de tiempo en microsegundos."""

    def __init__(self, ruta, interfaz="eth0"):
        self.f = open(ruta, "wb")
        self.f.write(_bloque(0x0A0D0D0A, struct.pack("<IHHq", 0x1A2B3C4D, 1, 0, -1)))
        nombre_if = interfaz.encode()
        opciones = struct.pack("<HH", 2, len(nombre_if)) + nombre_if + b"\x00" * (-len(nombre_if) % 4)
        opciones += struct.pack("<HH", 0, 0)
        self.f.write(_bloque(1, struct.pack("<HHI", 1, 0, 0) + opciones))

    def escribir(self, tiempo, trama):
        us = round(tiempo * 1_000_000)
        self.f.write(_bloque(6, struct.pack("<IIIII", 0, us >> 32, us & 0xFFFFFFFF, len(trama), len(trama)) + trama))

    def close(self):
        self.f.close()


def leer_pcapng(ruta):
    """Devuelve (tiempo, trama) de cada Enhanced Packet Block."""
    with open(ruta, "rb") as f:
        datos = f.read()
    i = 0
    while i < len(datos):
        tipo, n = struct.unpack_from("<II", datos, i)
        if tipo == 6:
            _, alto, bajo, cap, _ = struct.unpack_from("<IIIII", datos, i + 8)
            yield ((alto << 32) | bajo) / 1_000_000, datos[i + 28:i + 28 + cap]
        i += n
