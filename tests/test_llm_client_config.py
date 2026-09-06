"""通用 LLM/BYOK 配置合同测试，不发起网络请求。"""

from __future__ import annotations

import pytest

from core.llm_client import LLMClient, LLMConfigError, get_reasoner_model, llm_runtime_status


LLM_ENV_NAMES = (
    "LEARNBUDDY_LLM_API_KEY",
    "LEARNBUDDY_LLM_BASE_URL",
    "LEARNBUDDY_LLM_MODEL",
    "LEARNBUDDY_LLM_REASONER_MODEL",
    "LEARNBUDDY_LLM_PROVIDER",
    "DEEPSEEK_API_KEY",
    "DEEPSEEK_BASE_URL",
    "DEEPSEEK_MODEL",
    "DEEPSEEK_REASONER_MODEL",
    "ITUTOR_LESSON_REASONER",
)


@pytest.fixture(autouse=True)
def clean_llm_env(monkeypatch):
    for name in LLM_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


def test_unified_learnbuddy_config_takes_precedence(monkeypatch):
    monkeypatch.setenv("LEARNBUDDY_LLM_API_KEY", "owner-key")
    monkeypatch.setenv("LEARNBUDDY_LLM_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.setenv("LEARNBUDDY_LLM_MODEL", "gpt-example")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "legacy-key")
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-chat")

    client = LLMClient()

    assert client.api_key == "owner-key"
    assert client.base_url == "https://api.openai.com/v1"
    assert client.model == "gpt-example"
    assert client.provider == "OpenAI"
    assert get_reasoner_model() is None


def test_legacy_deepseek_config_remains_compatible(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "legacy-key")

    client = LLMClient()

    assert client.api_key == "legacy-key"
    assert client.base_url == "https://api.deepseek.com"
    assert client.model == "deepseek-chat"
    assert get_reasoner_model() == "deepseek-reasoner"


def test_explicit_reasoner_works_for_any_compatible_provider(monkeypatch):
    monkeypatch.setenv("LEARNBUDDY_LLM_BASE_URL", "https://models.example.test/v1")
    monkeypatch.setenv("LEARNBUDDY_LLM_MODEL", "chat-model")
    monkeypatch.setenv("LEARNBUDDY_LLM_REASONER_MODEL", "reasoning-model")

    assert get_reasoner_model() == "reasoning-model"


def test_runtime_status_never_exposes_api_key(monkeypatch):
    monkeypatch.setenv("LEARNBUDDY_LLM_API_KEY", "do-not-leak-this")
    monkeypatch.setenv("LEARNBUDDY_LLM_BASE_URL", "https://models.example.test/v1")
    monkeypatch.setenv("LEARNBUDDY_LLM_MODEL", "private-model")

    status = llm_runtime_status()

    assert status == {
        "configured": True,
        "provider": "模型服务",
        "model": "private-model",
    }
    assert "do-not-leak-this" not in repr(status)


def test_missing_key_has_provider_neutral_actionable_error():
    with pytest.raises(LLMConfigError, match="LEARNBUDDY_LLM_API_KEY"):
        LLMClient()
