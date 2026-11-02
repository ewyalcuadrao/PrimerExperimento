// Servidor de vídeo: responde cada contenido con un solo Data.
//
// Nombres que atiende (bajo su prefijo, p. ej. /vod/serie/dark/t1):
//   .../e<N>, /vod/peli/<slug>...  el vídeo entero, en un Data de VIDEO bytes
//   .../miniatura                  imagen de portada, en un Data de MINIATURA bytes
// Un canal en directo (/rtve/directo/<canal>) es también un solo nombre y un solo Data.
// Los tamaños no son los reales (un episodio son cientos de MB): basta con que un
// vídeo pese más que una miniatura y quepa en un paquete NDN.

#include <ns3/core-module.h>
#include <ns3/ndnSIM/apps/ndn-app.hpp>
#include <ns3/ndnSIM/helper/ndn-fib-helper.hpp>

namespace {

const size_t VIDEO = 6000;     // bytes del Data de un vídeo
const size_t MINIATURA = 2000; // bytes del Data de una miniatura

} // namespace

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
                      MakeStringAccessor(&VideoProducer::m_prefix), MakeStringChecker());
    return tid;
  }

protected:
  void
  StartApplication() override
  {
    App::StartApplication();
    FibHelper::AddRoute(GetNode(), Name(m_prefix), m_face, 0);
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
    data->setFreshnessPeriod(::ndn::time::hours(1));
    bool miniatura = !name.empty() && name[-1].toUri() == "miniatura";
    data->setContent(make_shared< ::ndn::Buffer>(miniatura ? MINIATURA : VIDEO));
    data->setSignatureInfo(SignatureInfo(static_cast< ::ndn::tlv::SignatureTypeValue>(255)));
    ::ndn::EncodingEstimator estimator;
    ::ndn::EncodingBuffer encoder(estimator.appendVarNumber(0), 0);
    encoder.appendVarNumber(0);
    data->setSignatureValue(encoder.getBuffer());
    data->wireEncode();

    m_transmittedDatas(data, this, m_face);
    m_appLink->onReceiveData(*data);
  }

  std::string m_prefix;
};

NS_OBJECT_ENSURE_REGISTERED(VideoProducer);

} // namespace ndn
} // namespace ns3
