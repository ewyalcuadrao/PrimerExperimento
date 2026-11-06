# PrimerExperimento

Simulación con ndnSIM 2.9 de una red de vídeo y del reenvío por similitud de nombres NDN.
El tráfico es sintético: sus parámetros son supuestos del escenario.

- `ndn.sh`, `docker/`: entorno de ejecución (el contenedor monta esta carpeta en `/work`).
- `proyecto/`: simulaciones, generador de usuarios (`video/humanos.py`), modelos y análisis.
- `proyecto/logs_casas.py`: logs de los Interest de cada casa sin simular la red (episodios de 1 ms,
  90 días): capturas pcapng con tramas Ethernet 0x8624 y el Interest en TLV (`proyecto/ndntlv.py`),
  decodificadas a CSV, y `etiquetas.csv` con el resultado de cada Interest para evaluar.
- `proyecto/informes/red_t0/`: genera la página de la red en t = 0 a partir de una simulación o de los logs.
- `proyecto/informes/trazas/`: genera la página que explica el formato de las trazas según las especificaciones de NDN.

`red_video.py` simula 3 días con 10 casas, cada una la máquina de un solo usuario que
solo ve episodios de series. Sus resultados se guardan en `proyecto/resultados/` (no
están en el repositorio: la última, `video_v19`, ocupa 1,6 GB).

## Lo que no está en el repositorio

Entornos de Python, `ndnSIM/` (5 GB), `proyecto/datos/` (datasets de IMDb, 776 MB, y de
YouTube Trending, 45 MB) y `proyecto/resultados/` (resultados de las simulaciones).

### ndnSIM

Versiones usadas: ns-3 `1b3bab9b6`, ndnSIM `90d5039`, NFD `361b381b`, ndn-cxx `2b51e579`, pybindgen `a619e22`.

```bash
mkdir ndnSIM && cd ndnSIM
git clone https://github.com/named-data-ndnSIM/ns-3-dev.git ns-3
git clone https://github.com/named-data-ndnSIM/pybindgen.git pybindgen
git -C ns-3 checkout 1b3bab9b6
git -C pybindgen checkout a619e22
git clone https://github.com/named-data-ndnSIM/ndnSIM.git ns-3/src/ndnSIM
git -C ns-3/src/ndnSIM checkout 90d5039
git -C ns-3/src/ndnSIM submodule update --init
git -C ns-3/src/ndnSIM/NFD apply ../../../../../docker/parches/NFD.patch
git -C ns-3/src/ndnSIM/ndn-cxx apply ../../../../../docker/parches/ndn-cxx.patch
```

Los parches hacen falta para compilar y corrigen una fuga de temporizadores en `Scheduler`.
Después, desde la raíz del repositorio:

```bash
./ndn.sh waf configure -d optimized --enable-examples
./ndn.sh waf
./ndn.sh make -C proyecto
```

### IMDb

Solo hace falta para regenerar `proyecto/video/catalogo_imdb.json`: descarga `title.basics`, `title.ratings`,
`title.episode` y `title.akas` de https://datasets.imdbws.com/ en `proyecto/datos/imdb/` y ejecuta
`python3 proyecto/video/importar_imdb.py`. Los datos de IMDb son para uso personal y no comercial.
