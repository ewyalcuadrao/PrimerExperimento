"""Catálogo de contenidos de la red de vídeo.

Tres plataformas, cada una con su servidor de origen:
    /vod   plataforma de pago: series, películas y documentales
    /rtve  RTVE Play: series y documentales de RTVE y sus canales en directo
    /yt    vídeos subidos por usuarios: canales temáticos y shorts

Los títulos son reales. Los de /vod salen de los datasets públicos de IMDb
(catalogo_imdb.json, generado con importar_imdb.py): título en España, títulos
alternativos como alias, temporadas y episodios reales, duración, géneros y votos
como popularidad. Los de RTVE son una lista a mano. Los canales de /yt son
inventados (no corresponden a personas reales).

Nombres NDN de cada contenido (cada uno es un solo Data; el servidor sirve también
<prefijo>/miniatura):
    /vod/serie/<slug>/t<temporada>/e<episodio>
    /vod/peli/<slug>
    /vod/doc/<slug>/t<temporada>/e<episodio>   (documental en episodios)
    /vod/doc/<slug>                            (documental de una pieza)
    /rtve/serie/<slug>/t<temporada>/e<episodio>
    /rtve/doc/<slug>/t<temporada>/e<episodio>
    /rtve/directo/<canal>
    /yt/<canal>/<id de vídeo>
    /yt/shorts/<id de vídeo>

Cada temporada (o película, o canal) es una aplicación del servidor de origen. Todo
el catálogo está publicado: un nombre recibe Nack NoRoute (en el origen, que sí
anuncia el prefijo de la plataforma) solo cuando no coincide con el nombre real de
un contenido que existe. Ver los errores de nombre en humanos.py.
"""

import json
import os
import random
from dataclasses import dataclass, field

# Afinidad de cada franja de edad por un contenido: joven (18-29), adulto (30-49),
# maduro (50-64), mayor (65+). 1 = normal.
EDADES = ["joven", "adulto", "maduro", "mayor"]


@dataclass
class Titulo:
    plataforma: str          # vod | rtve
    tipo: str                # serie | peli | doc
    slug: str
    nombre: str
    temporadas: list = field(default_factory=list)  # nº de episodios por temporada ([] = una pieza)
    minutos: int = 50        # duración de un episodio o de la película
    popularidad: float = 1.0
    afinidad: tuple = (1, 1, 1, 1)
    intro: bool = True       # tiene intro que se puede saltar
    alias: tuple = ()        # otros nombres con los que la gente lo busca (título original, apodo...)
    estreno: dict = field(default_factory=dict)  # {temporada: hora de estreno} dentro del día simulado
    espanola: bool = False   # producción española (se busca también en RTVE)
    generos: tuple = ()      # géneros de IMDb (filas "porque te gusta..." de la portada)

    def prefijo(self, temporada=None):
        base = f"/{self.plataforma}/{self.tipo}/{self.slug}"
        return f"{base}/t{temporada}" if temporada else base

    def base(self, temporada=None, episodio=None):
        if not self.temporadas:
            return self.prefijo()
        return f"{self.prefijo(temporada)}/e{episodio}"


def serie(plataforma, slug, nombre, temporadas, minutos, popularidad, afinidad, **kw):
    return Titulo(plataforma, "serie", slug, nombre, temporadas, minutos, popularidad, afinidad, **kw)


def peli(slug, nombre, minutos, popularidad, afinidad=(1, 1, 1, 1), **kw):
    return Titulo("vod", "peli", slug, nombre, [], minutos, popularidad, afinidad, intro=False, **kw)


def doc(plataforma, slug, nombre, temporadas, minutos, popularidad, afinidad=(0.6, 1, 1.3, 1.4), **kw):
    return Titulo(plataforma, "doc", slug, nombre, temporadas, minutos, popularidad, afinidad, **kw)


JOVEN = (2.0, 1.0, 0.4, 0.2)
ADULTO = (1.0, 1.5, 1.0, 0.5)
MADURO = (0.4, 1.0, 1.6, 1.4)
TODOS = (1, 1, 1, 1)
FAMILIA = (0.8, 1.6, 1.0, 0.8)

