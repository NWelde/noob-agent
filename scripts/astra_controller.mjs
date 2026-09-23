/** Bounded, durable Astra build orchestration. Importing this file launches no agents. */
import {spawn} from 'node:child_process';
import {createHash, randomUUID} from 'node:crypto';
import {constants, createWriteStream, statSync} from 'node:fs';
import {access, lstat, mkdir, mkdtemp, open, readFile, readlink, realpath, rename, rm, symlink, writeFile} from 'node:fs/promises';
import path from 'node:path';
import {tmpdir, homedir} from 'node:os';
import {fileURLToPath} from 'node:url';

const REPO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const DEFAULT_STATE = path.join(REPO, '.noob-agent/astra-controller/state.json');
const LIMITS = {'0': 200, '1': 120, '2': 250, '3': 320, '4': 390,
  '5': 250, '6': 140, review: 130, reserve: 200};
// One completed invocation is a checkpoint, not a context-occupancy measurement.
const MAX_PROMPT_BYTES = 64 * 1024;
const MAX_AGENT_RUNTIME_MS = 5 * 60 * 1000;
const MAX_AGENT_OUTPUT_BYTES = 8 * 1024 * 1024;
const MAX_RESPONSE_BYTES = 1024 * 1024;
const SAFETY_MARGIN = 25;
const COORDINATOR_ALLOWANCE = 25;

const shape = properties => ({
  type: 'object', additionalProperties: false, properties,
  required: Object.keys(properties),
});
const string = {type: 'string'};
const strings = {type: 'array', items: string};
const number = {type: 'number'};
const schemas = {
  coordinator: shape({
    action: {type: 'string', enum: ['worker', 'review', 'accept', 'transfer', 'pause']},
    milestone: {type: 'integer'}, allowanceCredits: number, instructions: string,
    ownedFiles: strings, reason: string, transferFrom: string, transferTo: string,
    transferCredits: number,
  }),
  worker: shape({
    status: {type: 'string', enum: ['complete', 'handoff', 'blocked']},
    summary: string, changedFiles: strings, tests: strings, nextAction: string,
  }),
  reviewer: shape({
    verdict: {type: 'string', enum: ['pass', 'fail']},
    findings: strings, evidence: strings,
  }),
};

function clone(value) { return structuredClone(value); }
function positive(value) { return Number.isFinite(value) && value > 0; }
function spent(state) {
  return Object.values(state.pools).reduce((sum, pool) => sum + pool.spent, 0);
}
function effectiveSpent(state) {
  const estimated = spent(state);
  if (state.reconciledTotal == null) return estimated;
  return Math.max(0,
    state.reconciledTotal + estimated - (state.reconciledAtEstimated ?? estimated));
}
function available(state, pool) { return state.pools[String(pool)].limit - state.pools[String(pool)].spent; }
function validFile(file) {
  return typeof file === 'string' && !!file && !path.isAbsolute(file)
    && !file.includes('\\') && !file.split('/').some(part => part === '.' || part === '..')
    && !file.startsWith('.noob-agent/');
}
function checkAllowance(state, pool, credits, max) {
  if (!positive(credits) || credits > max) {
    throw new Error('Agent allowance must be at most ' + max + ' credits.');
  }
  if (credits > available(state, pool)) throw new Error('Insufficient milestone credits.');
  if (effectiveSpent(state) + credits + SAFETY_MARGIN > state.totalCredits) {
    throw new Error('Run would exceed the global budget and safety margin.');
  }
}
function log(state, event) {
  state.history.push({time: new Date().toISOString(), ...event});
  return state;
}

export function estimateCredits(usage) {
  const input = usage?.input_tokens;
  const cached = usage?.cached_input_tokens;
  const output = usage?.output_tokens;
  if (![input, cached, output].every(value => Number.isInteger(value) && value >= 0)
      || cached > input) return null;
  return ((input - cached) * 250 + cached * 25 + output * 1250) / 1_000_000;
}

export function createInitialState() {
  return {
    version: 1, model: 'gpt-6-astra', totalCredits: 2000,
    reconciledTotal: null, reconciledAtEstimated: null,
    pools: Object.fromEntries(Object.entries(LIMITS).map(([name, limit]) =>
      [name, {limit, spent: 0}])),
    milestone: 0, phase: 'coordinator', assignment: null, pending: null, workUnit: null,
    coordinatorThreadId: null, workerThreadId: null, workerVersion: 0,
    lastWorker: null, lastReview: null, handoff: null, pauseReason: null, history: [],
  };
}

export function reconcileBilledCredits(original, billedTotal) {
  if (!Number.isFinite(billedTotal) || billedTotal < 0) {
    throw new Error('Provide nonnegative billed credits.');
  }
  const state = clone(original);
  if (state.pending) throw new Error('Cannot reconcile while an agent run is pending.');
  const priorLedgerSpent = spent(state);
  if (priorLedgerSpent === 0 && billedTotal > 0) {
    throw new Error('Cannot attribute billed credits without controller usage receipts.');
  }
  const ratio = priorLedgerSpent === 0 ? 0 : billedTotal / priorLedgerSpent;
  for (const pool of Object.values(state.pools)) pool.spent *= ratio;
  if (state.workUnit) state.workUnit.spent *= ratio;
  state.reconciledTotal = billedTotal;
  state.reconciledAtEstimated = spent(state);
  return log(state, {event: 'reconcile', billedTotal, priorLedgerSpent,
    attribution: 'proportional_token_estimate'});
}

export function attributeExistingReconciliation(original) {
  const state = clone(original);
  if (state.pending || state.reconciledTotal == null
      || !positive(state.reconciledAtEstimated)) {
    throw new Error('No completed reconciliation to attribute.');
  }
  const index = state.history.findLastIndex(entry => entry.event === 'reconcile');
  if (index < 0 || state.history.slice(index + 1).some(entry =>
    entry.event === 'attribute_reconciliation')) {
    throw new Error('Reconciliation is already attributed or has no receipt.');
  }
  const laterUsage = state.history.slice(index + 1).filter(entry => entry.event === 'usage');
  if (laterUsage.some(entry => entry.role === 'worker')) {
    throw new Error('Cannot attribute an older checkpoint across later worker usage.');
  }
  const laterByPool = Object.fromEntries(Object.keys(state.pools).map(name =>
    [name, laterUsage.filter(entry => entry.pool === name)
      .reduce((sum, entry) => sum + entry.credits, 0)]));
  const base = Object.entries(state.pools).reduce((sum, [name, pool]) =>
    sum + pool.spent - laterByPool[name], 0);
  if (Math.abs(base - state.reconciledAtEstimated) > 0.000001) {
    throw new Error('Reconciliation baseline does not match usage receipts.');
  }
  const ratio = state.reconciledTotal / base;
  for (const [name, pool] of Object.entries(state.pools)) {
    pool.spent = (pool.spent - laterByPool[name]) * ratio + laterByPool[name];
  }
  if (state.workUnit) state.workUnit.spent *= ratio;
  state.reconciledAtEstimated = state.reconciledTotal;
  return log(state, {event: 'attribute_reconciliation', priorEstimate: base,
    billedTotal: state.reconciledTotal,
    afterEstimate: Object.values(laterByPool).reduce((sum, value) => sum + value, 0),
    method: 'proportional_token_estimate'});
}

