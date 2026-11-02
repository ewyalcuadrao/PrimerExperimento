"""Comportamiento humano: qué ve cada usuario, cuándo y cuánto.

Genera la lista de sesiones que reproduce cada ns3::ndn::VideoPlayer. Una sesión es
un vídeo (o un trozo de vídeo) o una miniatura del catálogo. Las sesiones de una
misma visita van encadenadas (inicio = -1, pausa = segundos desde la anterior).

Modelo:
- Usuarios: franja de edad con la distribución española, dispositivo principal
  (limita la calidad máxima), actividad con cola larga (pocos usuarios ven mucho)
  y 1-3 series a medias con su temporada y episodio.
- Horario: visitas repartidas según la curva de audiencia de un día laborable
  (CURVA), con mínimo de madrugada, sobremesa y pico de 21 a 24 h.
- En cada visita, según edad y hora:
    serie       mira miniaturas del catálogo, sigue una serie a medias (o empieza una
                popular) y encadena episodios (más de madrugada), con autoplay; salta
                la intro el 30 % de las veces y deja el episodio el 8 %. Muchos
                abandonan en el piloto de una serie nueva.
    película    el 60 % la termina; algunas se paran y se retoman horas después.
    documental  como series o películas, según el título.
    youtube     vídeos de canales suscritos o recomendados, encadenados, vistos a
                medias; a veces empieza en un minuto concreto.
    shorts      ráfagas de vídeos de 15-60 s.
    directo     el programa en emisión de la parrilla de RTVE (Telediario, fútbol...).
- Errores que acaban en Nack NoRoute (columna causa) y lo que quería el usuario
  (columna intencion, vacía si no había contenido correcto):
    titulo_retirado         seguir viendo una serie o película que ya no está
    temporada_inexistente   pedir la temporada siguiente a la última
    estreno_no_disponible   pedir la temporada que se estrena antes de la hora
    enlace_mal_escrito      enlace compartido con erratas o con el título en inglés
    plataforma_equivocada   buscar un título en la plataforma que no lo tiene
    canal_borrado           vídeo de un canal que ya no existe
  Tras un enlace roto, la persona busca el título correcto (sesión con
  condicion=si_falla, que el reproductor solo ejecuta si la anterior falló).

Ejecutar solo el generador (desde PrimerExperimento/), para ver sus estadísticas:
    ./ndn.sh python3 proyecto/video/humanos.py
"""

import csv
import math
import os
import random
from collections import Counter

if __package__:
    from . import catalogo as cat
else:
    import catalogo as cat

SEGMENTO = 4  # s

# Peso de cada hora del día en el inicio de visitas (laborable, España)
CURVA = [3.0, 1.8, 1.0, 0.6, 0.4, 0.4, 0.7, 1.2, 1.8, 2.2, 2.5, 2.8,
         3.2, 4.5, 5.5, 4.0, 3.5, 3.8, 4.5, 5.5, 7.5, 10.0, 11.0, 7.0]

# Población adulta española por franja de edad (aprox.)
REPARTO_EDAD = {"joven": 0.17, "adulto": 0.33, "maduro": 0.25, "mayor": 0.25}
VISITAS_DIA = {"joven": 2.1, "adulto": 1.6, "maduro": 1.4, "mayor": 1.3}

# Qué tipo de contenido elige cada franja en una visita
PREFERENCIA = {
    "joven":  {"serie": .30, "peli": .08, "doc": .03, "youtube": .36, "shorts": .15, "directo": .03, "rtve": .05},
    "adulto": {"serie": .36, "peli": .14, "doc": .07, "youtube": .22, "shorts": .05, "directo": .07, "rtve": .09},
    "maduro": {"serie": .24, "peli": .14, "doc": .10, "youtube": .14, "shorts": .02, "directo": .16, "rtve": .20},
    "mayor":  {"serie": .12, "peli": .10, "doc": .12, "youtube": .08, "shorts": .00, "directo": .32, "rtve": .26},
}

