"""Compara la iteración 1 de la red de vídeo con una o varias iteraciones 2.

La iteración 1 usa best-route: un nombre sin ruta recibe Nack NoRoute. En la
iteración 2 los routers redirigen al nombre válido más parecido (red_video.py
--modelo) y el reproductor sigue la redirección. Las dos usan las mismas sesiones
(misma semilla), así que se comparan sesión a sesión.

Cada sesión con un error de nombre (columna causa de sesiones.csv) tiene en
intencion el nombre real de lo que quería el usuario. En la iteración 2 acaba en:
    acierto            redirigida al contenido que quería
    mismo_titulo       redirigida a la misma serie, película o canal, pero a otro
                       episodio o vídeo
    falso_positivo     redirigida a otro contenido
    noroute            no se redirigió (ningún nombre llega al umbral)
    otro               otro resultado (Timeout, bucle de redirección...)
Una sesión sin error que reciba una redirección también es un falso positivo
(redireccion_indebida).

Tiempo hasta ver: segundos desde que el usuario pide el nombre erróneo hasta que
empieza a ver el contenido que quería. En la iteración 1 nunca lo ve (tras el NoRoute
abandona, no corrige el nombre); en la iteración 2, si la redirección acierta.

Motivo de cada redirección equivocada (falso_positivo o mismo_titulo), con --sesiones
(sesiones.txt del modelo item2vec, las sesiones con las que se entrenó):
    misma_sesion     el nombre al que se redirigió se pidió en la misma sesión que el nombre
                     erróneo en la iteración 1 (item2vec los acerca por eso)
y además la relación con lo que quería el usuario: mismo_titulo, mismo_tema (comparten
género de IMDb o temática de canal), misma_plataforma u otra_plataforma.

Salida: tablas por pantalla y, en proyecto/resultados/comparacion_video/,
comparacion.csv (una fila por ejecución y causa) y sesiones.csv (una fila por
sesión con error y ejecución).

Ejecutar (desde PrimerExperimento/):
    python3 proyecto/comparar_video.py proyecto/resultados/video_v3 \\
        proyecto/resultados/video_v3_ft080 proyecto/resultados/video_v3_ft085 ...
"""

import argparse
import csv
import os
import sys
from collections import Counter, defaultdict

DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(DIR, "video"))
import catalogo as cat  # noqa: E402

CLASES = ["acierto", "mismo_titulo", "falso_positivo", "noroute", "otro"]


def titulo(base):
    """Serie, película o canal de un nombre: /vod/serie/dark/t1/e2 -> /vod/serie/dark."""
    partes = base.split("/")
    return "/".join(partes[:3] if partes[1] == "yt" else partes[:4])


def leer(carpeta):
    sesiones = list(csv.DictReader(open(os.path.join(carpeta, "sesiones.csv"))))
    qoe = {q["sesion"]: q for q in csv.DictReader(open(os.path.join(carpeta, "qoe.csv")))}
    return sesiones, qoe


def similitudes(carpeta):
    """Similitud de cada redirección (filas Redirect del log): nombre pedido -> similitud."""
    sim = {}
    with open(os.path.join(carpeta, "routers.csv"), newline="") as f:
        col = {c: i for i, c in enumerate(next(csv.reader(f)))}
        # El log es muy grande: solo se analizan las filas Redirect
        for r in csv.reader(linea for linea in f if ",Redirect," in linea):
            destino, s = r[col["motivo"]].rsplit("@", 1)
            sim.setdefault(r[col["nombre"]], float(s))
    return sim


def tiempo_hasta_ver(q):
    return float(q["inicio"]) + float(q["arranque_s"])


def clasificar(s, q):
    if not q["redirigido_a"]:
        return "noroute" if q["resultado"] == "Nack:NoRoute" else "otro"
    if q["resultado"] != "completa":
        return "otro"
    if q["redirigido_a"] == s["intencion"]:
        return "acierto"
    if titulo(q["redirigido_a"]) == titulo(s["intencion"]):
        return "mismo_titulo"
    return "falso_positivo"


TEMAS = {t.prefijo(): set(t.generos) or {t.tipo} for t in cat.TITULOS}
TEMAS.update({f"/yt/{c.slug}": {c.tematica} for c in cat.canales()})
TEMAS["/yt/shorts"] = {"shorts"}


def relacion(destino, intencion):
    """Qué tiene que ver el contenido al que se redirigió con el que quería el usuario."""
    if titulo(destino) == titulo(intencion):
        return "mismo_titulo"
    if TEMAS.get(titulo(destino), set()) & TEMAS.get(titulo(intencion), set()):
        return "mismo_tema"
    if destino.split("/")[1] == intencion.split("/")[1]:
        return "misma_plataforma"
    return "otra_plataforma"


def coocurrencias(ruta):
    """Nombre -> nombres que se pidieron en alguna de sus sesiones (sesiones.txt de item2vec)."""
    junto = defaultdict(set)
    if ruta:
        with open(ruta) as f:
            for linea in f:
                nombres = set(linea.split()[2:])
                for n in nombres:
                    junto[n] |= nombres
    return junto