export function resumeAfterReconciliation(original) {
  const state = clone(original);
  const lastDecision = [...state.history].reverse().find(entry => entry.event === 'decision');
  if (state.phase !== 'paused' || state.pending || state.reconciledTotal == null
      || lastDecision?.action !== 'pause' || state.lastWorker?.status !== 'handoff'
      || !state.workUnit
      || !/budget|credit|work unit|allowance/i.test(state.pauseReason ?? '')) {
    throw new Error('No reconciled budget pause to resume.');
  }
  checkAllowance(state, state.milestone, COORDINATOR_ALLOWANCE, COORDINATOR_ALLOWANCE);
  state.phase = 'coordinator';
  state.pauseReason = null;
  return log(state, {event: 'resume_reconciled_budget', milestone: state.milestone,
    billedTotal: state.reconciledTotal});
}

export function applyCoordinatorDecision(original, decision, evidence = {}) {
  const state = clone(original);
  if (state.phase !== 'coordinator' || state.pending) throw new Error('Coordinator is not idle.');
  if (decision.milestone !== state.milestone) throw new Error('Wrong milestone.');
  if (!decision.reason?.trim()) throw new Error('Decision requires a reason.');
  switch (decision.action) {
    case 'worker':
      checkAllowance(state, state.milestone, decision.allowanceCredits,
        state.pools[String(state.milestone)].limit);
      if (!decision.ownedFiles?.length || !decision.ownedFiles.every(validFile)) {
        throw new Error('Worker needs safe owned file paths.');
      }
      if (!decision.instructions?.trim()) throw new Error('Worker needs instructions.');
      state.assignment = {role: 'worker', allowanceCredits: decision.allowanceCredits,
        ownedFiles: decision.ownedFiles, instructions: decision.instructions};
      if (!state.workUnit) state.workUnit = {
        milestone: state.milestone, cap: state.pools[String(state.milestone)].limit, spent: 0,
      };
      state.phase = 'worker';
      break;
    case 'review':
      if (state.lastWorker?.status !== 'complete') throw new Error('Review needs completed work.');
      checkAllowance(state, 'review', decision.allowanceCredits, 25);
      // A final documentation worker does not narrow milestone-zero review to prose.
      const allChanged = [...new Set([...(state.workUnit?.changedFiles ?? []),
        ...state.lastWorker.changedFiles])];
      const reviewFiles = state.milestone === 0
        ? [...new Set([...allChanged, 'hackathon_plan.md',
          'scripts/astra_controller.mjs', 'tests/astra_controller.test.mjs'])]
        : allChanged;
      state.assignment = {role: 'reviewer', allowanceCredits: decision.allowanceCredits,
        ownedFiles: reviewFiles, workVersion: state.workerVersion};
      state.phase = 'review';
      break;
    case 'accept':
      if (state.lastWorker?.status !== 'complete' || !state.lastWorker.tests.length
          || state.workUnit?.scopeUnknown
          || state.lastReview?.verdict !== 'pass'
          || state.lastReview.workVersion !== state.workerVersion
          || state.lastReview.gatePassed !== true) {
        throw new Error('Acceptance needs test evidence, a passing review, and the controller test gate.');
      }
      if (!state.lastReview.contentDigest
          || state.lastReview.contentDigest !== evidence.contentDigest) {
        throw new Error('Acceptance needs fresh review and test evidence for the current content.');
      }
      log(state, {event: 'accepted', milestone: state.milestone,
        contentDigest: evidence.contentDigest, evidencePath: state.lastReview.evidencePath});
      state.milestone += 1;
      state.phase = state.milestone > 6 ? 'complete' : 'coordinator';
      state.assignment = null;
      state.workUnit = null;
      state.workerThreadId = null;
      state.lastWorker = null;
      state.lastReview = null;
      state.handoff = null;
      break;
    case 'transfer': {
      const {transferFrom: from, transferTo: to, transferCredits: credits} = decision;
      if (from === to || !state.pools[from] || to !== String(state.milestone)
          || !positive(credits) || credits > available(state, from)
          || (from !== 'reserve'
            && (!Number.isInteger(Number(from)) || Number(from) <= state.milestone))) {
        throw new Error('Invalid credit transfer.');
      }
      state.pools[from].limit -= credits;
      state.pools[to].limit += credits;
      if (state.workUnit?.milestone === state.milestone) {
        state.workUnit.cap = state.pools[to].limit;
      }
      log(state, {event: 'transfer', from, to, credits, reason: decision.reason});
      break;
    }
    case 'pause':
      state.phase = 'paused';
      state.pauseReason = decision.reason;
      break;
    default: throw new Error('Unknown coordinator action.');
  }
  return log(state, {event: 'decision', action: decision.action, reason: decision.reason});
}

export function transferForCoordinator(original, from, credits, reason) {
  const state = applyCoordinatorDecision(original, {
    action: 'transfer', milestone: original.milestone, reason,
    transferFrom: String(from), transferTo: String(original.milestone),
    transferCredits: credits,
  });
  return log(state, {event: 'operator_transfer', from: String(from),
    to: String(original.milestone), credits, reason});
}

export function invalidateLimitedReview(original, reason) {
  const state = clone(original);
  if (state.phase !== 'coordinator' || state.pending) {
    throw new Error('Cannot invalidate review while a call is pending or coordinator is not idle.');
  }
  if (!state.lastReview || !reason?.trim()) {
    throw new Error('A recorded review and reason are required.');
  }
  state.lastReview = null;
  return log(state, {event: 'invalidate_review', reason});
}

export function resumePausedCoordinatorDecision(original) {
  const state = clone(original);
  if (state.phase !== 'paused' || state.pending
      || state.pauseReason !== 'Usage exceeded an agent allowance or budget.') {
    throw new Error('No saved coordinator allowance pause to resume.');
  }
  const result = [...state.history].reverse().find(entry => entry.event === 'result');
  if (result?.role !== 'coordinator' || result.milestone !== state.milestone
      || !result.result) {
    throw new Error('No saved coordinator decision for the current milestone.');
  }
  if (available(state, '0') < 0
      || effectiveSpent(state) > state.totalCredits - SAFETY_MARGIN) {
    throw new Error('Milestone or global budget is still exceeded.');
  }
  state.phase = 'coordinator';
  state.pauseReason = null;
  const resumed = applyCoordinatorDecision(state, result.result);
  return log(resumed, {event: 'resume_saved_decision', milestone: state.milestone});
}

export function resumePausedWorkerOverrun(original) {
  const state = clone(original);
  if (state.phase !== 'paused' || state.pending
      || state.pauseReason !== 'Usage exceeded an agent allowance or budget.'
      || !state.lastWorker || !state.workUnit) {
    throw new Error('No completed worker overrun to resume.');
  }
  const result = [...state.history].reverse().find(entry => entry.event === 'result');
  const usage = [...state.history].reverse().find(entry => entry.event === 'usage');
  if (result?.role !== 'worker' || result.milestone !== state.milestone
      || usage?.role !== 'worker') {
    throw new Error('No worker overrun receipt for the current milestone.');
  }
  if (state.reconciledTotal == null) {
    throw new Error('Reconcile actual billed credits before resuming the overrun.');
  }
  if (available(state, state.milestone) < 0
      || effectiveSpent(state) > state.totalCredits - SAFETY_MARGIN) {
    throw new Error('Milestone or global budget is still exceeded.');
  }
  state.phase = 'coordinator';
  state.pauseReason = null;
  state.workUnit.cap = state.pools[String(state.milestone)].limit;
  return log(state, {event: 'resume_worker_overrun', milestone: state.milestone,
    sectionSpent: state.workUnit.spent, sectionCap: state.workUnit.cap,
    reconciledTotal: state.reconciledTotal});
}