def afinidad(generos, anio):
    """Afinidad por edad de un título de IMDb según sus géneros y su año."""
    if {"Animation", "Family"} & set(generos):
        return FAMILIA
    if "Documentary" in generos:
        return (0.6, 1, 1.3, 1.4)
    if anio and anio < 1990:
        return MADURO
    if {"Horror", "Sci-Fi", "Fantasy"} & set(generos) and (anio or 0) >= 2010:
        return JOVEN
    if {"History", "War", "Biography", "Western"} & set(generos):
        return MADURO
    return ADULTO


def desde_imdb(t):
    """Título de /vod a partir de una entrada de catalogo_imdb.json (ver importar_imdb.py).
    Popularidad: votos en IMDb, con las producciones españolas multiplicadas por 3
    (IMDb está dominado por público de EE. UU.)."""
    popularidad = (t["votos"] / 100_000) ** 0.7 * (3 if t["espanola"] else 1)
    minutos = t["minutos"] or (45 if t["temporadas"] else 100)
    tipo = t["tipo"]
    return Titulo("vod", tipo, t["slug"], t["nombre"], t["temporadas"],
                  min(minutos, 240), popularidad, afinidad(t["generos"], t["anio"]),
                  intro=tipo != "peli", alias=tuple(t["alias"]), espanola=t["espanola"],
                  generos=tuple(t["generos"]))


# ---------- /rtve: catálogo real de RTVE Play (a mano) ----------
RTVE = [
    serie("rtve", "cuentame-como-paso", "Cuéntame cómo pasó", [16] * 23, 70, 5, (0.2, 0.7, 1.8, 2.2),
          alias=("cuentame",)),
    serie("rtve", "el-ministerio-del-tiempo", "El Ministerio del Tiempo", [8, 13, 13, 8], 70, 3, ADULTO,
          alias=("ministerio-del-tiempo",)),
    serie("rtve", "isabel", "Isabel", [13, 13, 13], 70, 2, (0.2, 0.6, 1.6, 2.0)),
    serie("rtve", "aguila-roja", "Águila Roja", [13] * 9, 70, 2, MADURO),
    serie("rtve", "masterchef", "MasterChef", [13] * 12, 150, 4, FAMILIA, intro=False,
          alias=("masterchef-espana", "master-chef")),
    serie("rtve", "la-revuelta", "La Revuelta", [120], 60, 4, (1.6, 1.2, 0.8, 0.5), intro=False),
    serie("rtve", "informe-semanal", "Informe Semanal", [40], 50, 2, (0.2, 0.6, 1.5, 2.0), intro=False),
    serie("rtve", "la-promesa", "La Promesa", [250, 250, 250], 55, 4, (0.1, 0.5, 1.6, 2.4)),
    serie("rtve", "valle-salvaje", "Valle Salvaje", [200, 120], 55, 3, (0.1, 0.5, 1.6, 2.4)),
    serie("rtve", "servir-y-proteger", "Servir y proteger", [250] * 5, 50, 2, (0.1, 0.5, 1.6, 2.2)),
    serie("rtve", "acacias-38", "Acacias 38", [250] * 5, 50, 2, (0.1, 0.5, 1.6, 2.2),
          alias=("acacias",)),
    serie("rtve", "amar-en-tiempos-revueltos", "Amar en tiempos revueltos", [200] * 7, 50, 2,
          (0.1, 0.4, 1.5, 2.4)),
    serie("rtve", "estoy-vivo", "Estoy vivo", [13, 13, 13, 13], 70, 2, ADULTO),
    serie("rtve", "olmos-y-robles", "Olmos y Robles", [8, 8], 70, 1, MADURO),
    serie("rtve", "los-misterios-de-laura", "Los misterios de Laura", [13, 13, 13], 70, 2, MADURO),
    serie("rtve", "victor-ros", "Víctor Ros", [6, 8], 70, 1, MADURO),
    serie("rtve", "carlos-rey-emperador", "Carlos, rey emperador", [17], 70, 2, MADURO,
          alias=("carlos-v", "carlos-rey-y-emperador")),
    serie("rtve", "malaka", "Malaka", [8], 60, 1, ADULTO),
    serie("rtve", "hit", "HIT", [10, 8], 60, 1, ADULTO),
    serie("rtve", "la-caza", "La caza", [8, 8, 8, 8], 70, 2, ADULTO, alias=("la-caza-monteperdido",)),
    serie("rtve", "verano-azul", "Verano azul", [19], 60, 2, (0.3, 0.8, 1.8, 2.2)),
    serie("rtve", "curro-jimenez", "Curro Jiménez", [13, 14, 13], 55, 1, (0.1, 0.3, 1.5, 2.4)),
    serie("rtve", "fortunata-y-jacinta", "Fortunata y Jacinta", [10], 60, 1, (0.1, 0.3, 1.5, 2.4)),
    serie("rtve", "historias-para-no-dormir", "Historias para no dormir", [12, 13], 50, 1, MADURO),
    serie("rtve", "operacion-triunfo", "Operación Triunfo", [20, 20, 20, 20], 120, 3, JOVEN, intro=False,
          alias=("ot", "operacion-triunfo-2001")),
    serie("rtve", "masterchef-junior", "MasterChef Junior", [8] * 10, 120, 2, FAMILIA, intro=False),
    serie("rtve", "masterchef-celebrity", "MasterChef Celebrity", [10] * 9, 150, 3, FAMILIA, intro=False),
    serie("rtve", "saber-y-ganar", "Saber y ganar", [250], 30, 2, (0.1, 0.4, 1.6, 2.6), intro=False),
    serie("rtve", "aqui-la-tierra", "Aquí la Tierra", [200], 30, 1, (0.1, 0.4, 1.6, 2.4), intro=False),
    serie("rtve", "espana-directo", "España directo", [200], 60, 1, (0.1, 0.4, 1.6, 2.4), intro=False),
    serie("rtve", "comando-actualidad", "Comando Actualidad", [40] * 3, 60, 2, MADURO, intro=False),
    serie("rtve", "espanoles-en-el-mundo", "Españoles en el mundo", [40] * 4, 60, 2, MADURO, intro=False),
    serie("rtve", "cocina-con-sergio", "Cocina con Sergio", [100], 15, 1, MADURO, intro=False),
    serie("rtve", "pocoyo", "Pocoyó", [52, 52, 52, 52], 7, 3, (0.1, 1.8, 0.4, 0.6), alias=("pocoyo-clan",)),
    serie("rtve", "los-lunnis", "Los Lunnis", [52, 52], 15, 2, (0.1, 1.6, 0.4, 0.6)),
    serie("rtve", "telediario", "Telediario", [365], 45, 2, (0.2, 0.6, 1.6, 2.2), intro=False),
    doc("rtve", "el-hombre-y-la-tierra", "El hombre y la Tierra", [18, 70, 36], 25, 3),
    doc("rtve", "la-noche-tematica", "La noche temática", [40], 90, 1),
    doc("rtve", "grandes-documentales", "Grandes documentales", [60, 60], 55, 2),
    doc("rtve", "paraisos-cercanos", "Paraísos cercanos", [30], 55, 1),
    doc("rtve", "somos-documentales", "Somos documentales", [40], 55, 1),
    doc("rtve", "imprescindibles", "Imprescindibles", [30, 30], 60, 1, alias=("los-imprescindibles",)),
    doc("rtve", "documentos-tv", "Documentos TV", [40, 40], 55, 1),
    doc("rtve", "la-espana-de-los-reyes", "La España de los reyes", [], 90, 1, alias=("espana-de-los-reyes",)),
]



