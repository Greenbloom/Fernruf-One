from __future__ import annotations

import ctypes
import logging
import sys
from pathlib import Path

LOG = logging.getLogger("fernruf.stt.cuda")

_preloaded = False

# Minimal set ctranslate2/faster-whisper need; avoid optional cudnn engine .so that require libcuda at import.
_CUBLAS_LIBS = ("libcublas.so.12", "libcublasLt.so.12", "libnvblas.so.12")
_CUDNN_LIBS = ("libcudnn.so.9",)


def _lib_dir(package_name: str) -> Path | None:
    try:
        import importlib

        pkg = importlib.import_module(package_name)
        base = getattr(pkg, "__path__", None)
        if not base:
            return None
        lib = Path(base[0]) / "lib"
        return lib if lib.is_dir() else None
    except ImportError:
        return None


def _dlopen(path: Path) -> None:
    try:
        ctypes.CDLL(str(path.resolve()), mode=ctypes.RTLD_GLOBAL)
        LOG.debug("preloaded %s", path.name)
    except OSError as exc:
        LOG.debug("skip preload %s: %s", path.name, exc)


def preload_nvidia_cuda_libs() -> None:
    """Load cuBLAS/cuDNN from pip wheels so ctranslate2 finds them without LD_LIBRARY_PATH."""
    global _preloaded
    if _preloaded or sys.platform != "linux":
        return
    _preloaded = True

    cublas_dir = _lib_dir("nvidia.cublas")
    cudnn_dir = _lib_dir("nvidia.cudnn")
    if cublas_dir is None and cudnn_dir is None:
        LOG.debug("nvidia-cublas-cu12 / nvidia-cudnn-cu12 not installed; skipping CUDA lib preload")
        return

    if cublas_dir:
        for name in _CUBLAS_LIBS:
            path = cublas_dir / name
            if path.is_file():
                _dlopen(path)
    if cudnn_dir:
        for name in _CUDNN_LIBS:
            path = cudnn_dir / name
            if path.is_file():
                _dlopen(path)