export function applyAgentResult(original, role, result, meta = {}) {
  const state = clone(original);
  const expected = state.phase === 'review' ? 'reviewer' : state.phase;
  if (role !== expected || state.pending) throw new Error('Unexpected agent result.');
  const allowance = role === 'coordinator' ? COORDINATOR_ALLOWANCE
    : state.assignment?.allowanceCredits;
  if (!positive(allowance)) throw new Error('Missing agent allowance.');
  const credits = meta.billedCredits ?? estimateCredits(meta.usage);
  if (credits === null || !Number.isFinite(credits) || credits < 0) {
    state.phase = 'paused';
    state.pauseReason = 'Agent usage is missing; reconcile before dispatching more work.';
  } else {
    const pool = role === 'reviewer' ? 'review' : String(state.milestone);
    state.pools[pool].spent += credits;
    log(state, {event: 'usage', role, pool, credits,
      basis: meta.billedCredits == null ? 'token_estimate' : 'billed',
      usage: meta.usage ?? null, tracePath: meta.tracePath ?? null});
    if (available(state, pool) < 0
        || effectiveSpent(state) > state.totalCredits - SAFETY_MARGIN) {
      state.phase = 'paused';
      state.pauseReason = 'Usage exceeded a milestone or global budget.';
    }
  }
  if (role === 'coordinator') {
    state.coordinatorThreadId = null;
    if (state.phase !== 'paused') {
      try { return applyCoordinatorDecision(state, result, meta); }
      catch (error) {
        state.phase = 'paused';
        state.pauseReason = 'Invalid coordinator decision: ' + error.message;
      }
    }
  } else if (role === 'worker') {
    if (!['complete', 'handoff', 'blocked'].includes(result.status)
        || !Array.isArray(result.changedFiles) || !Array.isArray(result.tests)
        || typeof result.summary !== 'string' || typeof result.nextAction !== 'string') {
      throw new Error('Invalid worker result.');
    }
    const changed = meta.actualChangedFiles ?? result.changedFiles;
    state.workUnit.changedFiles = [...new Set([
      ...(state.workUnit.changedFiles ?? []), ...changed])].sort();
    state.workerVersion += 1;
    if (credits !== null) state.workUnit.spent += credits;
    state.lastReview = null;
    state.lastWorker = {...result, changedFiles: changed, workVersion: state.workerVersion};
    state.handoff = {summary: result.summary, changedFiles: changed,
      tests: result.tests, nextAction: result.nextAction, tracePath: meta.tracePath ?? null};
    state.workerThreadId = null;
    if (state.phase !== 'paused') state.phase = 'coordinator';
  } else {
    if (!['pass', 'fail'].includes(result.verdict)
        || !Array.isArray(result.findings) || !Array.isArray(result.evidence)) {
      throw new Error('Invalid reviewer result.');
    }
    state.lastReview = {...result, workVersion: state.assignment.workVersion,
      gatePassed: meta.gatePassed === true,
      contentDigest: meta.contentDigest ?? null, evidencePath: meta.evidencePath ?? null};
    if (state.phase !== 'paused') state.phase = 'coordinator';
  }
  state.assignment = null;
  return log(state, {event: 'result', role, milestone: state.milestone,
    threadId: meta.threadId ?? null, result});
}

async function exists(file) {
  try { await access(file); return true; } catch { return false; }
}
async function saveState(file, state) {
  await mkdir(path.dirname(file), {recursive: true});
  const temporary = file + '.' + randomUUID() + '.tmp';
  const handle = await open(temporary, 'wx', 0o600);
  try {
    await handle.writeFile(JSON.stringify(state, null, 2) + '\n');
    await handle.sync();
  } finally { await handle.close(); }
  await rename(temporary, file);
  const directory = await open(path.dirname(file), 'r');
  try { await directory.sync(); } finally { await directory.close(); }
}
async function loadOrCreate(file) {
  if (await exists(file)) return JSON.parse(await readFile(file, 'utf8'));
  const initial = createInitialState();
  await saveState(file, initial);
  return initial;
}
export async function processIdentity(pid = process.pid) {
  const bootId = (await readFile('/proc/sys/kernel/random/boot_id', 'utf8')).trim();
  const stat = await readFile('/proc/' + pid + '/stat', 'utf8');
  const fields = stat.slice(stat.lastIndexOf(')') + 2).split(' ');
  return {pid, bootId, startTicks: fields[19]};
}
async function probeProcess(owner) {
  if (!Number.isSafeInteger(owner?.pid) || owner.pid <= 0
      || !owner.bootId || !/^\d+$/.test(owner.startTicks)) return 'unknown';
  try {
    const current = await processIdentity(owner.pid);
    return current.bootId === owner.bootId && current.startTicks === owner.startTicks
      ? 'live' : 'dead';
  } catch (error) { return error.code === 'ENOENT' ? 'dead' : 'unknown'; }
}
async function withLock(file, action, {recover = false, processProbe = probeProcess} = {}) {
  const lockFile = file + '.lock';
  await mkdir(path.dirname(file), {recursive: true});
  // All acquisitions share this short guard, including stale-lock reclaim.
  // A crash inside this critical section leaves a guard requiring inspection.
  const guard = lockFile + '.guard';
  await mkdir(guard);
  let handle;
  try {
    try { handle = await open(lockFile, 'wx', 0o600); }
    catch (error) {
      if (error.code !== 'EEXIST' || !recover) throw error;
      let owner;
      try { owner = JSON.parse(await readFile(lockFile, 'utf8')).owner; } catch {}
      if (await processProbe(owner) !== 'dead') {
        throw new Error('Lock owner is live or unknown; refusing recovery.');
      }
      await rename(lockFile, lockFile + '.stale.' + randomUUID());
      handle = await open(lockFile, 'wx', 0o600);
    }
    await handle.writeFile(JSON.stringify({owner: await processIdentity()}));
    await handle.sync();
  } catch (error) {
    if (handle) await handle.close();
    throw error;
  } finally { await rm(guard, {recursive: true}); }
  try { return await action(); }
  finally { await handle.close(); await rm(lockFile); }
}
function summary(state) {
  return {
    milestone: state.milestone, phase: state.phase, pools: state.pools,
    estimatedSpent: spent(state), reconciledTotal: state.reconciledTotal,
    effectiveSpent: effectiveSpent(state),
    pending: state.pending, assignment: state.assignment,
    workUnit: state.workUnit, lastWorker: state.lastWorker, lastReview: state.lastReview,
    handoff: state.handoff, pauseReason: state.pauseReason,
    recentHistory: state.history.slice(-8),
  };
}
function promptFor(role, state, plan) {
  const common = [
    'Follow hackathon_plan.md. Work only on milestone ' + state.milestone + '.',
    'Do not spawn subagents, assign yourself credits, stash/reset Git, or overwrite unrelated work.',
    'The worktree has uncommitted user changes; preserve them.',
  ];
  if (role === 'coordinator') return [
    'You are the coordinator. Inspect files only; do not edit code, tests, or configuration.',
    'Choose one action: worker, review, accept, transfer, or pause.',
    'Return JSON matching the schema. Fill unused transfer fields with empty strings and zero.',
    'Worker call estimates are planning numbers, not hard caps. Milestone and global pools bind.',
    'A continuation uses the same milestone pool. Ignore legacy workUnit.cap values.',
    'Transfer unused future or reserve credits with a reason when the active pool needs more.',
    'Reviewers get at most 25 estimated credits. Give safe owned file paths.',
    'Do not accept without a passing fresh review and test evidence.',
    'Controller state: ' + JSON.stringify(summary(state)),
    'Plan:\n' + plan,
  ].join('\n\n');
  if (role === 'worker') return [
    ...common,
    'You are the only code writer. First inspect interfaces and write a failing behavioral test.',
    'Complete one bounded chunk, run focused checks, and return exact results.',
    'Your call cost estimate is ' + state.assignment.allowanceCredits
      + ' credits. It is not a hard cap; complete a bounded chunk and report usage.',
    'Keep CHANGELOG.md current for every code change.',
    'If unfinished or near half context, finish the current safe step and return handoff.',
    'Assigned files are task scope, not a filesystem limit: ' + state.assignment.ownedFiles.join(', '),
    'Task: ' + state.assignment.instructions,
    'Previous handoff: ' + JSON.stringify(state.handoff),
    'Return only JSON matching the required schema.',
    'Plan:\n' + plan,
  ].join('\n\n');
  return [
    'You are an independent reviewer. Inspect only; never edit files.',
    'Assume the implementation may be wrong. Inspect the current files and test evidence.',
    'A unit test or OBS clip does not prove live Minecraft behavior.',
    'Milestone: ' + state.milestone,
    'Files: ' + state.assignment.ownedFiles.join(', '),
    'Milestone audit: ' + JSON.stringify(state.workUnit),
    'Worker result: ' + JSON.stringify(state.lastWorker),
    'Return only JSON matching the review schema.',
    'Plan:\n' + plan,
  ].join('\n\n');
}

