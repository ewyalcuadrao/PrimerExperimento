"""Catálogo de contenidos de la red de vídeo.

Tres plataformas, cada una con su servidor de origen:
    /vod   plataforma de pago: series, películas y documentales
    /rtve  RTVE Play: series y documentales de RTVE y sus canales en directo
    /yt    vídeos subidos por usuarios: canales temáticos y shorts

Los títulos son reales; temporadas, episodios y duraciones son aproximados a los
reales. Los canales de /yt son inventados (no corresponden a personas reales).

Nombres NDN de cada contenido (la base; el reproductor añade /manifest o
/<calidad>/seg=N, el servidor sirve también /miniatura):
    /vod/serie/<slug>/t<temporada>/e<episodio>
    /vod/peli/<slug>
    /vod/doc/<slug>/t<temporada>/e<episodio>   (documental en episodios)
    /vod/doc/<slug>                            (documental de una pieza)
    /rtve/serie/<slug>/t<temporada>/e<episodio>
    /rtve/doc/<slug>/t<temporada>/e<episodio>
    /rtve/directo/<canal>
    /yt/<canal>/<id de vídeo>
    /yt/shorts/<id de vídeo>

Cada temporada (o película, o canal) es una aplicación del servidor de origen. Lo
que no tiene aplicación (títulos retirados, temporadas que no existen, canales
borrados) recibe Nack NoRoute en el origen, que sí anuncia el prefijo de la plataforma.
"""

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
    retirado: bool = False   # ya no está en el catálogo (licencia vencida)
    alias: tuple = ()        # otros nombres con los que la gente lo busca (título original...)
    estreno: dict = field(default_factory=dict)  # {temporada: hora de estreno} dentro del día simulado

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