def media(xs):
    return sum(xs) / len(xs) if xs else float("nan")


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("iteracion1")
    parser.add_argument("iteracion2", nargs="+")
    parser.add_argument("--sesiones", help="sesiones.txt del modelo item2vec, para el motivo de los falsos positivos")
    parser.add_argument("--salida", default=os.path.join(DIR, "resultados", "comparacion_video"))
    args = parser.parse_args()
    os.makedirs(args.salida, exist_ok=True)

    junto = coocurrencias(args.sesiones)
    ses1, qoe1 = leer(args.iteracion1)
    errores = [s for s in ses1 if s["causa"] and s["sesion"] in qoe1]
    causas = Counter(s["causa"] for s in errores)
    video1 = [q for q in qoe1.values() if q["tipo"] != "miniatura"]
    nr1 = sum(q["resultado"] == "Nack:NoRoute" for q in video1)
    print(f"Iteración 1 ({args.iteracion1}): {len(video1)} sesiones de vídeo, {nr1} NoRoute "
          f"({100 * nr1 / len(video1):.1f} %)")
    print(f"  Con error de nombre: {len(errores)}; el usuario no ve lo que quería en ninguna")

    filas, por_sesion = [], []
    for carpeta in args.iteracion2:
        ses2, qoe2 = leer(carpeta)
        sim = similitudes(carpeta)
        nombre = os.path.basename(os.path.normpath(carpeta))
        video2 = [q for q in qoe2.values() if q["tipo"] != "miniatura"]
        nr2 = sum(q["resultado"] == "Nack:NoRoute" for q in video2)
        causa2 = {s["sesion"]: s["causa"] for s in ses2}
        indebidas = [q for q in video2 if q["redirigido_a"] and not causa2.get(q["sesion"])]
        clases = defaultdict(Counter)
        tiempos, sims = defaultdict(list), defaultdict(list)
        motivos, ejemplos = Counter(), defaultdict(list)
        for s in errores:
            q = qoe2.get(s["sesion"])
            if q is None:
                continue
            c = clasificar(s, q)
            clases[s["causa"]][c] += 1
            clases["total"][c] += 1
            pedido = s["base"]
            if pedido in sim:
                sims[c].append(sim[pedido])
            if c == "acierto":
                tiempos["total"].append(tiempo_hasta_ver(q) - float(q["inicio"]))
            motivo = ""
            if c in ("falso_positivo", "mismo_titulo"):
                motivo = ("misma_sesion+" if q["redirigido_a"] in junto.get(pedido, ()) else "") + \
                    relacion(q["redirigido_a"], s["intencion"])
                motivos[motivo] += 1
                ejemplos[motivo].append((s["causa"], pedido, q["redirigido_a"], s["intencion"], sim.get(pedido)))
            por_sesion.append({"ejecucion": nombre, "sesion": s["sesion"], "causa": s["causa"],
                               "pedido": s["base"], "intencion": s["intencion"],
                               "redirigido_a": q["redirigido_a"], "resultado": q["resultado"],
                               "clase": c, "similitud": sim.get(pedido, ""), "motivo": motivo})

        t = clases["total"]
        redirigidas = t["acierto"] + t["mismo_titulo"] + t["falso_positivo"]
        print(f"\n== {nombre}: {len(video2)} sesiones de vídeo, {nr2} NoRoute ({100 * nr2 / len(video2):.1f} %)")
        print(f"  Redirigidas {redirigidas} de {len(errores)} sesiones con error "
              f"({100 * redirigidas / len(errores):.0f} %); sesiones sin error redirigidas: {len(indebidas)}")
        if redirigidas:
            print(f"  De las redirigidas: acierto {t['acierto']} ({100 * t['acierto'] / redirigidas:.0f} %), "
                  f"mismo título {t['mismo_titulo']} ({100 * t['mismo_titulo'] / redirigidas:.0f} %), "
                  f"falso positivo {t['falso_positivo']} ({100 * t['falso_positivo'] / redirigidas:.0f} %)")
        print(f"  El usuario ve lo que quería: {t['acierto']} ({100 * t['acierto'] / len(errores):.0f} %), "
              f"{media(tiempos['total']):.1f} s después de pedirlo")
        print("  Similitud media: " + ", ".join(f"{c} {media(sims[c]):.3f}" for c in CLASES if sims[c]))
        print(f"  {'causa':<24}" + "".join(f"{c:>16}" for c in CLASES))
        for causa in [*sorted(causas, key=lambda c: -causas[c]), "total"]:
            print(f"  {causa:<24}" + "".join(f"{clases[causa][c]:>16}" for c in CLASES))
            n = sum(clases[causa].values())
            filas.append({"ejecucion": nombre, "causa": causa, "sesiones": n,
                          **{c: clases[causa][c] for c in CLASES},
                          "indebidas": len(indebidas) if causa == "total" else ""})
        if motivos:
            print("  Motivo de las redirecciones equivocadas (falso positivo y mismo título):")
            for m, k in motivos.most_common():
                print(f"    {m:<34}{k:>5}")
                for causa, pedido, destino, intencion, si in ejemplos[m][:2]:
                    print(f"        {causa}: {pedido} -> {destino} ({si}); quería {intencion}")

    with open(os.path.join(args.salida, "comparacion.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, list(filas[0]))
        w.writeheader()
        w.writerows(filas)
    with open(os.path.join(args.salida, "sesiones.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, list(por_sesion[0]))
        w.writeheader()
        w.writerows(por_sesion)
    print(f"\nResultados en {args.salida}/")


if __name__ == "__main__":
    main()
