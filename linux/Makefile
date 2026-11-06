# Cross-build the 32-bit Windows pieces with MinGW-w64.
#
# The DLL must stay a 32-bit Windows PE because the game is a 32-bit Windows
# binary, whether it runs on Windows or under Wine. Everything outside the game
# (host library, CLI, GUI) is native Python and needs no build step at all.

# MINGW_CXX, not CXX: CXX is a built-in make variable and would already be
# set to the host g++, which has no Windows headers.
MINGW_CXX ?= i686-w64-mingw32-g++
BUILD     := build
SRC_DLL  := src/dll
SRC_INJ  := src/injector

WARN := -Wall -Wextra -Wno-unused-parameter
COMMON := -std=c++17 -O2 -mstackrealign -fno-exceptions -fno-rtti \
          -fno-pic -fno-pie -D_WIN32_WINNT=0x0601 -I$(SRC_DLL) $(WARN) \
          -MMD -MP
DLL_CXXFLAGS := $(COMMON) -DNTPL_BUILD_DLL
STATIC_LIBS := -static-libgcc -static-libstdc++

DLL_SOURCES := $(wildcard $(SRC_DLL)/*.cpp)
DLL_ASM     := $(wildcard $(SRC_DLL)/*.S)
DLL_OBJECTS := $(patsubst $(SRC_DLL)/%.cpp,$(BUILD)/dll/%.o,$(DLL_SOURCES)) \
               $(patsubst $(SRC_DLL)/%.S,$(BUILD)/dll/%.o,$(DLL_ASM))

INJ_SOURCES := $(wildcard $(SRC_INJ)/*.cpp)
INJ_OBJECTS := $(patsubst $(SRC_INJ)/%.cpp,$(BUILD)/injector/%.o,$(INJ_SOURCES))

# Header changes must rebuild the objects that include them; otherwise a
# changed PacketQueue capacity links against a stale ABI.
-include $(DLL_OBJECTS:.o=.d) $(INJ_OBJECTS:.o=.d)

.PHONY: all dll injector test-host host-test gui-test clean check

all: dll injector

dll: $(BUILD)/PacketLogger.dll
injector: $(BUILD)/Injector.exe
test-host: $(BUILD)/FakeGame.exe $(BUILD)/HookStubTest.exe

$(BUILD)/dll/%.o: $(SRC_DLL)/%.cpp
	@mkdir -p $(dir $@)
	$(MINGW_CXX) $(DLL_CXXFLAGS) -c $< -o $@

$(BUILD)/dll/%.o: $(SRC_DLL)/%.S
	@mkdir -p $(dir $@)
	$(MINGW_CXX) $(DLL_CXXFLAGS) -c $< -o $@

$(BUILD)/injector/%.o: $(SRC_INJ)/%.cpp
	@mkdir -p $(dir $@)
	$(MINGW_CXX) $(COMMON) -c $< -o $@

$(BUILD)/PacketLogger.dll: $(DLL_OBJECTS)
	@mkdir -p $(dir $@)
	$(MINGW_CXX) -shared -o $@ $^ -lws2_32 -lpsapi -luser32 -lkernel32 $(STATIC_LIBS)

$(BUILD)/Injector.exe: $(INJ_OBJECTS)
	@mkdir -p $(dir $@)
	$(MINGW_CXX) -mconsole -o $@ $^ -luser32 -lkernel32 $(STATIC_LIBS)

$(BUILD)/FakeGame.exe: test/fake_game.cpp
	@mkdir -p $(dir $@)
	$(MINGW_CXX) -mconsole -std=c++17 -O2 -o $@ $< \
		-static -static-libgcc -static-libstdc++ -lkernel32

$(BUILD)/HookStubTest.exe: test/hook_stub_test.cpp test/hook_call.S $(SRC_DLL)/hooks.S
	@mkdir -p $(dir $@)
	$(MINGW_CXX) -mconsole -std=c++17 -O0 -o $@ $^ \
		-static -static-libgcc -static-libstdc++

host-test:
	PYTHONPATH=host python3 -m unittest discover -s host/tests -v

gui-test:
	./scripts/dev-test-gui.sh

check: all test-host
	@file $(BUILD)/PacketLogger.dll $(BUILD)/Injector.exe \
		$(BUILD)/FakeGame.exe $(BUILD)/HookStubTest.exe

clean:
	rm -rf $(BUILD)