TITULOS = [
    # ---------- /vod: series ----------
    serie("vod", "la-casa-de-papel", "La casa de papel", [9, 6, 8, 8, 10], 50, 8, ADULTO,
          alias=("money-heist",)),
    serie("vod", "stranger-things", "Stranger Things", [8, 9, 8, 9, 8], 55, 9, JOVEN),
    serie("vod", "miercoles", "Miércoles", [8, 8, 8], 50, 7, JOVEN, alias=("wednesday",),
          estreno={3: 9.0}),  # estreno simulado de la 3ª temporada a las 9:00
    serie("vod", "el-juego-del-calamar", "El juego del calamar", [9, 7, 6], 55, 8, JOVEN,
          alias=("squid-game",)),
    serie("vod", "breaking-bad", "Breaking Bad", [7, 13, 13, 13, 16], 47, 6, ADULTO),
    serie("vod", "juego-de-tronos", "Juego de tronos", [10, 10, 10, 10, 10, 10, 7, 6], 57, 6, ADULTO,
          alias=("game-of-thrones",)),
    serie("vod", "la-casa-del-dragon", "La casa del dragón", [10, 8], 60, 5, ADULTO,
          alias=("house-of-the-dragon",)),
    serie("vod", "elite", "Élite", [8] * 8, 50, 5, JOVEN),
    serie("vod", "dark", "Dark", [10, 8, 8], 55, 4, ADULTO),
    serie("vod", "the-crown", "The Crown", [10] * 6, 58, 3, MADURO),
    serie("vod", "peaky-blinders", "Peaky Blinders", [6] * 6, 58, 4, ADULTO),
    serie("vod", "the-last-of-us", "The Last of Us", [9, 7], 55, 5, JOVEN),
    serie("vod", "los-bridgerton", "Los Bridgerton", [8, 8, 8], 60, 5, ADULTO,
          alias=("bridgerton",)),
    serie("vod", "merli", "Merlí", [13, 13, 14], 50, 2, ADULTO),
    serie("vod", "los-simpson", "Los Simpson", [22] * 35, 22, 6, TODOS, alias=("the-simpsons",)),
    serie("vod", "la-que-se-avecina", "La que se avecina", [14] * 14, 70, 5, MADURO),
    serie("vod", "aqui-no-hay-quien-viva", "Aquí no hay quien viva", [14, 16, 18, 20, 22], 70, 3, MADURO),
    serie("vod", "friends", "Friends", [24, 24, 25, 24, 24, 25, 24, 24, 24, 18], 22, 6, ADULTO,
          intro=True, retirado=True),
    serie("vod", "the-office", "The Office", [6, 22, 25, 19, 28, 26, 26, 24, 25], 22, 4, ADULTO,
          retirado=True),
    # ---------- /vod: películas ----------
    peli("titanic", "Titanic", 195, 5, MADURO),
    peli("avatar", "Avatar", 162, 4, FAMILIA),
    peli("interstellar", "Interstellar", 169, 5, ADULTO),
    peli("origen", "Origen", 148, 4, ADULTO, alias=("inception",)),
    peli("el-caballero-oscuro", "El caballero oscuro", 152, 4, ADULTO, alias=("the-dark-knight",)),
    peli("parasitos", "Parásitos", 132, 3, ADULTO),
    peli("dune", "Dune", 155, 4, ADULTO),
    peli("dune-parte-dos", "Dune: parte dos", 166, 5, ADULTO),
    peli("oppenheimer", "Oppenheimer", 180, 5, ADULTO),
    peli("barbie", "Barbie", 114, 5, JOVEN),
    peli("la-sociedad-de-la-nieve", "La sociedad de la nieve", 144, 6, TODOS),
    peli("el-irlandes", "El irlandés", 209, 2, MADURO),
    peli("roma", "Roma", 135, 2, MADURO),
    peli("coco", "Coco", 105, 5, FAMILIA),
    peli("frozen", "Frozen", 102, 5, FAMILIA),
    peli("frozen-2", "Frozen 2", 103, 4, FAMILIA),
    peli("toy-story", "Toy Story", 81, 3, FAMILIA),
    peli("shrek", "Shrek", 90, 4, FAMILIA),
    peli("el-rey-leon", "El rey león", 88, 4, FAMILIA),
    peli("el-laberinto-del-fauno", "El laberinto del fauno", 118, 3, ADULTO),
    peli("ocho-apellidos-vascos", "Ocho apellidos vascos", 98, 4, MADURO),
    peli("campeones", "Campeones", 124, 3, TODOS),
    peli("mar-adentro", "Mar adentro", 125, 2, MADURO),
    peli("el-hoyo", "El hoyo", 94, 3, JOVEN),
    peli("lo-imposible", "Lo imposible", 114, 3, MADURO),
    peli("el-padrino", "El padrino", 175, 3, MADURO, alias=("the-godfather",)),
    peli("pulp-fiction", "Pulp Fiction", 154, 3, ADULTO),
    peli("vengadores-endgame", "Vengadores: Endgame", 181, 5, JOVEN, alias=("avengers-endgame",)),
    peli("matrix", "Matrix", 136, 3, ADULTO, retirado=True),
    peli("torrente-el-brazo-tonto-de-la-ley", "Torrente, el brazo tonto de la ley", 97, 2, MADURO,
         retirado=True),
    # ---------- /vod: documentales ----------
    doc("vod", "nuestro-planeta", "Nuestro planeta", [8, 4], 50, 4),
    doc("vod", "the-last-dance", "The Last Dance", [10], 50, 3, (1.2, 1.3, 1, 0.6)),
    doc("vod", "making-a-murderer", "Making a Murderer", [10, 10], 60, 2),
    doc("vod", "el-dilema-de-las-redes", "El dilema de las redes", [], 94, 2,
        alias=("the-social-dilemma",)),
    doc("vod", "lo-que-el-pulpo-me-enseno", "Lo que el pulpo me enseñó", [], 85, 2,
        alias=("my-octopus-teacher",)),
    doc("vod", "cosmos", "Cosmos", [13], 44, 2),
    # ---------- /rtve ----------
    serie("rtve", "cuentame-como-paso", "Cuéntame cómo pasó", [16] * 23, 70, 5, (0.2, 0.7, 1.8, 2.2)),
    serie("rtve", "el-ministerio-del-tiempo", "El Ministerio del Tiempo", [8, 13, 13, 8], 70, 3, ADULTO),
    serie("rtve", "isabel", "Isabel", [13, 13, 13], 70, 2, (0.2, 0.6, 1.6, 2.0)),
    serie("rtve", "aguila-roja", "Águila Roja", [13] * 9, 70, 2, MADURO),
    serie("rtve", "masterchef", "MasterChef", [13] * 12, 150, 4, FAMILIA, intro=False),
    serie("rtve", "la-revuelta", "La Revuelta", [120], 60, 4, (1.6, 1.2, 0.8, 0.5), intro=False),
    serie("rtve", "informe-semanal", "Informe Semanal", [40], 50, 2, (0.2, 0.6, 1.5, 2.0), intro=False),
    doc("rtve", "el-hombre-y-la-tierra", "El hombre y la Tierra", [18, 70, 36], 25, 3),
    doc("rtve", "la-noche-tematica", "La noche temática", [40], 90, 1),
]

