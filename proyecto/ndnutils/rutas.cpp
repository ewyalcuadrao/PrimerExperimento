// Rutas por defecto para los routers de acceso.
//
// GlobalRoutingHelper calcula rutas en todos los nodos de la topología. En un operador
// real, los routers de acceso y agregación no llevan la tabla completa: mandan todo a
// su router superior con una ruta por defecto, y solo el núcleo conoce cada prefijo.
// ndnutils_ruta_por_defecto deja así la FIB de un nodo: quita todas sus rutas hacia
// otros nodos y añade "/" hacia el nodo indicado. Se aplica en t=0 de la simulación,
// después de que GlobalRoutingHelper haya instalado las suyas.

#include "packet-log.hpp"

#include <ns3/ndnSIM/model/ndn-l3-protocol.hpp>
#include <ns3/ndnSIM/NFD/daemon/fw/forwarder.hpp>
#include <ns3/ndnSIM/NFD/daemon/table/fib.hpp>

#include <iostream>
#include <vector>

namespace {

void
aplicar(ns3::Ptr<ns3::Node> node, std::string salida)
{
  auto l3 = node->GetObject<ns3::ndn::L3Protocol>();
  nfd::Fib& fib = l3->getForwarder()->getFib();

  nfd::face::Face* arriba = nullptr;
  std::vector<nfd::face::Face*> enlaces;
  for (auto& face : l3->getFaceTable()) {
    std::string peer = ndnutils::peerName(face);
    if (peer == "app")
      continue; // apps locales y faces internas de NFD
    enlaces.push_back(&face);
    if (peer == salida)
      arriba = &face;
  }
  if (arriba == nullptr) {
    std::cerr << "ndnutils: " << ndnutils::nodeName(node) << " no tiene enlace con " << salida << '\n';
    return;
  }

  std::vector<::ndn::Name> prefijos;
  for (const auto& entry : fib)
    prefijos.push_back(entry.getPrefix());
  for (const auto& prefijo : prefijos) {
    for (auto* face : enlaces) {
      // removeNextHop borra la entrada al quitarle el último siguiente salto
      if (nfd::fib::Entry* entry = fib.findExactMatch(prefijo))
        fib.removeNextHop(*entry, *face);
    }
  }
  fib.addOrUpdateNextHop(*fib.insert("/").first, *arriba, 1);
}

} // namespace

extern "C" void
ndnutils_ruta_por_defecto(const char* nodo, const char* salida)
{
  ns3::Ptr<ns3::Node> node = ns3::Names::Find<ns3::Node>(nodo);
  if (!node) {
    std::cerr << "ndnutils: no existe el nodo " << nodo << '\n';
    return;
  }
  ns3::Simulator::ScheduleWithContext(node->GetId(), ns3::Seconds(0), &aplicar, node, std::string(salida));
}