# Dispositivo principal: (nombre, calidad máxima, probabilidad por franja de edad)
DISPOSITIVOS = [
    ("tv-4k", "2160p", (0.20, 0.35, 0.35, 0.25)),
    ("tv-hd", "1080p", (0.15, 0.25, 0.35, 0.50)),
    ("portatil", "1080p", (0.15, 0.15, 0.10, 0.05)),
    ("tablet", "1080p", (0.05, 0.10, 0.10, 0.15)),
    ("movil", "720p", (0.45, 0.15, 0.10, 0.05)),
]

# Probabilidad de que una sesión de serie/película empiece en un enlace compartido roto
P_ENLACE_ROTO = 0.15
P_PLATAFORMA_EQUIVOCADA = 0.25  # solo para títulos de PLATAFORMA_EQUIVOCADA
P_CANAL_BORRADO = 0.12     # de los vídeos de youtube que se abren
P_BUSCA_CORRECTO = 0.7     # tras un enlace roto, busca el título correcto
P_TEMPORADA_SIGUIENTE = 0.8  # al acabar la última temporada, prueba con la siguiente
P_ACABADA = 0.25           # series a medias que en realidad ya se terminaron
SHORTS = 3000              # vídeos cortos distintos


class Generador:
    def __init__(self, barrios, seed=1, horas=24.0):
        self.rng = random.Random(seed)
        self.horas = horas
        self.titulos = cat.TITULOS
        self.canales = cat.canales()
        self.shorts = [cat.id_video(self.rng) for _ in range(SHORTS)]
        self.filas = []
        self.usuarios = []
        n = 0
        for barrio, cuantos in barrios.items():
            for _ in range(cuantos):
                n += 1
                self.usuarios.append(self.nuevo_usuario(f"u{n:04d}", barrio))

    # ---------- utilidades ----------

    def elegir(self, opciones, pesos):
        return self.rng.choices(opciones, weights=pesos, k=1)[0]

    def segs(self, minutos, fraccion=1.0):
        return max(1, math.ceil(minutos * 60 * fraccion / SEGMENTO))

    def edad_idx(self, u):
        return cat.EDADES.index(u["edad"])

    # ---------- usuarios ----------

    def nuevo_usuario(self, uid, barrio):
        edad = self.elegir(list(REPARTO_EDAD), list(REPARTO_EDAD.values()))
        e = cat.EDADES.index(edad)
        disp = self.elegir(DISPOSITIVOS, [d[2][e] for d in DISPOSITIVOS])
        u = {"usuario": uid, "barrio": barrio, "edad": edad, "dispositivo": disp[0],
             "calidad_max": disp[1], "actividad": self.rng.lognormvariate(0, 0.6),
             "en_curso": {}, "suscripciones": []}
        series = [t for t in self.titulos if t.tipo == "serie" and t.plataforma == "vod"]
        for t in self.muestra(series, lambda t: t.popularidad * t.afinidad[e], self.rng.randint(1, 3)):
            temporada = self.rng.randint(1, len(t.temporadas))
            if t.estreno and self.rng.random() < 0.6:
                temporada = min(t.estreno)  # fan que espera el estreno
                u["en_curso"][t.slug] = (temporada, 1)
                continue
            if self.rng.random() < P_ACABADA:
                u["en_curso"][t.slug] = (len(t.temporadas) + 1, 1)  # espera una temporada nueva
                continue
            u["en_curso"][t.slug] = (temporada, self.rng.randint(1, t.temporadas[temporada - 1]))
        canales = [c for c in self.canales if not c.borrado]
        u["suscripciones"] = self.muestra(canales, lambda c: c.popularidad * c.afinidad[e],
                                          self.rng.randint(4, 15))
        return u

    def titulo(self, slug):
        return next(t for t in self.titulos if t.slug == slug)

    def muestra(self, items, peso, k):
        """k elementos distintos, elegidos con probabilidad proporcional a peso."""
        items, elegidos = list(items), []
        for _ in range(min(k, len(items))):
            x = self.elegir(items, [peso(i) for i in items])
            items.remove(x)
            elegidos.append(x)
        return elegidos

    # ---------- sesiones ----------

    def sesion(self, u, base, n_segs, seg_inicio=0, tipo="vod", causa="", intencion="",
               plataforma="", categoria="", titulo="", calidad=None, condicion=""):
        return {"usuario": u["usuario"], "base": base, "tipo": tipo, "seg_inicio": seg_inicio,
                "n_segs": n_segs, "calidad_max": calidad or u["calidad_max"], "causa": causa,
                "intencion": intencion, "plataforma": plataforma, "categoria": categoria,
                "titulo": titulo, "condicion": condicion, "pausa": 0.0}

    def miniaturas(self, u, plataforma):
        e = self.edad_idx(u)
        visibles = [t for t in self.titulos if t.plataforma == plataforma and not t.retirado]
        sesiones = []
        for t in self.muestra(visibles, lambda t: t.popularidad * t.afinidad[e], self.rng.randint(2, 8)):
            base = (t.prefijo(1) if t.temporadas else t.prefijo()) + "/miniatura"
            s = self.sesion(u, base, 0, tipo="miniatura", plataforma=plataforma,
                            categoria="miniatura", titulo=t.slug)
            s["pausa"] = self.rng.uniform(0.8, 3)
            sesiones.append(s)
        return sesiones

    def roto(self, u, s, causa, base_rota):
        """Convierte una sesión en un enlace roto y, a veces, añade la búsqueda correcta."""
        correcta = dict(s)
        s.update(base=base_rota, causa=causa, intencion=correcta["base"])
        resultado = [s]
        if self.rng.random() < P_BUSCA_CORRECTO:
            correcta.update(condicion="si_falla", pausa=self.rng.uniform(10, 40))
            resultado.append(correcta)
        return resultado

    def errata(self, slug, alias):
        """Una variante con error del nombre de un título, como en un enlace compartido."""
        palabras = slug.split("-")
        opciones = ["borrar", "duplicar", "intercambiar", "guion_bajo", "plural"]
        if palabras[0] in ("el", "la", "los", "las") and len(palabras) > 1:
            opciones.append("sin_articulo")
        if alias:
            opciones += ["alias", "alias"]
        op = self.rng.choice(opciones)
        i = self.rng.randrange(len(palabras))
        w = palabras[i]
        if op == "alias":
            return self.rng.choice(alias)
        if op == "guion_bajo":
            return slug.replace("-", "_")
        if op == "sin_articulo":
            return "-".join(palabras[1:])
        if op == "plural":
            palabras[-1] = palabras[-1][:-1] if palabras[-1].endswith("s") else palabras[-1] + "s"
            return "-".join(palabras)
        if len(w) < 3:
            w = w + w[-1]
        else:
            k = self.rng.randrange(1, len(w) - 1)
            if op == "borrar":
                w = w[:k] + w[k + 1:]
            elif op == "duplicar":
                w = w[:k] + w[k] + w[k:]
            else:
                w = w[:k - 1] + w[k] + w[k - 1] + w[k + 1:]
        palabras[i] = w
        variante = "-".join(palabras)
        return variante if variante != slug else slug + "s"

    def con_error_de_enlace(self, u, sesiones, t):
        """Puede convertir la primera sesión de contenido en un enlace roto."""
        s = sesiones[0]
        r = self.rng.random()
        if r < P_ENLACE_ROTO:
            base = s["base"].replace(f"/{t.slug}/", f"/{self.errata(t.slug, t.alias)}/", 1) \
                if f"/{t.slug}/" in s["base"] else s["base"].replace(t.slug, self.errata(t.slug, t.alias))
            return self.roto(u, s, "enlace_mal_escrito", base) + sesiones[1:]
        if r < P_ENLACE_ROTO + P_PLATAFORMA_EQUIVOCADA:
            otra = [p for p, slug in cat.PLATAFORMA_EQUIVOCADA if slug == t.slug]
            if otra:
                base = s["base"].replace(f"/{t.plataforma}/", f"/{otra[0]}/", 1)
                return self.roto(u, s, "plataforma_equivocada", base) + sesiones[1:]
        return sesiones

    def visita_serie(self, u, hora, plataforma="vod"):
        e = self.edad_idx(u)
        candidatas = [t for t in self.titulos if t.tipo in ("serie", "doc") and t.temporadas
                      and t.plataforma == plataforma]
        en_curso = [t for t in candidatas if t.slug in u["en_curso"]]
        nueva = not en_curso or self.rng.random() < 0.3
        if nueva:
            visibles = [t for t in candidatas if not t.retirado and t.slug not in u["en_curso"]]
            if not visibles:
                return []

            def peso(t):
                p = t.popularidad * t.afinidad[e]
                if any(hora >= h for h in t.estreno.values()):
                    p *= 4  # efecto estreno
                return p
            t = self.elegir(visibles, [peso(t) for t in visibles])
            temporada, episodio = 1, 1
            if t.estreno and any(hora >= h for h in t.estreno.values()) and self.rng.random() < 0.5:
                temporada = min(t.estreno)  # va directo al estreno
        else:
            t = self.rng.choice(en_curso)
            temporada, episodio = u["en_curso"][t.slug]

        sesiones = self.miniaturas(u, plataforma) if nueva else []
        trasnoche = hora >= 22 or hora < 3
        n_eps = min(8, 1 + int(self.rng.expovariate(1 / (1.6 if trasnoche else 0.8))))
        categoria = "serie" if t.tipo == "serie" else "doc"
        contenido = []
        for k in range(n_eps):
            if temporada > len(t.temporadas):
                # Acabó la última temporada: prueba con la siguiente, que no existe
                if self.rng.random() < P_TEMPORADA_SIGUIENTE:
                    s = self.sesion(u, t.base(temporada, 1), self.segs(t.minutos), causa="temporada_inexistente",
                                    plataforma=plataforma, categoria=categoria, titulo=t.slug)
                    s["pausa"] = self.rng.uniform(5, 30)
                    contenido.append(s)
                u["en_curso"].pop(t.slug, None)
                break
            hora_estreno = t.estreno.get(temporada)
            causa = ""
            if t.retirado:
                causa = "titulo_retirado"
            elif hora_estreno is not None and hora < hora_estreno:
                causa = "estreno_no_disponible"
            abandona = self.rng.random() < (0.25 if nueva and k == 0 and temporada == 1 and episodio == 1 else 0.08)
            fraccion = self.rng.uniform(0.1, 0.8) if abandona else 0.96
            seg_inicio = 15 if t.intro and self.rng.random() < 0.3 else 0
            n = max(1, self.segs(t.minutos, fraccion) - seg_inicio)
            s = self.sesion(u, t.base(temporada, episodio), n, seg_inicio, causa=causa,
                            plataforma=plataforma, categoria=categoria, titulo=t.slug)
            s["pausa"] = self.rng.uniform(2, 8) if k == 0 else self.rng.uniform(5, 10)
            contenido.append(s)
            if causa:
                break  # no hay nada que ver: la visita acaba aquí
            episodio += 1
            if episodio > t.temporadas[temporada - 1]:
                temporada, episodio = temporada + 1, 1
            if abandona:
                break
        if t.slug in u["en_curso"] or not nueva:
            u["en_curso"][t.slug] = (temporada, episodio)
        elif nueva and contenido and not contenido[-1]["causa"]:
            u["en_curso"][t.slug] = (temporada, episodio)
        if contenido and not contenido[0]["causa"]:
            contenido = self.con_error_de_enlace(u, contenido, t)
        return sesiones + contenido

    def visita_peli(self, u, hora, tipo="peli"):
        e = self.edad_idx(u)
        candidatas = [t for t in self.titulos if t.tipo == tipo and not t.temporadas]
        t = self.elegir(candidatas, [t.popularidad * t.afinidad[e] for t in candidatas])
        sesiones = self.miniaturas(u, t.plataforma)
        causa = "titulo_retirado" if t.retirado else ""
        completa = self.rng.random() < 0.6
        fraccion = 0.97 if completa else min(0.9, self.rng.expovariate(1 / 0.25))
        total = self.segs(t.minutos, fraccion)
        partes = [total]
        if completa and not causa and self.rng.random() < 0.15:
            corte = self.rng.randint(total // 4, 3 * total // 4)
            partes = [corte, total - corte]  # la deja y la retoma más tarde
        contenido, hecho = [], 0
        for i, n in enumerate(partes):
            s = self.sesion(u, t.base(), n, hecho, causa=causa, plataforma=t.plataforma,
                            categoria=tipo, titulo=t.slug)
            s["pausa"] = self.rng.uniform(2, 8) if i == 0 else self.rng.uniform(1800, 7200)
            contenido.append(s)
            hecho += n
        if not causa:
            contenido = self.con_error_de_enlace(u, contenido, t)
        return sesiones + contenido

    def visita_youtube(self, u, hora):
        e = self.edad_idx(u)
        todos = self.canales
        sesiones = []
        for k in range(min(20, 1 + int(self.rng.expovariate(1 / 4)))):
            if self.rng.random() < P_CANAL_BORRADO:
                canal = self.rng.choice([c for c in todos if c.borrado])
            elif self.rng.random() < 0.6:
                canal = self.rng.choice(u["suscripciones"])
            else:
                vivos = [c for c in todos if not c.borrado]
                canal = self.elegir(vivos, [c.popularidad * c.afinidad[e] for c in vivos])
            # Los vídeos recientes son los más vistos
            idx = min(len(canal.videos) - 1, int(self.rng.paretovariate(1.2)) - 1)
            minutos = min(180, self.rng.lognormvariate(math.log(canal.minutos), 0.6))
            fraccion = min(1.0, self.rng.betavariate(1.2, 1.5) + 0.05)
            seg_inicio = self.segs(minutos, self.rng.uniform(0.1, 0.6)) if self.rng.random() < 0.1 else 0
            n = max(1, self.segs(minutos, fraccion) - seg_inicio)
            s = self.sesion(u, f"/yt/{canal.slug}/{canal.videos[idx]}", n, seg_inicio,
                            causa="canal_borrado" if canal.borrado else "", plataforma="yt",
                            categoria="youtube", titulo=canal.slug)
            s["pausa"] = self.rng.uniform(1, 4)
            sesiones.append(s)
            if canal.borrado:
                break
        return sesiones

    def visita_shorts(self, u, hora):
        sesiones = []
        for _ in range(min(60, 3 + int(self.rng.expovariate(1 / 15)))):
            idx = min(SHORTS - 1, int(self.rng.paretovariate(0.9)) - 1)
            duracion = self.rng.uniform(15, 60)
            fraccion = self.rng.uniform(0.1, 0.3) if self.rng.random() < 0.3 else self.rng.uniform(0.8, 1.0)
            s = self.sesion(u, f"/yt/shorts/{self.shorts[idx]}", max(1, math.ceil(duracion * fraccion / SEGMENTO)),
                            plataforma="yt", categoria="shorts", titulo="shorts",
                            calidad=min(u["calidad_max"], "720p", key=["480p", "720p", "1080p", "2160p"].index))
            s["pausa"] = self.rng.uniform(0.3, 1.5)
            sesiones.append(s)
        return sesiones

    def visita_directo(self, u, hora):
        e = self.edad_idx(u)
        emitiendo = [p for p in cat.PARRILLA if p[1] <= hora < p[2]]
        canal, _, fin, programa, afinidad = self.elegir(emitiendo, [p[4][e] * (1 if p[0] == "24h" else 4)
                                                                   for p in emitiendo])
        minutos = min((fin - hora) * 60, self.rng.expovariate(1 / 25) + 2)
        s = self.sesion(u, f"/rtve/directo/{canal}", self.segs(minutos), tipo="directo",
                        plataforma="rtve", categoria="directo", titulo=programa)
        return [s]

    def duracion(self, sesiones):
        return sum(s["pausa"] + s["n_segs"] * SEGMENTO for s in sesiones)

    def generar(self):
        for u in self.usuarios:
            n = self.rng_poisson(VISITAS_DIA[u["edad"]] * u["actividad"] * self.horas / 24)
            horas = sorted(self.elegir(range(24), CURVA) + self.rng.random() for _ in range(n))
            horas = [h for h in horas if h < self.horas]
            libre = 0.0
            k = 0
            for h in horas:
                inicio = max(h * 3600, libre)
                if inicio >= self.horas * 3600:
                    break
                hora = inicio / 3600
                tipo = self.elegir(list(PREFERENCIA[u["edad"]]), list(PREFERENCIA[u["edad"]].values()))
                if hora < 3 and any(self.titulo(s).estreno for s in u["en_curso"]):
                    tipo = "serie"  # fan que espera el estreno a medianoche
                visita = {
                    "serie": lambda: self.visita_serie(u, hora),
                    "rtve": lambda: self.visita_serie(u, hora, "rtve"),
                    "peli": lambda: self.visita_peli(u, hora),
                    "doc": lambda: self.visita_peli(u, hora, "doc") if self.rng.random() < 0.3
                    else self.visita_serie(u, hora),
                    "youtube": lambda: self.visita_youtube(u, hora),
                    "shorts": lambda: self.visita_shorts(u, hora),
                    "directo": lambda: self.visita_directo(u, hora),
                }[tipo]()
                if not visita:
                    continue
                for i, s in enumerate(visita):
                    k += 1
                    s["sesion"] = f"{u['usuario']}-{k:03d}"
                    s["inicio"] = round(inicio, 3) if i == 0 else -1
                    if i == 0:
                        s["pausa"] = 0.0
                    s["pausa"] = round(s["pausa"], 3)
                    self.filas.append(s)
                libre = inicio + self.duracion(visita) + 60
        return self.filas

    def rng_poisson(self, lam):
        # Knuth (lam pequeño)
        l, k, p = math.exp(-lam), 0, 1.0
        while True:
            p *= self.rng.random()
            if p <= l:
                return k
            k += 1

    def guardar(self, carpeta):
        os.makedirs(carpeta, exist_ok=True)
        campos = ["usuario", "sesion", "inicio", "pausa", "condicion", "base", "tipo", "seg_inicio",
                  "n_segs", "calidad_max", "causa", "intencion", "plataforma", "categoria", "titulo"]
        ruta = os.path.join(carpeta, "sesiones.csv")
        with open(ruta, "w", newline="") as f:
            w = csv.DictWriter(f, campos, extrasaction="ignore")
            w.writeheader()
            w.writerows(self.filas)
        with open(os.path.join(carpeta, "usuarios.csv"), "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["usuario", "barrio", "edad", "dispositivo", "calidad_max", "actividad"])
            for u in self.usuarios:
                w.writerow([u["usuario"], u["barrio"], u["edad"], u["dispositivo"], u["calidad_max"],
                            round(u["actividad"], 3)])
        return ruta

    def resumen(self):
        video = [s for s in self.filas if s["tipo"] != "miniatura"]
        if not video:
            return f"{len(self.usuarios)} usuarios, ninguna sesión de vídeo"
        segs = sum(s["n_segs"] for s in video)
        lineas = [f"{len(self.usuarios)} usuarios, {len(self.filas)} sesiones "
                  f"({len(video)} de vídeo, {len(self.filas) - len(video)} miniaturas)",
                  f"{segs} segmentos previstos = {segs * SEGMENTO / 3600:.0f} h de vídeo "
                  f"({segs * SEGMENTO / 60 / len(self.usuarios):.0f} min por usuario)"]
        lineas.append("Sesiones de vídeo por categoría: " + ", ".join(
            f"{k} {v}" for k, v in Counter(s["categoria"] for s in video).most_common()))
        causas = Counter(s["causa"] for s in video if s["causa"])
        total = sum(causas.values())
        lineas.append(f"Sesiones de vídeo sin ruta: {total} ({100 * total / len(video):.1f} %): " +
                      ", ".join(f"{k} {v}" for k, v in causas.most_common()))
        return "\n".join(lineas)


if __name__ == "__main__":
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    g = Generador({"barrio": 580})
    g.generar()
    print(g.resumen())
