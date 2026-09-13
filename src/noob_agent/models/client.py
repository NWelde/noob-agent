"""Provider-neutral model client with an offline default.

Nothing here authenticates, imports an optional package, or makes a network
call unless a provider is explicitly configured, which it is not by default.
The Builder depends on this Protocol rather than on a vendor SDK, so the whole
agent is testable against a deterministic stand-in with no credentials.

W&B Inference speaks the OpenAI chat-completions API, so the one adapter here
is built on the `openai` client from the optional `integrations` dependency
group and is imported lazily, only when that provider is selected.
"""

from __future__ import annotations

import importlib
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from noob_agent.settings import ModelSettings, WandbSettings


class ModelUnavailableError(RuntimeError):
    """No model provider is configured, or the configured one cannot be reached."""


class ModelRequest(BaseModel):
    """One bounded completion request.

    `thinking` is `None` to leave the provider's reasoning default untouched.
    A `response_schema` asks the provider for a reply constrained to that JSON
    Schema.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    system: str = Field(min_length=1)
    prompt: str = Field(min_length=1)
    max_output_tokens: int = Field(gt=0)
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    thinking: bool | None = None
    response_schema: dict[str, JsonValue] | None = None

    def options(self) -> dict[str, JsonValue]:
        """The request settings a Model call record keeps beside the prompt."""
        return {"thinking": self.thinking, "response_schema": self.response_schema}


class ModelResponse(BaseModel):
    """One completion, with the usage the Model call record needs.

    `input_tokens` and `output_tokens` stay zero when the provider reports no
    usage, so existing budget arithmetic is unchanged; `usage_reported` says
    whether those zeros are real. `reasoning` is the provider's separate
    reasoning text, which shares the output cap but is never part of `text`.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    model_id: str = Field(min_length=1)
    finish_reason: str | None = None
    reasoning: str | None = None
    usage_reported: bool = True


class ModelClient(Protocol):
    """Completes one bounded request through an approved provider."""

    async def complete(self, request: ModelRequest) -> ModelResponse:
        """Run one completion."""


class DisabledModelClient:
    """Safe default that never reaches a provider."""

    provider = "disabled"

    async def complete(self, request: ModelRequest) -> ModelResponse:
        del request
        raise ModelUnavailableError(
            "No model provider is configured. Set NOOB_AGENT_MODEL_PROVIDER and "
            "the matching credentials; see .env.example."
        )


class WandbInferenceClient:
    """W&B Inference through its OpenAI-compatible chat-completions endpoint."""

    provider = "wandb-inference"

    def __init__(self, model: ModelSettings, wandb: WandbSettings) -> None:
        if model.provider != "wandb-inference":
            raise ModelUnavailableError(
                f"WandbInferenceClient needs provider 'wandb-inference', got {model.provider!r}."
            )
        if not model.inference_model:
            raise ModelUnavailableError("NOOB_AGENT_INFERENCE_MODEL is not set.")
        if not wandb.api_key:
            raise ModelUnavailableError("WANDB_API_KEY is not set.")
        self._model = model
        self._wandb = wandb

    def _client(self) -> Any:
        try:
            openai: Any = importlib.import_module("openai")
        except ImportError as error:  # pragma: no cover - depends on optional install
            raise ModelUnavailableError(
                "The 'openai' package is required for W&B Inference. Install it with "
                "`uv sync --group dev --group integrations`."
            ) from error
        headers = (
            {"OpenAI-Project": self._model.inference_project}
            if self._model.inference_project
            else None
        )
        return openai.AsyncOpenAI(
            base_url=self._model.inference_base_url,
            api_key=self._wandb.api_key,
            default_headers=headers,
        )

    async def complete(self, request: ModelRequest) -> ModelResponse:
        """Run one completion against W&B Inference."""
        client = self._client()
        options: dict[str, Any] = {}
        if request.thinking is not None:
            # W&B Inference passes this to the model's chat template; verified for
            # DeepSeek-V4-Flash on 2026-09-13 (hackathon_plan.md section 22).
            options["extra_body"] = {"chat_template_kwargs": {"thinking": request.thinking}}
        if request.response_schema is not None:
            options["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "decision",
                    "strict": True,
                    "schema": request.response_schema,
                },
            }
        try:
            completion = await client.chat.completions.create(
                model=self._model.inference_model,
                messages=[
                    {"role": "system", "content": request.system},
                    {"role": "user", "content": request.prompt},
                ],
                max_tokens=request.max_output_tokens,
                temperature=request.temperature,
                **options,
            )
        finally:
            # Each call may run under a different event loop, so its HTTP client
            # is closed here rather than left for garbage collection.
            close = getattr(client, "close", None)
            if close is not None:
                await close()
        choice = completion.choices[0]
        usage = completion.usage
        input_tokens = getattr(usage, "prompt_tokens", None)
        output_tokens = getattr(usage, "completion_tokens", None)
        return ModelResponse(
            text=choice.message.content or "",
            input_tokens=input_tokens or 0,
            output_tokens=output_tokens or 0,
            model_id=completion.model or str(self._model.inference_model),
            finish_reason=getattr(choice, "finish_reason", None),
            reasoning=_reasoning_text(choice.message),
            usage_reported=isinstance(input_tokens, int) and isinstance(output_tokens, int),
        )


def _reasoning_text(message: Any) -> str | None:
    """The provider's separate reasoning text, under either field name it uses."""
    for field in ("reasoning", "reasoning_content"):
        value = getattr(message, field, None)
        if isinstance(value, str) and value:
            return value
    return None


def build_model_client(model: ModelSettings, wandb: WandbSettings) -> ModelClient:
    """Return the configured client, or the disabled default."""
    if model.provider == "wandb-inference":
        return WandbInferenceClient(model, wandb)
    return DisabledModelClient()
