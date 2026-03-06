"""LLM client and schemas for staged product-opportunity evaluation."""

from __future__ import annotations

import json
import ssl
import subprocess
from typing import Any, Literal, Protocol
from urllib import error as urlerror
from urllib import request as urlrequest

from pydantic import BaseModel, Field, ValidationError, model_validator

from .config import settings


class NewProductPayload(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    short_description: str = Field(min_length=1)
    full_description: str = Field(min_length=1)


class ScoreDimensions(BaseModel):
    development_difficulty: int = Field(ge=1, le=5)
    cold_start_cost: int = Field(ge=1, le=5)
    monetization_simplicity: int = Field(ge=1, le=5)
    maintenance_cost: int = Field(ge=1, le=5)
    vertical_focus: int = Field(ge=1, le=5)
    pain_severity: int = Field(ge=1, le=5)
    pain_investment: int = Field(ge=1, le=5)
    pain_frequency: int = Field(ge=1, le=5)
    pain_subjective: int = Field(ge=1, le=5)
    tam: int = Field(ge=1, le=5)
    sam: int = Field(ge=1, le=5)
    som: int = Field(ge=1, le=5)
    market_price: int = Field(ge=1, le=5)
    payment_habit: int = Field(ge=1, le=5)
    price_bandwidth: int = Field(ge=1, le=5)
    conversion: int = Field(ge=1, le=5)
    competition_direct: int = Field(ge=1, le=5)
    competition_head: int = Field(ge=1, le=5)
    competition_entry: int = Field(ge=1, le=5)
    competition_satisfaction: int = Field(ge=1, le=5)
    competition_complaint: int = Field(ge=1, le=5)
    competition_unmet: int = Field(ge=1, le=5)
    substitute_free: int = Field(ge=1, le=5)
    substitute_offline: int = Field(ge=1, le=5)
    self_skill: int = Field(ge=1, le=5)
    self_channel: int = Field(ge=1, le=5)
    self_resource: int = Field(ge=1, le=5)
    diff_core: int = Field(ge=1, le=5)
    diff_moat: int = Field(ge=1, le=5)
    diff_perceived: int = Field(ge=1, le=5)
    mvp_speed: int = Field(ge=1, le=5)
    validation_cost: int = Field(ge=1, le=5)
    iteration_speed: int = Field(ge=1, le=5)


class PrescreenLLMResult(BaseModel):
    pass_prescreen: bool
    prescreen_score: float = Field(ge=1, le=5)
    reason: str = ""


class MatchLLMResult(BaseModel):
    decision: Literal["matched", "new"]
    matched_product_id: int | None = None
    reason: str = ""

    @model_validator(mode="after")
    def _validate_payload(self) -> "MatchLLMResult":
        if self.decision == "matched" and self.matched_product_id is None:
            raise ValueError("matched_product_id is required when decision=matched")
        if self.decision == "new":
            self.matched_product_id = None
        return self


class ScoreLLMResult(BaseModel):
    personal_fit_score: float = Field(ge=1, le=5)
    value_score: float = Field(ge=1, le=5)
    competition_opportunity_score: float = Field(ge=1, le=5)
    self_control_score: float = Field(ge=1, le=5)
    total_score: float = Field(ge=0, le=100)
    dimensions: ScoreDimensions
    evidence: dict[str, Any] = Field(default_factory=dict)


class OpportunityLLM(Protocol):
    def prescreen(
        self,
        *,
        note: dict[str, Any],
        comments: list[dict[str, Any]],
        prescreen_threshold: float,
    ) -> PrescreenLLMResult:
        ...

    def match_existing(
        self,
        *,
        note: dict[str, Any],
        comments: list[dict[str, Any]],
        existing_products: list[dict[str, Any]],
        match_threshold: float,
    ) -> MatchLLMResult:
        ...

    def design_product(
        self,
        *,
        note: dict[str, Any],
        comments: list[dict[str, Any]],
    ) -> NewProductPayload:
        ...

    def score_product(
        self,
        *,
        product: dict[str, Any],
        supporting_notes: list[dict[str, Any]],
        supporting_comments: list[dict[str, Any]],
    ) -> ScoreLLMResult:
        ...


_BUILDER_PROFILE_PROMPT = """
固定背景（必须纳入判断）：
- 决策者是一名个人独立开发者，同时也是算法工程师。
- 更擅长：AI/LLM 应用、自动化流程、数据处理、信息提取与分析、工作流编排、后端工具型产品。
- 不应优先：重线下交付、重实施、重 BD 销售、重客服运营、重资质审批、重硬件供应链。
- 判断标准不仅需要“市场上是否有人需要”，而且需要考虑“这个人是否有优势把它做成、上线、迭代并变现”。
""".strip()

_PRESCREEN_SYSTEM_PROMPT = f"""
你负责【第一步：初筛】。
目标：判断该 note/comments 是否值得进入产品化流程。
{_BUILDER_PROFILE_PROMPT}
要求：
1) 输出 pass_prescreen(bool)、prescreen_score(1-5)、reason
2) 参考输入阈值 prescreen_threshold，但不要机械照搬
3) 偏向独立开发者可做的小而深机会
4) 优先放行能发挥 AI/自动化/数据处理优势的机会
5) 若机会主要依赖线下履约、重运营、重销售或重资质，必须谨慎甚至直接淘汰
严格输出 JSON 对象。
""".strip()

_MATCH_SYSTEM_PROMPT = """
你负责【第二步：已有产品匹配】。
目标：判断需求是否可由已有产品解决。
要求：
1) 输出 decision: matched 或 new
2) 若 matched，必须给 matched_product_id
3) 输出 reason
4) 使用输入 match_threshold 作为判断参考
严格输出 JSON 对象。
""".strip()

_DESIGN_SYSTEM_PROMPT = f"""
你负责【第三步：新产品设计】。
目标：当无法匹配已有产品时，设计一个独立开发者可落地的新产品。
{_BUILDER_PROFILE_PROMPT}
要求：
1) 输出 name / short_description / full_description
2) 强调低维护、快上线、可变现
3) 避免大而全、重线下、重资质
4) 优先设计能发挥 AI/自动化/数据处理/后端工程优势的产品形态
严格输出 JSON 对象。
""".strip()

_SCORE_SYSTEM_PROMPT = f"""
你负责【产品级多维打分】。
目标：基于候选产品及其支撑的 note/comment 证据，对“产品本身”给出评分。
{_BUILDER_PROFILE_PROMPT}
要求：
1) 输出 personal_fit_score/value_score/competition_opportunity_score/self_control_score(1-5)
2) 输出 total_score(0-100)
3) 输出 dimensions 全部细项，每项 1-5
4) 输出 evidence 对象，强调产品化、可落地性、变现逻辑，以及与该个人画像的技能匹配度
5) 不要逐条评价 note，要做产品层综合判断
6) personal_fit_score 必须重点衡量：该机会是否适合一个擅长 AI/自动化/数据处理/后端工程的人来做
7) 对重线下交付、重销售 BD、重客服运营、重实施、重资质审批、重硬件供应链方向要明显降分
8) 做分数校准，避免普遍高分：
   - <60：需求弱或商业化困难，不建议做
   - 60-74：有潜力但风险高，需强验证
   - 75-84：可做但仍有明显不确定性
   - 85+：证据充分且差异化清晰的强机会
9) 若证据不足、需求含混、竞争过强，必须明确降分
严格输出 JSON 对象。
""".strip()

_PRESCREEN_SCHEMA_HINT = {
    "pass_prescreen": "boolean",
    "prescreen_score": "number 1-5",
    "reason": "string",
}

_MATCH_SCHEMA_HINT = {
    "decision": "matched|new",
    "matched_product_id": "int|null",
    "reason": "string",
}

_DESIGN_SCHEMA_HINT = {
    "name": "string",
    "short_description": "string",
    "full_description": "string",
}

_SCORE_SCHEMA_HINT = {
    "personal_fit_score": "number 1-5",
    "value_score": "number 1-5",
    "competition_opportunity_score": "number 1-5",
    "self_control_score": "number 1-5",
    "total_score": "number 0-100",
    "dimensions": {
        "development_difficulty": "1-5",
        "cold_start_cost": "1-5",
        "monetization_simplicity": "1-5",
        "maintenance_cost": "1-5",
        "vertical_focus": "1-5",
        "pain_severity": "1-5",
        "pain_investment": "1-5",
        "pain_frequency": "1-5",
        "pain_subjective": "1-5",
        "tam": "1-5",
        "sam": "1-5",
        "som": "1-5",
        "market_price": "1-5",
        "payment_habit": "1-5",
        "price_bandwidth": "1-5",
        "conversion": "1-5",
        "competition_direct": "1-5",
        "competition_head": "1-5",
        "competition_entry": "1-5",
        "competition_satisfaction": "1-5",
        "competition_complaint": "1-5",
        "competition_unmet": "1-5",
        "substitute_free": "1-5",
        "substitute_offline": "1-5",
        "self_skill": "1-5",
        "self_channel": "1-5",
        "self_resource": "1-5",
        "diff_core": "1-5",
        "diff_moat": "1-5",
        "diff_perceived": "1-5",
        "mvp_speed": "1-5",
        "validation_cost": "1-5",
        "iteration_speed": "1-5",
    },
    "evidence": "object",
}


class OpenAIOpportunityLLM:
    """OpenAI-compatible staged evaluator for opportunity scoring."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        timeout_seconds: int,
        temperature: float,
    ) -> None:
        token = api_key.strip()
        if not token:
            raise ValueError("OPPORTUNITY_LLM_API_KEY is required when OPPORTUNITY_LLM_PROVIDER=openai")
        self.api_key = token
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_seconds = max(5, int(timeout_seconds))
        self.temperature = float(temperature)
        self._ssl_context = ssl.create_default_context()
        self._ssl_context.minimum_version = ssl.TLSVersion.TLSv1_2

    def prescreen(
        self,
        *,
        note: dict[str, Any],
        comments: list[dict[str, Any]],
        prescreen_threshold: float,
    ) -> PrescreenLLMResult:
        payload = {
            "note": note,
            "comments": comments[:20],
            "prescreen_threshold": float(prescreen_threshold),
            "output_schema": _PRESCREEN_SCHEMA_HINT,
        }
        data = self._chat_json(_PRESCREEN_SYSTEM_PROMPT, payload)
        return self._validate(PrescreenLLMResult, data)

    def match_existing(
        self,
        *,
        note: dict[str, Any],
        comments: list[dict[str, Any]],
        existing_products: list[dict[str, Any]],
        match_threshold: float,
    ) -> MatchLLMResult:
        payload = {
            "note": note,
            "comments": comments[:20],
            "existing_products": self._compact_products(existing_products),
            "match_threshold": float(match_threshold),
            "output_schema": _MATCH_SCHEMA_HINT,
        }
        data = self._chat_json(_MATCH_SYSTEM_PROMPT, payload)
        return self._validate(MatchLLMResult, data)

    def design_product(
        self,
        *,
        note: dict[str, Any],
        comments: list[dict[str, Any]],
    ) -> NewProductPayload:
        payload = {
            "note": note,
            "comments": comments[:20],
            "output_schema": _DESIGN_SCHEMA_HINT,
        }
        data = self._chat_json(_DESIGN_SYSTEM_PROMPT, payload)
        return self._validate(NewProductPayload, data)

    def score_product(
        self,
        *,
        product: dict[str, Any],
        supporting_notes: list[dict[str, Any]],
        supporting_comments: list[dict[str, Any]],
    ) -> ScoreLLMResult:
        payload = {
            "product": product,
            "supporting_notes": supporting_notes[:40],
            "supporting_comments": supporting_comments[:80],
            "output_schema": _SCORE_SCHEMA_HINT,
        }
        data = self._chat_json(_SCORE_SYSTEM_PROMPT, payload)
        score_payload = self._find_score_payload(data)
        if score_payload is not None:
            data = score_payload
        return self._validate(ScoreLLMResult, data)

    def _chat_json(self, system_prompt: str, user_payload: dict[str, Any]) -> dict[str, Any]:
        body = {
            "model": self.model,
            "temperature": self.temperature,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
            ],
        }
        response = self._post_chat_completion(body)
        return self._parse_response_payload(response)

    def _post_chat_completion(self, body: dict[str, Any]) -> dict[str, Any]:
        endpoint = f"{self.base_url}/chat/completions"
        request_body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = urlrequest.Request(
            endpoint,
            data=request_body,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "User-Agent": "rednote-spider/1.0",
            },
        )
        try:
            with urlrequest.urlopen(req, timeout=self.timeout_seconds, context=self._ssl_context) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urlerror.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise ValueError(f"llm api http {exc.code}: {detail[:400]}") from exc
        except urlerror.URLError as exc:
            try:
                return self._post_chat_completion_via_curl(endpoint=endpoint, request_body=request_body)
            except ValueError as curl_exc:
                raise ValueError(
                    f"llm api connection error: {exc.reason}; curl fallback failed: {curl_exc}"
                ) from exc
        except json.JSONDecodeError as exc:
            raise ValueError("llm api returned invalid json payload") from exc
        except Exception as exc:  # noqa: BLE001
            try:
                return self._post_chat_completion_via_curl(endpoint=endpoint, request_body=request_body)
            except ValueError as curl_exc:
                raise ValueError(f"llm api connection error: {exc}; curl fallback failed: {curl_exc}") from exc

    def _post_chat_completion_via_curl(self, *, endpoint: str, request_body: bytes) -> dict[str, Any]:
        result = subprocess.run(
            [
                "curl",
                "--silent",
                "--show-error",
                "--location",
                "--max-time",
                str(self.timeout_seconds),
                "--http1.1",
                "--tlsv1.2",
                "--retry",
                "2",
                "--retry-delay",
                "1",
                "--retry-all-errors",
                "-X",
                "POST",
                "-H",
                f"Authorization: Bearer {self.api_key}",
                "-H",
                "Content-Type: application/json",
                "-H",
                "User-Agent: rednote-spider/1.0",
                "--data-binary",
                "@-",
                "-w",
                "\\n%{http_code}",
                endpoint,
            ],
            input=request_body,
            capture_output=True,
            check=False,
        )
        stdout = result.stdout.decode("utf-8", errors="replace")
        stderr = result.stderr.decode("utf-8", errors="replace")
        if not stdout.strip():
            detail = stderr.strip() or f"curl exited with code {result.returncode}"
            raise ValueError(f"llm api connection error: {detail[:400]}")

        body_text, _, status_text = stdout.rpartition("\n")
        if not body_text:
            body_text = stdout
            status_code = 0
        else:
            try:
                status_code = int(status_text.strip())
            except ValueError:
                status_code = 0

        if status_code >= 400:
            raise ValueError(f"llm api http {status_code}: {body_text[:400]}")
        if result.returncode != 0:
            detail = stderr.strip() or body_text.strip()
            raise ValueError(f"llm api connection error: {detail[:400]}")

        try:
            return json.loads(body_text)
        except json.JSONDecodeError as exc:
            raise ValueError("llm api returned invalid json payload") from exc

    @staticmethod
    def _validate(model: type[BaseModel], payload: dict[str, Any]) -> Any:
        try:
            return model.model_validate(payload)
        except ValidationError as exc:
            raise ValueError(f"invalid llm response schema: {exc}") from exc

    @staticmethod
    def _parse_response_payload(response: dict[str, Any]) -> dict[str, Any]:
        choices = response.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ValueError("llm response missing choices")
        message = choices[0].get("message")
        if not isinstance(message, dict):
            raise ValueError("llm response missing message")
        text = OpenAIOpportunityLLM._normalize_content_text(message.get("content"))
        obj = OpenAIOpportunityLLM._extract_json_object(text)
        if not isinstance(obj, dict):
            raise ValueError("llm response json root must be object")
        return obj

    @staticmethod
    def _normalize_content_text(content: Any) -> str:
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            chunks: list[str] = []
            for row in content:
                if isinstance(row, dict) and isinstance(row.get("text"), str):
                    chunks.append(row["text"])
            text = "".join(chunks).strip()
            if text:
                return text
        raise ValueError("llm response missing content text")

    @staticmethod
    def _extract_json_object(text: str) -> Any:
        stripped = text.strip()
        if stripped.startswith("{") and stripped.endswith("}"):
            return json.loads(stripped)
        start = stripped.find("{")
        while start >= 0:
            depth = 0
            for idx in range(start, len(stripped)):
                char = stripped[idx]
                if char == "{":
                    depth += 1
                elif char == "}":
                    depth -= 1
                    if depth == 0:
                        candidate = stripped[start : idx + 1]
                        try:
                            return json.loads(candidate)
                        except json.JSONDecodeError:
                            break
            start = stripped.find("{", start + 1)
        raise ValueError("llm response does not contain valid json object")

    @staticmethod
    def _compact_products(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        compact: list[dict[str, Any]] = []
        for row in rows[:80]:
            compact.append(
                {
                    "id": row.get("id"),
                    "name": str(row.get("name") or "")[:255],
                    "short_description": str(row.get("short_description") or "")[:800],
                    "full_description": str(row.get("full_description") or "")[:1500],
                }
            )
        return compact

    @staticmethod
    def _find_score_payload(payload: Any, *, _depth: int = 0) -> dict[str, Any] | None:
        if _depth > 4:
            return None
        required = {
            "personal_fit_score",
            "value_score",
            "competition_opportunity_score",
            "self_control_score",
            "total_score",
            "dimensions",
        }
        if isinstance(payload, dict):
            if required.issubset(payload.keys()):
                return payload
            for value in payload.values():
                candidate = OpenAIOpportunityLLM._find_score_payload(value, _depth=_depth + 1)
                if candidate is not None:
                    return candidate
            return None
        if isinstance(payload, list):
            for value in payload[:10]:
                candidate = OpenAIOpportunityLLM._find_score_payload(value, _depth=_depth + 1)
                if candidate is not None:
                    return candidate
        return None


class MockOpportunityLLM:
    """Deterministic local evaluator used by tests and offline smoke runs."""

    def prescreen(
        self,
        *,
        note: dict[str, Any],
        comments: list[dict[str, Any]],
        prescreen_threshold: float,
    ) -> PrescreenLLMResult:
        text = self._merged_text(note=note, comments=comments)
        has_signal = any(token in text for token in ("焦虑", "痛苦", "麻烦", "踩坑", "求推荐", "急需"))
        score = 4.2 if has_signal else 2.2
        return PrescreenLLMResult(
            pass_prescreen=score >= float(prescreen_threshold),
            prescreen_score=score,
            reason="mock prescreen",
        )

    def match_existing(
        self,
        *,
        note: dict[str, Any],
        comments: list[dict[str, Any]],
        existing_products: list[dict[str, Any]],
        match_threshold: float,  # noqa: ARG002
    ) -> MatchLLMResult:
        text = self._merged_text(note=note, comments=comments)
        for row in existing_products:
            product_text = f"{row.get('name', '')} {row.get('short_description', '')}"
            if ("租房" in text and "租房" in product_text) or ("通勤" in text and "通勤" in product_text):
                return MatchLLMResult(
                    decision="matched",
                    matched_product_id=int(row["id"]),
                    reason="mock matched",
                )
        return MatchLLMResult(decision="new", reason="mock new")

    def design_product(
        self,
        *,
        note: dict[str, Any],
        comments: list[dict[str, Any]],
    ) -> NewProductPayload:
        text = self._merged_text(note=note, comments=comments)
        focus = "细分需求"
        for token in ("通勤", "租房", "备考", "求职", "副业", "育儿"):
            if token in text:
                focus = token
                break
        return NewProductPayload(
            name=f"{focus}效率助手",
            short_description=f"面向{focus}场景，解决高频低效问题的轻量化工具。",
            full_description=(
                f"产品定位：{focus}场景的独立开发者可运营产品。\n"
                "核心功能：问题诊断、操作清单、自动化模板。\n"
                "变现路径：模板包单次付费 + 订阅。\n"
                "维护策略：自动化优先，低客服负担。"
            ),
        )

    def score_product(
        self,
        *,
        product: dict[str, Any],
        supporting_notes: list[dict[str, Any]],  # noqa: ARG002
        supporting_comments: list[dict[str, Any]],  # noqa: ARG002
    ) -> ScoreLLMResult:
        product_text = f"{product.get('name', '')} {product.get('short_description', '')}"
        score = 4 if any(token in product_text for token in ("通勤", "租房", "求职", "副业")) else 3
        return ScoreLLMResult(
            personal_fit_score=float(score),
            value_score=float(score),
            competition_opportunity_score=float(score),
            self_control_score=float(score),
            total_score=82.0 if score >= 4 else 63.0,
            dimensions=ScoreDimensions(**{key: score for key in ScoreDimensions.model_fields}),
            evidence={"mode": "mock", "scope": "product"},
        )

    @staticmethod
    def _merged_text(*, note: dict[str, Any], comments: list[dict[str, Any]]) -> str:
        return "\n".join(
            [
                str(note.get("title") or ""),
                str(note.get("content") or ""),
                " ".join(str(row.get("content") or "") for row in comments[:20]),
            ]
        )


def build_opportunity_llm() -> OpportunityLLM:
    provider = settings.opportunity_llm_provider.strip().lower()
    if provider == "openai":
        return OpenAIOpportunityLLM(
            api_key=settings.opportunity_llm_api_key,
            base_url=settings.opportunity_llm_base_url,
            model=settings.opportunity_llm_model,
            timeout_seconds=settings.opportunity_llm_timeout_seconds,
            temperature=settings.opportunity_llm_temperature,
        )
    if provider == "mock":
        return MockOpportunityLLM()
    raise ValueError(f"unsupported opportunity llm provider: {provider}")
