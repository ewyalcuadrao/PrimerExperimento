# Compila el puente C++ de ndnutils (se ejecuta dentro del contenedor).
NS3 ?= /work/ndnSIM/ns-3
LIB := ndnutils/libndnutils.so

CXXFLAGS := -std=c++17 -O2 -fPIC -shared -DNS3_BUILD_PROFILE_OPTIMIZED \
            -I$(NS3)/build -I$(NS3)/build/ns3/ndnSIM -I$(NS3)/build/ns3/ndnSIM/ndn-cxx -I$(NS3)/build/ns3/ndnSIM/NFD \
            -I$(NS3)/src/ndnSIM/NFD/daemon -I$(NS3)/src/ndnSIM/NFD/core -I$(NS3)/build/src/ndnSIM/NFD/core
LDLIBS := -L$(NS3)/build/lib -lns3.35-ndnSIM-optimized -lns3.35-network-optimized -lns3.35-core-optimized

SRCS := $(wildcard ndnutils/*.cpp)

$(LIB): $(SRCS)
	$(CXX) $(CXXFLAGS) $^ -o $@ $(LDLIBS)

clean:
	rm -f $(LIB)

.PHONY: clean
