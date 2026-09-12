"""Provider-neutral seam for bounded generated-skill execution."""

from __future__ import annotations

from typing import Literal, Protocol

from pydantic import BaseModel


class SandboxRequest(BaseModel):
    """A future candidate-skill execution request, never executed by default."""

    source: str


class SandboxResult(BaseModel):
    """The normalized result of a future sandbox invocation."""

    status: Literal["completed", "failed", "unavailable"]
    code: str
    message: str


class SandboxExecutor(Protocol):
    """Executes untrusted code only through an approved bounded backend."""

    async def execute(self, request: SandboxRequest) -> SandboxResult:
        """Run one sandbox request."""


class DisabledSandboxExecutor:
    """Safe default that never evaluates supplied source code."""

    async def execute(self, request: SandboxRequest) -> SandboxResult:
        del request
        return SandboxResult(
            status="unavailable",
            code="SANDBOX_DISABLED",
            message="Sandbox execution is disabled in the current configuration.",
        )
