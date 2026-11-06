// Reproductor de vídeo (un usuario), con calidad adaptativa como un cliente DASH.
//
// Lee del fichero Sesiones las filas de su Usuario (generadas por video/humanos.py) y
// las reproduce en orden. Formato de los nombres en video-common.hpp. En cada sesión:
//   1. Pide <base>/manifest. Si recibe Nack, la persona pulsa "reintentar" una vez a
//      los 5 s; si vuelve a fallar, la sesión acaba con error. Si el Interest caduca,
//      se reintenta hasta 2 veces.
//   2. Pide los n_segs segmentos desde seg_inicio, de uno en uno, mientras el búfer
//      tenga menos de BUFER_MAX s. Empieza a reproducir con ARRANQUE segmentos; si el
//      búfer se vacía, se para (rebuffering) hasta que llega el siguiente.
//   3. Calidad de cada segmento: la mayor cuya tasa quepa en el 80 % del caudal medido
//      (media móvil de los segmentos anteriores), sin pasar de calidad_max; si el
//      búfer baja de BUFER_BAJO s después de haber llegado a 2 * BUFER_BAJO, baja una.
//      El primer segmento va en 480p.
// Un directo empieza en el último segmento emitido y pide cada uno cuando se emite.
// Una miniatura (tipo=miniatura) es un solo Interest, sin reintento.
// Una sesión con condicion=si_falla solo se reproduce si la anterior falló (la
// persona busca el nombre correcto después de un error).
// Si la red contesta el manifiesto con un Data de tipo Link (redirección por
// similitud, ver similarity-strategy.cpp), el reproductor sigue el enlace una vez y
// lo anota en qoe.csv (columna redirigido_a).
//
// Al acabar cada sesión escribe una fila en el log de calidad de experiencia (QoE).

#include "packet-log.hpp"
#include "video-common.hpp"

#include <ns3/core-module.h>
#include <ns3/ndnSIM/apps/ndn-app.hpp>

#include <ndn-cxx/link.hpp>

#include <algorithm>
#include <cmath>
#include <fstream>
#include <iomanip>
#include <map>
#include <memory>
#include <sstream>
#include <vector>

namespace {

using video::SEGMENTO;

const double LIFETIME = 2.0;   // s
const double BUFER_MAX = 30.0; // s de vídeo que pide por adelantado
const double BUFER_BAJO = 8.0; // s: por debajo, baja una calidad
const int ARRANQUE = 2;        // segmentos en el búfer para empezar a reproducir
const int CALIDAD_INICIAL = 1; // 480p
const int REINTENTOS_SEGMENTO = 3;

std::shared_ptr<std::ofstream> g_qoe;

} // namespace

namespace ns3 {
namespace ndn {

class VideoPlayer : public App
{
public:
  static TypeId
  GetTypeId()
  {
    static TypeId tid =
      TypeId("ns3::ndn::VideoPlayer")
        .SetParent<App>()
        .AddConstructor<VideoPlayer>()
        .AddAttribute("Sesiones", "Fichero CSV de sesiones", StringValue(""),
                      MakeStringAccessor(&VideoPlayer::m_fichero), MakeStringChecker())
        .AddAttribute("Usuario", "Usuario cuyas sesiones reproduce", StringValue(""),
                      MakeStringAccessor(&VideoPlayer::m_usuario), MakeStringChecker());
    return tid;
  }

  VideoPlayer()
    : m_rand(CreateObject<UniformRandomVariable>())
  {
  }

protected:
  void
  StartApplication() override
  {
    App::StartApplication();
    Cargar();
    Programar(0);
  }

  void
  StopApplication() override
  {
    Simulator::Cancel(m_pedido.timeout);
    Simulator::Cancel(m_espera);
    Simulator::Cancel(m_reproduccion);
    App::StopApplication();
  }

