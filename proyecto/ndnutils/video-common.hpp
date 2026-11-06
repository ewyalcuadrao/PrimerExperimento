// Formato de los vídeos, compartido por el reproductor y el servidor.
//
// Nombres (como DASH, con un manifiesto y segmentos por calidad):
//   <base>/manifest                 descripción del vídeo: duración y calidades
//   <base>/<calidad>/seg=<N>        SEGMENTO s de vídeo en esa calidad
//   <prefijo>/miniatura             imagen de portada
// En un directo (/rtve/directo/<canal>), N es el número de segmento desde las 00:00:
// el segmento N se emite entre N*SEGMENTO y (N+1)*SEGMENTO s.
//
// Los tamaños están a 1:1000 de los reales, como los anchos de banda de la red
// (red_video.py): un segmento de 1080p a 5 Mb/s ocupa 2,5 MB y aquí 2.500 bytes.

#ifndef NDNUTILS_VIDEO_COMMON_HPP
#define NDNUTILS_VIDEO_COMMON_HPP

#include <cstddef>
#include <string>

namespace video {

const double SEGMENTO = 4.0; // s de vídeo por segmento

struct Calidad
{
  const char* nombre;
  double kbps; // tasa real; a 1:1000 son bits por segundo
};

// De menor a mayor
const Calidad CALIDADES[] = {
  {"240p", 400}, {"480p", 1200}, {"720p", 2800}, {"1080p", 5000}, {"2160p", 16000},
};
const int N_CALIDADES = sizeof(CALIDADES) / sizeof(CALIDADES[0]);

const std::size_t MANIFIESTO = 20; // bytes (unos 20 KB reales)
const std::size_t MINIATURA = 60;  // bytes (unos 60 KB reales)

/// Índice de una calidad por su nombre, o -1
inline int
indiceCalidad(const std::string& nombre)
{
  for (int i = 0; i < N_CALIDADES; ++i)
    if (nombre == CALIDADES[i].nombre)
      return i;
  return -1;
}

/// Bytes de un segmento en la calidad i
inline std::size_t
bytesSegmento(int i)
{
  return static_cast<std::size_t>(CALIDADES[i].kbps * SEGMENTO / 8);
}

} // namespace video

#endif // NDNUTILS_VIDEO_COMMON_HPP
