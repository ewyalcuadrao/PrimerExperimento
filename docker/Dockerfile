# Entorno de compilación para ndnSIM 2.9 (soportado en Ubuntu 22.04)
FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive TZ=Europe/Madrid

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential git ca-certificates pkg-config sudo \
        libsqlite3-dev libboost-all-dev libssl-dev libpcap-dev \
        python3 python3-dev python3-pip python3-setuptools castxml \
        python3-pygccxml python3-pygraphviz python3-gi python3-gi-cairo \
        gir1.2-goocanvas-2.0 gir1.2-gtk-3.0 libgirepository1.0-dev \
        libcanberra-gtk3-module \
    && rm -rf /var/lib/apt/lists/*

# Embeddings de los logs (FastText y Word2Vec). Versiones fijadas: gensim 4.3 necesita numpy < 2
RUN pip3 install --no-cache-dir numpy==1.26.4 scipy==1.13.1 gensim==4.3.3

# Usuario con el mismo UID/GID que el del host para que los archivos no queden como root
ARG UID=1000
ARG GID=1000
RUN groupadd -g ${GID} ndn && useradd -m -u ${UID} -g ${GID} -s /bin/bash ndn \
    && echo "ndn ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/ndn

USER ndn
WORKDIR /work