# Títulos que la gente busca en la plataforma equivocada: (plataforma donde se busca, título)
PLATAFORMA_EQUIVOCADA = [("rtve", "la-casa-de-papel"), ("vod", "cuentame-como-paso"),
                         ("vod", "el-ministerio-del-tiempo"), ("rtve", "los-simpson"),
                         ("vod", "masterchef"), ("vod", "isabel"), ("vod", "aguila-roja"),
                         ("rtve", "la-que-se-avecina"), ("rtve", "aqui-no-hay-quien-viva"),
                         ("vod", "el-hombre-y-la-tierra"), ("rtve", "nuestro-planeta"),
                         ("rtve", "el-laberinto-del-fauno"), ("rtve", "campeones"),
                         ("rtve", "ocho-apellidos-vascos"), ("rtve", "lo-imposible")]

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
# (temática, minutos medianos de un vídeo, peso por edad, palabras para el nombre del canal)
TEMATICAS = [
    ("gaming", 22, (2.5, 1.0, 0.2, 0.1), ["gameplays", "partidas", "speedruns", "retro"]),
    ("cocina", 12, (0.6, 1.4, 1.6, 1.2), ["recetas", "cocina", "reposteria", "tapas"]),
    ("musica", 4, (2.0, 1.2, 0.8, 0.6), ["videoclips", "directos", "acusticos", "versiones"]),
    ("divulgacion", 15, (1.2, 1.2, 1.0, 0.7), ["ciencia", "historia", "astronomia", "filosofia"]),
    ("deporte", 8, (1.4, 1.4, 1.0, 0.6), ["resumenes", "goles", "baloncesto", "ciclismo"]),
    ("tutoriales", 12, (1.3, 1.4, 0.8, 0.4), ["tutoriales", "bricolaje", "informatica", "linux"]),
    ("vlogs", 15, (2.0, 0.8, 0.3, 0.1), ["viajes", "vlogs", "rutinas", "mochileros"]),
    ("humor", 6, (1.8, 1.0, 0.6, 0.4), ["sketches", "monologos", "parodias", "bromas"]),
    ("noticias", 5, (0.5, 1.0, 1.5, 1.6), ["actualidad", "economia", "politica", "tertulias"]),
    ("podcasts", 60, (1.2, 1.4, 0.8, 0.3), ["podcast", "entrevistas", "charlas", "tertulias"]),
]
COMPLEMENTOS = ["del-norte", "de-barrio", "en-5-minutos", "sin-filtros", "para-todos", "a-fondo",
                "de-la-abuela", "express", "en-casa", "de-madrugada", "con-calma", "low-cost",
                "desde-cero", "y-punto", "al-dia", "de-bolsillo"]
CANALES_POR_TEMATICA = 12
CANALES_BORRADOS = 6     # canales que ya no existen pero siguen enlazados en redes
ALFABETO_ID = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"


@dataclass
class Canal:
    slug: str
    tematica: str
    minutos: float
    afinidad: tuple
    popularidad: float
    videos: list           # ids, del más reciente al más antiguo
    borrado: bool = False


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
    for canal in rng.sample(resultado, CANALES_BORRADOS):
        canal.borrado = True
    return resultado
