"""Escenario básico: consumidor -> router -> productor.

      +----------+   10Mbps   +--------+   10Mbps   +----------+
      | consumer | <--------> | router | <--------> | producer |
      +----------+    10ms    +--------+    10ms    +----------+

El consumidor pide /prefix/0, /prefix/1, ... a 10 Interests/s y el productor
responde con Data de 1024 bytes.

Ejecutar (desde PrimerExperimento/):
    ./ndn.sh python3 proyecto/simple.py
    ./ndn.sh python3 proyecto/simple.py --vis   # con visualizador gráfico
"""

import csv
import os
import sys

from ns.core import *
from ns.network import *
from ns.point_to_point import *
from ns.ndnSIM import *

import ndnutils

RESULTADOS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "resultados")

# --vis activa el visualizador de ns-3
args = ["--SimulatorImplementationType=ns3::VisualSimulatorImpl" if a == "--vis" else a
        for a in sys.argv]

Config.SetDefault("ns3::PointToPointNetDevice::DataRate", StringValue("10Mbps"))
Config.SetDefault("ns3::PointToPointChannel::Delay", StringValue("10ms"))
Config.SetDefault("ns3::DropTailQueue<Packet>::MaxSize", StringValue("20p"))

cmd = CommandLine()
cmd.Parse(args)

# Topología
nodes = NodeContainer()
nodes.Create(3)
consumer, router, producer = nodes.Get(0), nodes.Get(1), nodes.Get(2)
Names.Add("consumer", consumer)
Names.Add("router", router)
Names.Add("producer", producer)

p2p = PointToPointHelper()
p2p.Install(consumer, router)
p2p.Install(router, producer)

# Pila NDN y estrategia de reenvío
ndnHelper = ndn.StackHelper()
ndnHelper.SetDefaultRoutes(True)
ndnHelper.InstallAll()

ndn.StrategyChoiceHelper.InstallAll("/prefix", "/localhost/nfd/strategy/multicast")

# Aplicaciones
consumerHelper = ndn.AppHelper("ns3::ndn::ConsumerCbr")
consumerHelper.SetPrefix("/prefix")
consumerHelper.SetAttribute("Frequency", StringValue("10"))
consumerHelper.Install(consumer)

producerHelper = ndn.AppHelper("ns3::ndn::Producer")
producerHelper.SetPrefix("/prefix")
producerHelper.SetAttribute("PayloadSize", StringValue("1024"))
producerHelper.Install(producer)

# Métricas
ndnutils.l3_rate(os.path.join(RESULTADOS, "rate-trace.txt"))
ndnutils.app_delay(os.path.join(RESULTADOS, "app-delays-trace.txt"))

Simulator.Stop(Seconds(20.0))
Simulator.Run()
ndnutils.close()
Simulator.Destroy()

# Resumen rápido
with open(os.path.join(RESULTADOS, "app-delays-trace.txt")) as f:
    full = [r for r in csv.DictReader(f, delimiter="\t") if r["Type"] == "FullDelay"]
if full:
    delay_ms = sum(float(r["DelayS"]) for r in full) / len(full) * 1000
    print(f"Data recibidos: {len(full)}  |  retardo medio: {delay_ms:.2f} ms")
print(f"Trazas en {RESULTADOS}/")
