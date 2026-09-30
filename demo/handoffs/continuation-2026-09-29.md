# Minecraft judges-demo continuation

Updated 2026-09-29 (America/Los_Angeles). This file supersedes older status text in `PROGRESS.md`, `handoffs/agent1-recording.md`, `handoffs/agent2-build.md`, and `handoffs/agent3-footage.md` wherever their status conflicts. The current state is incomplete. Resume by checking the live processes and files below; do not assume recording or the Minecraft server is still running.

## Immediate state at handoff

The user confirmed the intended setup is the existing `C:\Users\natha\noob-agent` Minecraft harness, release Fabric Loader 1.21.1, and authorized a recorded live run using Sol's initial design notes. The assistance must be disclosed. The build must still be produced by the live agent through real player movement and block placement; a prebuilt structure, server-side `setblock`, or scripted output is not evidence of success.

The Minecraft client joined `172.17.247.132:25567`. OBS and Replay Mod were both recording from 2026-09-29 16:52:35 local. OBS test capture/export passed at 1920x1080, 60 fps, H.264/AAC. The green `Inspect block` and `Place redstone torch` state labels were actually visible above the bot in OBS footage. OBS is required for status text because Replay playback has not yet been checked for text-display persistence. Minecraft's 16:9 framing was corrected. Keep the game foreground: alt-tabbing from fullscreen previously minimized it and produced a black OBS segment.

Agent 2 added an opt-in physical action adapter on the live WSL branch `codex/demo-physical-placement`, with a corresponding set of uncommitted edits in the Windows clone's `fix/redstone-computer-run-bottlenecks` branch. The live adapter passed a recorded geometry smoke: the bot walked, placed and read back a north-facing repeater, wall torch and floor lever; lever operation changed a real lamp off/on/off; the bot physically cleaned up the test. One pre-smoke world archive is `demo/world-backups/20260929T235341Z-before-physical-smoke.tar.gz`. Initial orientation failures and recovery records are retained. Do not assume Windows and WSL worktrees are identical; inspect both and preserve uncommitted source edits.

The latest full computer attempt is partial and failed. Manifest: `.noob-agent/redstone-trials/20260930T000604-db7ed63d964648adb414ebe83422a956/manifest.json`. The bot genuinely walked from `(48.5,64,98.5)` in four stages, then placed a stone support at `(10,64,10)` and a floor lever at `(10,65,10)`; lever readback was north-facing and off. The run reached 26 effect-verified actions, then stopped with `SidecarError` while trying to place stone at `(25,64,10)`. No module checks passed; no register or arithmetic success has been established. At interruption Agent 2 was inspecting the failed target/player pose and working on bounded obstacle-jump handling and safe continuation. Check the manifest, event journal, target/support blocks, player position, and current server before resuming. Never repeat an uncertain post-dispatch placement blindly. Preserve the partial build and evidence.

Attempt 2's rejected lever placement at `(20,65,10)` was refused before mutation because its support was missing; independent reads showed both cells remained air. A narrow diagnostic fix now distinguishes known pre-placement rejections from uncertain post-dispatch outcomes. Do not weaken the fatal handling of unknown outcomes.

## Recording and files

- OBS test original: `demo-production/test/capture-test-original.mkv` (122.25 s; 1920x1080@60, H.264/AAC).
- OBS test export: `demo-production/test/capture-test-export.mp4` (6 s; 1920x1080@60, H.264/AAC).
- Genuine status frame: `demo-production/test/inspect-status-59s.png`.
- OBS test Replay copy: `demo-production/test/2026_09_29_16_39_19.mcpr`; status survival is unverified.
- Main OBS source file was recorded under `\\wsl.localhost\Ubuntu\home\nathan\noob-agent\.noob-agent\recordings\20260926-persistent-computer-register\2026-09-29 16-52-34.mkv`. Check whether it is still open/writing before copying, remuxing, or stopping OBS.
- Agent 3 handoff and detailed follow-shot direction: `handoffs/agent3-footage.md`.
- Agent 2 implementation, smoke, and run evidence: `demo-production/agent2-handoff.md` in the Codex task workspace and the live WSL `demo/` directory. The older repo file `handoffs/agent2-build.md` predates the run; this continuation is authoritative.
- OBS settings backups and test image are in `demo-production/config-before/` and `demo-production/test/` in the Codex task workspace. Preserve them.
- Root progress notes are in `demo-production/progress.md` in the Codex task workspace; this repo handoff is the durable summary to commit.

