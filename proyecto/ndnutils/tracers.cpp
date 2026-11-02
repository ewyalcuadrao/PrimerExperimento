// Puente C para usar los tracers de ndnSIM desde Python (no tienen bindings oficiales).
// Se carga con ctypes desde ndnutils/__init__.py.

#include <ns3/core-module.h>
#include <ns3/ndnSIM/utils/tracers/l2-rate-tracer.hpp>
#include <ns3/ndnSIM/utils/tracers/ndn-app-delay-tracer.hpp>
#include <ns3/ndnSIM/utils/tracers/ndn-cs-tracer.hpp>
#include <ns3/ndnSIM/utils/tracers/ndn-l3-rate-tracer.hpp>

#include <exception>
#include <iostream>

template<class F>
static int
guard(F f)
{
  try {
    f();
    return 0;
  }
  catch (const std::exception& e) {
    std::cerr << "ndnutils: " << e.what() << std::endl;
    return -1;
  }
}

extern "C" {

int
ndnutils_l3_rate(const char* file, double period)
{
  return guard([=] { ns3::ndn::L3RateTracer::InstallAll(file, ns3::Seconds(period)); });
}

int
ndnutils_app_delay(const char* file)
{
  return guard([=] { ns3::ndn::AppDelayTracer::InstallAll(file); });
}

int
ndnutils_cs(const char* file, double period)
{
  return guard([=] { ns3::ndn::CsTracer::InstallAll(file, ns3::Seconds(period)); });
}

int
ndnutils_l2_rate(const char* file, double period)
{
  return guard([=] { ns3::L2RateTracer::InstallAll(file, ns3::Seconds(period)); });
}

int
ndnutils_close()
{
  return guard([] {
    ns3::ndn::L3RateTracer::Destroy();
    ns3::ndn::AppDelayTracer::Destroy();
    ns3::ndn::CsTracer::Destroy();
    ns3::L2RateTracer::Destroy();
  });
}

} // extern "C"
