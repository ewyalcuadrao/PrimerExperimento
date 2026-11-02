#!/usr/bin/env bash
# Ejecuta comandos dentro del contenedor ndnSIM.
# La carpeta del proyecto se monta en /work.
#   ./ndn.sh                              -> shell interactiva
#   ./ndn.sh python3 proyecto/simple.py   -> ejecutar una simulación en Python
#   ./ndn.sh waf --run ndn-simple         -> ejecutar waf dentro de ndnSIM/ns-3
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
IMAGE=ndnsim:2.9
NS3=/work/ndnSIM/ns-3

if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
    docker build -t "$IMAGE" --build-arg UID="$(id -u)" --build-arg GID="$(id -g)" "$DIR/docker"
fi

# Permite abrir ventanas (visualizador de ndnSIM) desde el contenedor
xhost +local: >/dev/null 2>&1 || true

if [ "$1" = waf ]; then
    shift
    set -- bash -c "cd $NS3 && ./waf \"\$@\"" waf "$@"
fi

TTY=-i; [ -t 0 ] && TTY=-it
docker run --rm $TTY \
    -v "$DIR":/work -w /work \
    -e DISPLAY="$DISPLAY" -v /tmp/.X11-unix:/tmp/.X11-unix \
    -e PYTHONPATH="$NS3/build/bindings/python:$NS3/src/visualizer:/work/proyecto" \
    -e LD_LIBRARY_PATH="$NS3/build/lib" \
    "$IMAGE" "${@:-bash}"