async function trackedSnapshot(repo) {
  const names = await new Promise((resolve, reject) => {
    const child = spawn('git', ['ls-files', '-z', '--cached', '--others', '--exclude-standard'],
      {cwd: repo, stdio: ['ignore', 'pipe', 'ignore']});
    const chunks = [];
    child.stdout.on('data', chunk => chunks.push(chunk));
    child.on('error', reject);
    child.on('close', code => code === 0 ? resolve(Buffer.concat(chunks).toString('utf8')) :
      reject(new Error('git ls-files failed')));
  });
  const snapshot = {};
  for (const name of [...new Set(names.split('\0').filter(Boolean))].sort()) {
    if (name.startsWith('.noob-agent/')) continue;
    try {
      const file = path.join(repo, name);
      const stat = await lstat(file);
      if (!stat.isFile() && !stat.isSymbolicLink()) {
        throw new Error('Cannot fingerprint non-file repository entry: ' + name);
      }
      const content = stat.isSymbolicLink() ? await readlink(file) : await readFile(file);
      snapshot[name] = createHash('sha256')
        .update(String(stat.mode) + '\0').update(content).digest('hex');
    } catch (error) {
      if (error.code !== 'ENOENT') throw error;
      snapshot[name] = null;
    }
  }
  return snapshot;
}
async function contentEvidence(repo, planPath) {
  const files = await trackedSnapshot(repo);
  const planDigest = createHash('sha256').update(await readFile(planPath)).digest('hex');
  const contentDigest = createHash('sha256')
    .update(JSON.stringify({files, planDigest})).digest('hex');
  return {files, planDigest, contentDigest};
}

// Only the controller supplies these receipts; agent claims never supply a digest.
async function reviewEvidence(repo, pending, gate) {
  const evidencePath = path.join(path.dirname(pending.tracePath), 'review-evidence.json');
  const gatePath = path.join(path.dirname(pending.tracePath), 'acceptance-gate.txt');
  let gatePassed = false;
  let reason = 'Review has no content baseline; run a fresh review.';
  if (pending.contentDigest && pending.planPath) {
    const before = await contentEvidence(repo, pending.planPath);
    reason = 'Content changed during review or before recovery; run a fresh review.';
    if (before.contentDigest === pending.contentDigest) {
      const passed = await gate(repo, gatePath, pending.milestone);
      const after = await contentEvidence(repo, pending.planPath);
      gatePassed = passed === true && after.contentDigest === pending.contentDigest;
      reason = gatePassed ? 'Review and tests match the content baseline.'
        : 'Test gate failed or content changed during the gate; run a fresh review.';
    }
  }
  const receipt = {gatePassed, contentDigest: pending.contentDigest ?? null,
    evidencePath, gatePath, baselinePath: pending.reviewBaselinePath ?? null, reason};
  await writeFile(evidencePath, JSON.stringify(receipt, null, 2) + '\n', {mode: 0o600});
  return receipt;
}
function changedFiles(before, after) {
  return [...new Set([...Object.keys(before), ...Object.keys(after)])]
    .filter(name => before[name] !== after[name]).sort();
}
async function interruptedChanges(pending) {
  if (!pending.baselinePath) return null;
  const before = JSON.parse(await readFile(pending.baselinePath, 'utf8'));
  return changedFiles(before, await trackedSnapshot(pending.repo ?? REPO));
}
function recordInterruptedChanges(state, changes) {
  if (changes === null) state.workUnit.scopeUnknown = true;
  else state.workUnit.changedFiles = [...new Set([
    ...(state.workUnit.changedFiles ?? []), ...changes])].sort();
}
function safeEnvironment() {
  const names = ['PATH', 'HOME', 'USER', 'SHELL', 'LANG', 'LC_ALL', 'TERM',
    'CODEX_HOME', 'CODEX_ACCESS_TOKEN', 'OPENAI_API_KEY', 'HTTPS_PROXY',
    'HTTP_PROXY', 'NO_PROXY'];
  return Object.fromEntries(names.filter(name => process.env[name] != null)
    .map(name => [name, process.env[name]]));
}
async function readResponse(file, partialPath = null) {
  // Inspect the opened inode, reject special files, and never follow a final
  // symlink. Nonblocking open prevents a substituted FIFO from hanging recovery.
  const handle = await open(file, constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK);
  try {
    const stat = await handle.stat();
    if (!stat.isFile()) throw new Error('Response checkpoint requires a regular file.');
    let oversized = stat.size > MAX_RESPONSE_BYTES;
    if (oversized && !partialPath) {
      throw new Error('Response checkpoint: response exceeds 1 MiB; inspect pending artifacts.');
    }
    // A fixed buffer and one-byte probe also bound reads if the file grows
    // after stat. Oversized runtime files retain only an inspectable prefix.
    const buffer = Buffer.alloc(MAX_RESPONSE_BYTES + 1);
    let length = 0;
    while (length < buffer.length) {
      const {bytesRead} = await handle.read(buffer, length, buffer.length - length, length);
      if (!bytesRead) break;
      length += bytesRead;
    }
    oversized ||= length > MAX_RESPONSE_BYTES;
    if (oversized) {
      if (partialPath) await writeFile(partialPath,
        buffer.subarray(0, Math.min(length, MAX_RESPONSE_BYTES)), {mode: 0o600, flag: 'wx'});
      throw new Error('Response checkpoint: response exceeds 1 MiB; inspect pending artifacts.');
    }
    return buffer.subarray(0, length);
  } finally {
    await handle.close();
  }
}

