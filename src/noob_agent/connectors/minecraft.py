"""The Minecraft game connector.

This is build-order step 1 from `hackathon_plan.md` section 13: reset a prepared
scenario world and perform one hard-coded primitive action against the local
development server described in `docs/minecraft-server.md`.

Following section 8, the game protocol is spoken by a small Node.js and
Mineflayer sidecar. This module drives that sidecar over JSON Lines on its
stdio using only the standard library, so no new Python dependency is required.
The sidecar is a transport: it knows how to talk to a Minecraft server and
gather nearby state. This module owns the public contract - manifest freezing,
sequence numbering, argument validation, result classification, and the trust
boundary.

Trust boundary: every field delivered to an agent is copied through an explicit
allow-list. Scoreboard values, scenario tags, clean/faulty identity, grader
state, and any other private field the sidecar might report are dropped rather
than forwarded. Widening an allow-list changes the benchmark surface and
therefore requires a connector version change.

Only `observe` is implemented at this step. Every other primitive named in
`connector_contract.md` is deferred, is absent from the manifest, and is
rejected as an undeclared tool.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final, Protocol

from pydantic import ValidationError

from noob_agent.connectors.protocol import ConnectorError, ConnectorLostError
from noob_agent.domain.model import (
    ConnectorManifest,
    Observation,
    PublicMessage,
    PublicPlayerState,
    PublicPosition,
    StepResult,
    ToolDefinition,
    ToolRequest,
    VisibleObject,
)

GAME_ID: Final = "minecraft"
CONNECTOR_VERSION: Final = "minecraft-0.1.0"
OBSERVATION_MODE: Final = "structured_nearby_state"
TIMING_MODEL: Final = "game_ticks"

# The exact public task text from `minecraft_scenario.md`. It names no
# resonator, recipe, quantity, variation, or grader condition.
PUBLIC_GOAL: Final = (
    "Open the sealed gateway using the objects and devices in this area. You may "
    "inspect, collect, place, and use what you find. The purpose of unfamiliar "
    "objects is not documented, so use visible results as evidence. If behavior "
    "contradicts evidence you established earlier, report expected and observed "
    "behavior separately. Finish within the action budget."
)

MIN_OBSERVE_RADIUS: Final = 1
MAX_OBSERVE_RADIUS: Final = 8
DEFAULT_OBSERVE_RADIUS: Final = 5

# `connector_contract.md`: Minecraft settles for five ticks after a completed
# interaction, and its call and reset timeouts are ten and thirty seconds.
SETTLE_TICKS: Final = 5
CALL_TIMEOUT_SECONDS: Final = 10.0
RESET_TIMEOUT_SECONDS: Final = 30.0

# Primitives the contract requires of a complete Minecraft connector but which
# this version does not implement. They are deliberately absent from the
# manifest, so a request naming one is rejected as an undeclared tool.
DEFERRED_TOOLS: Final = (
    "move_to",
    "look_at",
    "inspect_object",
    "collect_object",
    "use_object",
    "place_object",
    "wait",
)

SIDECAR_DIRECTORY: Final = Path(__file__).parent / "minecraft_sidecar"

OBSERVE_TOOL: Final = ToolDefinition(
    name="observe",
    description=(
        "Refresh the visible nearby state within a radius of 1 to 8 blocks. "
        "Reports the player's public status and the ordinary names, positions, "
        "and distances of nearby objects."
    ),
    argument_schema={
        "type": "object",
        "properties": {
            "radius": {
                "type": "integer",
                "minimum": MIN_OBSERVE_RADIUS,
                "maximum": MAX_OBSERVE_RADIUS,
                "default": DEFAULT_OBSERVE_RADIUS,
            }
        },
        "additionalProperties": False,
    },
    preconditions=("The connector is connected to the game.",),
    max_duration=int(CALL_TIMEOUT_SECONDS),
    state_changing=False,
)

MANIFEST: Final = ConnectorManifest(
    connector_version=CONNECTOR_VERSION,
    game_id=GAME_ID,
    observation_mode=OBSERVATION_MODE,
    timing_model=TIMING_MODEL,
    tools=(OBSERVE_TOOL,),
)

# Result codes defined by `connector_contract.md`. A new code requires a
# connector version change, so an unrecognized code from the sidecar is
# narrowed to the generic game rejection rather than invented here.
_RESULT_CODES: Final = frozenset(
    {
        "OK",
        "INVALID_TOOL",
        "INVALID_ARGUMENT",
        "PRECONDITION_FAILED",
        "UNREACHABLE",
        "NO_VISIBLE_TARGET",
        "GAME_REJECTED",
        "TIMEOUT_CONFIRMED",
        "TIMEOUT_UNKNOWN",
        "BUDGET_EXHAUSTED",
        "CONNECTOR_LOST",
    }
)

# Allow-lists for the trust boundary. Anything absent is dropped.
_PUBLIC_STATUS_KEYS: Final = frozenset(
    {"health", "food", "air", "on_ground", "game_mode", "observe_radius", "experience_level"}
)
_PUBLIC_PLAYER_PROPERTY_KEYS: Final = frozenset(
    {"game_mode", "held_item", "selected_slot", "inventory_labels"}
)
_PUBLIC_OBJECT_PROPERTY_KEYS: Final = frozenset(
    {"kind", "count", "facing", "open", "lit", "powered", "enabled", "waterlogged"}
)
_PUBLIC_ORIENTATION_KEYS: Final = frozenset({"yaw", "pitch"})

# A carried observation repeats the last state the connector could confirm.
_STALE_STATUS_KEY: Final = "stale_observation"

# Bounds that keep one observation small enough to store and hash cheaply.
_MAX_VISIBLE_OBJECTS: Final = 64
_MAX_MESSAGES: Final = 20

# Coordinates are rounded before hashing so that float noise cannot make an
# otherwise identical reset look different.
_POSITION_DIGITS: Final = 3
_DISTANCE_DIGITS: Final = 2


class SidecarUnavailableError(ConnectorError):
    """The Node sidecar or the game server could not be reached at all.

    Raised before any game state exists, so a caller may treat it as "this
    environment cannot run live Minecraft tests" rather than as a game failure.
    """


class _NotDelivered(Exception):
    """Internal: the request provably never reached the sidecar."""


class _Unconfirmed(Exception):
    """Internal: the request may have been delivered, but no result arrived."""


@dataclass(frozen=True)
class MinecraftSettings:
    """Connection and timing values for the local development server.

    Defaults match the frozen baseline in `docs/minecraft-server.md`.
    """

    host: str = "127.0.0.1"
    port: int = 25566
    # Offline-mode UUIDs are case-sensitive. Keep this byte-for-byte identical
    # to the name recorded by the setup guide's whitelist command.
    username: str = "noobagentbot"
    minecraft_version: str = "1.21.1"
    # Reset is performed by the scenario data pack's own reset function.
    scenario_reset_commands: tuple[str, ...] = ("function noob_agent:reset",)
    observe_radius: int = DEFAULT_OBSERVE_RADIUS
    settle_ticks: int = SETTLE_TICKS
    call_timeout_seconds: float = CALL_TIMEOUT_SECONDS
    reset_timeout_seconds: float = RESET_TIMEOUT_SECONDS
    sidecar_directory: Path = field(default=SIDECAR_DIRECTORY)
    node_executable: str = "node"

    def __post_init__(self) -> None:
        if not MIN_OBSERVE_RADIUS <= self.observe_radius <= MAX_OBSERVE_RADIUS:
            raise ValueError(
                f"observe_radius must be between {MIN_OBSERVE_RADIUS} and {MAX_OBSERVE_RADIUS}."
            )
        if not 1 <= self.port <= 65535:
            raise ValueError("port must be a valid TCP port.")
        if self.call_timeout_seconds <= 0 or self.reset_timeout_seconds <= 0:
            raise ValueError("Timeouts must be positive.")
        if self.settle_ticks < 0:
            raise ValueError("settle_ticks cannot be negative.")


class SidecarTransport(Protocol):
    """A started, connected JSON Lines channel to one game session.

    `start` owns connecting to the game, so a substituted transport can serve
    contract tests without a server. `send` raising `TimeoutError` means the
    request was never delivered; `receive` raising `TimeoutError` means it may
    have been.
    """

    async def start(self) -> None: ...

    async def send(self, message: dict[str, Any]) -> None: ...

    async def receive(self, *, timeout: float) -> dict[str, Any]: ...

    async def close(self) -> None: ...


class NodeSidecarTransport:
    """Runs the Node.js and Mineflayer sidecar as a child process.

    One JSON object per line in each direction on the child's stdio. The
    sidecar's diagnostics go to stderr, so stdout carries only protocol lines.
    """

    def __init__(self, settings: MinecraftSettings) -> None:
        self._settings = settings
        self._process: asyncio.subprocess.Process | None = None
        self._last_sent_id: int | None = None

    async def start(self) -> None:
        if self._process is not None:
            return
        settings = self._settings
        entry_point = settings.sidecar_directory / "index.js"
        if shutil.which(settings.node_executable) is None:
            raise SidecarUnavailableError(
                f"The Node runtime {settings.node_executable!r} is not on PATH."
            )
        if not entry_point.is_file():
            raise SidecarUnavailableError(f"The sidecar entry point {entry_point} is missing.")
        if not (settings.sidecar_directory / "node_modules" / "mineflayer").is_dir():
            raise SidecarUnavailableError(
                f"Mineflayer is not installed. Run 'npm install' in {settings.sidecar_directory}."
            )
        try:
            self._process = await asyncio.create_subprocess_exec(
                settings.node_executable,
                str(entry_point),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(settings.sidecar_directory),
            )
        except OSError as spawn_failure:
            raise SidecarUnavailableError(
                f"The sidecar process could not be started: {spawn_failure}"
            ) from spawn_failure

        try:
            await self.send(
                {
                    "op": "connect",
                    "host": settings.host,
                    "port": settings.port,
                    "username": settings.username,
                    "version": settings.minecraft_version,
                }
            )
            reply = await self.receive(timeout=settings.reset_timeout_seconds)
        except (TimeoutError, ConnectorError, OSError) as handshake_failure:
            await self.close()
            raise SidecarUnavailableError(
                f"The sidecar could not join {settings.host}:{settings.port}: {handshake_failure}"
            ) from handshake_failure
        if reply.get("ok") is not True:
            await self.close()
            raise SidecarUnavailableError(
                f"The sidecar refused to join the game: {reply.get('error', 'no reason given')}"
            )

    async def send(self, message: dict[str, Any]) -> None:
        process = self._process
        if process is None or process.stdin is None:
            raise ConnectorLostError("The sidecar process is not running.")
        self._last_sent_id = message.get("id") if isinstance(message.get("id"), int) else None
        line = json.dumps(message, sort_keys=True, separators=(",", ":")) + "\n"
        try:
            process.stdin.write(line.encode("utf-8"))
            await process.stdin.drain()
        except (BrokenPipeError, ConnectionResetError) as broken:
            raise ConnectorLostError(
                f"The sidecar stopped accepting requests: {broken}"
            ) from broken

    async def receive(self, *, timeout: float) -> dict[str, Any]:
        process = self._process
        if process is None or process.stdout is None:
            raise ConnectorLostError("The sidecar process is not running.")
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("The sidecar did not answer within its timeout.")
            try:
                line = await asyncio.wait_for(process.stdout.readline(), timeout=remaining)
            except TimeoutError:
                raise TimeoutError("The sidecar did not answer within its timeout.") from None
            if not line:
                raise ConnectorLostError("The sidecar closed its output stream.")
            try:
                decoded = json.loads(line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as malformed:
                raise ConnectorLostError(
                    f"The sidecar wrote a line that is not JSON: {malformed}"
                ) from malformed
            if not isinstance(decoded, dict):
                raise ConnectorLostError("The sidecar wrote a JSON value that is not an object.")
            reply_id = decoded.get("id")
            if self._last_sent_id is not None and reply_id != self._last_sent_id:
                # A late answer to an abandoned request. Never treat it as this
                # request's result; keep reading.
                continue
            return decoded

    async def close(self) -> None:
        process, self._process = self._process, None
        if process is None:
            return
        if process.returncode is None and process.stdin is not None:
            with _suppress_transport_errors():
                process.stdin.write(b'{"op":"close"}\n')
                await process.stdin.drain()
        if process.returncode is None:
            try:
                await asyncio.wait_for(process.wait(), timeout=5.0)
            except TimeoutError:
                process.kill()
                with _suppress_transport_errors():
                    await process.wait()


class _suppress_transport_errors:
    """Ignore the ordinary races of shutting a child process's pipes down."""

    def __enter__(self) -> None:
        return None

    def __exit__(self, exc_type: type[BaseException] | None, *_: object) -> bool:
        return exc_type is not None and issubclass(
            exc_type, OSError | ProcessLookupError | ValueError
        )


