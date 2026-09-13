"use strict";

const { once } = require("node:events");
const readline = require("node:readline");
const mineflayer = require("mineflayer");
const { chatCommand, isNarration } = require("./chat");

const MAX_MESSAGES = 20;
const MAX_OBJECTS = 64;
const REACH_DISTANCE = 5;
const PUBLIC_BLOCKS = new Set([
  "barrel",
  "hopper",
  "iron_bars",
  "lodestone",
  "stone_button",
  "redstone_lamp",
  "redstone_block",
]);

let bot = null;
let publicMessages = [];
let inspectedContainer = null;

class ActionError extends Error {
  constructor(code, message, stateChanged = false) {
    super(message);
    this.code = code;
    this.stateChanged = stateChanged;
  }
}

function write(reply) {
  process.stdout.write(`${JSON.stringify(reply)}\n`);
}

function diagnose(message) {
  process.stderr.write(`[minecraft-sidecar] ${message}\n`);
}

function text(value) {
  if (typeof value === "string") return value;
  if (value && typeof value === "object") {
    if (typeof value.text === "string") return value.text;
    if (typeof value.value === "string") return text(value.value);
    if (value.value && typeof value.value === "object" && value.value.text) {
      return text(value.value.text);
    }
    if (typeof value.data === "string") return text(value.data);
    if (Array.isArray(value.extra)) return value.extra.map(text).join("");
  }
  if (value && typeof value.toString === "function") {
    const rendered = value.toString();
    if (rendered !== "[object Object]") return rendered;
  }
  return "";
}

function publicName(value) {
  const rendered = text(value).trim();
  if (!rendered) return "";
  try {
    const component = JSON.parse(rendered);
    return text(component).trim() || rendered;
  } catch {
    return rendered;
  }
}

// Operator command echoes such as `[Server: Set ...]` are administrative
// transport detail, not game state.
const OPERATOR_ECHO = /^\[[^\]:]+: .*\]$/;

function isPublicMessage(rendered) {
  // Diagnostic narration is deliberately human-visible but must not be
  // reflected into the model's next public observation.
  return Boolean(rendered) && !isNarration(rendered) && !OPERATOR_ECHO.test(rendered);
}

function rememberMessage(value) {
  const rendered = text(value).trim();
  if (!isPublicMessage(rendered)) return;
  publicMessages.push({ kind: "game", text: rendered });
  publicMessages = publicMessages.slice(-MAX_MESSAGES);
}

function distance(position) {
  return bot.entity.position.distanceTo(position);
}

function itemLabel(item) {
  return publicName(item.customName) || item.displayName || item.name;
}

function vector(raw) {
  if (!raw || ![raw.x, raw.y, raw.z].every(Number.isFinite)) {
    throw new ActionError("INVALID_ARGUMENT", "The public position is invalid.");
  }
  const current = bot.entity.position;
  return current.offset(raw.x - current.x, raw.y - current.y, raw.z - current.z);
}

function samePosition(left, right, tolerance = 0.01) {
  return left && right && left.distanceTo(right) <= tolerance;
}

function namedEntities(radius) {
  const objects = [];
  for (const entity of Object.values(bot.entities)) {
    if (entity === bot.entity || !entity.position || distance(entity.position) > radius) continue;

    if (entity.name === "armor_stand") {
      const label = text(entity.customName).trim();
      if (label) {
        objects.push({
          label,
          position: entity.position,
          distance: distance(entity.position),
          properties: { kind: "label" },
        });
      }
      continue;
    }

    if (entity.name === "item" && typeof entity.getDroppedItem === "function") {
      const item = entity.getDroppedItem();
      if (!item) continue;
      const label = itemLabel(item);
      objects.push({
        label,
        position: entity.position,
        distance: distance(entity.position),
        properties: { kind: "item", count: item.count },
      });
    }
  }
  return objects;
}

function nearbyPublicBlocks(radius) {
  const objects = [];
  const center = bot.entity.position.floored();
  const verticalRadius = Math.min(radius, 4);
  for (let x = -radius; x <= radius; x += 1) {
    for (let y = -verticalRadius; y <= verticalRadius; y += 1) {
      for (let z = -radius; z <= radius; z += 1) {
        if (x * x + y * y + z * z > radius * radius) continue;
        const block = bot.blockAt(center.offset(x, y, z), false);
        if (!block || !PUBLIC_BLOCKS.has(block.name)) continue;
        objects.push({
          label: block.displayName,
          position: block.position,
          distance: distance(block.position),
          properties: {
            kind: "block",
            facing: block.getProperties().facing ?? null,
            open: block.getProperties().open ?? null,
            powered: block.getProperties().powered ?? null,
            lit: block.getProperties().lit ?? null,
            enabled: block.getProperties().enabled ?? null,
          },
        });
      }
    }
  }
  return objects;
}

