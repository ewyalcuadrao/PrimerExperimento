"""Comportamiento humano: qué ve cada usuario, cuándo y cuánto.

Genera la lista de sesiones que reproduce cada ns3::ndn::VideoPlayer. Una sesión es
un vídeo (o un trozo de vídeo) o una miniatura del catálogo. Las sesiones de una
misma visita van encadenadas (inicio = -1, pausa = segundos desde la anterior).

Modelo:
- Usuarios: franja de edad con la distribución española, dispositivo principal
  (limita la calidad máxima), actividad con cola larga (pocos usuarios ven mucho)
  y 1-3 series a medias con su temporada y episodio.
- Gustos que se mantienen todo el día: 2-3 géneros favoritos, 1-3 temáticas de
  YouTube y cuánto usa cada tipo de contenido (la preferencia de su edad, con
  variación personal). Casi todo lo que ve es de ahí, como en los historiales de
  Netflix de Georgia Tech (ICC 2021, "A Real-world Dataset of Netflix Videos and
  User Watch-Behavior"): cada usuario repite unos pocos géneros y el 68 % de los días
  ve una sola serie.
- Horario: visitas repartidas según la curva de audiencia de un día laborable
  (CURVA), con mínimo de madrugada, sobremesa y pico de 21 a 24 h.
- En cada visita, según edad y hora:
    portada     al abrir una plataforma carga 3-6 filas de 6-10 miniaturas (tendencias,
                "porque te gusta <género>", producciones españolas)
    serie       casi siempre sigue con el episodio siguiente de la última serie que
                vio (P_SERIE_NUEVA, P_MISMA_SERIE); si empieza una, la elige entre las
                miniaturas de la portada (P_CLIC) o de sus géneros; encadena
                episodios (media de unos 3, más de madrugada), con autoplay; salta
                la intro el 30 % de las veces y deja el episodio el 8 %. Deja la
                serie como en el estudio de Netflix: el 45 % de las temporadas no
                se acaban y la mitad de esas se dejan antes del primer cuarto.
    película    el 60 % la termina; algunas se paran y se retoman horas después.
    documental  como series o películas, según el título.
    youtube     vídeos encadenados, vistos a medias: el siguiente suele ser del
                mismo canal (P_MISMO_CANAL) o uno de los recomendados que acaba de
                ver (P_RECOMENDADO); a veces empieza en un minuto concreto. Si busca uno
                nuevo, suele ser de la temática del último que vio, también en la visita
                siguiente (P_MISMA_TEMATICA), como en el estudio de Netflix, donde el 68 %
                de los días se ve una sola serie. Junto a cada vídeo se cargan 6-12
                miniaturas de recomendados (mismo canal o misma temática).
    shorts      ráfagas de vídeos de 15-60 s (unas 10 por visita).
    directo     el programa en emisión de la parrilla de RTVE (Telediario, fútbol...).
- Errores que acaban en Nack NoRoute (columna causa). En todos el contenido existe
  en la red con otro nombre, que es lo que quería el usuario (columna intencion):
    enlace_mal_escrito      errata al teclear o copiar: letra que falta, sobra o se
                            cambia de sitio, guion bajo, plural (también en el título
                            de un vídeo de /yt que busca)
    nombre_aproximado       no sabe el nombre exacto: título original, apodo
                            (lqsa), sin artículo (casa-de-papel), parte del título; en
                            /yt, el título sin las últimas palabras o sin una del medio
    mayusculas_acentos      lo escribe como se ve en pantalla (Miércoles,
                            Stranger-Things) y la app no lo normaliza
    plataforma_equivocada   lo busca en la plataforma que no lo tiene
    categoria_equivocada    busca un documental entre las películas o las series
    formato_episodio        enlace de una web externa o de una versión vieja de la
                            app con otro formato de episodio (t01/e01, s1/e1...)
    canal_renombrado        vídeo de /yt enlazado con el nombre antiguo de su canal
  Tras el error, la persona busca el nombre correcto el P_BUSCA_CORRECTO de las veces
  (sesión con condicion=si_falla, que el reproductor solo ejecuta si la anterior
  falló) y sigue con la visita; si no, la abandona.
- n_segs y seg_inicio cuentan segmentos de SEGMENTO s: el reproductor los pide uno a
  uno y elige la calidad de cada uno (ver ndnutils/video-player.cpp).

Ejecutar solo el generador (desde PrimerExperimento/), para ver sus estadísticas:
    ./ndn.sh python3 proyecto/video/humanos.py
"""

