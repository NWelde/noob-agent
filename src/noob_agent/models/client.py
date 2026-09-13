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

from pydantic import BaseModel, ConfigDict, Field

from noob_agent.settings import ModelSettings, WandbSettings


class ModelUnavailableError(RuntimeError):
    """No model provider is configured, or the configured one cannot be reached."""


class ModelRequest(BaseModel):
    """One bounded completion request."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    system: str = Field(min_length=1)
    prompt: str = Field(min_length=1)
    max_output_tokens: int = Field(gt=0)
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)


class ModelResponse(BaseModel):
    """One completion, with the usage the Model call record needs."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    model_id: str = Field(min_length=1)


class ModelClient(Protocol):
    """Completes one bounded request through an approved provider."""

    async def complete(self, request: ModelRequest) -> ModelResponse:
        """Run one completion."""


class DisabledModelClient:
    """Safe default that never reaches a provider."""

    async def complete(self, request: ModelRequest) -> ModelResponse:
        del request
        raise ModelUnavailableError(
            "No model provider is configured. Set NOOB_AGENT_MODEL_PROVIDER and "
            "the matching credentials; see .env.example."
        )


class WandbInferenceClient:
    """W&B Inference through its OpenAI-compatible chat-completions endpoint."""

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
        completion = await client.chat.completions.create(
            model=self._model.inference_model,
            messages=[
                {"role": "system", "content": request.system},
                {"role": "user", "content": request.prompt},
            ],
            max_tokens=request.max_output_tokens,
            temperature=request.temperature,
        )
        usage = completion.usage
        return ModelResponse(
            text=completion.choices[0].message.content or "",
            input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            output_tokens=getattr(usage, "completion_tokens", 0) or 0,
            model_id=completion.model or str(self._model.inference_model),
        )


def build_model_client(model: ModelSettings, wandb: WandbSettings) -> ModelClient:
    """Return the configured client, or the disabled default."""
    if model.provider == "wandb-inference":
        return WandbInferenceClient(model, wandb)
    return DisabledModelClient()