function inventoryObjects() {
  return bot.inventory.items().map((item) => ({
    label: itemLabel(item),
    position: null,
    distance: 0,
    properties: { kind: "inventory_item", count: item.count },
  }));
}

function inspectedContainerObjects() {
  if (!inspectedContainer) return [];
  if (distance(inspectedContainer.position) > inspectedContainer.radius) return [];
  return inspectedContainer.items.map((item) => ({
    label: item.label,
    position: inspectedContainer.position,
    distance: distance(inspectedContainer.position),
    properties: { kind: "container_item", count: item.count },
  }));
}

function position(value) {
  return { x: value.x, y: value.y, z: value.z };
}

function snapshot(radius) {
  const heldItem = bot.heldItem;
  const objects = [
    ...namedEntities(radius),
    ...nearbyPublicBlocks(radius),
    ...inventoryObjects(),
    ...inspectedContainerObjects(),
  ]
    .sort((left, right) =>
      left.label.localeCompare(right.label) ||
      (left.position?.x ?? 0) - (right.position?.x ?? 0) ||
      (left.position?.y ?? 0) - (right.position?.y ?? 0) ||
      (left.position?.z ?? 0) - (right.position?.z ?? 0)
    )
    .slice(0, MAX_OBJECTS)
    .map((object) => ({
      ...object,
      position: object.position ? position(object.position) : null,
    }));

  return {
    logical_time: bot.time.age,
    radius,
    terminal: false,
    terminal_reason: null,
    player: {
      position: position(bot.entity.position),
      orientation: { yaw: bot.entity.yaw, pitch: bot.entity.pitch },
      properties: {
        game_mode: bot.game.gameMode,
        held_item: heldItem ? itemLabel(heldItem) : null,
        selected_slot: bot.quickBarSlot,
        inventory_labels: bot.inventory.items().map(itemLabel).sort().join(", "),
      },
    },
    status: {
      health: bot.health,
      food: bot.food,
      air: Number.isFinite(bot.oxygenLevel) ? bot.oxygenLevel : 20,
      on_ground: bot.entity.onGround,
      game_mode: bot.game.gameMode,
      experience_level: bot.experience.level,
    },
    visible_objects: objects,
    messages: publicMessages,
  };
}

async function connect(message) {
  if (bot) throw new Error("the sidecar is already connected");
  bot = mineflayer.createBot({
    host: message.host,
    port: message.port,
    username: message.username,
    version: message.version,
    auth: "offline",
    hideErrors: true,
  });
  // `messagestr` already covers overlay system chat. `title ... actionbar`
  // arrives as its own packet, which Mineflayer's `actionBar` event ignores.
  bot.on("messagestr", rememberMessage);
  bot._client.on("action_bar", (packet) => rememberMessage(packet.text));
  bot.on("error", (error) => diagnose(error.message));

  await Promise.race([
    once(bot, "spawn"),
    once(bot, "kicked").then(([reason]) => {
      throw new Error(`kicked: ${text(reason)}`);
    }),
    once(bot, "error").then(([error]) => {
      throw error;
    }),
  ]);
}

function targetPosition(target) {
  return target && target.position ? vector(target.position) : null;
}

function blockTarget(target) {
  if (!target || !["block", "container_item"].includes(target.kind)) return null;
  const targetPoint = targetPosition(target);
  if (!targetPoint) return null;
  const block = bot.blockAt(targetPoint.floored(), false);
  return block && block.name !== "air" ? block : null;
}

function entityTarget(target) {
  const targetPoint = targetPosition(target);
  const candidates = Object.values(bot.entities).filter((entity) => {
    if (entity === bot.entity || !entity.position) return false;
    if (target.kind === "item") {
      if (entity.name !== "item" || typeof entity.getDroppedItem !== "function") return false;
      const item = entity.getDroppedItem();
      return item && itemLabel(item) === target.label;
    }
    if (target.kind === "label") {
      return entity.name === "armor_stand" && text(entity.customName).trim() === target.label;
    }
    return false;
  });
  candidates.sort((left, right) => {
    if (targetPoint) {
      return left.position.distanceTo(targetPoint) - right.position.distanceTo(targetPoint);
    }
    return distance(left.position) - distance(right.position);
  });
  const entity = candidates[0];
  if (!entity) return null;
  if (targetPoint && !samePosition(entity.position, targetPoint, 1.25)) return null;
  return entity;
}

function inventoryTarget(target) {
  if (!target || !["inventory_item", "held_item"].includes(target.kind)) return null;
  return bot.inventory.items().find((item) => itemLabel(item) === target.label) ?? null;
}

