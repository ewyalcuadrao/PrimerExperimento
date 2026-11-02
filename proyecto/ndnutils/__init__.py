"""Utilidades para simulaciones ndnSIM en Python.

Expone los tracers de ndnSIM, que no tienen bindings oficiales de Python.
Todos instalan el tracer en TODOS los nodos existentes en el momento de la
llamada, así que hay que llamarlos después de crear la topología, instalar la
pila NDN y las aplicaciones, y antes de Simulator.Run().

Tras Simulator.Run() hay que llamar a close() para que las trazas se
escriban completas en disco antes de leerlas.
"""

import ctypes
import os
import subprocess
import traceback

import ns.ndnSIM  # noqa: F401  (carga libns3-ndnSIM antes que nuestro puente)

_DIR = os.path.dirname(os.path.abspath(__file__))
_PROYECTO = os.path.dirname(_DIR)
_LIB = os.path.join(_DIR, "libndnutils.so")

# Recompila el puente si falta o si tracers.cpp ha cambiado
subprocess.run(["make", "-s", "-C", _PROYECTO], check=True)
_lib = ctypes.CDLL(_LIB)


def _call(func, *args):
    if func(*args) != 0:
        raise RuntimeError(f"fallo al instalar el tracer ({func.__name__})")


def _prepare(path):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    return path.encode()


def l3_rate(path, period=1.0):
    """Paquetes/kB por nodo y face (Interests, Data, Nacks...), cada `period` s."""
    _call(_lib.ndnutils_l3_rate, _prepare(path), ctypes.c_double(period))


def app_delay(path):
    """Retardo de cada Interest/Data en los consumidores (DelayS, RetxCount, HopCount)."""
    _call(_lib.ndnutils_app_delay, _prepare(path))


def cs(path, period=1.0):
    """Aciertos y fallos de la caché (Content Store) por nodo, cada `period` s."""
    _call(_lib.ndnutils_cs, _prepare(path), ctypes.c_double(period))


def l2_rate(path, period=1.0):
    """Paquetes descartados en la capa de enlace por nodo, cada `period` s."""
    _call(_lib.ndnutils_l2_rate, _prepare(path), ctypes.c_double(period))


def packet_log(path):
    """Log de cada Interest/Data/Nack que entra o sale de cada nodo, más aciertos
    y fallos de caché y expiraciones de la PIT. Mismas columnas que las trazas
    del testbed (time,src,dst,tipo,nombre,bytes,motivo) más node, dir, face y nonce."""
    _call(_lib.ndnutils_packet_log, _prepare(path))


def qoe_log(path):
    """Una fila por sesión de los reproductores de vídeo (ns3::ndn::VideoPlayer):
    resultado, tiempo de arranque, paradas, bitrate medio y cambios de calidad."""
    _call(_lib.ndnutils_qoe_log, _prepare(path))


_SIMILARITY_CB =ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_char_p, ctypes.c_char_p,
                                  ctypes.c_void_p, ctypes.c_int)
_similarity_ref = None  # ctypes libera el callback si nadie guarda una referencia


def similarity(parecidos, redirigir=False):
    """Activa la estrategia /localhost/nfd/strategy/similarity.

    parecidos(nodo, nombre) -> [(nombre conocido, similitud), ...], de más a menos
    parecido. La estrategia lo llama cuando no hay ruta para un Interest y toma el
    primer candidato que tenga una. Con redirigir=False reenvía el Interest por la ruta
    de ese candidato; con redirigir=True contesta al consumidor con un Data de tipo
    Link que lleva el nombre del candidato. Hay que instalar la estrategia con
    ndn.StrategyChoiceHelper (p. ej. en "/") para que se use."""
    global _similarity_ref

    def callback(node, name, buf, size):
        try:
            texto = "".join(f"{n}\t{s:.4f}\n" for n, s in parecidos(node.decode(), name.decode()))
            datos = texto.encode()[:size]
            ctypes.memmove(buf, datos, len(datos))
            return len(datos)
        except Exception:
            traceback.print_exc()
            return 0

    _similarity_ref = _SIMILARITY_CB(callback)
    _lib.ndnutils_similarity_callback(_similarity_ref, int(redirigir))


def ruta_por_defecto(nodo, salida):
    """En t=0 deja la FIB de `nodo` con una única ruta "/" hacia el nodo vecino
    `salida`, quitando las que haya instalado GlobalRoutingHelper (como un router de
    acceso de un operador). Nombres de nodo de Names::Add."""
    _lib.ndnutils_ruta_por_defecto(nodo.encode(), salida.encode())


def close():
    """Cierra todos los tracers y vuelca sus archivos a disco."""
    _call(_lib.ndnutils_close)
    _lib.ndnutils_packet_log_close()
    _lib.ndnutils_qoe_close()