# ---------- /vod: catálogo de IMDb (catalogo_imdb.json, ver importar_imdb.py) ----------
_IMDB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "catalogo_imdb.json")
if not os.path.exists(_IMDB):
    raise SystemExit(f"Falta {_IMDB}: ejecuta python3 proyecto/video/importar_imdb.py")
_rtve = {t.slug for t in RTVE}
with open(_IMDB, encoding="utf-8") as f:
    VOD = [desde_imdb(t) for t in json.load(f) if t["slug"] not in _rtve]
ESTRENOS = {"miercoles": {3: 9.0}}  # estreno simulado de la 3ª temporada a las 9:00
for t in VOD:
    t.estreno = {k: h for k, h in ESTRENOS.get(t.slug, {}).items() if k <= len(t.temporadas)}

TITULOS = VOD + RTVE

# Títulos que la gente busca en la plataforma equivocada: (plataforma donde se busca, título).
# Lo de RTVE se busca en /vod y las producciones españolas de /vod, en RTVE.
PLATAFORMA_EQUIVOCADA = ([("vod", t.slug) for t in RTVE]
                         + [("rtve", t.slug) for t in VOD if t.espanola])

# Documentales que la gente busca en la sección equivocada de su plataforma:
# (sección donde se busca, título). Los de una pieza se buscan como película y los
# de varios episodios, como serie.
CATEGORIA_EQUIVOCADA = [("serie" if t.temporadas else "peli", t.slug) for t in TITULOS if t.tipo == "doc"]