class MinecraftConnector:
    """The public Minecraft adapter: reset a scenario and observe it.

    Implements `GameConnector`. The harness calls `reset` and `close`; an agent
    may call only the primitives the manifest declares.
    """

    def __init__(
        self,
        settings: MinecraftSettings | None = None,
        *,
        transport: SidecarTransport | None = None,
    ) -> None:
        self._settings = settings if settings is not None else MinecraftSettings()
        self._transport: SidecarTransport = (
            transport if transport is not None else NodeSidecarTransport(self._settings)
        )
        self._started = False
        self._closed = False
        self._scenario_id: str | None = None
        self._episode_id: str | None = None
        self._resets = 0
        self._sequence = 0
        self._latest: Observation | None = None
        self._message_id = 0

    # --- GameConnector ----------------------------------------------------

    async def manifest(self) -> ConnectorManifest:
        """Return the frozen manifest. It never changes, before or after reset."""
        return MANIFEST

    async def reset(self, scenario_id: str, seed: int) -> Observation:
        """Restore the scenario's declared initial state and observe it."""
        await self.start()
        self._resets += 1
        self._sequence = 0
        self._scenario_id = scenario_id
        self._episode_id = f"mc-{scenario_id}-s{seed}-r{self._resets:03d}"
        self._latest = None
        try:
            reply = await self._exchange(
                {
                    "op": "reset",
                    "scenario_id": scenario_id,
                    "seed": seed,
                    "commands": list(self._settings.scenario_reset_commands),
                    "settle_ticks": self._settings.settle_ticks,
                    "radius": self._settings.observe_radius,
                },
                timeout=self._settings.reset_timeout_seconds,
            )
        except (_NotDelivered, _Unconfirmed) as timed_out:
            raise ConnectorError(
                f"Resetting scenario {scenario_id!r} timed out: {timed_out}"
            ) from timed_out
        if reply.get("ok") is not True:
            raise ConnectorError(
                f"The sidecar could not reset scenario {scenario_id!r}: "
                f"{reply.get('error', 'no reason given')}"
            )
        observation = self._build_observation(
            reply.get("observation"), sequence=0, last_action_id=None
        )
        self._latest = observation
        return observation

    async def step(self, request: ToolRequest) -> StepResult:
        """Perform one declared primitive and return its one durable result."""
        started_ms = time.monotonic()
        latest = self._latest
        if latest is None or self._episode_id is None:
            raise ConnectorError("reset must succeed before an action is attempted.")

        if request.tool_name != OBSERVE_TOOL.name:
            return self._rejected(
                request,
                "INVALID_TOOL",
                f"{request.tool_name!r} is not declared by connector {CONNECTOR_VERSION}."
                + (
                    " It is deferred to a later connector version."
                    if request.tool_name in DEFERRED_TOOLS
                    else ""
                ),
                started_ms,
            )

        radius = _observe_radius(request.arguments, self._settings.observe_radius)
        if radius is None:
            return self._rejected(
                request,
                "INVALID_ARGUMENT",
                f"radius must be an integer from {MIN_OBSERVE_RADIUS} to {MAX_OBSERVE_RADIUS}.",
                started_ms,
            )

        try:
            reply = await self._exchange(
                {"op": "observe", "radius": radius},
                timeout=self._settings.call_timeout_seconds,
            )
        except _NotDelivered as not_delivered:
            # Confirmed non-delivery: the game cannot have changed.
            return self._charged(
                request,
                status="failed",
                code="TIMEOUT_CONFIRMED",
                message=f"The request was never delivered: {not_delivered}",
                state_changed=False,
                started_ms=started_ms,
            )
        except _Unconfirmed as unconfirmed:
            # Possible delivery: the result cannot be confirmed either way.
            return self._charged(
                request,
                status="unknown",
                code="TIMEOUT_UNKNOWN",
                message=f"The result could not be confirmed: {unconfirmed}",
                state_changed=None,
                started_ms=started_ms,
            )

        if reply.get("ok") is not True:
            return self._charged(
                request,
                status="failed",
                code=_result_code(reply.get("code")),
                message=str(reply.get("error", "The game did not complete the action.")),
                state_changed=False,
                started_ms=started_ms,
            )

        self._sequence += 1
        observation = self._build_observation(
            reply.get("observation"),
            sequence=self._sequence,
            last_action_id=request.action_id,
        )
        self._latest = observation
        return StepResult(
            action_id=request.action_id,
            sequence=self._sequence,
            status="succeeded",
            code="OK",
            message="Visible nearby state refreshed.",
            observation=observation,
            state_changed=False,
            primitive_actions_charged=1,
            logical_duration=_logical_duration(observation, previous=latest),
            wall_time_ms=_elapsed_ms(started_ms),
        )

    async def is_terminal(self) -> bool:
        """Report the terminal flag of the latest public observation."""
        return self._latest is not None and self._latest.terminal

    async def close(self) -> None:
        """Release the sidecar. Safe to call more than once."""
        if self._closed:
            return
        self._closed = True
        self._started = False
        await self._transport.close()

    # --- Lifecycle --------------------------------------------------------

    async def start(self) -> None:
        """Start the sidecar and join the game. Idempotent.

        Raises `SidecarUnavailableError` when the Node runtime, the sidecar's
        dependencies, or the game server is unavailable, so a caller can tell
        an unusable environment from a game failure.
        """
        if self._started:
            return
        if self._closed:
            raise ConnectorError("This connector was closed and cannot be restarted.")
        await self._transport.start()
        self._started = True

    # --- Internals --------------------------------------------------------

    async def _exchange(self, message: dict[str, Any], *, timeout: float) -> dict[str, Any]:
        """Send one request and read its reply, distinguishing the two timeouts."""
        self._message_id += 1
        try:
            await self._transport.send({**message, "id": self._message_id})
        except TimeoutError as send_timeout:
            raise _NotDelivered(str(send_timeout) or "the send timed out") from send_timeout
        try:
            return await self._transport.receive(timeout=timeout)
        except TimeoutError as receive_timeout:
            raise _Unconfirmed(str(receive_timeout) or "the reply timed out") from receive_timeout

    def _rejected(
        self, request: ToolRequest, code: str, message: str, started_ms: float
    ) -> StepResult:
        """Refuse a request that was never sent, charging no primitive call.

        A rejection is still one durable result, and the harness records every
        result as a step with its own sequence, so the public sequence advances
        here exactly as it does for a delivered primitive.
        """
        self._sequence += 1
        observation = self._carried_observation(last_action_id=request.action_id, stale=False)
        self._latest = observation
        return StepResult(
            action_id=request.action_id,
            sequence=self._sequence,
            status="rejected",
            code=code,
            message=message,
            observation=observation,
            state_changed=False,
            primitive_actions_charged=0,
            logical_duration=0,
            wall_time_ms=_elapsed_ms(started_ms),
        )

    def _charged(
        self,
        request: ToolRequest,
        *,
        status: str,
        code: str,
        message: str,
        state_changed: bool | None,
        started_ms: float,
    ) -> StepResult:
        """Record a delivered primitive that did not return a fresh observation."""
        self._sequence += 1
        observation = self._carried_observation(last_action_id=request.action_id, stale=True)
        self._latest = observation
        return StepResult.model_validate(
            {
                "action_id": request.action_id,
                "sequence": self._sequence,
                "status": status,
                "code": code,
                "message": message,
                "observation": observation,
                "state_changed": state_changed,
                "primitive_actions_charged": 1,
                "logical_duration": 0,
                "wall_time_ms": _elapsed_ms(started_ms),
            }
        )

    def _carried_observation(self, *, last_action_id: str, stale: bool) -> Observation:
        """Repeat the last confirmed public state under the current sequence."""
        latest = self._latest
        if latest is None:
            raise ConnectorError("reset must succeed before an action is attempted.")
        status = dict(latest.status)
        if stale:
            # Say plainly that this snapshot was not re-read from the game.
            status[_STALE_STATUS_KEY] = True
        else:
            status.pop(_STALE_STATUS_KEY, None)
        return latest.model_copy(
            update={
                "sequence": self._sequence,
                "last_action_id": last_action_id,
                "status": status,
            }
        )

    def _build_observation(
        self, raw: object, *, sequence: int, last_action_id: str | None
    ) -> Observation:
        """Copy a sidecar snapshot into a public observation through allow-lists."""
        if not isinstance(raw, Mapping):
            raise ConnectorLostError("The sidecar did not report a public snapshot.")
        scenario_id = self._scenario_id
        episode_id = self._episode_id
        if scenario_id is None or episode_id is None:
            raise ConnectorError("reset must succeed before an observation is built.")
        player_raw = raw.get("player")
        player_mapping: Mapping[str, object] = (
            player_raw if isinstance(player_raw, Mapping) else {}
        )
        status = _public_mapping(raw.get("status"), _PUBLIC_STATUS_KEYS)
        status["observe_radius"] = _int_or(raw.get("radius"), self._settings.observe_radius)
        terminal = raw.get("terminal") is True
        reason = raw.get("terminal_reason")
        try:
            return Observation(
                episode_id=episode_id,
                sequence=sequence,
                game_id=GAME_ID,
                scenario_id=scenario_id,
                public_goal=PUBLIC_GOAL,
                status=status,
                player=PublicPlayerState(
                    position=_position(player_mapping.get("position")),
                    orientation=_public_mapping(
                        player_mapping.get("orientation"), _PUBLIC_ORIENTATION_KEYS
                    ),
                    properties=_public_mapping(
                        player_mapping.get("properties"), _PUBLIC_PLAYER_PROPERTY_KEYS
                    ),
                ),
                visible_objects=_visible_objects(raw.get("visible_objects")),
                messages=_messages(raw.get("messages")),
                last_action_id=last_action_id,
                terminal=terminal,
                terminal_reason=str(reason) if terminal and isinstance(reason, str) else None,
                logical_time=max(0, _int_or(raw.get("logical_time"), 0)),
            )
        except ValidationError as invalid:
            raise ConnectorLostError(
                f"The sidecar reported a snapshot that is not a valid observation: {invalid}"
            ) from invalid


