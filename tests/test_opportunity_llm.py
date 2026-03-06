from __future__ import annotations

import json
from typing import Any

from urllib import error as urlerror

from rednote_spider.opportunity_llm import OpenAIOpportunityLLM, ScoreDimensions


class _FakeResponse:
    def __init__(self, payload: dict[str, Any]):
        self._payload = payload

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
        return None

    def read(self) -> bytes:
        return json.dumps(self._payload, ensure_ascii=False).encode("utf-8")


def _build_llm() -> OpenAIOpportunityLLM:
    return OpenAIOpportunityLLM(
        api_key="test-key",
        base_url="https://example.com/v1",
        model="mock-model",
        timeout_seconds=30,
        temperature=0.1,
    )


def _score_payload(score: int = 4) -> dict[str, Any]:
    return {
        "personal_fit_score": float(score),
        "value_score": float(score),
        "competition_opportunity_score": float(score),
        "self_control_score": float(score),
        "total_score": 80.0,
        "dimensions": {key: score for key in ScoreDimensions.model_fields},
        "evidence": {"reason": "ok"},
    }


def test_post_chat_completion_uses_tls_context_and_user_agent(monkeypatch):
    llm = _build_llm()
    captured: dict[str, Any] = {}

    def _fake_urlopen(req, timeout: int, context):  # noqa: ANN001
        captured["timeout"] = timeout
        captured["context"] = context
        captured["ua"] = req.get_header("User-agent")
        return _FakeResponse({"choices": [{"message": {"content": "{}"}}]})

    monkeypatch.setattr("rednote_spider.opportunity_llm.urlrequest.urlopen", _fake_urlopen)

    payload = llm._post_chat_completion({"messages": [], "model": "m"})
    assert isinstance(captured["context"], object)
    assert captured["timeout"] == 30
    assert captured["ua"] == "rednote-spider/1.0"
    assert "choices" in payload


def test_post_chat_completion_falls_back_to_curl_when_ssl_eof(monkeypatch):
    llm = _build_llm()

    def _raise_ssl_eof(*args, **kwargs):  # noqa: ANN002, ANN003
        raise urlerror.URLError("[SSL: UNEXPECTED_EOF_WHILE_READING] EOF occurred in violation of protocol")

    monkeypatch.setattr("rednote_spider.opportunity_llm.urlrequest.urlopen", _raise_ssl_eof)

    class _Result:
        returncode = 0
        stdout = b'{\"choices\":[{\"message\":{\"content\":\"{}\"}}]}\n200'
        stderr = b""

    def _fake_run(*args, **kwargs):  # noqa: ANN002, ANN003
        return _Result()

    monkeypatch.setattr("rednote_spider.opportunity_llm.subprocess.run", _fake_run)

    payload = llm._post_chat_completion({"messages": [], "model": "m"})
    assert "choices" in payload


def test_prescreen_prompt_mentions_builder_profile(monkeypatch):
    llm = _build_llm()
    captured: dict[str, Any] = {}

    def _fake_chat_json(system_prompt: str, user_payload: dict[str, Any]) -> dict[str, Any]:
        captured["system_prompt"] = system_prompt
        captured["user_payload"] = user_payload
        return {"pass_prescreen": True, "prescreen_score": 4.2, "reason": "ok"}

    monkeypatch.setattr(llm, "_chat_json", _fake_chat_json)

    llm.prescreen(
        note={"title": "通勤焦虑", "content": "每天都很麻烦"},
        comments=[],
        prescreen_threshold=3.2,
    )

    assert "个人独立开发者，同时也是算法工程师" in captured["system_prompt"]
    assert "AI/LLM 应用" in captured["system_prompt"]
    assert "是否有优势把它做成、上线、迭代并变现" in captured["system_prompt"]


def test_find_score_payload_can_extract_nested_score_object():
    payload = {
        "product": {"id": 1, "name": "x"},
        "assessment": {
            "scores": {
                "personal_fit_score": 4.0,
                "value_score": 4.0,
                "competition_opportunity_score": 4.0,
                "self_control_score": 4.0,
                "total_score": 80.0,
                "dimensions": {"development_difficulty": 3},
            }
        },
    }
    extracted = OpenAIOpportunityLLM._find_score_payload(payload)
    assert extracted is not None
    assert extracted["total_score"] == 80.0


def test_score_prompt_mentions_builder_profile(monkeypatch):
    llm = _build_llm()
    captured: dict[str, Any] = {}

    def _fake_chat_json(system_prompt: str, user_payload: dict[str, Any]) -> dict[str, Any]:
        captured["system_prompt"] = system_prompt
        captured["user_payload"] = user_payload
        return _score_payload()

    monkeypatch.setattr(llm, "_chat_json", _fake_chat_json)

    llm.score_product(
        product={"id": 1, "name": "通勤效率助手", "short_description": "减少通勤摩擦"},
        supporting_notes=[{"note_id": "n1", "title": "通勤焦虑", "content": "太麻烦了"}],
        supporting_comments=[{"comment_id": "c1", "content": "想要自动化解决"}],
    )

    assert "个人独立开发者，同时也是算法工程师" in captured["system_prompt"]
    assert "personal_fit_score 必须重点衡量" in captured["system_prompt"]
    assert "重销售 BD" in captured["system_prompt"]


def test_post_chat_completion_falls_back_to_curl_on_unexpected_exception(monkeypatch):
    llm = _build_llm()

    def _raise_remote_closed(*args, **kwargs):  # noqa: ANN002, ANN003
        raise RuntimeError("Remote end closed connection without response")

    monkeypatch.setattr("rednote_spider.opportunity_llm.urlrequest.urlopen", _raise_remote_closed)

    class _Result:
        returncode = 0
        stdout = b'{\"choices\":[{\"message\":{\"content\":\"{}\"}}]}\n200'
        stderr = b""

    def _fake_run(*args, **kwargs):  # noqa: ANN002, ANN003
        return _Result()

    monkeypatch.setattr("rednote_spider.opportunity_llm.subprocess.run", _fake_run)

    payload = llm._post_chat_completion({"messages": [], "model": "m"})
    assert "choices" in payload
