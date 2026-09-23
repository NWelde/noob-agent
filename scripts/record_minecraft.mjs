/** Record each TLauncher-launched Minecraft demo attempt through Windows OBS. */

import {execFile, execFileSync} from 'node:child_process';
import {createHash, randomUUID} from 'node:crypto';
import {access, mkdir, readFile, rm, writeFile} from 'node:fs/promises';
import path from 'node:path';
import {promisify} from 'node:util';
import {pathToFileURL} from 'node:url';

const execFileAsync = promisify(execFile);
const SCENE = 'Minecraft Demo';
const INPUT = 'Minecraft Window';
const DEFAULT_ROOT = path.resolve('.noob-agent/recordings');
const CONTROL_TIMEOUT_MS = 10_000;

export function validateRunId(value) {
  if (typeof value !== 'string' || !/^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$/.test(value)) {
    throw new Error('Run ID must be 1–80 letters, digits, hyphens, or underscores.');
  }
  return value;
}

export function windowsPathToWsl(value) {
  const normalized = value.replaceAll('/', '\\');
  const drive = /^([A-Za-z]):\\(.*)$/.exec(normalized);
  if (drive) return `/mnt/${drive[1].toLowerCase()}/${drive[2].replaceAll('\\', '/')}`;
  const unc = /^\\\\wsl(?:\.localhost|\$)\\[^\\]+\\(.*)$/i.exec(normalized);
  if (unc) return `/${unc[1].replaceAll('\\', '/')}`;
  throw new Error(`OBS returned an unsupported recording path: ${value}`);
}

function windowsDirectory(linuxDirectory) {
  return execFileSync('wslpath', ['-w', linuxDirectory], {encoding: 'utf8'}).trim();
}

async function exists(file) {
  try { await access(file); return true; } catch { return false; }
}

async function saveJson(file, value) {
  await writeFile(file, `${JSON.stringify(value, null, 2)}\n`, {flag: 'wx'});
}

function requireCapture(sceneList, inputList, inputSettings, sceneItems) {
  if (!sceneList.scenes?.some(scene => scene.sceneName === SCENE)) {
    throw new Error(`OBS scene ${SCENE} is missing. Run setup with Minecraft open.`);
  }
  if (!inputList.inputs?.some(input => input.inputName === INPUT && input.inputKind === 'window_capture')
      || inputSettings.inputKind !== 'window_capture'
      || inputSettings.inputSettings?.method !== 2
      || !sceneItems.sceneItems?.some(item => item.sourceName === INPUT && item.sceneItemEnabled)
      || !/minecraft/i.test(inputSettings.inputSettings?.window ?? '')) {
    throw new Error('Minecraft Window Capture is missing or is not bound to the Minecraft window.');
  }
}

export async function setupCapture({obs, window}) {
  if (!window || !/minecraft/i.test(window) || !/javaw?\.exe$/i.test(window)) {
    throw new Error('A running Minecraft Java game window is required for setup.');
  }
  const sceneList = await obs.request('GetSceneList');
  if (!sceneList.scenes?.some(scene => scene.sceneName === SCENE)) {
    await obs.request('CreateScene', {sceneName: SCENE});
  }
  const inputList = await obs.request('GetInputList');
  const inputSettings = {window, method: 2, cursor: false, client_area: true};
  if (inputList.inputs?.some(input => input.inputName === INPUT)) {
    const existing = inputList.inputs.find(input => input.inputName === INPUT);
    if (existing.inputKind !== 'window_capture') {
      throw new Error(`${INPUT} already exists with a different source type.`);
    }
    await obs.request('SetInputSettings', {inputName: INPUT, inputSettings, overlay: true});
  } else {
    await obs.request('CreateInput', {
      sceneName: SCENE, inputName: INPUT, inputKind: 'window_capture',
      inputSettings, sceneItemEnabled: true,
    });
  }
  const sceneItems = await obs.request('GetSceneItemList', {sceneName: SCENE});
  const windowItem = sceneItems.sceneItems?.find(item => item.sourceName === INPUT);
  if (!windowItem) {
    await obs.request('CreateSceneItem', {sceneName: SCENE, sourceName: INPUT, sceneItemEnabled: true});
  } else if (!windowItem.sceneItemEnabled) {
    await obs.request('SetSceneItemEnabled', {
      sceneName: SCENE, sceneItemId: windowItem.sceneItemId, sceneItemEnabled: true,
    });
  }
  for (const item of sceneItems.sceneItems ?? []) {
    if (item.sourceName === 'Minecraft Game' && item.sceneItemEnabled) {
      await obs.request('SetSceneItemEnabled', {
        sceneName: SCENE, sceneItemId: item.sceneItemId, sceneItemEnabled: false,
      });
    }
  }
  await obs.request('SetCurrentProgramScene', {sceneName: SCENE});
  return {scene: SCENE, source: INPUT, window};
}

