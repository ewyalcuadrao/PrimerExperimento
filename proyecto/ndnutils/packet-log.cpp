// Log de paquetes por nodo, con el mismo formato que las trazas del testbed
// (time,src,dst,tipo,nombre,bytes,motivo) más columnas propias de la simulación.
//
// Una fila por evento:
//   - Interest / Data / Nack con dir=in|out. src/dst son nombres de nodo
//     (Names::Add) o "app" si el paquete viene de/va a una aplicación local.
//   - CsHit / CsMiss: consulta al Content Store del nodo.
//   - Timeout: una entrada de la PIT expira sin respuesta.
//   - Redirect: la estrategia por similitud reenvía un Interest sin ruta
//     (similarity-strategy.cpp); motivo = <nombre parecido>@<similitud>.

#include "packet-log.hpp"

#include <ns3/ndnSIM/model/ndn-l3-protocol.hpp>
#include <ns3/ndnSIM/model/ndn-net-device-transport.hpp>
#include <ns3/ndnSIM/NFD/daemon/fw/forwarder.hpp>

#include <cstdio>
#include <fstream>
#include <iostream>
#include <map>
#include <memory>

namespace {

const char HEADER[] = "time,node,dir,tipo,src,dst,nombre,bytes,motivo,face,nonce\n";

std::shared_ptr<std::ofstream> g_out;

} // namespace

namespace ndnutils {

using namespace ns3;

std::string
nodeName(Ptr<Node> node)
{
  std::string name = Names::FindName(node);
  return name.empty() ? "node" + std::to_string(node->GetId()) : name;
}

std::string
peerName(const nfd::face::Face& face)
{
  std::string peer = "app";
  if (auto t = dynamic_cast<ns3::ndn::NetDeviceTransport*>(face.getTransport())) {
    Ptr<NetDevice> dev = t->GetNetDevice();
    Ptr<Channel> ch = dev->GetChannel();
    for (std::size_t i = 0; ch && i < ch->GetNDevices(); ++i) {
      if (ch->GetDevice(i) != dev)
        peer = nodeName(ch->GetDevice(i)->GetNode());
    }
  }
  return peer;
}

std::string
hexNonce(const ns3::ndn::Interest& interest)
{
  auto n = interest.getNonce();
  char buf[9];
  std::snprintf(buf, sizeof(buf), "%02x%02x%02x%02x", n[0], n[1], n[2], n[3]);
  return buf;
}

void
writeRow(const std::string& node, const char* dir, const char* tipo, const std::string& peer,
         const ::ndn::Name& name, std::size_t bytes, const std::string& motivo, int64_t face,
         const std::string& nonce)
{
  if (!g_out)
    return;
  // Eventos internos del nodo (sin dir) no tienen origen ni destino
  bool in = dir[0] == 'i', out = dir[0] == 'o';
  const std::string& src = in ? peer : out ? node : peer;
  const std::string& dst = in ? node : peer;
  *g_out << Simulator::Now().GetSeconds() << ',' << node << ',' << dir << ',' << tipo << ','
         << src << ',' << dst << ',' << name.toUri() << ',' << bytes << ',' << motivo << ','
         << face << ',' << nonce << '\n';
}

} // namespace ndnutils

