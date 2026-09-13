"""The local skill worker: the only process that ever loads candidate source.

Started by `LocalSubprocessSkillExecutor` as `python -I worker.py <src_root>`.
It reads one `start` message from stdin, loads the candidate with guarded
builtins and imports, runs its `run` entry point with a proxy `SkillContext`,
and relays every `observe`, `call`, `remaining_budget`, and `log` to the parent
over JSON Lines on the real stdout. The candidate's own `print` output, if any,
goes to a bounded stderr log instead, so it can never corrupt the protocol.

This is the labeled weaker-isolation fallback: it enforces neither a network
nor a memory limit. Those are what CoreWeave Sandbox is for.
"""

from __future__ import annotations

import asyncio
import builtins
import inspect
import io
import json
import os
import sys
from collections.abc import Callable, Mapping, Sequence
from types import ModuleType
from typing import Any, TextIO

MAX_LOG_BYTES = 64 * 1024

# Builtins a candidate must not reach even if the static policy missed a path.
_REMOVED_BUILTINS = frozenset(
    {
        "eval",
        "exec",
        "compile",
        "open",
        "globals",
        "locals",
        "vars",
        "getattr",
        "setattr",
        "delattr",
        "input",
        "breakpoint",
        "exit",
        "quit",
        "help",
        "memoryview",
        "copyright",
        "credits",
        "license",
    }
)


class _BoundedLog(io.TextIOBase):
    """Forwards candidate output to stderr until the log budget is spent."""

    def __init__(self, target: TextIO, limit: int) -> None:
        super().__init__()
        self._target = target
        self._remaining = limit

    def writable(self) -> bool:
        return True

    def write(self, text: str) -> int:
        if self._remaining <= 0:
            return len(text)
        kept = text[: self._remaining]
        self._remaining -= len(kept.encode("utf-8"))
        self._target.write(kept)
        self._target.flush()
        return len(text)


class _Protocol:
    """One JSON Lines conversation with the parent process."""

    def __init__(self, reader: TextIO, writer: TextIO) -> None:
        self._reader = reader
        self._writer = writer

    def send(self, message: dict[str, Any]) -> None:
        self._writer.write(json.dumps(message, ensure_ascii=False) + "\n")
        self._writer.flush()

    def receive(self) -> dict[str, Any]:
        line = self._reader.readline()
        if not line:
            raise EOFError("The parent closed the connection.")
        message = json.loads(line)
        if not isinstance(message, dict):
            raise ValueError("The parent sent a non-object message.")
        return message

    def exchange(self, message: dict[str, Any], expected: str) -> dict[str, Any]:
        self.send(message)
        reply = self.receive()
        if reply.get("type") != expected:
            raise ValueError(f"Expected a {expected!r} reply, got {reply.get('type')!r}.")
        return reply


class _ProxyContext:
    """The `SkillContext` a candidate receives: every call crosses to the parent."""

    def __init__(self, protocol: _Protocol, contract: ModuleType, models: ModuleType) -> None:
        self._protocol = protocol
        self._observation = models.Observation
        self._step_result = models.StepResult
        self._budget = contract.SkillBudget

    async def observe(self) -> Any:
        reply = self._protocol.exchange({"type": "observe"}, "observation")
        return self._observation.model_validate(reply["observation"])

    async def call(self, tool_name: str, **arguments: object) -> Any:
        reply = self._protocol.exchange(
            {"type": "call", "tool_name": tool_name, "arguments": arguments}, "step_result"
        )
        return self._step_result.model_validate(reply["result"])

    def remaining_budget(self) -> Any:
        reply = self._protocol.exchange({"type": "remaining_budget"}, "budget")
        return self._budget.model_validate(reply["budget"])

    def log(self, event: str, fields: Mapping[str, object]) -> None:
        self._protocol.exchange({"type": "log", "event": event, "fields": dict(fields)}, "ok")


def _guarded_import(
    allowed_modules: frozenset[str], contract_module: str
) -> Callable[..., ModuleType]:
    real_import = builtins.__import__

    def guarded(
        name: str,
        globals_: Mapping[str, object] | None = None,
        locals_: Mapping[str, object] | None = None,
        fromlist: Sequence[str] = (),
        level: int = 0,
    ) -> ModuleType:
        root = name.split(".")[0]
        if level != 0 or not (name == contract_module or root in allowed_modules):
            raise ImportError(f"Import of {name!r} is not permitted.")
        return real_import(name, globals_, locals_, fromlist, level)

    return guarded