export async function startAttempt({runId, root = DEFAULT_ROOT, obs, windowsRoot,
                                    now = () => new Date().toISOString()}) {
  validateRunId(runId);
  const runDir = path.join(root, runId);
  if (await exists(runDir)) throw new Error(`Recording run ${runId} already exists.`);
  if (await exists(path.join(root, 'active.json'))) {
    throw new Error('A recording is already active; stop it before starting another.');
  }
  const status = await obs.request('GetRecordStatus');
  if (status.outputActive) throw new Error('OBS is already recording outside this script.');
  const [scenes, inputs, sceneItems, format] = await Promise.all([
    obs.request('GetSceneList'), obs.request('GetInputList'),
    obs.request('GetSceneItemList', {sceneName: SCENE}),
    obs.request('GetProfileParameter', {
      parameterCategory: 'SimpleOutput', parameterName: 'RecFormat2',
    }),
  ]);
  if (!inputs.inputs?.some(input => input.inputName === INPUT)) {
    throw new Error('Minecraft Window Capture is missing. Run setup with Minecraft open.');
  }
  const settings = await obs.request('GetInputSettings', {inputName: INPUT});
  requireCapture(scenes, inputs, settings, sceneItems);
  if (format.parameterValue !== 'mkv') {
    throw new Error('OBS recording format must be MKV. Set it in OBS Settings → Output, then restart OBS.');
  }
  const previousScene = (await obs.request('GetCurrentProgramScene')).currentProgramSceneName;
  const previousDirectory = (await obs.request('GetRecordDirectory')).recordDirectory;
  await mkdir(root, {recursive: true});
  await mkdir(runDir, {recursive: false});
  const recordDirectory = path.win32.join(windowsRoot ?? windowsDirectory(root), runId);
  try {
    await obs.request('SetCurrentProgramScene', {sceneName: SCENE});
    await obs.request('SetRecordDirectory', {recordDirectory});
    await obs.request('StartRecord');
  } catch (error) {
    await Promise.allSettled([
      obs.request('SetCurrentProgramScene', {sceneName: previousScene}),
      obs.request('SetRecordDirectory', {recordDirectory: previousDirectory}),
    ]);
    throw error;
  }
  const result = {
    runId, status: 'recording', startedAt: now(), scene: SCENE,
    recordDirectory, previousScene, previousDirectory,
  };
  await saveJson(path.join(runDir, 'manifest.json'), result);
  await saveJson(path.join(root, 'active.json'), result);
  return result;
}

async function defaultRemux(source, target) {
  await execFileAsync('ffmpeg', ['-hide_banner', '-loglevel', 'error', '-n', '-i', source,
    '-map', '0', '-c', 'copy', '-movflags', '+faststart', target]);
}

export async function stopAttempt({runId, root = DEFAULT_ROOT, obs,
                                   now = () => new Date().toISOString(), remux = defaultRemux}) {
  validateRunId(runId);
  const activeFile = path.join(root, 'active.json');
  const active = JSON.parse(await readFile(activeFile, 'utf8'));
  if (active.runId !== runId) throw new Error(`Active run is ${active.runId}, not ${runId}.`);
  const status = await obs.request('GetRecordStatus');
  if (!status.outputActive) throw new Error('OBS is not recording; original MKV may need recovery.');
  const stopped = await obs.request('StopRecord');
  if (!stopped.outputPath) throw new Error('OBS stopped but returned no recording path.');
  const originalVideo = windowsPathToWsl(stopped.outputPath);
  const mp4Video = path.join(root, runId, 'recording.mp4');
  const result = {
    ...active, status: 'captured', stoppedAt: now(), originalVideo,
    mp4Video: null,
  };
  const manifestFile = path.join(root, runId, 'manifest.json');
  await writeFile(manifestFile, `${JSON.stringify(result, null, 2)}\n`);
  try {
    await remux(originalVideo, mp4Video);
    result.status = 'complete';
    result.mp4Video = mp4Video;
    await writeFile(manifestFile, `${JSON.stringify(result, null, 2)}\n`);
  } finally {
    await Promise.allSettled([
      obs.request('SetCurrentProgramScene', {sceneName: active.previousScene}),
      obs.request('SetRecordDirectory', {recordDirectory: active.previousDirectory}),
    ]);
    await rm(activeFile);
  }
  return result;
}

function authResponse(password, salt, challenge) {
  const secret = createHash('sha256').update(password + salt).digest('base64');
  return createHash('sha256').update(secret + challenge).digest('base64');
}