async function readArtifacts(pending) {
  const response = await readResponse(pending.responsePath);
  const receipt = await readUsage(pending);
  return {...receipt, result: JSON.parse(response)};
}
async function readUsage(pending) {
  const trace = await readFile(pending.tracePath, 'utf8');
  const events = trace.split(/\r?\n/).filter(Boolean).map(line => JSON.parse(line));
  const completions = events.filter(event => event.type === 'turn.completed');
  const completed = completions[0];
  if (!completed) throw new Error('No completed turn in trace; cannot safely recover.');
  if (completions.length !== 1 || events.at(-1) !== completed) {
    throw new Error('Usage evidence is ambiguous or incomplete; inspect the trace.');
  }
  if (estimateCredits(completed.usage) === null) throw new Error('Usage evidence is missing or invalid.');
  const started = events.find(event => event.type === 'thread.started');
  return {
    threadId: started?.thread_id ?? pending.resumeId ?? null,
    usage: completed.usage ?? null,
    tracePath: pending.tracePath,
  };
}


export async function codexRunner({role, prompt, state, pending, repo = REPO,
                                   sandboxExecutable = 'bwrap',
                                   maxModelTurns = null,
                                   maxRuntimeMs = MAX_AGENT_RUNTIME_MS,
                                   maxOutputBytes = MAX_AGENT_OUTPUT_BYTES}) {
  repo = path.resolve(repo);
  // exec's turn.completed covers the entire tool/model loop, not each model
  // request. Reject a requested bound until a pre-request enforcement exists.
  if (maxModelTurns !== null) {
    throw new Error('Model-turn limit unsupported by the CLI adapter; no agent was launched.');
  }
  if (!['worker', 'coordinator', 'reviewer'].includes(role)) throw new Error('Unknown role.');
  if (process.platform !== 'linux') throw new Error('Observer confinement requires Linux Bubblewrap.');
  if (await realpath(repo) !== repo) throw new Error('Controller needs a canonical repo path.');
  if (!Number.isInteger(maxRuntimeMs) || maxRuntimeMs <= 0) {
    throw new Error('Runtime checkpoint requires a positive millisecond limit.');
  }
  if (!Number.isSafeInteger(maxOutputBytes) || maxOutputBytes <= 0) {
    throw new Error('Output checkpoint requires a positive safe integer byte limit.');
  }
  if (Buffer.byteLength(prompt, 'utf8') > MAX_PROMPT_BYTES) {
    throw new Error('Context checkpoint: prompt exceeds 64 KiB; reduce the bounded chunk.');
  }
  const directory = path.dirname(pending.tracePath);
  await mkdir(directory, {recursive: true});
  const schemaFile = path.join(directory, 'schema.json');
  await writeFile(schemaFile, JSON.stringify(schemas[role]), {mode: 0o600});
  const runtime = await mkdtemp(path.join(tmpdir(), 'astra-runtime-'));
  try {
    const codexHome = path.join(runtime, 'codex-home');
    await mkdir(codexHome);
    // Authentication remains read-only; no user config, history or writable home is inherited.
    const auth = path.join(process.env.CODEX_HOME || path.join(homedir(), '.codex'), 'auth.json');
    if (await exists(auth)) await symlink(await realpath(auth), path.join(codexHome, 'auth.json'));
    const output = path.join(runtime, 'response.json');
    const args = ['exec', '-m', state.model, '--json', '--disable', 'multi_agent',
      '--ignore-user-config', '--ignore-rules', '--ephemeral',
      '-c', 'approval_policy=never', '-c', 'sandbox_mode="danger-full-access"',
      '-C', repo, '--output-schema', schemaFile, '-o', output, '-'];
    // Always fresh, even for legacy saved thread IDs. Worker calls run directly
    // with host permissions; observer calls retain the read-only mount namespace.
    const sandboxArgs = ['--die-with-parent', '--new-session', '--unshare-user',
      '--unshare-pid', '--unshare-ipc', '--unshare-uts', '--cap-drop', 'ALL',
      '--ro-bind', '/', '/', '--proc', '/proc', '--dev', '/dev',
      '--bind', runtime, runtime,
      '--chdir', repo, '--', 'codex', ...args];
    const trace = createWriteStream(pending.tracePath, {flags: 'wx', mode: 0o600});
    const errors = createWriteStream(path.join(directory, 'stderr.txt'),
      {flags: 'wx', mode: 0o600});
    let spawnError = null;
    if (pending.processPath) await saveState(pending.processPath, {status: 'spawning'});
    const worker = role === 'worker';
    const child = spawn(worker ? 'codex' : sandboxExecutable,
      worker ? args : sandboxArgs, {
      cwd: repo, env: {...safeEnvironment(), CODEX_HOME: codexHome, TMPDIR: runtime},
      stdio: ['pipe', 'pipe', 'pipe'],
      detached: worker,
    });
    const stopChild = signal => {
      if (!child.pid) return;
      try {
        if (worker) process.kill(-child.pid, signal);
        else child.kill(signal);
      } catch (error) {
        if (error.code !== 'ESRCH') throw error;
      }
    };
    const activeItems = new Set();
    let partialLine = '';
    let runtimeReached = false;
    let outputReached = false;
    let responseReached = false;
    let outputBytes = 0;
    const capture = (destination, chunk) => {
      if (outputReached) return null;
      const remaining = maxOutputBytes - outputBytes;
      const kept = chunk.subarray(0, remaining);
      outputBytes += kept.length;
      if (kept.length) destination.write(kept);
      if (chunk.length > remaining) {
        outputReached = true;
        // Hard resource ceiling: even an active tool cannot defer this stop.
        // Bubblewrap's PID namespace removes descendants with its init process.
        stopChild('SIGKILL');
        return null;
      }
      return kept;
    };
    let stopSent = false;
    const stopAtBoundary = () => {
      if (runtimeReached && !stopSent && activeItems.size === 0) {
        stopSent = true;
        stopChild('SIGTERM');
      }
    };
    child.stdout.on('data', chunk => {
      const kept = capture(trace, chunk);
      if (!kept) return;
      partialLine += kept.toString('utf8');
      const lines = partialLine.split('\n');
      partialLine = lines.pop();
      for (const line of lines) {
        try {
          const event = JSON.parse(line);
          const id = event.item?.id;
          if (id && event.type === 'item.started') activeItems.add(id);
          if (id && (event.type === 'item.completed' || event.type === 'item.failed')) {
            activeItems.delete(id);
          }
        } catch { /* Artifact parsing reports malformed JSONL after the process exits. */ }
      }
      stopAtBoundary();
    });
    const runtimeTimer = setTimeout(() => {
      runtimeReached = true;
      stopAtBoundary();
    }, maxRuntimeMs);
    // A hung tool cannot defer the limit forever. Its partial work remains for inspection.
    const hardTimer = setTimeout(() => {
      if (runtimeReached) stopChild('SIGKILL');
    }, maxRuntimeMs + 60_000);
    hardTimer.unref();
    // Inspect the CLI's -o file while it runs, not only after it exits.
    // Polling stops a runaway file promptly; it is not an atomic disk quota.
    const responseTimer = setInterval(() => {
      try {
        const info = statSync(output);
        if (!info.isFile() || info.size > MAX_RESPONSE_BYTES) {
          responseReached = true;
          stopChild('SIGKILL');
        }
      } catch (error) {
        if (error.code !== 'ENOENT') {
          responseReached = true;
          stopChild('SIGKILL');
        }
      }
    }, 50);
    child.on('error', error => { spawnError = error; });
    child.stdin.on('error', () => {});
    child.stderr.on('data', chunk => capture(errors, chunk));
    const closed = new Promise(resolve => child.on('close', resolve));
    let childOwner = null;
    if (pending.processPath && child.pid) {
      try { childOwner = await processIdentity(child.pid); } catch {}
      await saveState(pending.processPath, {status: 'running', owner: childOwner});
    }
    child.stdin.end(prompt);
    const code = await closed;
    clearInterval(responseTimer);
    if (pending.processPath) await saveState(pending.processPath, {status: 'exited', code, owner: childOwner});
    clearTimeout(runtimeTimer);
    clearTimeout(hardTimer);
    await Promise.all([
      new Promise(resolve => trace.end(resolve)),
      new Promise(resolve => errors.end(resolve)),
    ]);
    if (spawnError) throw spawnError;
    if (outputReached) throw new Error('Output checkpoint stopped Codex ' + role
      + '; inspect capped stdout/stderr and partial work; usage may be incomplete.');
    if (responseReached) {
      try { await readResponse(output, pending.responsePath + '.partial'); } catch {}
      throw new Error('Response checkpoint stopped Codex ' + role
        + '; inspect capped response and partial work; usage may be incomplete.');
    }
    if (runtimeReached) throw new Error('Runtime checkpoint stopped Codex ' + role
      + '; inspect partial trace and reconcile billed usage before recovery.');
    if (code !== 0) throw new Error('Confined Codex ' + role + ' exited ' + code
      + '; inspect ' + path.join(directory, 'stderr.txt'));
    // Only the controller writes durable response/usage artifacts.
    const response = await readResponse(output, pending.responsePath + '.partial');
    await writeFile(pending.responsePath, response, {mode: 0o600, flag: 'wx'});
    return await readArtifacts(pending);
  } finally {
    await rm(runtime, {recursive: true, force: true});
  }
}

