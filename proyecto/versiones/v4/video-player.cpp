// Reproductor de vídeo (un usuario).
//
// Lee del fichero Sesiones las filas de su Usuario (generadas por video/humanos.py) y
// las reproduce en orden. Cada contenido es un solo nombre y un solo Data, sin
// segmentos ni calidades (así cada nombre del log es un contenido):
//   1. Pide <base>. Si recibe Nack, la persona pulsa "reintentar" una vez a los 5 s;
//      si vuelve a fallar, la sesión acaba con error. Si el Interest caduca, se
//      reintenta hasta 2 veces.
//   2. Con el Data empieza a verlo, y la sesión dura lo que dura el vídeo
//      (n_segs * SEGMENTO s; n_segs sigue midiendo la duración en trozos de 4 s).
// Si la red contesta con un Data de tipo Link (redirección por similitud, ver
// similarity-strategy.cpp), el reproductor sigue el enlace una vez: pide el nombre
// sugerido y reproduce ese contenido. Lo anota en qoe.csv (columna redirigido_a).
// Una miniatura (tipo=miniatura) es igual, pero acaba al recibirla y sin reintento.
// Una sesión con condicion=si_falla solo se reproduce si la anterior falló (la
// persona busca el título correcto después de un enlace roto).
//
// Al acabar cada sesión escribe una fila en el log de calidad de experiencia (QoE).

#include "packet-log.hpp"

#include <ns3/core-module.h>
#include <ns3/ndnSIM/apps/ndn-app.hpp>

#include <ndn-cxx/link.hpp>

#include <fstream>
#include <map>
#include <memory>
#include <sstream>
#include <vector>

namespace {

const double SEGMENTO = 4.0; // s de vídeo por unidad de n_segs
const double LIFETIME = 2.0; // s

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
    Simulator::Cancel(m_fin);
    App::StopApplication();
  }

  void
  OnData(shared_ptr<const Data> data) override
  {
    App::OnData(data);
    if (m_terminada || data->getName() != m_pedido.nombre)
      return; // respuesta tardía de algo ya abandonado
    Simulator::Cancel(m_pedido.timeout);

    if (data->getContentType() == ::ndn::tlv::ContentType_Link) {
      Redirigir(::ndn::Link(data->wireEncode()));
      return;
    }
    m_arranque = (Simulator::Now() - m_inicioSesion).GetSeconds();
    const Sesion& s = m_sesiones[m_actual];
    if (s.tipo == "miniatura")
      Terminar("completa");
    else
      m_fin = Simulator::Schedule(Seconds(s.nSegs * SEGMENTO), &VideoPlayer::Terminar, this,
                                  std::string("completa"));
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
    if (m_pedido.intentos < 1 && m_sesiones[m_actual].tipo != "miniatura") {
      // La persona ve el error y pulsa "reintentar"
      m_pedido.timeout = Simulator::Schedule(Seconds(5), &VideoPlayer::Enviar, this,
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
    uint64_t nSegs;
  };

  struct Pedido
  {
    Name nombre;
    int intentos;
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
      s.nSegs = std::stoull(v[col["n_segs"]]);
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
    Enviar(s.base, 0);
  }

  void
  Redirigir(const ::ndn::Link& link)
  {
    if (!m_redirigido.empty() || link.getDelegationList().empty()) {
      Terminar("Redirect:bucle"); // solo se sigue una redirección por sesión
      return;
    }
    m_redirigido = link.getDelegationList().front();
    Enviar(m_redirigido, 0);
  }

  void
  Enviar(Name nombre, int intentos)
  {
    if (m_terminada)
      return;
    auto interest = make_shared<Interest>(nombre);
    interest->setNonce(m_rand->GetInteger(0, std::numeric_limits<uint32_t>::max()));
    interest->setCanBePrefix(false);
    interest->setInterestLifetime(::ndn::time::milliseconds(static_cast<int>(LIFETIME * 1000)));

    m_pedido = {nombre, intentos,
                Simulator::Schedule(Seconds(LIFETIME + 0.1), &VideoPlayer::Caducado, this)};
    m_transmittedInterests(interest, this, m_face);
    m_appLink->onReceiveInterest(*interest);
  }

  void
  Caducado()
  {
    if (m_pedido.intentos < 2)
      Enviar(m_pedido.nombre, m_pedido.intentos + 1);
    else
      Terminar("Timeout");
  }

  void
  Terminar(const std::string& resultado)
  {
    if (m_terminada)
      return;
    m_terminada = true;
    Simulator::Cancel(m_pedido.timeout);
    Simulator::Cancel(m_fin);
    m_ultimaFallo = resultado != "completa";

    const Sesion& s = m_sesiones[m_actual];
    if (g_qoe) {
      *g_qoe << m_usuario << ',' << s.id << ',' << ndnutils::nodeName(GetNode()) << ','
             << s.base.toUri() << ',' << s.tipo << ',' << m_inicioSesion.GetSeconds() << ','
             << Simulator::Now().GetSeconds() << ',' << resultado << ','
             << s.nSegs * SEGMENTO << ',' << m_arranque << ','
             << (m_redirigido.empty() ? "" : m_redirigido.toUri()) << '\n';
    }
    Programar(m_actual + 1);
  }

  std::string m_fichero, m_usuario;
  std::vector<Sesion> m_sesiones;
  Ptr<UniformRandomVariable> m_rand;
  Pedido m_pedido;   // el Interest en curso (solo hay uno a la vez)
  Name m_redirigido; // nombre al que redirigió la red, vacío si no hubo redirección

  size_t m_actual = 0;
  bool m_ultimaFallo = false, m_terminada = true;
  Time m_inicioSesion;
  double m_arranque = -1;
  EventId m_fin; // fin del vídeo
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
  *g_qoe << "usuario,sesion,barrio,base,tipo,inicio,fin,resultado,duracion_s,arranque_s,"
            "redirigido_a\n";
  return 0;
}

extern "C" void
ndnutils_qoe_close()
{
  if (g_qoe)
    g_qoe->close();
}