import csv
import math
import os
import random
from collections import Counter
from urllib.parse import quote

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
    "joven":  {"serie": .64, "peli": .08, "doc": .03, "youtube": .10, "shorts": .04, "directo": .03, "rtve": .05},
    "adulto": {"serie": .55, "peli": .14, "doc": .07, "youtube": .06, "shorts": .02, "directo": .07, "rtve": .09},
    "maduro": {"serie": .35, "peli": .14, "doc": .10, "youtube": .04, "shorts": .01, "directo": .16, "rtve": .20},
    "mayor":  {"serie": .18, "peli": .10, "doc": .12, "youtube": .02, "shorts": .00, "directo": .32, "rtve": .26},
}
# Al acabar una visita, sigue sentado y pasa a otra cosa (un episodio y luego unos
# vídeos de YouTube, o al revés), sin la misma de antes
P_SIGUE = 0.4

# Dispositivo principal: (nombre, calidad máxima, probabilidad por franja de edad)
DISPOSITIVOS = [
    ("tv-4k", "2160p", (0.20, 0.35, 0.35, 0.25)),
    ("tv-hd", "1080p", (0.15, 0.25, 0.35, 0.50)),
    ("portatil", "1080p", (0.15, 0.15, 0.10, 0.05)),
    ("tablet", "1080p", (0.05, 0.10, 0.10, 0.15)),
    ("movil", "720p", (0.45, 0.15, 0.10, 0.05)),
]

# Probabilidad de cada error de nombre al empezar una serie, película o documental.
# Se prueban en orden; los que no aplican al título se saltan.
# Ajustadas para que ~20 % de las sesiones de vídeo de las casas acaben en NoRoute
# (medido con logs_casas.py en 90 días; las casas encadenan muchos episodios por visita
# y el error solo puede estar al empezarla).
ERRORES = [
    ("plataforma_equivocada", 0.41),  # solo títulos de PLATAFORMA_EQUIVOCADA
    ("categoria_equivocada", 0.41),   # solo títulos de CATEGORIA_EQUIVOCADA
    ("enlace_mal_escrito", 0.16),
    ("nombre_aproximado", 0.19),      # solo títulos con alias o con artículo
    ("formato_episodio", 0.10),       # solo títulos con temporadas
    ("mayusculas_acentos", 0.10),
]
P_CANAL_RENOMBRADO = 0.15  # de los vídeos de youtube que se abren, enlazados con el nombre antiguo
# Errores al buscar un vídeo de /yt por su título (no al pulsar el siguiente o un recomendado)
ERRORES_YT = [
    ("enlace_mal_escrito", 0.30),
    ("nombre_aproximado", 0.30),
    ("mayusculas_acentos", 0.15),
]
FILAS_PORTADA = (3, 6)     # filas de miniaturas que carga la portada de una plataforma
MINIATURAS_FILA = (6, 10)  # miniaturas visibles por fila
RECOMENDADOS = (6, 12)     # miniaturas de vídeos recomendados junto a un vídeo de /yt
P_BUSCA_CORRECTO = 0.7     # tras un error de nombre, busca el nombre correcto

# Continuidad (estudio de Netflix de Georgia Tech, ICC 2021, salvo donde se indica)
GUSTO = 4.0                 # peso de un título o canal de sus géneros o temáticas favoritas
VARIACION_TIPOS = 0.7       # sigma lognormal del uso personal de cada tipo de contenido (supuesto)
EPISODIOS_MEDIA = (1.8, 2.5)  # episodios extra por visita, de día y de madrugada (maratones de 2-6)
DEJA_SERIE = (0.10, 0.05)   # prob. de dejar la serie tras un episodio, antes y después del
                            # primer cuarto de temporada: 45 % de temporadas sin acabar
