# Register workshop

The optional `register_workshop` profile supplies only a marked 9×9 flat stone
support pad at x=40..48, y=64, z=40..48, four blocks of clear headroom, and a
glass marker at x=40, y=65, z=40. It does not place controls, wire, probes, a
circuit, or a blueprint. Its setup metadata and scenario version are
kept separate from the frozen `redstone-computer-v1` template. Trials using this
profile set `baseline_comparable` to false; their fullcomputer result must not be
reported as acceptance under the original baseline.

Trusted setup commands are journaled separately from construction actions.
Scaffold block checks use ordinary charged reads. The effective profile, setup
commands, scenario path, and scenario SHA-256 are saved to the trial manifest.

`grade_register_bit(actions, declaration, bit)` checks exactly one logical bit
of an already declared four-bit register. The input recipes are expanded from
the declaration's `load` recipe template and may drive only declared
`test_input` controls. The grader owns reset and STEP and runs the sequence:

1. Reset, load zero, and clock once.
2. Load one at the selected logical bit and clock once.
3. Change the input back to zero while STEP stays low, then sample the held bit.
4. Assert reset while STEP stays low, then sample the cleared bit.

Probe arrays are MSB first, so logical bit 0 maps to probe index 3. The result
contains observed load, hold, and reset evidence plus verified control effects.
Its `passed` field is diagnostic only; `checkpoint_eligible` and
`fullcomputer_eligible` are always false. A passing one-bit probe does not alter
the frozen all-16 four-bit register suite or grant any checkpoint.

The scaffold helper returns deterministic setup commands; only the trusted
setup adapter executes them. The setup never seeds a logical circuit.
