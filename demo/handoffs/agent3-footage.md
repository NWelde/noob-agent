# Agent 3 cinematography and capture handoff

## Current status

Capture was verified before construction: OBS recording at 1920x1080/60 fps with H.264/AAC, and Replay Mod's red recording indicator visible. OBS must carry the status moments until Replay text-display persistence is explicitly verified. Main OBS source started 2026-09-29 16:52:35 local at:

`\\wsl.localhost\Ubuntu\home\nathan\noob-agent\.noob-agent\recordings\20260926-persistent-computer-register\2026-09-29 16-52-34.mkv`

Do not assume it is still recording after interruption. Check the OBS state and file modification time first. Minecraft must stay foreground; switching away from fullscreen previously caused a black/minimized capture. Client was corrected to 16:9. The build is partial, and the last full run stopped after 26 verified actions, before any module grade. Preserve the partial world and footage; the complete state and paths are in `handoffs/continuation-2026-09-29.md`.

## User's camera direction

The spectator or the recording should follow the agent around. Make a stable, elevated trailing shot the main construction view. Keep these three things together whenever possible: the moving bot, its real overhead status text, and the blocks/circuit being placed. A rear three-quarter angle should give enough distance to see movement and enough scale to read the status. Pan or track smoothly with the player. Do not jump to a different side or unrelated angle between nearby actions.

If the agent moves farther than the current composition can cover, retain the movement in the same shot when possible. If a camera relocation is required, make it a hard cut at a real milestone, re-establish the same screen direction and a recognizable landmark, then follow continuously again. Use wide overhead/side views sparingly to explain a completed stage; return to the established follow angle. Avoid rapid circling, confusing reversals, clipping into blocks, and shots that show circuitry while losing the player/status text.

Hold genuine status transitions (`Thinking`, `Building`, `Placing`, `Place ...`, `Inspect block`) at normal speed long enough to read, including the action context in the frame. Do not create substitute labels or present edited captions as the agent's status. Existing hero evidence: `Place redstone torch` around 17:07:19 local (about 884 seconds after the OBS start) showed the green label, bot, held torch, supports and lever. `Inspect block` was also seen around 17:07:57 local. Confirm precise timecodes against the source before editing. A camera-only move to `(14,69,18)` facing the register work near `(10,65,10)` occurred at 00:07:32.586Z. This is a documented observer intervention, not a build action.

## Capture and Replay handling

During construction, keep OBS and Replay recording and the Minecraft window foreground. Do not open Replay playback/editing in this same client until live construction ends. Preserve raw OBS MKV, original `.mcpr`, harness logs/manifests and milestone timecodes. The OBS test/export evidence and Replay test copy are listed in the continuation handoff. Replay status survival is not verified; use actual OBS status shots unless a later playback test proves the text survives.

Before each resumed construction stretch, confirm OBS is actually recording at 60 fps, Replay's recording indicator is active, the live frame is clean 16:9, and the follow view contains player/status/work area. The active camera should follow actual bot movement. Camera-only RCON/spectator changes can be used when needed to maintain the shot, but record their time and destination and do not let them look like player construction.

## Editing notes for later

Keep spatial continuity through the build. Accelerate repetitive placement only when the viewer can still understand how the layout develops; label accelerated footage. Hard cuts should land on actual architecture milestones and keep orientation consistent. Hold the final calculation at normal speed and show real inputs, execution, and independently verified output. If full-computer evidence never passes, do not caption the partial register or wiring as a working computer. The final judges' demo target is 90–120 seconds, landscape 1920x1080 at 60 fps, H.264 video and AAC audio. Music is music-free unless the exact requested recording and audiovisual permissions are identified.