  void
  OnData(shared_ptr<const Data> data) override
  {
    App::OnData(data);
    if (m_terminada || data->getName() != m_pedido.nombre)
      return; // respuesta tardía de algo ya abandonado
    Simulator::Cancel(m_pedido.timeout);

    const Sesion& s = m_sesiones[m_actual];
    if (m_pedido.segmento < 0) { // manifiesto o miniatura
      if (data->getContentType() == ::ndn::tlv::ContentType_Link) {
        Redirigir(::ndn::Link(data->wireEncode()));
        return;
      }
      if (s.tipo == "miniatura") {
        m_arranque = (Simulator::Now() - m_inicioSesion).GetSeconds();
        Terminar("completa");
        return;
      }
      m_siguiente = 0;
      if (s.tipo == "directo") // el último segmento ya emitido
        m_primero = static_cast<uint64_t>(Simulator::Now().GetSeconds() / SEGMENTO) - 1;
      PedirSiguiente();
      return;
    }

    // Segmento: caudal medido (media móvil) para elegir la calidad del siguiente
    double t = (Simulator::Now() - m_pedido.enviado).GetSeconds();
    double bps = data->getContent().value_size() * 8 / std::max(t, 1e-3);
    m_caudal = m_caudal < 0 ? bps : 0.7 * m_caudal + 0.3 * bps;
    m_segmentos++;
    m_sumaKbps += video::CALIDADES[m_pedido.calidad].kbps;
    if (m_ultimaCalidad >= 0 && m_pedido.calidad != m_ultimaCalidad)
      m_cambios++;
    m_ultimaCalidad = m_pedido.calidad;

    m_enVuelo = false;
    m_listos++;
    m_siguiente++;
    if (m_listos * SEGMENTO >= 2 * BUFER_BAJO)
      m_buferLleno = true;
    if (!m_reproduciendo && (m_listos >= std::min<uint64_t>(ARRANQUE, s.nSegs) || m_parado))
      Reproducir();
    PedirSiguiente();
  }

  void
  OnNack(shared_ptr<const lp::Nack> nack) override
  {
    App::OnNack(nack);
    if (m_terminada || nack->getInterest().getName() != m_pedido.nombre)
      return;
    Simulator::Cancel(m_pedido.timeout);

    std::ostringstream motivo;
    motivo << nack->getReason();
    if (m_pedido.segmento < 0 && m_pedido.intentos < 1 && m_sesiones[m_actual].tipo != "miniatura") {
      // La persona ve el error y pulsa "reintentar"
      m_pedido.timeout = Simulator::Schedule(Seconds(5), &VideoPlayer::EnviarManifiesto, this,
                                             m_pedido.nombre, m_pedido.intentos + 1);
      return;
    }
    Terminar("Nack:" + motivo.str());
  }

private:
  struct Sesion
  {
    std::string id, tipo, condicion;
    double inicio, pausa;
    Name base;
    uint64_t segInicio, nSegs;
    int calidadMax;
  };

  struct Pedido
  {
    Name nombre;
    int intentos = 0;
    int64_t segmento = -1; // -1: manifiesto o miniatura
    int calidad = 0;
    Time enviado;
    EventId timeout; // caducidad del Interest, o el reintento pendiente tras un Nack
  };

  void
  Cargar()
  {
    std::ifstream f(m_fichero);
    std::string linea;
    std::getline(f, linea);
    std::map<std::string, size_t> col;
    std::istringstream cab(linea);
    for (std::string c; std::getline(cab, c, ',');)
      col[c] = col.size();

    while (std::getline(f, linea)) {
      std::vector<std::string> v;
      std::istringstream ss(linea);
      for (std::string c; std::getline(ss, c, ',');)
        v.push_back(c);
      if (v.size() < col.size() || v[col["usuario"]] != m_usuario)
        continue;
      Sesion s;
      s.id = v[col["sesion"]];
      s.inicio = std::stod(v[col["inicio"]]);
      s.pausa = std::stod(v[col["pausa"]]);
      s.condicion = v[col["condicion"]];
      s.base = Name(v[col["base"]]);
      s.tipo = v[col["tipo"]];
      s.segInicio = std::stoull(v[col["seg_inicio"]]);
      s.nSegs = std::stoull(v[col["n_segs"]]);
      s.calidadMax = std::max(0, video::indiceCalidad(v[col["calidad_max"]]));
      m_sesiones.push_back(s);
    }
  }

  void
  Programar(size_t k)
  {
    if (k >= m_sesiones.size())
      return;
    const Sesion& s = m_sesiones[k];
    double espera = s.inicio >= 0 ? std::max(0.0, s.inicio - Simulator::Now().GetSeconds())
                                  : s.pausa;
    Simulator::Schedule(Seconds(espera), &VideoPlayer::Iniciar, this, k);
  }