async function runMilestoneGate(repo, outputPath, milestone) {
  if (milestone !== 0) {
    await writeFile(outputPath,
      'No controller-run acceptance check is configured for milestone '
      + milestone + '. Define and verify the live gate before advancing.\n',
      {mode: 0o600});
    return false;
  }
  const chunks = [];
  const code = await new Promise((resolve, reject) => {
    const child = spawn(process.execPath, ['--test', 'tests/astra_controller.test.mjs'],
      {cwd: repo, stdio: ['ignore', 'pipe', 'pipe']});
    child.stdout.on('data', chunk => chunks.push(chunk));
    child.stderr.on('data', chunk => chunks.push(chunk));
    child.on('error', reject);
    child.on('close', resolve);
  });
  await writeFile(outputPath, Buffer.concat(chunks), {mode: 0o600});
  return code === 0;
}

export async function runOneStep({statePath = DEFAULT_STATE, runner = codexRunner,
                                  repo = REPO, planPath = path.join(REPO, 'hackathon_plan.md'),
                                  gate = runMilestoneGate} = {}) {
  return withLock(statePath, async () => {
    let state = await loadOrCreate(statePath);
    if (state.pending) throw new Error('A pending agent run exists; recover or inspect it.');
    if (state.phase === 'paused' || state.phase === 'complete') return state;
    if (!['coordinator', 'worker', 'review'].includes(state.phase)) {
      throw new Error('Unknown phase: ' + state.phase);
    }
    const role = state.phase === 'review' ? 'reviewer' : state.phase;
    const allowance = role === 'coordinator' ? COORDINATOR_ALLOWANCE
      : state.assignment.allowanceCredits;
    checkAllowance(state, role === 'reviewer' ? 'review' : state.milestone,
    allowance, role === 'coordinator' ? COORDINATOR_ALLOWANCE
      : role === 'reviewer' ? 25 : state.pools[String(state.milestone)].limit);
    const plan = await readFile(planPath, 'utf8');
    const prompt = promptFor(role, state, plan);
    if (Buffer.byteLength(prompt, 'utf8') > MAX_PROMPT_BYTES) {
      throw new Error('Context checkpoint: prompt exceeds 64 KiB; reduce the bounded chunk.');
    }
    const id = randomUUID();
    const directory = path.join(path.dirname(statePath), 'runs', id);
    await mkdir(directory, {recursive: true});
    const pending = {
      id, role, milestone: state.milestone, allowanceCredits: allowance,
      tracePath: path.join(directory, 'events.jsonl'),
      responsePath: path.join(directory, 'response.json'),
      processPath: path.join(directory, 'process.json'),
      baselinePath: role === 'worker' ? path.join(directory, 'baseline.json') : null,
      repo,
      resumeId: null,
      statePath: path.resolve(statePath),
      contextCheckpoint: {policy: 'fresh-per-chunk', maxPromptBytes: MAX_PROMPT_BYTES},
      startedAt: new Date().toISOString(),
      planPath: path.resolve(planPath),
    };
    if (role === 'reviewer') {
      const evidence = await contentEvidence(repo, planPath);
      pending.contentDigest = evidence.contentDigest;
      pending.reviewBaselinePath = path.join(directory, 'review-baseline.json');
      await writeFile(pending.reviewBaselinePath, JSON.stringify(evidence, null, 2) + '\n',
        {mode: 0o600});
    }
    const before = role === 'worker' ? await trackedSnapshot(repo) : null;
    if (before) await writeFile(pending.baselinePath, JSON.stringify(before), {mode: 0o600});
    state.pending = pending;
    await saveState(statePath, state);
    await saveState(pending.processPath, {status: 'launching', owner: await processIdentity()});
    let response;
    try {
      response = await runner({role, prompt, state, pending, repo});
    } finally {
      // Injected in-process runners have no external worker. codexRunner owns
      // its process receipt, including when wrapped by a test adapter.
      const receipt = JSON.parse(await readFile(pending.processPath, 'utf8'));
      if (runner !== codexRunner && receipt.status === 'launching') {
        await saveState(pending.processPath, {status: 'exited', kind: 'in-process-runner'});
      }
    }
    const after = before ? await trackedSnapshot(repo) : null;
    const meta = {...response, contentDigest: null, gatePassed: false, evidencePath: null};
    if (before) meta.actualChangedFiles = changedFiles(before, after);
    if (role === 'reviewer' && response.result.verdict === 'pass') {
      Object.assign(meta, await reviewEvidence(repo, pending, gate));
    }
    if (role === 'coordinator' && response.result.action === 'accept') {
      meta.contentDigest = (await contentEvidence(repo, planPath)).contentDigest;
    }
    state.pending = null;
    state = applyAgentResult(state, role, response.result, meta);
    await saveState(statePath, state);
    return state;
  });
}