function ensureReachable(point) {
  if (distance(point) > REACH_DISTANCE) {
    throw new ActionError("UNREACHABLE", "The target is outside ordinary interaction reach.");
  }
}

async function moveTo(message) {
  const destination = vector(message);
  const tolerance = message.tolerance;
  const start = bot.entity.position.clone();
  let previousDistance = Infinity;
  let stalledTicks = 0;
  try {
    for (let ticks = 0; ticks < 160; ticks += 1) {
      const current = bot.entity.position;
      const horizontal = Math.hypot(destination.x - current.x, destination.z - current.z);
      const vertical = Math.abs(destination.y - current.y);
      if (horizontal <= tolerance && vertical <= 1.25) {
        return !samePosition(start, current, 0.05);
      }
      await bot.lookAt(
        current.offset(destination.x - current.x, 1.62, destination.z - current.z),
        true
      );
      bot.setControlState("forward", true);
      bot.setControlState("jump", destination.y > current.y + 0.4);
      await bot.waitForTicks(1);
      if (horizontal >= previousDistance - 0.01) stalledTicks += 1;
      else stalledTicks = 0;
      if (stalledTicks >= 30) {
        throw new ActionError("UNREACHABLE", "Walking made no progress toward the position.", true);
      }
      previousDistance = horizontal;
    }
  } finally {
    bot.setControlState("forward", false);
    bot.setControlState("jump", false);
  }
  throw new ActionError("UNREACHABLE", "The position was not reached in time.", true);
}

async function lookAtTarget(message) {
  const block = blockTarget(message.target);
  if (block) {
    await bot.lookAt(block.position.offset(0.5, 0.5, 0.5), true);
    return;
  }
  const entity = entityTarget(message.target);
  if (entity) {
    await bot.lookAt(entity.position.offset(0, entity.name === "item" ? 0.15 : 1, 0), true);
    return;
  }
  throw new ActionError("NO_VISIBLE_TARGET", "The visible target is no longer present.");
}

function rememberContainer(container, block, radius) {
  inspectedContainer = {
    position: block.position.clone(),
    radius,
    items: container.containerItems().map((item) => ({
      label: itemLabel(item),
      count: item.count,
    })),
  };
}

async function inspectObject(message) {
  const block = blockTarget(message.target);
  if (block && ["barrel", "hopper"].includes(block.name)) {
    ensureReachable(block.position);
    const container = await bot.openContainer(block);
    try {
      rememberContainer(container, block, message.radius ?? 5);
    } finally {
      container.close();
    }
    return;
  }
  await lookAtTarget(message);
}

async function collectObject(message) {
  if (message.target.kind === "container_item") {
    const block = blockTarget(message.target);
    if (!block || !["barrel", "hopper"].includes(block.name)) {
      throw new ActionError("NO_VISIBLE_TARGET", "The inspected container is no longer present.");
    }
    ensureReachable(block.position);
    const container = await bot.openContainer(block);
    try {
      const matches = container
        .containerItems()
        .filter((item) => itemLabel(item) === message.target.label);
      const available = matches.reduce((total, item) => total + item.count, 0);
      if (available < message.count || matches.length === 0) {
        throw new ActionError("PRECONDITION_FAILED", "The requested item count is unavailable.");
      }
      const item = matches[0];
      await container.withdraw(item.type, item.metadata, message.count);
      rememberContainer(container, block, message.radius ?? 5);
    } finally {
      container.close();
    }
    return;
  }

  const entity = entityTarget(message.target);
  if (!entity) throw new ActionError("NO_VISIBLE_TARGET", "The visible item is no longer present.");
  const dropped = entity.getDroppedItem();
  if (!dropped || dropped.count < message.count) {
    throw new ActionError("PRECONDITION_FAILED", "The requested item count is unavailable.");
  }
  await moveTo({
    x: entity.position.x,
    y: entity.position.y,
    z: entity.position.z,
    tolerance: 1,
  });
  await bot.waitForTicks(5);
}

async function equipPublicItem(target) {
  const item = inventoryTarget(target);
  if (!item) throw new ActionError("NO_VISIBLE_TARGET", "The inventory item is no longer present.");
  await bot.equip(item, "hand");
  return item;
}

async function useObject(message) {
  if (message.held_item) await equipPublicItem(message.held_item);
  const block = blockTarget(message.target);
  if (block) {
    ensureReachable(block.position);
    await bot.activateBlock(block);
    return;
  }
  const entity = entityTarget(message.target);
  if (!entity) {
    throw new ActionError("NO_VISIBLE_TARGET", "The visible target is no longer present.");
  }
  ensureReachable(entity.position);
  await bot.activateEntity(entity);
}