P_CLIC = 0.6                # una serie o película nueva sale de la portada (supuesto)
P_SERIE_NUEVA = 0.10        # con una serie a medias, empieza otra en lugar de seguirla
P_MISMA_SERIE = 0.85        # con varias a medias, sigue con la última que vio
INSTANTE = 0.001             # s que dura cada sesión con Generador(instantaneo=True)
P_MISMO_CANAL = 0.45        # YouTube: el siguiente vídeo es del mismo canal (supuesto)
P_RECOMENDADO = 0.35        # YouTube: el siguiente es uno de los recomendados (supuesto)
P_MISMA_TEMATICA = 0.75     # YouTube: un vídeo que busca es de la temática del último que vio


class Generador:
    def __init__(self, barrios, seed=1, horas=24.0, solo_episodios=False, instantaneo=False):
        """solo_episodios: cada usuario solo ve episodios de series (de /vod y de RTVE),
        sin miniaturas de portada ni otros tipos de contenido.
        instantaneo: cada sesión dura INSTANTE (un solo segmento) y las pausas dentro de
        una visita también; las visitas siguen empezando a su hora. El comportamiento
        (qué ve y en qué orden) es el mismo que sin él para la misma semilla: permite
        simular muchos días sin que pesen."""
        self.rng = random.Random(seed)
        self.horas = horas
        self.solo_episodios = solo_episodios
        self.instantaneo = instantaneo
        self.titulos = cat.TITULOS
        self.canales = cat.canales()
        self.por_slug = {c.slug: c for c in self.canales}
        self.shorts = cat.SHORTS
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
             "en_curso": {}, "suscripciones": [], "ultimo_canal": None}
        # Gustos: géneros (por lo que pesan en el catálogo) y temáticas de YouTube
        peso_genero, peso_tema = {}, {}
        for t in self.titulos:
            for g in t.generos:
                peso_genero[g] = peso_genero.get(g, 0) + t.popularidad * t.afinidad[e]
        for c in self.canales:
            peso_tema[c.tematica] = peso_tema.get(c.tematica, 0) + c.popularidad * c.afinidad[e]
        u["generos"] = set(self.muestra(list(peso_genero), peso_genero.get, self.rng.randint(2, 3)))
        u["tematicas"] = set(self.muestra(list(peso_tema), peso_tema.get, self.rng.randint(1, 3)))
        u["tipos"] = {k: p * self.rng.lognormvariate(0, VARIACION_TIPOS) for k, p in PREFERENCIA[edad].items()
                      if not self.solo_episodios or k in ("serie", "rtve")}

        series = [t for t in self.titulos if t.tipo == "serie" and t.plataforma == "vod"]
        for t in self.muestra(series, lambda t: t.popularidad * t.afinidad[e] * self.gusto(u, t),
                              self.rng.randint(1, 3)):
            temporada = self.rng.randint(1, len(t.temporadas))
            if t.estreno and self.rng.random() < 0.6:
                temporada = min(t.estreno)  # fan que espera el estreno
                u["en_curso"][t.slug] = (temporada, 1)
                continue
            u["en_curso"][t.slug] = (temporada, self.rng.randint(1, t.temporadas[temporada - 1]))
        u["suscripciones"] = self.muestra(
            self.canales, lambda c: c.popularidad * c.afinidad[e] * self.gusto_canal(u, c),
            self.rng.randint(4, 15))
        return u

    def gusto(self, u, t):
        """Peso extra de un título de sus géneros favoritos (los de RTVE no tienen género)."""
        return GUSTO if set(t.generos) & u["generos"] else 1.0

    def gusto_canal(self, u, c):
        return GUSTO if c.tematica in u["tematicas"] else 1.0

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
        """La portada de la plataforma: FILAS_PORTADA filas de miniaturas, como en las apps
        reales (tendencias, "porque te gusta <género>", producciones españolas). La app
        las carga casi a la vez, una detrás de otra."""
        e = self.edad_idx(u)
        visibles = [t for t in self.titulos if t.plataforma == plataforma]
        peso = lambda t: t.popularidad * t.afinidad[e] * self.gusto(u, t)
        vistos = [t for t in visibles if t.slug in u["en_curso"]]
        generos = [g for t in vistos for g in t.generos] + sorted(u["generos"])
        elegidos, sesiones = set(), []
        for fila in range(self.rng.randint(*FILAS_PORTADA)):
            grupo = visibles
            if fila > 0 and generos and self.rng.random() < 0.6:
                genero = self.rng.choice(generos)  # "Porque te gusta..."
                grupo = [t for t in visibles if genero in t.generos]
            elif fila > 0 and plataforma == "vod" and self.rng.random() < 0.3:
                grupo = [t for t in visibles if t.espanola]  # "Producciones españolas"
            grupo = [t for t in grupo if t.slug not in elegidos]
            for t in self.muestra(grupo, peso, self.rng.randint(*MINIATURAS_FILA)):
                elegidos.add(t.slug)
                base = (t.prefijo(1) if t.temporadas else t.prefijo()) + "/miniatura"
                s = self.sesion(u, base, 0, tipo="miniatura", plataforma=plataforma,
                                categoria="miniatura", titulo=t.slug)
                s["pausa"] = self.rng.uniform(0.05, 0.3)
                sesiones.append(s)
            if sesiones:
                sesiones[-1]["pausa"] += self.rng.uniform(0.5, 3)  # baja a la fila siguiente
        return sesiones

    def recomendados(self, u, canal):
        """Miniaturas de los vídeos recomendados junto a un vídeo de /yt: unos del mismo
        canal y otros de canales de la misma temática."""
        e = self.edad_idx(u)
        parecidos = [c for c in self.canales if c.tematica == canal.tematica and c is not canal]
        sesiones = []
        for _ in range(self.rng.randint(*RECOMENDADOS)):
            c = canal if not parecidos or self.rng.random() < 0.4 else self.elegir(
                parecidos, [c.popularidad * c.afinidad[e] for c in parecidos])
            video = c.videos[min(len(c.videos) - 1, int(self.rng.paretovariate(1.0)) - 1)]
            s = self.sesion(u, f"/yt/{c.slug}/{video}/miniatura", 0, tipo="miniatura",
                            plataforma="yt", categoria="miniatura", titulo=c.slug)
            s["pausa"] = self.rng.uniform(0.05, 0.3)
            sesiones.append(s)
        return sesiones

    def roto(self, u, sesiones, causa, base_rota):
        """La primera sesión pide base_rota en lugar de su nombre. Después la persona
        busca el nombre correcto y sigue con la visita, o la abandona."""
        s, correcta = dict(sesiones[0]), sesiones[0]
        s.update(base=base_rota, causa=causa, intencion=correcta["base"])
        if self.rng.random() >= P_BUSCA_CORRECTO:
            return [s]
        correcta.update(condicion="si_falla", pausa=self.rng.uniform(10, 40))
        return [s] + sesiones

    def errata(self, slug):
        """Una variante con erratas del nombre de un título."""
        palabras = slug.split("-")
        op = self.rng.choice(["borrar", "duplicar", "intercambiar", "guion_bajo", "plural"])
        i = self.rng.randrange(len(palabras))
        w = palabras[i]
        if op == "guion_bajo":
            return slug.replace("-", "_")
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

    def aproximado(self, t):
        """Cómo llama al título alguien que no sabe el nombre exacto, o None."""
        palabras = t.slug.split("-")
        opciones = list(t.alias)
        if palabras[0] in ("el", "la", "los", "las") and len(palabras) > 1:
            opciones.append("-".join(palabras[1:]))  # sin el artículo
        if len(palabras) >= 4:
            opciones.append("-".join(palabras[:3]))  # solo el principio del título
        return self.rng.choice(opciones) if opciones else None

    def como_en_pantalla(self, t):
        """El título escrito como se ve, con mayúsculas o acentos; codificado en el
        nombre como lo haría el navegador (UTF-8 con %)."""
        acentos = "-".join(t.nombre.lower().replace(",", "").replace(":", "").split())
        if acentos != t.slug and self.rng.random() < 0.6:
            return quote(acentos, safe="-")
        return "-".join(p.capitalize() for p in t.slug.split("-"))

    def con_error_de_nombre(self, u, sesiones, t):
        """Puede convertir la primera sesión de contenido en una petición con el nombre
        mal; el contenido existe con su nombre real."""
        partes = sesiones[0]["base"].split("/")  # ['', plataforma, tipo, slug, (tN, eM)]
        for causa, p in ERRORES:
            nuevas = list(partes)
            if causa == "plataforma_equivocada":
                otra = [pl for pl, slug in cat.PLATAFORMA_EQUIVOCADA if slug == t.slug]
                if not otra:
                    continue
                nuevas[1] = otra[0]
            elif causa == "categoria_equivocada":
                otra = [c for c, slug in cat.CATEGORIA_EQUIVOCADA if slug == t.slug]
                if not otra:
                    continue
                nuevas[2] = otra[0]
            elif causa == "enlace_mal_escrito":
                nuevas[3] = self.errata(t.slug)
            elif causa == "nombre_aproximado":
                nuevas[3] = self.aproximado(t)
                if not nuevas[3]:
                    continue
            elif causa == "formato_episodio":
                if len(partes) < 6:
                    continue
                temporada, episodio = int(partes[4][1:]), int(partes[5][1:])
                nuevas[4:] = self.rng.choice([
                    [f"t{temporada:02d}", f"e{episodio:02d}"], [f"s{temporada}", f"e{episodio}"],
                    [f"temporada-{temporada}", f"capitulo-{episodio}"],
                    [f"t{temporada}e{episodio}"], [f"{temporada}x{episodio:02d}"]])
            elif causa == "mayusculas_acentos":
                nuevas[3] = self.como_en_pantalla(t)
            if self.rng.random() < p and nuevas != partes:
                return self.roto(u, sesiones, causa, "/".join(nuevas))
        return sesiones

    def visita_serie(self, u, hora, plataforma="vod"):
        e = self.edad_idx(u)
        candidatas = [t for t in self.titulos if t.tipo in ("serie", "doc") and t.temporadas
                      and t.plataforma == plataforma]
        en_curso = [t for t in candidatas if t.slug in u["en_curso"]]
        nueva = not en_curso or self.rng.random() < P_SERIE_NUEVA
        sesiones = self.miniaturas(u, plataforma) if nueva and not self.solo_episodios else []
        if nueva:
            visibles = [t for t in candidatas if t.slug not in u["en_curso"]]
            portada = [t for t in visibles if t.slug in {s["titulo"] for s in sesiones}]
            if portada and self.rng.random() < P_CLIC:
                visibles = portada  # pulsa una de las miniaturas
            if not visibles:
                return []

            def peso(t):
                p = t.popularidad * t.afinidad[e] * self.gusto(u, t)
                if any(0 <= hora - h < 24 for h in t.estreno.values()):
                    p *= 4  # efecto estreno
                return p
            t = self.elegir(visibles, [peso(t) for t in visibles])
            temporada, episodio = 1, 1
            if t.estreno and any(0 <= hora - h < 24 for h in t.estreno.values()) and self.rng.random() < 0.5:
                temporada = min(t.estreno)  # va directo al estreno
        else:
            ultima = [t for t in en_curso if t.slug == u.get("ultima_serie")]
            t = ultima[0] if ultima and self.rng.random() < P_MISMA_SERIE else self.rng.choice(en_curso)
            temporada, episodio = u["en_curso"][t.slug]
        u["ultima_serie"] = t.slug

        trasnoche = hora % 24 >= 22 or hora % 24 < 3
        n_eps = min(8, 1 + int(self.rng.expovariate(1 / EPISODIOS_MEDIA[trasnoche])))
        desde = (temporada, episodio)
        dejada = False
        categoria = "serie" if t.tipo == "serie" else "doc"
        contenido = []
        for k in range(n_eps):
            if temporada > len(t.temporadas):  # acabó la serie
                u["en_curso"].pop(t.slug, None)
                break
            if hora < t.estreno.get(temporada, 0):
                break  # el estreno aún no ha salido: espera sin pedirlo
            abandona = self.rng.random() < (0.25 if nueva and k == 0 and temporada == 1 and episodio == 1 else 0.08)
            fraccion = self.rng.uniform(0.1, 0.8) if abandona else 0.96
            seg_inicio = 15 if t.intro and self.rng.random() < 0.3 else 0
            n = max(1, self.segs(t.minutos, fraccion) - seg_inicio)
            s = self.sesion(u, t.base(temporada, episodio), n, seg_inicio,
                            plataforma=plataforma, categoria=categoria, titulo=t.slug)
            s["pausa"] = self.rng.uniform(2, 8) if k == 0 else self.rng.uniform(5, 10)
            contenido.append(s)
            # Deja la serie: más al principio de la temporada
            primer_cuarto = episodio <= t.temporadas[temporada - 1] / 4
            if self.rng.random() < DEJA_SERIE[0 if primer_cuarto else 1]:
                dejada = True
                u["en_curso"].pop(t.slug, None)
                break
            episodio += 1
            if episodio > t.temporadas[temporada - 1]:
                temporada, episodio = temporada + 1, 1
            if abandona:
                break
        if dejada:
            pass
        elif t.slug in u["en_curso"] or not nueva:
            u["en_curso"][t.slug] = (temporada, episodio)
        elif nueva and contenido:
            u["en_curso"][t.slug] = (temporada, episodio)
        if contenido:
            contenido = self.con_error_de_nombre(u, contenido, t)
            if len(contenido) == 1 and contenido[0]["causa"]:
                u["en_curso"][t.slug] = desde  # no encontró el episodio: no lo ha visto
        return sesiones + contenido

    def visita_peli(self, u, hora, tipo="peli"):
        e = self.edad_idx(u)
        candidatas = [t for t in self.titulos if t.tipo == tipo and not t.temporadas]
        plataforma = candidatas[0].plataforma if candidatas else "vod"
        sesiones = self.miniaturas(u, plataforma)
        portada = [t for t in candidatas if t.slug in {s["titulo"] for s in sesiones}]
        if portada and self.rng.random() < P_CLIC:
            candidatas = portada  # pulsa una de las miniaturas
        t = self.elegir(candidatas, [t.popularidad * t.afinidad[e] * self.gusto(u, t) for t in candidatas])
        completa = self.rng.random() < 0.6
        fraccion = 0.97 if completa else min(0.9, self.rng.expovariate(1 / 0.25))
        total = self.segs(t.minutos, fraccion)
        partes = [total]
        if completa and self.rng.random() < 0.15:
            corte = self.rng.randint(total // 4, 3 * total // 4)
            partes = [corte, total - corte]  # la deja y la retoma más tarde
        contenido, hecho = [], 0
        for i, n in enumerate(partes):
            s = self.sesion(u, t.base(), n, hecho, plataforma=t.plataforma, categoria=tipo, titulo=t.slug)
            s["pausa"] = self.rng.uniform(2, 8) if i == 0 else self.rng.uniform(1800, 7200)
            contenido.append(s)
            hecho += n
        return sesiones + self.con_error_de_nombre(u, contenido, t)

    def canal_parecido(self, u, tematica):
        """Un canal de esa temática: de sus suscripciones si tiene alguno, si no de los
        más vistos."""
        e = self.edad_idx(u)
        suyos = [c for c in u["suscripciones"] if c.tematica == tematica]
        if suyos and self.rng.random() < 0.6:
            return self.rng.choice(suyos)
        tema = [c for c in self.canales if c.tematica == tematica]
        return self.elegir(tema, [c.popularidad * c.afinidad[e] for c in tema])

    def video_mal_escrito(self, u, s, canal, video):
        """Puede convertir la petición de un vídeo que busca por su título en una con el
        título mal; el vídeo existe con su título real."""
        palabras = video.split("-")
        for causa, p in ERRORES_YT:
            if causa == "enlace_mal_escrito":
                otro = self.errata(video)
            elif causa == "nombre_aproximado":
                if len(palabras) < 4:
                    continue
                if self.rng.random() < 0.6:
                    otro = "-".join(palabras[:self.rng.randint(2, len(palabras) - 1)])
                else:
                    k = self.rng.randrange(1, len(palabras) - 1)
                    otro = "-".join(palabras[:k] + palabras[k + 1:])
            else:
                otro = "-".join(w.capitalize() for w in palabras)
            if otro != video and otro not in canal.videos and self.rng.random() < p:
                return self.roto(u, [s], causa, f"/yt/{canal.slug}/{otro}")
        return [s]

    def visita_youtube(self, u, hora):
        e = self.edad_idx(u)
        sesiones, canal, idx, recomendados = [], None, 0, []
        for k in range(min(10, 1 + int(self.rng.expovariate(1 / 2)))):
            # Enlace compartido en redes a un canal que se renombró: lleva el nombre antiguo
            antiguo = self.rng.random() < P_CANAL_RENOMBRADO
            busca = False
            r = self.rng.random()
            if antiguo:
                # Le llega por los grupos o perfiles que sigue: de lo que le gusta, si hay
                renombrados = [c for c in self.canales if c.antiguo]
                cerca = [c for c in renombrados if c.tematica in u["tematicas"]
                         or (canal or u["ultimo_canal"]) and c.tematica == (canal or u["ultimo_canal"]).tematica]
                canal = self.rng.choice(cerca if cerca and self.rng.random() < 0.8 else renombrados)
                idx = min(len(canal.videos) - 1, int(self.rng.paretovariate(1.2)) - 1)
            elif canal and r < P_MISMO_CANAL:
                idx = min(len(canal.videos) - 1, idx + 1)  # el siguiente del mismo canal
            elif recomendados and r < P_MISMO_CANAL + P_RECOMENDADO:
                _, _, slug, video, _ = self.rng.choice(recomendados)["base"].split("/")
                canal = self.por_slug[slug]
                idx = canal.videos.index(video)
            else:
                # Busca otro vídeo: casi siempre de la temática del último que vio
                busca = True
                anterior = canal or u["ultimo_canal"]
                if anterior and self.rng.random() < P_MISMA_TEMATICA:
                    canal = self.canal_parecido(u, anterior.tematica)
                elif self.rng.random() < 0.6:
                    canal = self.rng.choice(u["suscripciones"])
                else:
                    canal = self.elegir(self.canales, [c.popularidad * c.afinidad[e] * self.gusto_canal(u, c)
                                                       for c in self.canales])
                # Los vídeos recientes son los más vistos
                idx = min(len(canal.videos) - 1, int(self.rng.paretovariate(1.2)) - 1)
            u["ultimo_canal"] = canal
            minutos = min(180, self.rng.lognormvariate(math.log(canal.minutos), 0.6))
            fraccion = min(1.0, self.rng.betavariate(1.2, 1.5) + 0.05)
            seg_inicio = self.segs(minutos, self.rng.uniform(0.1, 0.6)) if self.rng.random() < 0.1 else 0
            n = max(1, self.segs(minutos, fraccion) - seg_inicio)
            s = self.sesion(u, f"/yt/{canal.slug}/{canal.videos[idx]}", n, seg_inicio, plataforma="yt",
                            categoria="youtube", titulo=canal.slug)
            s["pausa"] = self.rng.uniform(1, 4)
            if antiguo:
                # Enlace con el nombre antiguo: busca el canal por su nombre nuevo o deja YouTube
                visita = self.roto(u, [s], "canal_renombrado", f"/yt/{canal.antiguo}/{canal.videos[idx]}")
            elif busca:
                visita = self.video_mal_escrito(u, s, canal, canal.videos[idx])
            else:
                visita = [s]
            sesiones += visita
            if visita[0]["causa"] and len(visita) == 1:
                return sesiones  # no encuentra el vídeo y deja YouTube
            recomendados = self.recomendados(u, canal)
            sesiones += recomendados
        return sesiones

    def visita_shorts(self, u, hora):
        sesiones = []
        for _ in range(min(30, 3 + int(self.rng.expovariate(1 / 7)))):
            idx = min(len(self.shorts) - 1, int(self.rng.paretovariate(0.9)) - 1)
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
        emitiendo = [p for p in cat.PARRILLA if p[1] <= hora % 24 < p[2]]
        canal, _, fin, programa, afinidad = self.elegir(emitiendo, [p[4][e] * (1 if p[0] == "24h" else 4)
                                                                   for p in emitiendo])
        minutos = min((fin - hora) * 60, self.rng.expovariate(1 / 25) + 2)
        s = self.sesion(u, f"/rtve/directo/{canal}", self.segs(minutos), tipo="directo",
                        plataforma="rtve", categoria="directo", titulo=programa)
        return [s]

    def duracion(self, sesiones):
        if self.instantaneo:
            return sum(s["pausa"] + INSTANTE for s in sesiones)
        return sum(s["pausa"] + s["n_segs"] * SEGMENTO for s in sesiones)

    def generar(self):
        for u in self.usuarios:
            # Visitas de cada día del periodo simulado (las series siguen de un día al otro)
            horas = []
            for dia in range(math.ceil(self.horas / 24)):
                n = self.rng_poisson(VISITAS_DIA[u["edad"]] * u["actividad"])
                horas += sorted(24 * dia + self.elegir(range(24), CURVA) + self.rng.random() for _ in range(n))
            horas = [h for h in horas if h < self.horas]  # las del periodo simulado
            libre = 0.0
            k = 0
            anterior = None  # tipo de la visita que acaba de terminar, si sigue sentado
            while horas:
                inicio = libre if anterior else max(horas.pop(0) * 3600, libre)
                if inicio >= self.horas * 3600:
                    break
                hora = inicio / 3600
                # Su uso de cada tipo; con una serie a medias, más probable seguirla
                tipos = dict(u["tipos"])
                if any(self.titulo(slug).plataforma == "vod" for slug in u["en_curso"]):
                    tipos["serie"] *= 2
                tipos.pop(anterior, None)
                tipo = self.elegir(list(tipos), list(tipos.values()))
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
                anterior = None
                if not visita:
                    continue
                for i, s in enumerate(visita):
                    k += 1
                    s["sesion"] = f"{u['usuario']}-{k:03d}"
                    s["inicio"] = round(inicio, 3) if i == 0 else -1
                    if i == 0:
                        s["pausa"] = 0.0
                    elif self.instantaneo:
                        s["pausa"] = INSTANTE
                    if self.instantaneo:
                        s["seg_inicio"], s["n_segs"] = 0, 1
                    s["pausa"] = round(s["pausa"], 3)
                    self.filas.append(s)
                libre = inicio + self.duracion(visita) + 60
                if not self.solo_episodios and self.rng.random() < P_SIGUE:
                    anterior = tipo
        return self.filas

    def rng_poisson(self, lam):
        # Knuth (lam pequeño)
        l, k, p = math.exp(-lam), 0, 1.0
        while True:
            p *= self.rng.random()
            if p <= l:
                return k
            k += 1

    def una_casa_por_barrio(self):
        """Se queda con un usuario por barrio, que será la máquina de ese barrio: el que
        más veces sigue con el episodio siguiente de lo que estaba viendo (episodio tras
        episodio de la misma serie)."""
        seguidos, ultimo = Counter(), {}
        for s in self.filas:
            if s["tipo"] == "miniatura" or s["condicion"]:
                continue
            if s["categoria"] == "serie" and ultimo.get(s["usuario"]) == s["titulo"]:
                seguidos[s["usuario"]] += 1
            ultimo[s["usuario"]] = s["titulo"] if s["categoria"] == "serie" else None
        elegidos = {}
        for u in self.usuarios:
            actual = elegidos.get(u["barrio"])
            if actual is None or seguidos[u["usuario"]] > seguidos[actual["usuario"]]:
                elegidos[u["barrio"]] = u
        ids = {u["usuario"] for u in elegidos.values()}
        self.usuarios = [u for u in self.usuarios if u["usuario"] in ids]
        self.filas = [s for s in self.filas if s["usuario"] in ids]

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