# --- Public-field filtering -------------------------------------------------


def _scalar(value: object) -> str | int | float | bool | None:
    """Keep only JSON scalars, so no nested private structure can ride along."""
    if value is None or isinstance(value, str | bool | int | float):
        return value
    return None


def _public_mapping(raw: object, allowed: frozenset[str]) -> dict[str, Any]:
    """Copy allow-listed scalar entries and drop everything else."""
    if not isinstance(raw, Mapping):
        return {}
    public: dict[str, Any] = {}
    for key in sorted(allowed):
        if key not in raw:
            continue
        value = raw[key]
        if value is None or isinstance(value, str | bool | int | float):
            public[key] = _scalar(value)
    return public


def _position(raw: object) -> PublicPosition | None:
    if not isinstance(raw, Mapping):
        return None
    try:
        return PublicPosition(
            x=round(float(raw["x"]), _POSITION_DIGITS),
            y=round(float(raw["y"]), _POSITION_DIGITS),
            z=round(float(raw["z"]), _POSITION_DIGITS),
        )
    except (KeyError, TypeError, ValueError, ValidationError):
        return None


def _visible_objects(raw: object) -> tuple[VisibleObject, ...]:
    """Order snapshot objects deterministically and give them opaque IDs.

    IDs are positional (`obj_0001`), so they cannot encode an object's purpose.
    Ordering is by public label and position rather than by the sidecar's
    iteration order, so the same restored world always hashes the same way.
    """
    if not isinstance(raw, Sequence) or isinstance(raw, str | bytes):
        return ()
    prepared: list[
        tuple[tuple[str, float, float, float], str, PublicPosition | None, Mapping[str, Any]]
    ] = []
    for entry in raw:
        if not isinstance(entry, Mapping):
            continue
        label = entry.get("label")
        if not isinstance(label, str) or not label.strip():
            continue
        position = _position(entry.get("position"))
        sort_key = (
            label,
            position.x if position else 0.0,
            position.y if position else 0.0,
            position.z if position else 0.0,
        )
        prepared.append((sort_key, label.strip(), position, entry))
    prepared.sort(key=lambda item: item[0])

    objects: list[VisibleObject] = []
    for index, (_, label, position, entry) in enumerate(prepared[:_MAX_VISIBLE_OBJECTS], start=1):
        assert isinstance(entry, Mapping)
        distance = entry.get("distance")
        objects.append(
            VisibleObject(
                object_id=f"obj_{index:04d}",
                label=label,
                position=position,
                distance=(
                    round(float(distance), _DISTANCE_DIGITS)
                    if isinstance(distance, int | float) and not isinstance(distance, bool)
                    else None
                ),
                properties=_public_mapping(entry.get("properties"), _PUBLIC_OBJECT_PROPERTY_KEYS),
            )
        )
    return tuple(objects)


