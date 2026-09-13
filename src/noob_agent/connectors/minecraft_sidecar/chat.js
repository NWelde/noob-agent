"use strict";

const COLORS = { Seeing: "aqua", Doing: "gold", Pressing: "green", Learning: "light_purple" };

function isNarration(text) {
  return /^(?:<[^>]+> )?\[noob:/.test(text);
}

function chatCommand(text) {
  const match = /^\[noob:(Seeing|Doing|Pressing|Learning)\] (.+)$/.exec(text);
  if (!match) throw new Error("Expected a demo narration message");
  const verb = match[1];
  let body = match[2].replace(/§./g, "").replace(/[\r\n]/g, " ");
  const render = () => "/tellraw @a " + JSON.stringify([
    { text: "[noob:] ", color: "gray" },
    { text: verb, color: COLORS[verb] },
    { text: " " + body, color: "white" },
  ]);
  // Mineflayer splits long chat at 256 characters, including commands.
  while (render().length > 256) body = body.slice(0, -1);
  return render();
}

module.exports = { chatCommand, isNarration };