export function connectObs({host, port = 4455, password}) {
  return new Promise((resolve, reject) => {
    const socket = new WebSocket(`ws://${host}:${port}`);
    const pending = new Map();
    const timeout = setTimeout(() => {
      socket.close();
      reject(new Error(`OBS WebSocket did not respond at ${host}:${port}.`));
    }, CONTROL_TIMEOUT_MS);
    socket.addEventListener('error', () => {
      clearTimeout(timeout);
      reject(new Error(`Cannot connect to OBS WebSocket at ${host}:${port}.`));
    });
    socket.addEventListener('close', () => {
      for (const item of pending.values()) item.reject(new Error('OBS WebSocket closed.'));
      pending.clear();
    });
    socket.addEventListener('message', event => {
      const message = JSON.parse(event.data);
      if (message.op === 0) {
        const auth = message.d.authentication;
        if (!auth) {
          clearTimeout(timeout);
          socket.close();
          reject(new Error('OBS WebSocket authentication is disabled; enable its password.'));
          return;
        }
        socket.send(JSON.stringify({op: 1, d: {
          rpcVersion: 1, authentication: authResponse(password, auth.salt, auth.challenge),
        }}));
      } else if (message.op === 2) {
        clearTimeout(timeout);
        resolve({
          close: () => socket.close(),
          request(type, data = {}) {
            const requestId = randomUUID();
            return new Promise((res, rej) => {
              const timer = setTimeout(() => {
                pending.delete(requestId);
                rej(new Error(`OBS request ${type} timed out.`));
              }, CONTROL_TIMEOUT_MS);
              pending.set(requestId, {
                resolve: value => {clearTimeout(timer); res(value);},
                reject: error => {clearTimeout(timer); rej(error);},
              });
              socket.send(JSON.stringify({op: 6, d: {
                requestType: type, requestId, requestData: data,
              }}));
            });
          },
        });
      } else if (message.op === 7) {
        const item = pending.get(message.d.requestId);
        if (!item) return;
        pending.delete(message.d.requestId);
        if (message.d.requestStatus.result) item.resolve(message.d.responseData ?? {});
        else item.reject(new Error(`OBS ${message.d.requestType}: ${message.d.requestStatus.comment}`));
      }
    });
  });
}

function obsConfigPath() {
  if (process.env.OBS_CONFIG_PATH) return process.env.OBS_CONFIG_PATH;
  const appData = execFileSync('powershell.exe', ['-NoProfile', '-Command',
    '[Environment]::GetFolderPath("ApplicationData")'], {encoding: 'utf8'}).trim();
  const linuxPath = execFileSync('wslpath', ['-u', appData], {encoding: 'utf8'}).trim();
  return path.join(linuxPath, 'obs-studio/plugin_config/obs-websocket/config.json');
}

async function connectLocalObs() {
  const config = JSON.parse(await readFile(obsConfigPath(), 'utf8'));
  if (!config.server_enabled || !config.auth_required || !config.server_password) {
    throw new Error('Enable OBS WebSocket with password authentication first.');
  }
  const password = process.env.OBS_WEBSOCKET_PASSWORD ?? config.server_password;
  const gateway = /default via (\d+\.\d+\.\d+\.\d+)/.exec(
    execFileSync('ip', ['route', 'show', 'default'], {encoding: 'utf8'}),
  )?.[1];
  const hosts = process.env.OBS_HOST ? [process.env.OBS_HOST] :
    ['127.0.0.1', ...(gateway ? [gateway] : [])];
  let lastError;
  for (const host of hosts) {
    try { return await connectObs({host, port: config.server_port, password}); }
    catch (error) { lastError = error; }
  }
  throw lastError;
}

function detectMinecraftWindow() {
  const script = String.raw`
Add-Type -TypeDefinition 'using System; using System.Text; using System.Runtime.InteropServices;
public class WinClass { [DllImport("user32.dll", CharSet=CharSet.Unicode)]
public static extern int GetClassName(IntPtr hWnd, StringBuilder text, int max);
[DllImport("user32.dll")] public static extern bool IsIconic(IntPtr hWnd);
[DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int command); }'
Get-Process javaw -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowTitle -match 'Minecraft' } |
ForEach-Object { $name = New-Object System.Text.StringBuilder 256;
[void]([WinClass]::IsIconic($_.MainWindowHandle) -and [WinClass]::ShowWindow($_.MainWindowHandle, 9));
[void][WinClass]::GetClassName($_.MainWindowHandle, $name, 256);
"$($_.MainWindowTitle):$($name.ToString()):$($_.ProcessName).exe" }`;
  const result = execFileSync('powershell.exe', ['-NoProfile', '-Command', script],
    {encoding: 'utf8'}).trim().split(/\r?\n/).filter(Boolean);
  if (result.length !== 1) throw new Error('Open one Minecraft Java window through TLauncher, then run setup.');
  return result[0];
}

async function main() {
  const [command, ...arguments_] = process.argv.slice(2);
  const flagIndex = arguments_.indexOf('--run-id');
  const runId = flagIndex >= 0 ? arguments_[flagIndex + 1] : undefined;
  if (!['setup', 'start', 'stop'].includes(command)) {
    throw new Error('Usage: node scripts/record_minecraft.mjs setup | start --run-id ID | stop --run-id ID');
  }
  if (command !== 'setup') validateRunId(runId);
  if (command === 'start') detectMinecraftWindow();
  const obs = await connectLocalObs();
  try {
    let result;
    if (command === 'setup') result = await setupCapture({obs, window: detectMinecraftWindow()});
    else if (command === 'start') result = await startAttempt({runId, obs});
    else result = await stopAttempt({runId, obs});
    process.stdout.write(`${JSON.stringify(result, null, 2)}\n`);
  } finally { obs.close(); }
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  main().catch(error => {
    process.stderr.write(`${error.message}\n`);
    process.exitCode = 1;
  });
}
