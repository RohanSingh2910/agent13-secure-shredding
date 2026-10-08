CC ?= cc
CPPFLAGS += -Iinclude -D_FILE_OFFSET_BITS=64
CFLAGS ?= -O2 -g
CFLAGS += -std=c11 -Wall -Wextra -Wpedantic -Werror -fstack-protector-strong
LDFLAGS ?=
PREFIX ?= /usr
DESTDIR ?=
LIBDIR = $(PREFIX)/lib/agent13
PYTHON ?= python3

.PHONY: all test check install clean
all: build/libagent13.so build/libagent13.a

build:
	mkdir -p build

build/agent13.o: src/agent13.c include/agent13.h | build
	$(CC) $(CPPFLAGS) $(CFLAGS) -fPIC -c $< -o $@

build/libagent13.so: build/agent13.o
	$(CC) -shared $(LDFLAGS) -Wl,-z,relro,-z,now -Wl,-soname,libagent13.so $< -o $@

build/libagent13.a: build/agent13.o
	$(AR) rcs $@ $<

build/test_native: tests/test_native.c src/agent13.c include/agent13.h | build
	$(CC) $(CPPFLAGS) $(CFLAGS) tests/test_native.c src/agent13.c $(LDFLAGS) \
	    -Wl,--wrap=pwrite64,--wrap=fsync,--wrap=getrandom,--wrap=unlinkat -o $@

test: all build/test_native
	./build/test_native
	$(PYTHON) -m unittest discover -s tests -v

check: test
	$(PYTHON) -m compileall -q agent13 run_service.py examples/class_shell_adapter.py
	$(PYTHON) -m json.tool integration/tools.json >/dev/null

install: all
	install -d $(DESTDIR)$(LIBDIR)/agent13 $(DESTDIR)$(PREFIX)/bin \
	    $(DESTDIR)$(PREFIX)/include $(DESTDIR)$(PREFIX)/lib/systemd/user \
	    $(DESTDIR)$(PREFIX)/share/doc/agent13/integration
	install -m 644 agent13/*.py $(DESTDIR)$(LIBDIR)/agent13/
	install -m 644 build/libagent13.so run_service.py $(DESTDIR)$(LIBDIR)/
	install -m 644 build/libagent13.a $(DESTDIR)$(PREFIX)/lib/
	install -m 644 include/agent13.h $(DESTDIR)$(PREFIX)/include/
	install -m 755 bin/agent13 $(DESTDIR)$(PREFIX)/bin/agent13
	sed 's|@LIBDIR@|$(LIBDIR)|g' systemd/agent13.service.in >$(DESTDIR)$(PREFIX)/lib/systemd/user/agent13.service
	chmod 644 $(DESTDIR)$(PREFIX)/lib/systemd/user/agent13.service
	install -m 644 README.md ROADMAP.md docs/*.md $(DESTDIR)$(PREFIX)/share/doc/agent13/
	install -m 644 integration/tools.json $(DESTDIR)$(PREFIX)/share/doc/agent13/integration/

clean:
	$(RM) build/agent13.o build/libagent13.so build/libagent13.a build/test_native
