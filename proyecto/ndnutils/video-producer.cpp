// Servidor de vídeo: responde el manifiesto, los segmentos y las miniaturas de los
// contenidos bajo su prefijo (p. ej. /vod/serie/dark/t1). Formato de los nombres y
// tamaños en video-common.hpp.

#include "video-common.hpp"

#include <ns3/core-module.h>
#include <ns3/ndnSIM/apps/ndn-app.hpp>
#include <ns3/ndnSIM/helper/ndn-fib-helper.hpp>
#include <sstream>

namespace ns3 {
namespace ndn {

class VideoProducer : public App
{
public:
  static TypeId
  GetTypeId()
  {
    static TypeId tid =
      TypeId("ns3::ndn::VideoProducer")
        .SetParent<App>()
        .AddConstructor<VideoProducer>()
        .AddAttribute("Prefix", "Prefijo que sirve", StringValue("/"),
                      MakeStringAccessor(&VideoProducer::m_prefix), MakeStringChecker())
        .AddAttribute("Directo", "Emite en directo (el manifiesto cambia con cada segmento)",
                      BooleanValue(false), MakeBooleanAccessor(&VideoProducer::m_directo),
                      MakeBooleanChecker())
        .AddAttribute("Nombres", "Contenidos bajo el prefijo, separados por espacios; si se dan, "
                      "solo se anuncian esos (otro nombre bajo el prefijo da NoRoute)",
                      StringValue(""), MakeStringAccessor(&VideoProducer::m_nombres),
                      MakeStringChecker());
    return tid;
  }

protected:
  void
  StartApplication() override
  {
    App::StartApplication();
    if (m_nombres.empty()) {
      FibHelper::AddRoute(GetNode(), Name(m_prefix), m_face, 0);
      return;
    }
    std::istringstream nombres(m_nombres);
    std::string nombre;
    while (nombres >> nombre)
      FibHelper::AddRoute(GetNode(), Name(m_prefix).append(nombre), m_face, 0);
  }

  void
  OnInterest(shared_ptr<const Interest> interest) override
  {
    App::OnInterest(interest);
    if (m_active)
      Responder(interest->getName());
  }

private:
  void
  Responder(const Name& name)
  {
    auto data = make_shared<Data>(name);
    std::size_t bytes = video::MANIFIESTO;
    ::ndn::time::milliseconds frescura = ::ndn::time::hours(1);
    if (!name.empty() && name[-1].isSegment() && name.size() >= 2) {
      int q = video::indiceCalidad(name[-2].toUri());
      bytes = video::bytesSegmento(q >= 0 ? q : 0);
    }
    else if (!name.empty() && name[-1].toUri() == "miniatura") {
      bytes = video::MINIATURA;
    }
    else if (m_directo) {
      frescura = ::ndn::time::seconds(static_cast<int>(video::SEGMENTO)); // manifiesto de un directo
    }
    data->setFreshnessPeriod(frescura);
    data->setContent(make_shared< ::ndn::Buffer>(bytes));
    data->setSignatureInfo(SignatureInfo(static_cast< ::ndn::tlv::SignatureTypeValue>(255)));
    ::ndn::EncodingEstimator estimator;
    ::ndn::EncodingBuffer encoder(estimator.appendVarNumber(0), 0);
    encoder.appendVarNumber(0);
    data->setSignatureValue(encoder.getBuffer());
    data->wireEncode();

    m_transmittedDatas(data, this, m_face);
    m_appLink->onReceiveData(*data);
  }

  std::string m_prefix, m_nombres;
  bool m_directo = false;
};

NS_OBJECT_ENSURE_REGISTERED(VideoProducer);

} // namespace ndn
} // namespace ns3