def _messages(raw: object) -> tuple[PublicMessage, ...]:
    """Forward ordinary game feedback only, never connector commentary."""
    if not isinstance(raw, Sequence) or isinstance(raw, str | bytes):
        return ()
    messages: list[PublicMessage] = []
    for entry in raw:
        if not isinstance(entry, Mapping):
            continue
        text = entry.get("text")
        kind = entry.get("kind")
        if not isinstance(text, str) or not text.strip():
            continue
        messages.append(
            PublicMessage(
                kind=kind.strip() if isinstance(kind, str) and kind.strip() else "game",
                text=text.strip(),
            )
        )
    return tuple(messages[-_MAX_MESSAGES:])


# --- Small helpers ----------------------------------------------------------


def _observe_radius(arguments: Mapping[str, Any], default: int) -> int | None:
    """Validate `observe`'s only argument, returning None when it is invalid."""
    unexpected = set(arguments) - {"radius"}
    if unexpected:
        return None
    raw = arguments.get("radius", default)
    if isinstance(raw, bool) or not isinstance(raw, int):
        return None
    if not MIN_OBSERVE_RADIUS <= raw <= MAX_OBSERVE_RADIUS:
        return None
    return int(raw)


def _result_code(raw: object) -> str:
    return raw if isinstance(raw, str) and raw in _RESULT_CODES else "GAME_REJECTED"


def _int_or(raw: object, default: int) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int | float):
        return default
    return int(raw)


def _logical_duration(current: Observation, *, previous: Observation) -> int:
    """Game ticks the action took, as reported by the server's own clock."""
    return max(0, current.logical_time - previous.logical_time)


def _elapsed_ms(started_ms: float) -> int:
    return max(0, int((time.monotonic() - started_ms) * 1000))