  void
  Iniciar(size_t k)
  {
    m_actual = k;
    const Sesion& s = m_sesiones[k];
    if (s.condicion == "si_falla" && !m_ultimaFallo) {
      Programar(k + 1);
      return;
    }
    m_inicioSesion = Simulator::Now();
    m_arranque = -1;
    m_terminada = false;
    m_redirigido = Name();
    m_base = s.base;
    m_primero = s.segInicio;
    m_siguiente = m_listos = m_vistos = 0;
    m_reproduciendo = m_parado = m_enVuelo = m_buferLleno = false;
    m_paradas = 0;
    m_tiempoParado = 0;
    m_segmentos = m_cambios = 0;
    m_sumaKbps = 0;
    m_ultimaCalidad = -1;
    m_caudal = -1;
    if (s.tipo == "miniatura")
      EnviarManifiesto(s.base, 0);
    else
      EnviarManifiesto(Name(s.base).append("manifest"), 0);
  }

  void
  Redirigir(const ::ndn::Link& link)
  {
    if (!m_redirigido.empty() || link.getDelegationList().empty()) {
      Terminar("Redirect:bucle"); // solo se sigue una redirección por sesión
      return;
    }
    m_redirigido = link.getDelegationList().front();
    if (!m_redirigido.empty() && m_redirigido[-1].toUri() == "manifest")
      m_redirigido = m_redirigido.getPrefix(-1);
    m_base = m_redirigido;
    if (m_sesiones[m_actual].tipo == "miniatura")
      EnviarManifiesto(m_base, 0);
    else
      EnviarManifiesto(Name(m_base).append("manifest"), 0);
  }

  void
  EnviarManifiesto(Name nombre, int intentos)
  {
    Pedido p;
    p.nombre = nombre;
    p.intentos = intentos;
    Enviar(p);
  }

  void
  Enviar(Pedido p)
  {
    if (m_terminada)
      return;
    auto interest = make_shared<Interest>(p.nombre);
    interest->setNonce(m_rand->GetInteger(0, std::numeric_limits<uint32_t>::max()));
    interest->setCanBePrefix(false);
    interest->setInterestLifetime(::ndn::time::milliseconds(static_cast<int>(LIFETIME * 1000)));

    p.enviado = Simulator::Now();
    p.timeout = Simulator::Schedule(Seconds(LIFETIME + 0.1), &VideoPlayer::Caducado, this);
    m_pedido = p;
    m_transmittedInterests(interest, this, m_face);
    m_appLink->onReceiveInterest(*interest);
  }

  void
  Caducado()
  {
    int maximo = m_pedido.segmento < 0 ? 2 : REINTENTOS_SEGMENTO;
    if (m_pedido.intentos >= maximo) {
      Terminar("Timeout");
      return;
    }
    Pedido p = m_pedido;
    p.intentos++;
    Enviar(p);
  }

  /// Calidad del próximo segmento
  int
  ElegirCalidad()
  {
    const Sesion& s = m_sesiones[m_actual];
    int q = CALIDAD_INICIAL;
    if (m_caudal >= 0) {
      q = 0;
      for (int i = 0; i < video::N_CALIDADES; ++i)
        if (video::CALIDADES[i].kbps <= 0.8 * m_caudal)
          q = i;
      if (m_buferLleno && m_listos * SEGMENTO < BUFER_BAJO && m_ultimaCalidad >= 0)
        q = std::min(q, std::max(0, m_ultimaCalidad - 1));
    }
    return std::min(q, s.calidadMax);
  }

  /// Pide el siguiente segmento si hace falta: uno a la vez y con el búfer sin llenar
  void
  PedirSiguiente()
  {
    const Sesion& s = m_sesiones[m_actual];
    if (m_terminada || m_enVuelo || m_siguiente >= s.nSegs || m_listos * SEGMENTO >= BUFER_MAX)
      return; // al recibir o reproducir un segmento se vuelve a llamar
    Simulator::Cancel(m_espera);
    uint64_t n = m_primero + m_siguiente;
    if (s.tipo == "directo") {
      double emitido = (n + 1) * SEGMENTO;
      if (Simulator::Now().GetSeconds() < emitido) {
        m_espera = Simulator::Schedule(Seconds(emitido - Simulator::Now().GetSeconds()),
                                       &VideoPlayer::PedirSiguiente, this);
        return;
      }
    }
    Pedido p;
    p.calidad = ElegirCalidad();
    p.segmento = static_cast<int64_t>(n);
    p.nombre = Name(m_base).append(video::CALIDADES[p.calidad].nombre).appendSegment(n);
    m_enVuelo = true;
    Enviar(p);
  }

