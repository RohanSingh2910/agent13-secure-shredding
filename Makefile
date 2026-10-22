CC = gcc
PYTHON ?= python3
CPPFLAGS += -Iinclude
CFLAGS ?= -O2 -g
CFLAGS += -std=c11 -Wall -Wextra -Wpedantic -Werror -fstack-protector-strong
LDFLAGS += -Wl,-z,relro,-z,now

.PHONY: all native-test test check clean
all: build/libagent13_shred.so

build:
	mkdir -p build

build/libagent13_shred.so: src/c/agent13_shred.c include/agent13_shred.h | build
	$(CC) $(CPPFLAGS) $(CFLAGS) -fPIC -shared $(LDFLAGS) -o $@ $<

build/test_native: tests/test_native.c src/c/agent13_shred.c include/agent13_shred.h | build
	$(CC) $(CPPFLAGS) $(CFLAGS) $(LDFLAGS) -o $@ tests/test_native.c src/c/agent13_shred.c -Wl,--wrap=write,--wrap=getrandom,--wrap=fsync,--wrap=renameat2

native-test: build/test_native
	./build/test_native

test: all native-test
	$(PYTHON) -m unittest discover -s tests -v

check: test
	$(PYTHON) -m compileall -q agent13

clean:
	$(PYTHON) -c 'import shutil; shutil.rmtree("build", ignore_errors=True)'