namespace {

using namespace ns3;
using namespace ndnutils;
using ns3::ndn::Data;
using ns3::ndn::Interest;
using ns3::ndn::Face;
namespace lp = ::ndn::lp;

struct NodeLog
{
  std::string name;
  std::map<uint64_t, std::string> peers; // FaceId -> nodo vecino
};

std::list<NodeLog> g_nodes;

const std::string&
peerOf(NodeLog* log, const Face& face)
{
  auto it = log->peers.find(face.getId());
  if (it != log->peers.end())
    return it->second;
  return log->peers.emplace(face.getId(), peerName(face)).first->second;
}

void
row(NodeLog* log, const char* dir, const char* tipo, const std::string& peer,
    const ::ndn::Name& name, std::size_t bytes, const std::string& motivo, int64_t face,
    const std::string& nonce)
{
  writeRow(log->name, dir, tipo, peer, name, bytes, motivo, face, nonce);
}

void
onInterest(NodeLog* log, const char* dir, const Interest& i, const Face& f)
{
  row(log, dir, "Interest", peerOf(log, f), i.getName(), i.wireEncode().size(), "",
      f.getId(), hexNonce(i));
}

void
onData(NodeLog* log, const char* dir, const Data& d, const Face& f)
{
  row(log, dir, "Data", peerOf(log, f), d.getName(), d.wireEncode().size(), "", f.getId(), "");
}

void
onNack(NodeLog* log, const char* dir, const lp::Nack& n, const Face& f)
{
  std::ostringstream motivo;
  motivo << n.getReason();
  row(log, dir, "Nack", peerOf(log, f), n.getInterest().getName(),
      n.getInterest().wireEncode().size(), motivo.str(), f.getId(), hexNonce(n.getInterest()));
}

void inInterest(NodeLog* l, const Interest& i, const Face& f) { onInterest(l, "in", i, f); }
void outInterest(NodeLog* l, const Interest& i, const Face& f) { onInterest(l, "out", i, f); }
void inData(NodeLog* l, const Data& d, const Face& f) { onData(l, "in", d, f); }
void outData(NodeLog* l, const Data& d, const Face& f) { onData(l, "out", d, f); }
void inNack(NodeLog* l, const lp::Nack& n, const Face& f) { onNack(l, "in", n, f); }
void outNack(NodeLog* l, const lp::Nack& n, const Face& f) { onNack(l, "out", n, f); }

void
timedOut(NodeLog* l, const nfd::pit::Entry& e)
{
  row(l, "", "Timeout", "", e.getName(), 0, "", -1, hexNonce(e.getInterest()));
}

} // namespace

extern "C" int
ndnutils_packet_log(const char* file)
{
  try {
    g_out = std::make_shared<std::ofstream>(file);
    if (!*g_out)
      throw std::runtime_error(std::string("no se puede abrir ") + file);
    *g_out << HEADER;

    for (auto node = NodeList::Begin(); node != NodeList::End(); ++node) {
      Ptr<ns3::ndn::L3Protocol> l3 = (*node)->GetObject<ns3::ndn::L3Protocol>();
      if (!l3)
        continue;

      g_nodes.push_back({nodeName(*node), {}});
      NodeLog* log = &g_nodes.back();

      l3->TraceConnectWithoutContext("InInterests", MakeBoundCallback(&inInterest, log));
      l3->TraceConnectWithoutContext("OutInterests", MakeBoundCallback(&outInterest, log));
      l3->TraceConnectWithoutContext("InData", MakeBoundCallback(&inData, log));
      l3->TraceConnectWithoutContext("OutData", MakeBoundCallback(&outData, log));
      l3->TraceConnectWithoutContext("InNack", MakeBoundCallback(&inNack, log));
      l3->TraceConnectWithoutContext("OutNack", MakeBoundCallback(&outNack, log));
      l3->TraceConnectWithoutContext("TimedOutInterests", MakeBoundCallback(&timedOut, log));

      auto& fw = *l3->getForwarder();
      fw.afterCsHit.connect([log](const Interest& i, const Data& d) {
        row(log, "", "CsHit", "", i.getName(), d.wireEncode().size(), "", -1, hexNonce(i));
      });
      fw.afterCsMiss.connect([log](const Interest& i) {
        row(log, "", "CsMiss", "", i.getName(), 0, "", -1, hexNonce(i));
      });
    }
    return 0;
  }
  catch (const std::exception& e) {
    std::cerr << "ndnutils: " << e.what() << std::endl;
    return -1;
  }
}

extern "C" void
ndnutils_packet_log_close()
{
  if (g_out)
    g_out->close();
}
