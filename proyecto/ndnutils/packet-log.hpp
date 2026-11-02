// Funciones del log de paquetes (packet-log.cpp) que usan otros módulos de ndnutils.

#ifndef NDNUTILS_PACKET_LOG_HPP
#define NDNUTILS_PACKET_LOG_HPP

#include <ns3/core-module.h>
#include <ns3/network-module.h>
#include <ns3/ndnSIM/model/ndn-common.hpp>
#include <ns3/ndnSIM/NFD/daemon/face/face.hpp>

#include <string>

namespace ndnutils {

/// Nombre del nodo (Names::Add) o "node<id>" si no tiene
std::string
nodeName(ns3::Ptr<ns3::Node> node);

/// Nodo al otro lado de una face, o "app" si es una aplicación local
std::string
peerName(const nfd::face::Face& face);

std::string
hexNonce(const ns3::ndn::Interest& interest);

/// Escribe una fila en el log (no hace nada si el log no está activo)
void
writeRow(const std::string& node, const char* dir, const char* tipo, const std::string& peer,
         const ::ndn::Name& name, std::size_t bytes, const std::string& motivo, int64_t face,
         const std::string& nonce);

} // namespace ndnutils

#endif // NDNUTILS_PACKET_LOG_HPP