  /// Empieza a ver el siguiente segmento del búfer, o se para si no hay
  void
  Reproducir()
  {
    if (m_terminada)
      return;
    const Sesion& s = m_sesiones[m_actual];
    if (m_vistos >= s.nSegs) {
      Terminar("completa");
      return;
    }
    if (m_listos == 0) { // búfer vacío: rebuffering
      m_reproduciendo = false;
      m_parado = true;
      m_inicioParada = Simulator::Now();
      m_paradas++;
      return;
    }
    if (m_arranque < 0)
      m_arranque = (Simulator::Now() - m_inicioSesion).GetSeconds();
    if (m_parado) {
      m_tiempoParado += (Simulator::Now() - m_inicioParada).GetSeconds();
      m_parado = false;
    }
    m_reproduciendo = true;
    m_listos--;
    m_vistos++;
    m_reproduccion = Simulator::Schedule(Seconds(SEGMENTO), &VideoPlayer::Reproducir, this);
    PedirSiguiente();
  }

  void
  Terminar(const std::string& resultado)
  {
    if (m_terminada)
      return;
    m_terminada = true;
    m_ultimaFallo = resultado != "completa";
    Simulator::Cancel(m_pedido.timeout);
    Simulator::Cancel(m_espera);
    Simulator::Cancel(m_reproduccion);

    const Sesion& s = m_sesiones[m_actual];
    if (g_qoe) {
      *g_qoe << m_usuario << ',' << s.id << ',' << ndnutils::nodeName(GetNode()) << ','
             << s.base.toUri() << ',' << s.tipo << ',' << m_inicioSesion.GetSeconds() << ','
             << Simulator::Now().GetSeconds() << ',' << resultado << ','
             << s.nSegs * SEGMENTO << ',' << m_arranque << ','
             << (m_redirigido.empty() ? "" : m_redirigido.toUri()) << ',' << m_segmentos << ','
             << m_paradas << ',' << m_tiempoParado << ','
             << (m_segmentos ? m_sumaKbps / m_segmentos : 0) << ',' << m_cambios << '\n';
    }
    Programar(m_actual + 1);
  }

  std::string m_fichero, m_usuario;
  std::vector<Sesion> m_sesiones;
  Ptr<UniformRandomVariable> m_rand;
  Pedido m_pedido;   // el Interest en curso (solo hay uno a la vez)
  Name m_redirigido; // nombre al que redirigió la red, vacío si no hubo redirección
  Name m_base;       // nombre del vídeo que se reproduce (el de la sesión o el redirigido)

  size_t m_actual = 0;
  bool m_ultimaFallo = false, m_terminada = true;
  Time m_inicioSesion, m_inicioParada;
  double m_arranque = -1;

  uint64_t m_primero = 0;   // número del primer segmento
  uint64_t m_siguiente = 0; // segmentos pedidos y recibidos
  uint64_t m_listos = 0;    // en el búfer, sin ver
  uint64_t m_vistos = 0;
  bool m_reproduciendo = false, m_parado = false;
  bool m_enVuelo = false;    // hay un segmento pedido sin respuesta
  bool m_buferLleno = false; // el búfer ya llegó a 2 * BUFER_BAJO s
  double m_caudal = -1; // b/s medidos
  int m_ultimaCalidad = -1;
  uint64_t m_segmentos = 0, m_paradas = 0, m_cambios = 0;
  double m_tiempoParado = 0, m_sumaKbps = 0;
  EventId m_espera, m_reproduccion;
};

NS_OBJECT_ENSURE_REGISTERED(VideoPlayer);

} // namespace ndn
} // namespace ns3

extern "C" int
ndnutils_qoe_log(const char* file)
{
  g_qoe = std::make_shared<std::ofstream>(file);
  if (!*g_qoe)
    return -1;
  *g_qoe << std::fixed << std::setprecision(3);  // milisegundos, también pasados los 100 000 s
  *g_qoe << "usuario,sesion,nodo,base,tipo,inicio,fin,resultado,duracion_s,arranque_s,"
            "redirigido_a,segmentos,paradas,parado_s,kbps_medio,cambios_calidad\n";
  return 0;
}

extern "C" void
ndnutils_qoe_close()
{
  if (g_qoe)
    g_qoe->close();
}