export async function recoverPending(statePath = DEFAULT_STATE, repo = REPO,
                                     {gate = runMilestoneGate, processProbe = probeProcess} = {}) {
  return withLock(statePath, async () => {
    let state = await loadOrCreate(statePath);
    if (!state.pending) return state;
    const pending = state.pending;
    // Check artifacts first so oversized legacy responses retain their existing
    // bounded-read failure. No state is changed until process exit is proven.
    let response;
    try { response = await readArtifacts(pending); }
    catch (error) {
      if (error.code !== 'ENOENT' || error.path !== pending.responsePath) throw error;
      if (await exists(pending.responsePath + '.partial')) {
        throw new Error('Response checkpoint left a partial artifact; inspect pending work.');
      }
    }
    let processReceipt;
    try { processReceipt = JSON.parse(await readFile(pending.processPath, 'utf8')); } catch {}
    if (processReceipt?.status !== 'exited') {
      throw new Error('Worker process may still be live; exit evidence is missing.');
    }
    if (pending.interruptedUsage) return state;
    if (!response) {
      const receipt = await readUsage(pending);
      const credits = estimateCredits(receipt.usage);
      const pool = pending.role === 'reviewer' ? 'review' : String(pending.milestone);
      state.pools[pool].spent += credits;
      if (pending.role === 'worker') state.workUnit.spent += credits;
      pending.interruptedUsage = {credits, pool};
      state.phase = 'paused';
      state.pauseReason = 'Interrupted usage recorded; missing response requires inspection.';
      log(state, {event: 'usage', role: pending.role, pool, credits,
        basis: 'token_estimate', usage: receipt.usage, tracePath: pending.tracePath,
        runId: pending.id, interrupted: true});
      await saveState(statePath, state);
      return state;
    }
    if (pending.role === 'worker') {
      const before = JSON.parse(await readFile(pending.baselinePath, 'utf8'));
      response.actualChangedFiles = changedFiles(before, await trackedSnapshot(repo));
    }
    if (pending.role === 'reviewer' && response.result.verdict === 'pass') {
      Object.assign(response, await reviewEvidence(repo, pending, gate));
    }
    if (pending.role === 'coordinator' && response.result.action === 'accept' && pending.planPath) {
      response.contentDigest = (await contentEvidence(repo, pending.planPath)).contentDigest;
    }
    state.pending = null;
    state = applyAgentResult(state, pending.role, response.result, response);
    await saveState(statePath, state);
    return state;
  }, {recover: true, processProbe});
}

export async function continueInterruptedUsage(statePath, runId) {
  return withLock(statePath, async () => {
    const state = await loadOrCreate(statePath);
    if (typeof runId !== 'string' || !runId) throw new Error('Require an exact run ID.');
    // A retry of an already committed transition must not touch newer work.
    if (state.history.some(entry => entry.event === 'continue_interrupted_usage'
        && entry.runId === runId)) return state;
    const pending = state.pending;
    if (!pending || pending.id !== runId || pending.role !== 'worker'
        || pending.milestone !== state.milestone || state.phase !== 'paused'
        || !pending.interruptedUsage || !state.workUnit
        || state.workUnit.milestone !== pending.milestone) {
      throw new Error('Recover usage for the exact interrupted worker before continuation.');
    }
    let processReceipt;
    try { processReceipt = JSON.parse(await readFile(pending.processPath, 'utf8')); } catch {}
    if (processReceipt?.status !== 'exited') {
      throw new Error('Confirmed process exit evidence is required.');
    }
    if (await exists(pending.responsePath) || await exists(pending.responsePath + '.partial')) {
      throw new Error('Response artifacts require inspection; usage-only continuation refused.');
    }
    const receipt = await readUsage(pending);
    const credits = estimateCredits(receipt.usage);
    const pool = String(pending.milestone);
    const charges = state.history.filter(entry => entry.event === 'usage' && entry.runId === runId);
    const charge = charges[0];
    if (!Number.isFinite(credits) || charges.length !== 1 || charge.role !== 'worker'
        || charge.pool !== pool || charge.interrupted !== true
        || charge.basis !== 'token_estimate' || charge.credits !== credits
        || charge.tracePath !== pending.tracePath
        || ['input_tokens', 'cached_input_tokens', 'output_tokens'].some(key =>
          charge.usage?.[key] !== receipt.usage[key])
        || pending.interruptedUsage.credits !== credits || pending.interruptedUsage.pool !== pool) {
      throw new Error('Durable usage evidence does not match the recorded interruption.');
    }
    // Preserve the original receipts and section spending. No result was returned,
    // so no previous completion or review may authorize acceptance.
    const changes = await interruptedChanges(pending);
    recordInterruptedChanges(state, changes);
    state.pending = null;
    state.assignment = null;
    state.phase = 'coordinator';
    state.pauseReason = null;
    state.workerVersion += 1;
    state.workerThreadId = null;
    state.lastWorker = null;
    state.lastReview = null;
    state.handoff = {summary: 'Worker exited with usage but no result; inspect preserved work.',
      changedFiles: changes ?? [], scopeUnknown: changes === null, tests: [],
      nextAction: 'Inspect artifacts and scope a fresh worker in the same milestone.',
      tracePath: pending.tracePath};
    if (available(state, pool) < 0 || effectiveSpent(state) + SAFETY_MARGIN > state.totalCredits) {
      state.phase = 'paused';
      state.pauseReason = 'Interrupted usage exceeds a milestone or global budget.';
    }
    log(state, {event: 'continue_interrupted_usage', runId, role: pending.role,
      milestone: pending.milestone, artifacts: pending,
      billingBasis: 'token_estimate', exitBasis: 'process_receipt'});
    await saveState(statePath, state);
    return state;
  }, {recover: true});
}

async function inspectRetirableWorker(state, runId, exitObserved) {
    const pending = state.pending;
    if (!pending) throw new Error('No pending interrupted run to retire.');
    if (pending.id !== runId) throw new Error('Run ID does not match the pending call.');
    if (pending.role !== 'worker' || state.phase !== 'worker') {
      throw new Error('Manual retirement only supports interrupted workers.');
    }
    if (!exitObserved) throw new Error('Operator must record an observed process exit.');
    if (pending.processPath) {
      let receipt;
      try { receipt = JSON.parse(await readFile(pending.processPath, 'utf8')); } catch {}
      if (receipt?.status !== 'exited') throw new Error('Worker exit receipt is missing.');
    }
    if (await exists(pending.responsePath)) {
      throw new Error('A response exists; use normal recovery instead.');
    }
    const trace = await readFile(pending.tracePath, 'utf8');
    if (trace.split(/\r?\n/).some(line => {
      try { return JSON.parse(line).type === 'turn.completed'; } catch { return false; }
    })) throw new Error('Usage evidence exists; use normal recovery instead.');
    return pending;
}

function retireWorkerState(state, pending, billingBasis, changes) {
    recordInterruptedChanges(state, changes);
    state.pending = null;
    state.assignment = null;
    state.phase = 'coordinator';
    state.pauseReason = null;
    state.workerVersion += 1;
    state.workerThreadId = null;
    state.lastWorker = null;
    state.lastReview = null;
    state.handoff = {summary: 'Worker interrupted before returning a result; inspect preserved artifacts.',
      changedFiles: changes ?? [], scopeUnknown: changes === null, tests: [],
      nextAction: 'Inspect partial work and dispatch a fresh worker.',
      tracePath: pending.tracePath};
    log(state, {event: 'retire_interrupted', runId: pending.id, role: 'worker',
      milestone: pending.milestone, tracePath: pending.tracePath,
      responsePath: pending.responsePath, billingBasis, exitBasis: 'operator_observed'});
    return state;
}