async function placeObject(message) {
  const item = await equipPublicItem(message.held_item);
  const destination = vector(message.position);
  ensureReachable(destination);
  const destinationBlock = bot.blockAt(destination.floored(), false);
  const blockItem = bot.registry.blocksByName[item.name];
  if (blockItem && destinationBlock && destinationBlock.name === "air") {
    const faces = [
      [0, -1, 0],
      [0, 1, 0],
      [-1, 0, 0],
      [1, 0, 0],
      [0, 0, -1],
      [0, 0, 1],
    ];
    for (const [x, y, z] of faces) {
      const reference = bot.blockAt(destinationBlock.position.offset(-x, -y, -z), false);
      if (reference && reference.name !== "air") {
        const face = reference.position.offset(x, y, z).minus(reference.position);
        await bot.placeBlock(reference, face);
        return;
      }
    }
  }
  await bot.lookAt(destination, true);
  await bot.toss(item.type, item.metadata, 1);
}

async function perform(message) {
  let stateChanged = false;
  if (message.op === "move_to") stateChanged = await moveTo(message);
  else if (message.op === "look_at") {
    await lookAtTarget(message);
    stateChanged = true;
  } else if (message.op === "inspect_object") await inspectObject(message);
  else if (message.op === "collect_object") {
    await collectObject(message);
    stateChanged = true;
  } else if (message.op === "use_object") {
    await useObject(message);
    stateChanged = true;
  } else if (message.op === "place_object") {
    await placeObject(message);
    stateChanged = true;
  } else if (message.op === "wait") await bot.waitForTicks(message.ticks);
  else throw new ActionError("INVALID_TOOL", "The operation is not supported.");

  if (message.settle_ticks) await bot.waitForTicks(message.settle_ticks);
  return { observation: snapshot(message.radius ?? 5), state_changed: stateChanged };
}

async function reset(message) {
  inspectedContainer = null;
  for (const command of message.commands ?? []) {
    if (typeof command !== "string" || !command.trim()) continue;
    bot.chat(command.startsWith("/") ? command : `/${command}`);
  }
  await bot.waitForTicks(message.settle_ticks ?? 5);
  // Command feedback is administrative transport detail, not public game state.
  publicMessages = [];
  return snapshot(message.radius ?? 5);
}

async function handle(message) {
  const id = message && message.id;
  try {
    if (!message || typeof message !== "object") throw new Error("request must be an object");
    if (message.op === "connect") {
      await connect(message);
      write({ id, ok: true });
    } else if (message.op === "reset") {
      if (!bot) throw new Error("connect must succeed before reset");
      write({ id, ok: true, observation: await reset(message) });
    } else if (message.op === "observe") {
      if (!bot) throw new Error("connect must succeed before observe");
      write({ id, ok: true, observation: snapshot(message.radius ?? 5) });
    } else if (message.op === "chat") {
      if (!bot) throw new Error("connect must succeed before chat");
      if (typeof message.text !== "string" || !message.text || message.text.length > 256) {
        throw new Error("chat text must contain 1 to 256 characters");
      }
      bot.chat(chatCommand(message.text));
      write({ id, ok: true });
    } else if (
      [
        "move_to",
        "look_at",
        "inspect_object",
        "collect_object",
        "use_object",
        "place_object",
        "wait",
      ].includes(message.op)
    ) {
      if (!bot) throw new Error(`connect must succeed before ${message.op}`);
      write({ id, ok: true, ...(await perform(message)) });
    } else if (message.op === "close") {
      write({ id, ok: true });
      if (bot) bot.quit("Connector closed");
      bot = null;
      process.exitCode = 0;
      input.close();
    } else {
      throw new Error(`unsupported operation: ${String(message.op)}`);
    }
  } catch (error) {
    diagnose(error instanceof Error ? error.message : String(error));
    write({
      id,
      ok: false,
      code: error instanceof ActionError ? error.code : "GAME_REJECTED",
      error: error instanceof ActionError ? error.message : "The game request failed.",
      state_changed: error instanceof ActionError ? error.stateChanged : false,
    });
  }
}

let input = null;

if (require.main === module) {
  input = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });
  let queue = Promise.resolve();
  input.on("line", (line) => {
    queue = queue.then(async () => {
      let message;
      try {
        message = JSON.parse(line);
      } catch {
        write({ id: null, ok: false, code: "GAME_REJECTED", error: "Invalid JSON request." });
        return;
      }
      await handle(message);
    });
  });
}

// Exported only so pure message helpers can be tested without a game server.
module.exports = { isPublicMessage, text };
