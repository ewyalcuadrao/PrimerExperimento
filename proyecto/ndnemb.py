"""Nombres NDN: normalización, similitud entre nombres y nombres conocidos.

Lo usan construir_embeddings.py y la simulación, así que ambos tratan los nombres
exactamente igual.

    from ndnemb import Modelo
    m = Modelo("proyecto/modelos/iteracion1")
    m.parecidos("/ndn/edu/ucla/lectrues/cs118", k=3)  # [(nombre válido, similitud), ...]
    Modelo(carpeta, metodo="fasttext")                 # misma consulta con FastText
    Modelo(carpeta, "fasttext", peso_comunes=0.2)      # las componentes comunes cuentan un 20 %

Hay tres medidas de similitud (parámetro metodo de Modelo):

item2vec
El vector de un nombre lo aprende item2vec.py a partir de las sesiones que ven los
routers de acceso (nombres pedidos juntos quedan cerca). La similitud es el coseno
entre los vectores de los dos nombres. Solo tienen vector los nombres que salieron en
los logs de entrenamiento.


coseno (por defecto)

La similitud entre dos nombres es el coseno entre sus recuentos de trigramas de
caracteres. Cada componente se rodea de < y > para marcar sus bordes, así
/ndn/edu/ucla/lecture/cs118 se cuenta como <nd ndn dn> <ed edu du> ... <le lec
ect ctu tur ure re> <cs cs1 s11 118 18>. El coseno vale 1 si los dos nombres tienen
los mismos trigramas en la misma proporción y 0 si no comparten ninguno: mide qué
parte del texto comparten, sin ningún modelo entrenado.

fasttext
Se comparan sobre todo las componentes en las que los dos nombres se diferencian.
Las que están en los dos (vod, serie, t1, e1...) no dicen cuál de los dos nombres es
el bueno, y si cuentan igual que las demás todos los nombres se parecen (0,98). Cada
nombre se convierte en la suma de los vectores FastText de sus componentes (modelo
nombres.ft de construir_embeddings.py) ponderada por IDF, con las comunes
multiplicadas por peso_comunes (0 = no cuentan), y la similitud es el coseno entre
las dos sumas:
    /vod/serie/la-casa-de-papl/t1/e1  frente a  /vod/serie/la-casa-de-papel/t1/e1
    con peso_comunes=0 compara solo  la-casa-de-papl  con  la-casa-de-papel
    /rtve/serie/la-casa-de-papel/t1/e1  frente a  /vod/serie/la-casa-de-papel/t1/e1
    compara solo  rtve  con  vod
Si los dos nombres son iguales la similitud es 1; si uno no tiene ninguna componente
propia (el otro solo le añade componentes), 0. FastText calcula el vector de una
componente nunca vista a partir de sus n-gramas de caracteres, así que funciona
también con nombres mal escritos.
"""

import csv
import math
import os
from collections import Counter

import numpy as np


def normalizar(nombre):
    """Quita el número de secuencia o de segmento final (seq=N, seg=N): es el trozo,
    no el contenido."""
    base, _, ultima = nombre.rpartition("/")
    return base if ultima.startswith(("seq=", "seg=")) else nombre


def componentes(nombre):
    return [c for c in nombre.split("/") if c]


def trigramas(nombre):
    """Recuento de trigramas de caracteres de cada componente, con < y > en los bordes."""
    t = Counter()
    for c in componentes(normalizar(nombre)):
        s = f"<{c}>"
        t.update(s[i:i + 3] for i in range(len(s) - 2))
    return t


def coseno(a, b):
    """Similitud del coseno entre dos recuentos (Counter)."""
    if len(a) > len(b):
        a, b = b, a
    producto = sum(n * b[k] for k, n in a.items())
    normas = math.sqrt(sum(n * n for n in a.values())) * math.sqrt(sum(n * n for n in b.values()))
    return producto / normas if normas else 0.0


def calcular_idf(nombres):
    """Peso de cada componente: las que aparecen en muchos nombres (ndn, edu) pesan poco."""
    df = {}
    for n in nombres:
        for c in set(componentes(n)):
            df[c] = df.get(c, 0) + 1
    return {c: math.log((1 + len(nombres)) / (1 + d)) + 1 for c, d in df.items()}


