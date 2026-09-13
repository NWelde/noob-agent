"""Local configuration for optional remote integrations.

Values are read only from an explicitly supplied environment mapping or the
process environment. Importing this module never reads a dotenv file, contacts
an external service, or authenticates.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Literal, cast

from pydantic import BaseModel, Field

DEFAULT_ACTION_MAX_OUTPUT_TOKENS = 1_024
DEFAULT_BUILDER_MAX_OUTPUT_TOKENS = 6_000
ACTION_MAX_OUTPUT_TOKENS_CEILING = 2_000
BUILDER_MAX_OUTPUT_TOKENS_CEILING = 8_000


def _read_bool(environment: Mapping[str, str], name: str, default: bool) -> bool:
    raw_value = environment.get(name)
    if raw_value is None:
        return default
    normalized = raw_value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean value")


class WandbSettings(BaseModel):
    """Shared W&B authentication and project identity."""

    api_key: str | None = None
    entity: str | None = None
    project: str = "noob-agent"
    base_url: str | None = None


class TraceSettings(BaseModel):
    """Controls the future Weave trace adapter."""

    mode: Literal["disabled", "weave"] = "disabled"
    weave_disabled: bool = True

    @property
    def enabled(self) -> bool:
        return self.mode == "weave" and not self.weave_disabled


class ModelSettings(BaseModel):
    """Controls the future provider-neutral model adapter."""

    provider: Literal["disabled", "wandb-inference"] = "disabled"
    inference_base_url: str = "https://api.inference.wandb.ai/v1"
    inference_project: str | None = None
    inference_model: str | None = None
    action_max_output_tokens: int = Field(
        default=DEFAULT_ACTION_MAX_OUTPUT_TOKENS,
        gt=0,
        le=ACTION_MAX_OUTPUT_TOKENS_CEILING,
    )
    builder_max_output_tokens: int = Field(
        default=DEFAULT_BUILDER_MAX_OUTPUT_TOKENS,
        gt=0,
        le=BUILDER_MAX_OUTPUT_TOKENS_CEILING,
    )
    # Provider reasoning per role; off by default (hackathon_plan.md section 22).
    action_thinking: bool = False
    builder_thinking: bool = False


class SandboxSettings(BaseModel):
    """Controls the future CoreWeave Sandbox adapter."""

    api_key: str | None = None
    mode: Literal["disabled", "serverless", "cks", "local"] = "disabled"
    placement: Literal["serverless", "cks"] = "serverless"
    runner_id: str | None = None
    image: str = "python:3.12-slim"
    max_lifetime_seconds: int = 30

    @property
    def enabled(self) -> bool:
        return self.mode != "disabled"


class IntegrationSettings(BaseModel):
    """All optional-integration settings with safe offline defaults."""

    wandb: WandbSettings
    trace: TraceSettings
    model: ModelSettings
    sandbox: SandboxSettings

    @classmethod
    def from_environ(cls, environment: Mapping[str, str] | None = None) -> IntegrationSettings:
        values = os.environ if environment is None else environment
        return cls(
            wandb=WandbSettings(
                api_key=values.get("WANDB_API_KEY") or None,
                entity=values.get("WANDB_ENTITY") or None,
                project=values.get("WANDB_PROJECT") or "noob-agent",
                base_url=values.get("WANDB_BASE_URL") or None,
            ),
            trace=TraceSettings(
                mode=cast(
                    Literal["disabled", "weave"],
                    values.get("NOOB_AGENT_TRACE_MODE", "disabled"),
                ),
                weave_disabled=_read_bool(values, "WEAVE_DISABLED", True),
            ),
            model=ModelSettings(
                provider=cast(
                    Literal["disabled", "wandb-inference"],
                    values.get("NOOB_AGENT_MODEL_PROVIDER", "disabled"),
                ),
                inference_base_url=values.get(
                    "NOOB_AGENT_INFERENCE_BASE_URL", "https://api.inference.wandb.ai/v1"
                ),
                inference_project=values.get("NOOB_AGENT_INFERENCE_PROJECT") or None,
                inference_model=values.get("NOOB_AGENT_INFERENCE_MODEL") or None,
                action_max_output_tokens=int(
                    values.get(
                        "NOOB_AGENT_ACTION_MAX_OUTPUT_TOKENS",
                        str(DEFAULT_ACTION_MAX_OUTPUT_TOKENS),
                    )
                ),
                builder_max_output_tokens=int(
                    values.get(
                        "NOOB_AGENT_BUILDER_MAX_OUTPUT_TOKENS",
                        str(DEFAULT_BUILDER_MAX_OUTPUT_TOKENS),
                    )
                ),
                action_thinking=_read_bool(values, "NOOB_AGENT_ACTION_THINKING", False),
                builder_thinking=_read_bool(values, "NOOB_AGENT_BUILDER_THINKING", False),
            ),
            sandbox=SandboxSettings(
                api_key=values.get("CWSANDBOX_API_KEY") or None,
                mode=cast(
                    Literal["disabled", "serverless", "cks", "local"],
                    values.get("NOOB_AGENT_SANDBOX_MODE", "disabled"),
                ),
                placement=cast(
                    Literal["serverless", "cks"],
                    values.get("NOOB_AGENT_SANDBOX_PLACEMENT", "serverless"),
                ),
                runner_id=values.get("NOOB_AGENT_SANDBOX_RUNNER_ID") or None,
                image=values.get("NOOB_AGENT_SANDBOX_IMAGE", "python:3.12-slim"),
                max_lifetime_seconds=int(
                    values.get("NOOB_AGENT_SANDBOX_MAX_LIFETIME_SECONDS", "30")
                ),
            ),
        )
