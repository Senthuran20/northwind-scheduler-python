import io
import json
import urllib.error

import pytest

from scheduler import real_model
from scheduler.model import ModelRequestError


def _model(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "https://example.test/v1/")
    monkeypatch.setenv("LLM_API_KEY", "test-token")
    monkeypatch.setenv("LLM_MODEL", "test-model")
    return real_model.OpenAICompatibleModel()


def test_real_model_sends_authorization_and_uses_a_timeout(monkeypatch):
    captured = {}
    response = io.BytesIO(
        json.dumps(
            {"choices": [{"message": {"content": '{"final":"ok"}'}}]}
        ).encode()
    )

    def fake_urlopen(request, timeout):
        captured["authorization"] = request.get_header("Authorization")
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        return response

    monkeypatch.setattr(real_model.urllib.request, "urlopen", fake_urlopen)

    result = _model(monkeypatch).complete([{"role": "user", "content": "hello"}])

    assert result == '{"final":"ok"}'
    assert captured == {
        "authorization": "Bearer test-token",
        "url": "https://example.test/v1/chat/completions",
        "timeout": real_model.REQUEST_TIMEOUT_SECONDS,
    }


def test_real_model_maps_endpoint_errors_to_a_controlled_exception(monkeypatch):
    def fail_request(*args):
        assert args[1] == real_model.REQUEST_TIMEOUT_SECONDS
        raise urllib.error.URLError("offline")

    monkeypatch.setattr(real_model.urllib.request, "urlopen", fail_request)

    with pytest.raises(ModelRequestError, match="endpoint failed"):
        _model(monkeypatch).complete([{"role": "user", "content": "hello"}])