export async function retireInterruptedAfterBilling(statePath, runId, billedTotal,
                                                    {exitObserved = false} = {}) {
  return withLock(statePath, async () => {
    const state = await loadOrCreate(statePath);
    const pending = await inspectRetirableWorker(state, runId, exitObserved);
    if (!Number.isFinite(billedTotal) || billedTotal < 0
        || (state.reconciledTotal != null && billedTotal < state.reconciledTotal)) {
      throw new Error('Reported billing cannot be below the prior billed total.');
    }
    if (!positive(state.pools[String(pending.milestone)]?.spent)) {
      throw new Error('Cannot attribute billing to a milestone without prior receipts.');
    }
    retireWorkerState(state, pending, 'operator_reported_total', await interruptedChanges(pending));
    const reconciled = reconcileBilledCredits(state, billedTotal);
    if (reconciled.pools[String(pending.milestone)].spent
        > reconciled.pools[String(pending.milestone)].limit
        || effectiveSpent(reconciled) + SAFETY_MARGIN > reconciled.totalCredits) {
      reconciled.phase = 'paused';
      reconciled.pauseReason = 'Reconciled billing exceeds a milestone or global budget.';
    }
    await saveState(statePath, reconciled);
    return reconciled;
  }, {recover: true});
}

export async function retireInterruptedWithReserve(statePath, runId, reservedCredits,
                                                  {exitObserved = false, sourcePool = 'reserve'} = {}) {
  return withLock(statePath, async () => {
    const state = await loadOrCreate(statePath);
    const pending = await inspectRetirableWorker(state, runId, exitObserved);
    const from = String(sourcePool);
    if (from !== 'reserve' && (!/^[1-6]$/.test(from)
        || Number(from) <= pending.milestone)) {
      throw new Error('Provisional credits must come from reserve or a future milestone.');
    }
    if (!positive(reservedCredits) || reservedCredits > available(state, from)) {
      throw new Error('Provisional credits must fit the source pool.');
    }
    if (effectiveSpent(state) + reservedCredits + SAFETY_MARGIN > state.totalCredits) {
      throw new Error('Provisional credits exceed the global budget and safety margin.');
    }
    const pool = String(pending.milestone);
    state.pools[from].limit -= reservedCredits;
    state.pools[pool].limit += reservedCredits;
    log(state, {event: 'transfer', from, to: pool, credits: reservedCredits,
      reason: 'Conservative provision for an interrupted worker without a usage receipt.'});
    state.pools[pool].spent += reservedCredits;
    state.workUnit.spent += reservedCredits;
    state.workUnit.cap = state.pools[pool].limit;
    log(state, {event: 'usage', role: 'worker', pool, credits: reservedCredits,
      basis: 'provisional_missing_receipt', runId, tracePath: pending.tracePath});
    retireWorkerState(state, pending, 'provisional_' + from, await interruptedChanges(pending));
    if (state.pools[pool].spent > state.pools[pool].limit) {
      state.phase = 'paused';
      state.pauseReason = 'Provisional usage exceeds the milestone budget.';
    }
    await saveState(statePath, state);
    return state;
  }, {recover: true});
}

export function parseRetirementArgs(args) {
  const [command, runId, amount, exitFlag, sourceFlag] = args;
  if (!['retire-interrupted', 'retire-provisional'].includes(command)
      || !runId || exitFlag !== '--observed-exit'
      || (args.length !== 4 && args.length !== 5)
      || (args.length === 5 && (command !== 'retire-provisional'
        || !/^--from=(?:reserve|[1-6])$/.test(sourceFlag)))) {
    throw new Error('Use retire-interrupted|retire-provisional RUN_ID CREDITS --observed-exit.');
  }
  const credits = Number(amount);
  if (!Number.isFinite(credits) || credits < 0) throw new Error('Credits must be nonnegative.');
  return sourceFlag ? {runId, credits, sourcePool: sourceFlag.slice('--from='.length)}
    : {runId, credits};
}

async function main() {
  const [command, argument] = process.argv.slice(2);
  if (command === 'init') {
    if (await exists(DEFAULT_STATE)) throw new Error('Controller state already exists.');
    await saveState(DEFAULT_STATE, createInitialState());
  } else if (command === 'step') {
    await runOneStep();
  } else if (command === 'run') {
    for (let count = 0; count < 100; count += 1) {
      const state = await runOneStep();
      if (state.phase === 'paused' || state.phase === 'complete') break;
    }
  } else if (command === 'recover') {
    await recoverPending();
  } else if (command === 'continue-usage') {
    if (process.argv.length !== 4) throw new Error('Use continue-usage RUN_ID.');
    await continueInterruptedUsage(DEFAULT_STATE, argument);
  } else if (command === 'resume-decision') {
    await withLock(DEFAULT_STATE, async () => {
      const state = await loadOrCreate(DEFAULT_STATE);
      await saveState(DEFAULT_STATE, resumePausedCoordinatorDecision(state));
    });
  } else if (command === 'resume-overrun') {
    await withLock(DEFAULT_STATE, async () => {
      const state = await loadOrCreate(DEFAULT_STATE);
      await saveState(DEFAULT_STATE, resumePausedWorkerOverrun(state));
    });
  } else if (command === 'resume-reconciled') {
    await withLock(DEFAULT_STATE, async () => {
      const state = await loadOrCreate(DEFAULT_STATE);
      await saveState(DEFAULT_STATE, resumeAfterReconciliation(state));
    });
  } else if (command === 'attribute-reconcile') {
    await withLock(DEFAULT_STATE, async () => {
      const state = await loadOrCreate(DEFAULT_STATE);
      await saveState(DEFAULT_STATE, attributeExistingReconciliation(state));
    });
  } else if (command === 'reconcile') {
    const total = Number(argument);
    await withLock(DEFAULT_STATE, async () => {
      const state = await loadOrCreate(DEFAULT_STATE);
      await saveState(DEFAULT_STATE, reconcileBilledCredits(state, total));
    });
  } else if (command === 'retire-interrupted') {
    const {runId, credits} = parseRetirementArgs(process.argv.slice(2));
    await retireInterruptedAfterBilling(DEFAULT_STATE, runId, credits, {exitObserved: true});
  } else if (command === 'retire-provisional') {
    const {runId, credits, sourcePool} = parseRetirementArgs(process.argv.slice(2));
    await retireInterruptedWithReserve(DEFAULT_STATE, runId, credits,
      {exitObserved: true, sourcePool});
  } else if (command === 'transfer-pool') {
    if (process.argv.length !== 6) {
      throw new Error('Use transfer-pool FUTURE_POOL CREDITS REASON.');
    }
    await withLock(DEFAULT_STATE, async () => {
      const state = await loadOrCreate(DEFAULT_STATE);
      await saveState(DEFAULT_STATE, transferForCoordinator(state, argument,
        Number(process.argv[4]), process.argv[5]));
    });
  } else if (command === 'invalidate-review') {
    if (process.argv.length !== 4) throw new Error('Use invalidate-review REASON.');
    await withLock(DEFAULT_STATE, async () => {
      const state = await loadOrCreate(DEFAULT_STATE);
      await saveState(DEFAULT_STATE, invalidateLimitedReview(state, argument));
    });
  } else if (command !== 'status') {
    throw new Error('Usage: node scripts/astra_controller.mjs init|status|step|run|recover|continue-usage RUN_ID|resume-decision|resume-overrun|resume-reconciled|attribute-reconcile|reconcile CREDITS|retire-interrupted RUN_ID BILLED_TOTAL --observed-exit|retire-provisional RUN_ID CREDITS --observed-exit [--from=FUTURE_POOL]|transfer-pool FUTURE_POOL CREDITS REASON|invalidate-review REASON');
  }
  process.stdout.write(JSON.stringify(summary(await loadOrCreate(DEFAULT_STATE)), null, 2) + '\n');
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main().catch(error => {
    process.stderr.write(error.message + '\n');
    process.exitCode = 1;
  });
}
