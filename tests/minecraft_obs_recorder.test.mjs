import assert from 'node:assert/strict';
import {mkdtemp, mkdir, readFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import path from 'node:path';
import test from 'node:test';

import {setupCapture, startAttempt, stopAttempt, validateRunId, windowsPathToWsl} from '../scripts/record_minecraft.mjs';

function fakeObs(overrides = {}) {
  const calls = [];
  let recording = false;
  const responses = {
    GetSceneList: {scenes: [{sceneName: 'Minecraft Demo'}]},
    GetInputList: {inputs: [{inputName: 'Minecraft Window', inputKind: 'window_capture'}]},
    GetSceneItemList: {sceneItems: [{sourceName: 'Minecraft Window', sceneItemId: 1, sceneItemEnabled: true}]},
    GetInputSettings: {
      inputKind: 'window_capture',
      inputSettings: {method: 2, window: 'Minecraft 1.21.1:GLFW30:javaw.exe'},
    },
    GetProfileParameter: {parameterValue: 'mkv'},
    GetCurrentProgramScene: {currentProgramSceneName: 'Scene'},
    GetRecordDirectory: {recordDirectory: 'C:\\Users\\natha\\Videos'},
    StopRecord: {outputPath: 'C:\\Users\\natha\\Videos\\attempt-01\\capture.mkv'},
    ...overrides,
  };
  return {
    calls,
    async request(type, data = {}) {
      calls.push({type, data});
      if (type === 'GetRecordStatus') return {outputActive: recording};
      if (type === 'StartRecord') recording = true;
      if (type === 'StopRecord') recording = false;
      return responses[type] ?? {};
    },
  };
}

test('run IDs cannot escape the recording directory', () => {
  for (const id of ['../outside', 'a/b', 'a\\b', '', '.']) {
    assert.throws(() => validateRunId(id), /run ID/i);
  }
  assert.equal(validateRunId('attempt-01_a'), 'attempt-01_a');
  assert.equal(windowsPathToWsl('C:\\Users\\natha\\Videos\\clip.mkv'), '/mnt/c/Users/natha/Videos/clip.mkv');
  assert.equal(
    windowsPathToWsl('\\\\wsl.localhost\\Ubuntu\\home\\nathan\\noob-agent\\clip.mkv'),
    '/home/nathan/noob-agent/clip.mkv',
  );
});

test('setup enables the Minecraft window source and disables the failed game hook', async () => {
  const obs = fakeObs({GetSceneItemList: {sceneItems: [
    {sourceName: 'Minecraft Window', sceneItemId: 2, sceneItemEnabled: false},
    {sourceName: 'Minecraft Game', sceneItemId: 1, sceneItemEnabled: true},
  ]}});

  await setupCapture({obs, window: 'Minecraft 1.21.1:GLFW30:javaw.exe'});
  assert.deepEqual(obs.calls.filter(call => call.type === 'SetSceneItemEnabled').map(call => call.data), [
    {sceneName: 'Minecraft Demo', sceneItemId: 2, sceneItemEnabled: true},
    {sceneName: 'Minecraft Demo', sceneItemId: 1, sceneItemEnabled: false},
  ]);
});

test('start refuses an existing run before touching OBS', async () => {
  const root = await mkdtemp(path.join(tmpdir(), 'obs-test-'));
  await mkdir(path.join(root, 'attempt-01'));
  const obs = fakeObs();

  await assert.rejects(startAttempt({runId: 'attempt-01', root, obs}), /already exists/i);
  assert.deepEqual(obs.calls, []);
});

test('start selects only the dedicated Minecraft window and writes an active run', async () => {
  const root = await mkdtemp(path.join(tmpdir(), 'obs-test-'));
  const obs = fakeObs();

  const result = await startAttempt({
    runId: 'attempt-01',
    root,
    obs,
    windowsRoot: 'C:\\Users\\natha\\Videos\\noob-agent',
    now: () => '2026-09-22T20:00:00.000Z',
  });

  assert.equal(result.runId, 'attempt-01');
  assert.equal(result.status, 'recording');
  assert.deepEqual(obs.calls.slice(-3).map(call => call.type), [
    'SetCurrentProgramScene', 'SetRecordDirectory', 'StartRecord',
  ]);
  assert.equal(obs.calls.at(-2).data.recordDirectory, 'C:\\Users\\natha\\Videos\\noob-agent\\attempt-01');
  const active = JSON.parse(await readFile(path.join(root, 'active.json'), 'utf8'));
  assert.equal(active.runId, 'attempt-01');
});

test('start rejects an OBS scene without a specific Minecraft window capture', async () => {
  const root = await mkdtemp(path.join(tmpdir(), 'obs-test-'));
  const obs = fakeObs({GetInputSettings: {
    inputKind: 'display_capture', inputSettings: {capture_mode: 'any_fullscreen'},
  }});

  await assert.rejects(startAttempt({runId: 'attempt-02', root, obs}), /Minecraft Window Capture/i);
  assert.equal(obs.calls.some(call => call.type === 'StartRecord'), false);
});

test('start refuses OBS formats other than MKV', async () => {
  const root = await mkdtemp(path.join(tmpdir(), 'obs-test-'));
  const obs = fakeObs({GetProfileParameter: {parameterValue: 'hybrid_mp4'}});

  await assert.rejects(startAttempt({runId: 'attempt-02', root, obs}), /MKV/i);
  assert.equal(obs.calls.some(call => call.type === 'StartRecord'), false);
});

test('stop rejects a different run ID and preserves the active recording', async () => {
  const root = await mkdtemp(path.join(tmpdir(), 'obs-test-'));
  const obs = fakeObs();
  await startAttempt({runId: 'attempt-01', root, obs, windowsRoot: 'C:\\Videos'});
  obs.calls.length = 0;

  await assert.rejects(stopAttempt({runId: 'attempt-02', root, obs}), /active run is attempt-01/i);
  assert.deepEqual(obs.calls, []);
});

test('stop keeps the original MKV and writes a manifest after MP4 remux', async () => {
  const root = await mkdtemp(path.join(tmpdir(), 'obs-test-'));
  const obs = fakeObs();
  await startAttempt({runId: 'attempt-01', root, obs, windowsRoot: 'C:\\Users\\natha\\Videos'});
  obs.calls.length = 0;
  const remuxed = [];

  const result = await stopAttempt({
    runId: 'attempt-01', root, obs,
    now: () => '2026-09-22T20:05:00.000Z',
    remux: async (source, target) => { remuxed.push({source, target}); },
  });

  assert.equal(result.status, 'complete');
  assert.equal(obs.calls.at(0).type, 'GetRecordStatus');
  assert.equal(obs.calls.at(1).type, 'StopRecord');
  assert.equal(remuxed.length, 1);
  assert.equal(remuxed[0].source, '/mnt/c/Users/natha/Videos/attempt-01/capture.mkv');
  const saved = JSON.parse(await readFile(path.join(root, 'attempt-01', 'manifest.json'), 'utf8'));
  assert.equal(saved.originalVideo, remuxed[0].source);
  assert.equal(saved.status, 'complete');
});