# ---------- Canales en directo de RTVE ----------
# Programación: (canal, hora inicio, hora fin, programa, peso de audiencia por edad)
DIRECTOS = ["la1", "la2", "24h", "teledeporte", "clan"]
PARRILLA = [
    ("la1", 15.0, 15.75, "telediario-1", (0.3, 0.8, 1.6, 2.4)),
    ("la1", 21.0, 21.75, "telediario-2", (0.3, 0.8, 1.6, 2.4)),
    ("la1", 22.0, 24.0, "prime-time", (0.6, 1.0, 1.4, 1.6)),
    ("teledeporte", 21.0, 23.0, "partido-de-futbol", (1.6, 1.8, 1.2, 0.8)),
    ("24h", 0.0, 24.0, "noticias", (0.3, 0.6, 1.2, 1.5)),
    ("clan", 8.0, 10.0, "infantil", (0.1, 1.5, 0.3, 0.6)),
    ("clan", 17.0, 20.0, "infantil", (0.1, 1.5, 0.3, 0.6)),
    ("la2", 18.0, 23.0, "documentales", (0.2, 0.6, 1.4, 1.8)),
]

# ---------- Canales de vídeo de usuarios (/yt) ----------
# Temáticas a partir de las categorías de vídeo de la API de YouTube (Gaming, Music,
# Sports, Autos & Vehicles, Pets & Animals, Travel & Events, Howto & Style, Education,
# Science & Technology, Comedy, News & Politics, Film & Animation, People & Blogs),
# desdobladas en las que más se ven en España.
# (temática, minutos medianos de un vídeo, peso por edad, palabras para el nombre del canal)
TEMATICAS = [
    ("gaming", 22, (2.5, 1.0, 0.2, 0.1), ["gameplays", "partidas", "speedruns", "retro", "minecraft",
                                          "fortnite", "rol", "indies"]),
    ("cocina", 12, (0.6, 1.4, 1.6, 1.2), ["recetas", "cocina", "reposteria", "tapas", "guisos",
                                          "panaderia", "cocina-vegana", "barbacoa"]),
    ("musica", 4, (2.0, 1.2, 0.8, 0.6), ["videoclips", "directos", "acusticos", "versiones", "flamenco",
                                         "reggaeton", "rock", "karaoke"]),
    ("divulgacion", 15, (1.2, 1.2, 1.0, 0.7), ["ciencia", "historia", "astronomia", "filosofia",
                                               "biologia", "matematicas", "geografia", "arqueologia"]),
    ("deporte", 8, (1.4, 1.4, 1.0, 0.6), ["resumenes", "goles", "baloncesto", "ciclismo", "tenis",
                                          "padel", "running", "formula-1"]),
    ("tutoriales", 12, (1.3, 1.4, 0.8, 0.4), ["tutoriales", "bricolaje", "informatica", "linux",
                                              "excel", "programacion", "fotografia", "reparaciones"]),
    ("vlogs", 15, (2.0, 0.8, 0.3, 0.1), ["vlogs", "rutinas", "vida-diaria", "familia", "estudiantes",
                                         "erasmus", "mudanzas", "retos"]),
    ("humor", 6, (1.8, 1.0, 0.6, 0.4), ["sketches", "monologos", "parodias", "bromas", "doblajes",
                                        "memes", "imitaciones", "chistes"]),
    ("noticias", 5, (0.5, 1.0, 1.5, 1.6), ["actualidad", "economia", "politica", "tertulias",
                                           "internacional", "analisis", "sucesos", "opinion"]),
    ("podcasts", 60, (1.2, 1.4, 0.8, 0.3), ["podcast", "entrevistas", "charlas", "conversaciones",
                                            "true-crime", "misterio", "debates", "historias"]),
    ("motor", 14, (1.4, 1.5, 1.0, 0.4), ["coches", "motos", "pruebas", "mecanica", "clasicos",
                                         "electricos", "rally", "camiones"]),
    ("mascotas", 6, (1.2, 1.3, 1.2, 1.0), ["perros", "gatos", "animales", "adiestramiento",
                                           "acuarios", "aves", "granja", "fauna"]),
    ("viajes", 16, (1.4, 1.2, 1.0, 0.7), ["viajes", "mochileros", "rutas", "escapadas", "furgoneta",
                                          "pueblos", "camping", "senderismo"]),
    ("belleza", 10, (2.0, 1.2, 0.5, 0.2), ["maquillaje", "moda", "peinados", "cuidado-de-la-piel",
                                           "estilo", "unas", "perfumes", "outfits"]),
    ("fitness", 20, (1.8, 1.4, 0.7, 0.3), ["fitness", "yoga", "pilates", "gimnasio", "estiramientos",
                                           "calistenia", "nutricion", "crossfit"]),
    ("tecnologia", 12, (1.6, 1.4, 0.6, 0.3), ["moviles", "unboxing", "gadgets", "analisis-tech",
                                              "ordenadores", "domotica", "inteligencia-artificial",
                                              "consolas"]),
    ("cine", 14, (1.6, 1.3, 0.8, 0.4), ["resenas", "criticas", "trailers", "curiosidades-de-cine",
                                        "series", "anime", "comics", "teorias"]),
    ("finanzas", 15, (1.2, 1.5, 1.0, 0.5), ["finanzas", "inversion", "ahorro", "bolsa", "cripto",
                                            "emprendedores", "hipotecas", "impuestos"]),
    ("educacion", 12, (1.6, 1.0, 0.6, 0.4), ["idiomas", "ingles", "oposiciones", "selectividad",
                                             "lengua", "fisica", "quimica", "dibujo"]),
    ("hogar", 10, (0.5, 1.4, 1.6, 1.3), ["jardineria", "huerto", "manualidades", "decoracion",
                                         "limpieza", "costura", "ganchillo", "plantas"]),
]
COMPLEMENTOS = ["del-norte", "de-barrio", "en-5-minutos", "sin-filtros", "para-todos", "a-fondo",
                "de-la-abuela", "express", "en-casa", "de-madrugada", "con-calma", "low-cost",
                "desde-cero", "y-punto", "al-dia", "de-bolsillo", "del-sur", "con-ana", "con-pablo",
                "para-dummies", "facil", "en-espanol", "pro", "de-verdad", "hoy", "y-mas",
                "de-andalucia", "de-galicia", "made-in-spain", "sin-rodeos", "a-lo-grande", "en-vivo"]
