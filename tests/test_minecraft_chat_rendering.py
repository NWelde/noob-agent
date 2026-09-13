"""Exercise the actual JavaScript chat renderer without a running server."""

import subprocess
from pathlib import Path


def test_colored_chat_and_echo_filter() -> None:
    module = Path(__file__).parents[1] / "src/noob_agent/connectors/minecraft_sidecar/chat.js"
    subprocess.run(
        [
            "node",
            "-e",
            """
const assert = require('node:assert/strict');
const {chatCommand, isNarration} = require(process.argv[1]);
const colors = {Seeing:'aqua', Doing:'gold', Pressing:'green', Learning:'light_purple'};
for (const [verb, color] of Object.entries(colors)) {
  const command = chatCommand(`[noob:${verb}] Gate Button.`);
  const parts = JSON.parse(command.slice('/tellraw @a '.length));
  assert.equal(parts[1].text, verb);
  assert.equal(parts[1].color, color);
  assert.equal(parts[2].color, 'white');
  assert(isNarration(parts.map(p => p.text).join('')));
}
assert(isNarration('<NoobAgent> [noob:Doing] moving.'));
assert(!isNarration('The iron-bar gateway opens.'));
assert(chatCommand('[noob:Doing] ' + '"\\\\'.repeat(200)).length <= 256);
assert.throws(() => chatCommand('/op someone'));
""",
            str(module),
        ],
        check=True,
    )