def _restricted_builtins(allowed_modules: frozenset[str], contract_module: str) -> dict[str, Any]:
    restricted = {
        name: value
        for name, value in vars(builtins).items()
        if name not in _REMOVED_BUILTINS and not name.startswith("_")
    }
    restricted["__import__"] = _guarded_import(allowed_modules, contract_module)
    restricted["__build_class__"] = builtins.__build_class__
    restricted["__name__"] = "builtins"
    return restricted


def _load(source: str, entry_point: str, namespace: dict[str, Any]) -> Callable[..., Any]:
    code = compile(source, "skill.py", "exec")
    exec(code, namespace)  # noqa: S102 - this process exists to run untrusted code.
    entry = namespace.get(entry_point)
    if not inspect.iscoroutinefunction(entry):
        raise LookupError(f"The candidate has no async entry point named {entry_point!r}.")
    return entry


def main(argv: Sequence[str]) -> int:
    if len(argv) != 2:
        sys.stderr.write("usage: worker.py <src_root>\n")
        return 2
    sys.path.insert(0, argv[1])

    protocol_in = io.TextIOWrapper(os.fdopen(os.dup(0), "rb", 0), encoding="utf-8")
    protocol_out = io.TextIOWrapper(
        os.fdopen(os.dup(1), "wb", 0), encoding="utf-8", write_through=True
    )
    protocol = _Protocol(protocol_in, protocol_out)
    # The candidate must never touch the real pipes.
    sys.stdin = io.StringIO()
    sys.stdout = _BoundedLog(sys.stderr, MAX_LOG_BYTES)

    try:
        start = protocol.receive()
    except (EOFError, ValueError, json.JSONDecodeError) as error:
        sys.stderr.write(f"worker: no start message: {error}\n")
        return 1
    if start.get("type") != "start":
        protocol.send({"type": "error", "code": "PROTOCOL_ERROR", "message": "Expected start."})
        return 1

    source = start.get("source")
    inputs = start.get("inputs")
    entry_point = start.get("entry_point", "run")
    if not isinstance(source, str) or not isinstance(inputs, dict):
        protocol.send(
            {"type": "error", "code": "PROTOCOL_ERROR", "message": "Malformed start message."}
        )
        return 1

    from noob_agent.domain import model as models
    from noob_agent.skills import contract, policy

    namespace: dict[str, Any] = {
        "__builtins__": _restricted_builtins(
            policy.ALLOWED_IMPORT_MODULES, policy.SKILL_CONTRACT_MODULE
        ),
        "__name__": "skill",
    }
    try:
        entry = _load(source, str(entry_point), namespace)
    except BaseException as error:  # noqa: BLE001 - every load failure is reported, not raised.
        protocol.send(
            {
                "type": "error",
                "code": "SKILL_LOAD_FAILED",
                "message": f"{type(error).__name__}: {error}",
            }
        )
        return 1

    from pydantic import ValidationError

    context = _ProxyContext(protocol, contract, models)
    try:
        result = asyncio.run(entry(context, inputs))
    except EOFError:
        # The parent stopped the conversation; there is no one to report to.
        return 1
    except ValidationError as error:
        # Building a contract-violating SkillResult raises inside the skill.
        protocol.send({"type": "error", "code": "SKILL_INVALID_RESULT", "message": f"{error}"})
        return 1
    except BaseException as error:  # noqa: BLE001 - a raising skill is a reported failure.
        protocol.send(
            {"type": "error", "code": "SKILL_RAISED", "message": f"{type(error).__name__}: {error}"}
        )
        return 1

    if isinstance(result, contract.SkillResult):
        protocol.send({"type": "result", "result": result.model_dump(mode="json")})
        return 0
    protocol.send(
        {
            "type": "error",
            "code": "SKILL_INVALID_RESULT",
            "message": f"The entry point returned {type(result).__name__}, not a SkillResult.",
        }
    )
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
