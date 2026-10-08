"""Bindings to the public C API. The service owns all prepared handles."""

import ctypes as C
import os
from pathlib import Path


class Info(C.Structure):
    _fields_ = [(name, C.c_uint64) for name in
                ("device", "inode", "size", "filesystem_type", "bytes_written")] + [
                    (name, C.c_uint32) for name in
                    ("random_passes", "zero_pass", "unlinked", "directory_synced")]

    def as_dict(self):
        result = {name: getattr(self, name) for name, _ in self._fields_}
        for name in ("zero_pass", "unlinked", "directory_synced"):
            result[name] = bool(result[name])
        result["filesystem_type"] = hex(result["filesystem_type"])
        return result


class NativeError(Exception):
    def __init__(self, message, details=None):
        super().__init__(message)
        self.details = details or {}


class Engine:
    def __init__(self, root, max_bytes, library=None):
        base = Path(__file__).resolve().parent.parent
        candidates = [base / "libagent13.so", base / "build/libagent13.so"]
        selected = Path(library) if library else next((p for p in candidates if p.exists()), candidates[0])
        self.lib = C.CDLL(str(selected), use_errno=True)
        self.lib.a13_abi_version.argtypes = []
        self.lib.a13_abi_version.restype = C.c_uint32
        if self.lib.a13_abi_version() != 1:
            raise RuntimeError("C library ABI mismatch: rebuild agent13")
        self.lib.a13_root_open.argtypes = [C.c_char_p, C.c_char_p, C.c_size_t]
        self.lib.a13_root_open.restype = C.c_int
        self.lib.a13_prepare.argtypes = [C.c_int, C.c_char_p, C.c_uint64,
                                        C.POINTER(C.c_void_p), C.POINTER(Info), C.c_char_p, C.c_size_t]
        self.lib.a13_prepare.restype = C.c_int
        self.lib.a13_execute.argtypes = [C.c_void_p, C.c_uint32, C.c_uint32,
                                        C.POINTER(Info), C.c_char_p, C.c_size_t]
        self.lib.a13_execute.restype = C.c_int
        self.lib.a13_job_free.argtypes = [C.c_void_p]
        self.lib.a13_job_free.restype = None
        self.max_bytes = max_bytes
        error = C.create_string_buffer(256)
        self.root_fd = self.lib.a13_root_open(os.fsencode(root), error, len(error))
        if self.root_fd < 0:
            raise NativeError(error.value.decode("utf-8", "replace"))

    def prepare(self, path):
        handle, info, error = C.c_void_p(), Info(), C.create_string_buffer(256)
        if self.lib.a13_prepare(self.root_fd, path.encode("utf-8"), self.max_bytes,
                               C.byref(handle), C.byref(info), error, len(error)) < 0:
            raise NativeError(error.value.decode("utf-8", "replace"))
        return handle, info.as_dict()

    def execute(self, handle, passes, zero):
        info, error = Info(), C.create_string_buffer(256)
        if self.lib.a13_execute(handle, passes, int(zero), C.byref(info), error, len(error)) < 0:
            raise NativeError(error.value.decode("utf-8", "replace"), info.as_dict())
        return info.as_dict()

    def free(self, handle):
        self.lib.a13_job_free(handle)

    def close(self):
        if self.root_fd >= 0:
            os.close(self.root_fd)
            self.root_fd = -1