def vector_forma(wv, idf, nombre, idf_desconocida):
    """Embedding FastText del nombre (construir_embeddings.py): media de sus componentes
    ponderada por IDF. No se usa para decidir el reenvío."""
    comps = componentes(normalizar(nombre))
    pesos = np.array([idf.get(c, idf_desconocida) for c in comps])
    return pesos @ np.array([wv[c] for c in comps]) / pesos.sum()


class Modelo:
    """Nombres conocidos de una ejecución (nombres.csv de construir_embeddings.py)."""

    def __init__(self, carpeta, metodo="coseno", peso_comunes=0.0):
        self.metodo = metodo
        if metodo == "item2vec":
            # Modelo de item2vec.py: vector de cada nombre según las sesiones en que aparece
            from gensim.models import Word2Vec
            self.wv = Word2Vec.load(os.path.join(carpeta, "item2vec.w2v")).wv
            self.nombres = list(self.wv.index_to_key)
            with open(os.path.join(carpeta, "validos.txt")) as f:
                self.validos = [n for n in f.read().split() if n in self.wv.key_to_index]
            self._grupos = {}
            return
        with open(os.path.join(carpeta, "nombres.csv"), newline="") as f:
            self.stats = {r["nombre"]: r for r in csv.DictReader(f)}
        self.nombres = list(self.stats)
        # Válidos: los que recibieron Data (por la red o desde una caché)
        self.validos = [n for n in self.nombres
                        if int(self.stats[n]["data"]) + int(self.stats[n]["cshit"]) > 0]
        self.peso_comunes = peso_comunes
        self._grupos = {}
        self._vectores = {}
        if metodo == "coseno":
            self.trigramas = {n: trigramas(n) for n in self.nombres}
        elif metodo == "fasttext":
            from gensim.models import FastText
            self.wv = FastText.load(os.path.join(carpeta, "nombres.ft")).wv
            npz = np.load(os.path.join(carpeta, "nombres.npz"))
            self.idf = dict(zip(npz["idf_componentes"].tolist(), npz["idf_pesos"].tolist()))
            self.idf_desconocida = max(self.idf.values())
        else:
            raise ValueError(f"método de similitud desconocido: {metodo}")

    def _candidatos(self, solo_validos, miniaturas):
        """Nombres candidatos, calculados una vez."""
        clave = (solo_validos, miniaturas)
        if clave not in self._grupos:
            self._grupos[clave] = [n for n in (self.validos if solo_validos else self.nombres)
                                   if miniaturas is None or (n.rsplit("/", 1)[-1] == "miniatura") == miniaturas]
        return self._grupos[clave]

    def _vector(self, componente):
        """Vector FastText de una componente multiplicado por su IDF."""
        if componente not in self._vectores:
            self._vectores[componente] = self.idf.get(componente, self.idf_desconocida) * self.wv[componente]
        return self._vectores[componente]

    def similitud_fasttext(self, a, b):
        """Coseno entre dos nombres dando peso_comunes a las componentes que están en los dos."""
        ca, cb = set(componentes(normalizar(a))), set(componentes(normalizar(b)))
        if ca == cb:
            return 1.0
        comunes = sum((self._vector(c) for c in ca & cb), np.zeros(self.wv.vector_size))
        va = sum((self._vector(c) for c in ca - cb), np.zeros(self.wv.vector_size)) + self.peso_comunes * comunes
        vb = sum((self._vector(c) for c in cb - ca), np.zeros(self.wv.vector_size)) + self.peso_comunes * comunes
        normas = np.linalg.norm(va) * np.linalg.norm(vb)
        return float(va @ vb / normas) if normas else 0.0

    def parecidos(self, nombre, k=3, solo_validos=True, miniaturas=None):
        """Los k nombres conocidos más parecidos; por defecto solo los que reciben Data.
        Con miniaturas=True solo se comparan miniaturas (nombres que acaban en
        /miniatura); con False, solo los que no."""
        candidatos = self._candidatos(solo_validos, miniaturas)
        if not candidatos:
            return []
        if self.metodo == "item2vec":
            # Coseno entre vectores; un nombre que no salió en ninguna sesión no tiene vector
            if nombre not in self.wv.key_to_index:
                return []
            sims = [float(self.wv.similarity(nombre, n)) for n in candidatos]
        elif self.metodo == "coseno":
            t = trigramas(nombre)
            sims = [coseno(t, self.trigramas[n]) for n in candidatos]
        else:
            sims = [self.similitud_fasttext(nombre, n) for n in candidatos]
        orden = np.argsort(sims)[::-1][:k]
        return [(candidatos[i], float(sims[i])) for i in orden]