CANALES_POR_TEMATICA = 25
CANALES_RENOMBRADOS = 15  # canales que cambiaron de nombre; en redes siguen enlazados con el antiguo
ALFABETO_ID = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"


@dataclass
class Canal:
    slug: str
    tematica: str
    minutos: float
    afinidad: tuple
    popularidad: float
    videos: list           # ids, del más reciente al más antiguo
    antiguo: str = ""      # nombre anterior, si el canal se renombró


def id_video(rng):
    return "".join(rng.choice(ALFABETO_ID) for _ in range(11))


def canales(seed=7):
    """Canales inventados con sus vídeos; deterministas para una semilla."""
    rng = random.Random(seed)
    resultado, usados = [], set()
    for tematica, minutos, afinidad, palabras in TEMATICAS:
        for _ in range(CANALES_POR_TEMATICA):
            while True:
                slug = f"{rng.choice(palabras)}-{rng.choice(COMPLEMENTOS)}"
                if slug not in usados:
                    usados.add(slug)
                    break
            n = int(rng.lognormvariate(4.3, 0.8)) + 10       # ~75 vídeos de mediana
            popularidad = rng.paretovariate(1.2)               # pocos canales muy grandes
            resultado.append(Canal(slug, tematica, minutos, afinidad, popularidad,
                                   [id_video(rng) for _ in range(n)]))
    for canal in rng.sample(resultado, CANALES_RENOMBRADOS):
        canal.antiguo = canal.slug
        canal.slug = renombrar(rng, canal.slug, usados)
        usados.add(canal.slug)
    return resultado


def renombrar(rng, slug, usados):
    """Nombre nuevo de un canal, como los cambios de nombre reales: añadir un sufijo,
    juntar las palabras o cambiar el complemento."""
    palabra = slug.split("-")[0]
    while True:
        nuevo = rng.choice([slug + "-oficial", slug + "-tv", slug.replace("-", ""),
                            f"{palabra}-{rng.choice(COMPLEMENTOS)}"])
        if nuevo not in usados:
            return nuevo
