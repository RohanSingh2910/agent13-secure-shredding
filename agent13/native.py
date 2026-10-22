"""Small ctypes interface; all file mutation is implemented in C.

Direct callers must obtain confirmation before calling Backend.shred(). The
socket service supplies confirmation, authentication, and token handling.
"""

import ctypes as ct
import os
from pathlib import Path
import stat


class FileInfo(ct.Structure):
    _fields_ = [
        ("device", ct.c_uint64), ("inode", ct.c_uint64), ("size", ct.c_uint64),
        ("mtime_sec", ct.c_int64), ("mtime_nsec", ct.c_int64),
        ("ctime_sec", ct.c_int64), ("ctime_nsec", ct.c_int64),
        ("mode", ct.c_uint32), ("uid", ct.c_uint32),
        ("links", ct.c_uint32), ("reserved", ct.c_uint32),
    ]

    def as_dict(self):
        return {name: getattr(self, name) for name, _ in self._fields_ if name != "reserved"}


class ErrorInfo(ct.Structure):
    _fields_ = [
        ("code", ct.c_int32), ("system_errno", ct.c_int32),
        ("bytes_written", ct.c_uint64), ("unlinked", ct.c_int32),
        ("reserved", ct.c_int32), ("message", ct.c_char * 256),
        ("recovery_path", ct.c_char * 4096),
    ]


CODES = {1: "INVALID_PATH", 2: "NATIVE_IO", 3: "UNSAFE_TARGET",
         4: "FILE_CHANGED", 5: "FILE_BUSY"}


class NativeError(Exception):
    def __init__(self, code, message, **details):
        super().__init__(message)
        self.code, self.details = code, details

    def as_dict(self):
        return {"code": self.code, "message": str(self), "details": self.details}


def open_absolute_directory(path):
    """Open an absolute directory chain with O_NOFOLLOW at every component."""
    if not isinstance(path, str) or not path.startswith("/") or "\x00" in path:
        raise ValueError("Directory must be an absolute path")
    parts = path.split("/")[1:]
    if not parts or any(p in ("", ".", "..") for p in parts):
        raise ValueError("Directory needs canonical components and no trailing slash")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    fd = os.open("/", flags)
    try:
        for part in parts:
            next_fd = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        return fd
    except BaseException:
        os.close(fd)
        raise


class Backend:
    def __init__(self, allowed_root, library=None):
        if os.geteuid() == 0:
            raise ValueError("Run Agent 13 as an ordinary user, never root")
        self.allowed_root = os.fspath(allowed_root)
        if self.allowed_root == str(Path.home()):
            raise ValueError("Use a dedicated allowed directory, not your home directory")
        self.root_fd = open_absolute_directory(self.allowed_root)
        try:
            s = os.fstat(self.root_fd)
            if s.st_uid != os.geteuid() or stat.S_IMODE(s.st_mode) != 0o700:
                raise ValueError("Allowed directory must be owned by you with mode 0700")
            default = Path(__file__).resolve().parent.parent / "build/libagent13_shred.so"
            self.library = ct.CDLL(os.fspath(library or default))
            self.library.agent13_abi_version.argtypes = []
            self.library.agent13_abi_version.restype = ct.c_uint32
            if self.library.agent13_abi_version() != 1:
                raise ValueError("Incompatible Agent 13 C ABI")
            signature = [ct.c_int, ct.c_char_p, ct.POINTER(FileInfo), ct.POINTER(ErrorInfo)]
            for name in ("agent13_inspect_at", "agent13_shred_at"):
                function = getattr(self.library, name)
                function.argtypes = signature
                function.restype = ct.c_int
        except BaseException:
            self.close()
            raise

    def close(self):
        if self.root_fd >= 0:
            os.close(self.root_fd)
            self.root_fd = -1

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def relative_path(self, path):
        if not isinstance(path, str) or not path or "\x00" in path:
            raise NativeError("INVALID_PATH", "Provide a nonempty file path")
        if path.startswith("/"):
            prefix = self.allowed_root + "/"
            if not path.startswith(prefix):
                raise NativeError("INVALID_PATH", "Target is outside the allowed directory")
            path = path[len(prefix):]
        try:
            encoded = path.encode("utf-8")
        except UnicodeError as exc:
            raise NativeError("INVALID_PATH", "Path must be valid UTF-8") from exc
        parts = path.split("/")
        if len(encoded) >= 4096 or any(
            p in ("", ".", "..") or p.startswith(".agent13-") or
            len(p.encode("utf-8")) > 255 for p in parts
        ):
            raise NativeError("INVALID_PATH", "Traversal, empty components, and reserved names are forbidden")
        return path

    @staticmethod
    def _check(result, error):
        if result:
            raise NativeError(
                CODES.get(result, "NATIVE_ERROR"),
                error.message.decode("utf-8", "replace"),
                errno=error.system_errno, bytes_written=error.bytes_written,
                unlinked=bool(error.unlinked),
                recovery_path=error.recovery_path.decode("utf-8", "replace"),
            )

    def inspect(self, path):
        path = self.relative_path(path)
        info, error = FileInfo(), ErrorInfo()
        result = self.library.agent13_inspect_at(
            self.root_fd, path.encode("utf-8"), ct.byref(info), ct.byref(error))
        self._check(result, error)
        return info

    def shred(self, path, expected):
        """Destructive native operation; expected must come from inspect()."""
        path = self.relative_path(path)
        if not isinstance(expected, FileInfo):
            raise TypeError("expected must be a FileInfo from inspect()")
        error = ErrorInfo()
        result = self.library.agent13_shred_at(
            self.root_fd, path.encode("utf-8"), ct.byref(expected), ct.byref(error))
        self._check(result, error)
        return {"bytes_written": error.bytes_written, "unlinked": bool(error.unlinked),
                "passes": 1}