## Camera direction from the user

Keep one understandable visual geography. The spectator camera should follow the agent continuously, or the recording should follow it. The key shot is a smooth, slightly elevated trailing view that keeps the moving player, the genuine status text above the player, and the current placement area in the same frame. Favor a stable rear three-quarter angle at a modest height and distance; adjust smoothly as the bot walks rather than jumping the spectator to unrelated coordinates between individual actions. Do not alternate unexplained sides or rotate around the build just to add variety.

Use one wider establishing angle only when it helps explain the whole layout. Return to the same follow direction after a meaningful hard cut, keeping the same landmark/orientation visible so spatial continuity is clear. A top-down or opposite-side milestone shot is appropriate only at a real stage boundary, and should cut back to the established follow shot. Hold close enough for the existing `Thinking`, `Building`, `Placing`, `Place ...`, or `Inspect block` text to be read; capture genuine state changes at normal speed for a few seconds. Do not add replacement labels and imply they are the agent's status. If the camera must relocate to follow a distant work area, make the move a clean cut at a milestone, then hold a continuous follow shot for the next construction stretch.

Replay camera editing is post-run work. Do not open/play/edit the Replay in the active Minecraft client during construction. OBS is the verified status-text capture fallback. Preserve OBS and Replay originals and note their timestamps before editing.

## Next actions

1. Recheck Minecraft, server, OBS and Replay process state and the latest live manifest before any action. Keep the original world and partial construction; archive the current world before any reset.
2. Resume the movement/placement fix from actual failed pose and block reads. Keep movement bounded, use real walking/jump controls, and verify final block identity/properties. Known preflight failures may be reported to the planner as rejected actions. Any uncertain post-dispatch action stays fatal until independently read back.
3. Confirm live OBS and Replay recording, foreground 16:9 framing, and the spectator follow view before another construction stretch. Start/continue footage before construction; do not claim a recording-ready test is the final footage.
4. Continue the original assisted stored-program computer objective only after the physical movement correction is confirmed. Accurate intended architecture: 4-bit accumulator/output, modulo-16 ADD, 3-bit program counter, eight 6-bit instruction words, LOAD/ADD/OUT/HALT. Intended demonstration: `LOAD 3 → OUT → ADD 5 → OUT → HALT`, with actual outputs 3 then 8. These are target expectations, not current evidence. The initial Sol register design guidance is assistance and must be identified in the verification note.
5. Record milestone timecodes and actual independent signal reads. Do not call a register complete until its checks pass; do not call arithmetic/computer success until output 8 is physically observed and independently verified. If the planner fails, preserve the attempt and repair the specific failure before retrying.
6. Only after BUILD_VERIFIED and usable FOOTAGE_READY should the editor create a draft, then a 90–120 second 1920x1080, 60 fps H.264/AAC MP4; review the entire export and probe it. Use music-free export if no exact Pirates recording and audiovisual permissions are available. The current candidate and rights status are documented in `assets/MUSIC.md` and `demo-production/music-dependency.md`.

## Material interventions and disclosure

Sol supplied initial design notes for the assisted live planner; those notes guide architecture but do not create blocks or drive outputs. The physical smoke used real player movement, Mineflayer item placement/interactions, independent reads and physical cleanup. OBS setup required changing output to 1080p60 and correcting Minecraft to 16:9 window/fullscreen settings. RCON was used only for observer camera positioning and earlier camera-only setup; spectator mode was enabled to hold the camera. Do not describe camera positioning as agent construction. The previous test demonstrated status text in OBS but did not prove Replay status persistence. The full computer has not been verified, and no final demo file exists as of this handoff.
