"""Post-proceso del log de paquetes (ndnutils.packet_log).

reordenar() deja paquetes.csv en orden lógico y escribe el log de los routers:
    paquetes.csv       todos los nodos
    routers.csv        todos los routers en un solo fichero
    routers/<R>.csv    un fichero por router

Las filas ya salen en orden de tiempo, pero ndnSIM emite la entrada de un Interest
después de que el forwarder lo procese. Dentro de cada instante se reordena a:
entra -> caché -> sale -> PIT. Se hace en streaming para no cargar el log en memoria.
"""

import csv
import os

ORDEN = {("in", "Interest"): 0, ("in", "Data"): 0, ("in", "Nack"): 0,
         ("", "CsHit"): 1, ("", "CsMiss"): 1, ("", "Timeout"): 3}


def reordenar(carpeta, routers, por_fila=None):
    """Reordena paquetes.csv de carpeta y separa el log de los routers.

    por_fila(fila, col) se llama con cada fila (lista de valores) ya en orden y el
    índice de cada columna (col["nombre"]), para calcular estadísticas sin leer el
    log otra vez."""
    raw = os.path.join(carpeta, "paquetes.csv")
    tmp = raw + ".tmp"
    os.makedirs(os.path.join(carpeta, "routers"), exist_ok=True)
    abiertos = [open(os.path.join(carpeta, "routers", r + ".csv"), "w", newline="") for r in routers]
    todos = open(os.path.join(carpeta, "routers.csv"), "w", newline="")
    try:
        with open(raw, newline="") as fin, open(tmp, "w", newline="") as fout:
            reader = csv.reader(fin)
            header = next(reader)
            col = {c: i for i, c in enumerate(header)}
            TIME, NODE, DIR, TIPO = col["time"], col["node"], col["dir"], col["tipo"]
            writer = csv.writer(fout)
            por_router = {r: csv.writer(f) for r, f in zip(routers, abiertos)}
            todos_w = csv.writer(todos)
            for w in [writer, todos_w, *por_router.values()]:
                w.writerow(header)

            def volcar(grupo):
                grupo.sort(key=lambda r: ORDEN.get((r[DIR], r[TIPO]), 2))
                for r in grupo:
                    writer.writerow(r)
                    w = por_router.get(r[NODE])
                    if w:
                        w.writerow(r)
                        todos_w.writerow(r)
                    if por_fila:
                        por_fila(r, col)

            grupo, actual = [], None
            for r in reader:
                if r[TIME] != actual:
                    volcar(grupo)
                    grupo, actual = [], r[TIME]
                grupo.append(r)
            volcar(grupo)
    finally:
        for f in [*abiertos, todos]:
            f.close()
    os.replace(tmp, raw)
