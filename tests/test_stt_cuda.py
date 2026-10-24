from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

from fernruf.settings import Settings
from fernruf.stt import cuda_libs
from fernruf.stt.whisper import SttPreloadError, prepare_stt_for_call


def test_cuda_lib_preload_idempotent() -> None:
    cuda_libs._preloaded = False
    cuda_libs.preload_nvidia_cuda_libs()
    cuda_libs.preload_nvidia_cuda_libs()


def test_prepare_stt_cuda_required_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    if sys.platform != "linux":
        pytest.skip("CUDA wheel preload is Linux-only")
    import fernruf.stt.whisper as whisper_mod

    whisper_mod._model = None
    whisper_mod._active_device = None
    settings = Settings(_env_file=None, whisper_device="cuda", whisper_model="tiny")
    mock_model = MagicMock()
    mock_model.transcribe.return_value = ([], None)

    with patch("fernruf.stt.whisper._try_init_on_device", side_effect=RuntimeError("libcublas")):
        with pytest.raises(SttPreloadError, match="libcublas"):
            prepare_stt_for_call(settings)


def test_stt_device_alias_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FERNRUF_STT_DEVICE", "cpu")
    s = Settings(_env_file=None)
    assert s.whisper_device == "cpu"
