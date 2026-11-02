// Estrategia de reenvío por similitud de nombres.
//
// Se comporta como best-route. La única diferencia es el caso en que best-route
// respondería Nack NoRoute (ningún siguiente salto válido en la FIB). Entonces
// pregunta a Python (ndnutils.similarity) qué nombres conocidos se parecen al del
// Interest y toma el primero que tenga un siguiente salto válido. Según el modo:
//   reenviar   reenvía el Interest, sin cambiarle el nombre, por la ruta de ese nombre
//   redirigir  contesta al consumidor con un Data de tipo Link (ContentType_Link) con
//              el nombre de ese contenido, como una redirección: el consumidor decide
//              si lo pide (ver video-player.cpp)
// Si ningún candidato tiene ruta, responde NoRoute como best-route.
//
// Cada decisión queda en el log de paquetes como una fila Redirect con
// motivo = <nombre parecido>@<similitud>.

#include "packet-log.hpp"

#include <ns3/ndnSIM/NFD/daemon/fw/algorithm.hpp>
#include <ns3/ndnSIM/NFD/daemon/fw/best-route-strategy.hpp>
#include <ns3/ndnSIM/NFD/daemon/fw/forwarder.hpp>

#include <ndn-cxx/link.hpp>

#include <set>
#include <sstream>

namespace {

// Callback de Python: (nodo, nombre, buffer, tamaño) -> bytes escritos.
// Escribe una línea "<nombre parecido>\t<similitud>" por candidato, de más a menos parecido.
using SimilarityCallback = int (*)(const char*, const char*, char*, int);
SimilarityCallback g_callback = nullptr;
bool g_redirigir = false;

} // namespace

namespace nfd {
namespace fw {

class SimilarityStrategy : public BestRouteStrategy
{
public:
  explicit
  SimilarityStrategy(Forwarder& forwarder, const Name& name = getStrategyName())
    : BestRouteStrategy(forwarder)
    , m_fib(forwarder.getFib())
  {
    this->setInstanceName(makeInstanceName(name, getStrategyName()));
  }

  static const Name&
  getStrategyName()
  {
    static const auto strategyName = Name("/localhost/nfd/strategy/similarity").appendVersion(1);
    return strategyName;
  }

  void
  afterReceiveInterest(const Interest& interest, const FaceEndpoint& ingress,
                       const shared_ptr<pit::Entry>& pitEntry) override
  {
    if (g_callback && !hasEligibleNextHop(lookupFib(*pitEntry), interest, ingress, pitEntry) &&
        redirect(interest, ingress, pitEntry)) {
      return;
    }
    BestRouteStrategy::afterReceiveInterest(interest, ingress, pitEntry);
  }

private:
  static bool
  hasEligibleNextHop(const fib::Entry& entry, const Interest& interest,
                     const FaceEndpoint& ingress, const shared_ptr<pit::Entry>& pitEntry)
  {
    for (const auto& nexthop : entry.getNextHops()) {
      if (isNextHopEligible(ingress.face, interest, nexthop, pitEntry))
        return true;
    }
    return false;
  }

  bool
  redirect(const Interest& interest, const FaceEndpoint& ingress,
           const shared_ptr<pit::Entry>& pitEntry)
  {
    auto node = ns3::NodeList::GetNode(ns3::Simulator::GetContext());
    std::string nodeName = ndnutils::nodeName(node);

    char buf[4096];
    int n = g_callback(nodeName.c_str(), interest.getName().toUri().c_str(), buf, sizeof(buf));
    std::istringstream lines(std::string(buf, n > 0 ? n : 0));

    std::string candidate, similarity;
    while (std::getline(lines, candidate, '\t') && std::getline(lines, similarity)) {
      for (const auto& nexthop : m_fib.findLongestPrefixMatch(Name(candidate)).getNextHops()) {
        if (!isNextHopEligible(ingress.face, interest, nexthop, pitEntry))
          continue;
        if (g_redirigir) {
          ndnutils::writeRow(nodeName, "out", "Redirect", ndnutils::peerName(ingress.face),
                             interest.getName(), 0, candidate + "@" + similarity,
                             ingress.face.getId(), ndnutils::hexNonce(interest));
          // A todos los que esperan este nombre (la PIT puede haber agregado varios)
          auto link = enlace(interest.getName(), Name(candidate));
          std::set<Face*> downstreams;
          for (const auto& inRecord : pitEntry->getInRecords())
            downstreams.insert(&inRecord.getFace());
          for (Face* face : downstreams)
            this->sendData(link, *face, pitEntry);
          this->rejectPendingInterest(pitEntry);
          return true;
        }
        Face& outFace = nexthop.getFace();
        ndnutils::writeRow(nodeName, "out", "Redirect", ndnutils::peerName(outFace),
                           interest.getName(), 0, candidate + "@" + similarity,
                           outFace.getId(), ndnutils::hexNonce(interest));
        this->sendInterest(interest, outFace, pitEntry);
        return true;
      }
    }
    return false;
  }

  /// Data de redirección: se llama como el Interest y su contenido es el otro nombre.
  /// Firma vacía, como los Data de video-producer.cpp (ndnSIM no comprueba firmas).
  static ::ndn::Link
  enlace(const Name& pedido, const Name& destino)
  {
    ::ndn::Link link(pedido, {destino});
    link.setFreshnessPeriod(::ndn::time::seconds(1));
    link.setSignatureInfo(::ndn::SignatureInfo(static_cast< ::ndn::tlv::SignatureTypeValue>(255)));
    link.setSignatureValue(std::make_shared< ::ndn::Buffer>(1));
    link.wireEncode();
    return link;
  }

  Fib& m_fib;
};

NFD_REGISTER_STRATEGY(SimilarityStrategy);

} // namespace fw
} // namespace nfd

extern "C" void
ndnutils_similarity_callback(SimilarityCallback callback, int redirigir)
{
  g_callback = callback;
  g_redirigir = redirigir != 0;
}
