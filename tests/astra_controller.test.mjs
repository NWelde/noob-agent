import assert from 'node:assert/strict';
import {execFileSync} from 'node:child_process';
import {chmod, link, mkdtemp, mkdir, readFile, rm, symlink, writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import path from 'node:path';
import test from 'node:test';
import * as controller from '../scripts/astra_controller.mjs';
import {
  applyAgentResult,
  applyCoordinatorDecision,
  attributeExistingReconciliation,
  codexRunner,
  createInitialState,
  estimateCredits,
  invalidateLimitedReview,
  processIdentity,
  parseRetirementArgs,
  recoverPending,
  reconcileBilledCredits,
  retireInterruptedAfterBilling,
  retireInterruptedWithReserve,
  transferForCoordinator,
  resumeAfterReconciliation,
  resumePausedCoordinatorDecision,
  resumePausedWorkerOverrun,
  runOneStep,
} from '../scripts/astra_controller.mjs';

test('stale-lock recovery refuses an owner whose identity is unknown', async t => {
  const root = await mkdtemp(path.join(tmpdir(), 'astra-lock-recovery-'));
  t.after(() => rm(root, {recursive: true, force: true}));
  const statePath = path.join(root, 'state.json');
  const state = createInitialState();
  await writeFile(statePath, JSON.stringify(state));
  const lock = JSON.stringify({owner: {pid: 123, bootId: 'fake', startTicks: '1'}});
  await writeFile(statePath + '.lock', lock);
  await assert.rejects(recoverPending(statePath, root, {
    processProbe: async () => 'unknown',
  }), /live|unknown/i);
  assert.equal(await readFile(statePath + '.lock', 'utf8'), lock);
  assert.deepEqual(JSON.parse(await readFile(statePath, 'utf8')), state);
});

const coordinator = (action, extra = {}) => ({
  action, milestone: 0, allowanceCredits: 0, instructions: 'Inspect the section.',
  ownedFiles: [], reason: 'Next bounded step', ...extra,
});

async function interruptedFixture(t, includeResponse = true, usageEvidence = usage) {
  const root = await mkdtemp(path.join(tmpdir(), 'astra-interrupted-'));
  t.after(() => rm(root, {recursive: true, force: true}));
  const statePath = path.join(root, 'state.json');
  await assert.rejects(runOneStep({statePath, runner: async ({pending}) => {
    await writeFile(pending.tracePath, JSON.stringify({type: 'turn.completed', usage: usageEvidence}) + '\n');
    if (includeResponse) await writeFile(pending.responsePath, JSON.stringify(coordinator('pause')));
    throw new Error('fake interruption');
  }}), /fake interruption/);
  return {statePath, state: JSON.parse(await readFile(statePath, 'utf8'))};
}

test('stale-lock recovery serializes reclaim and charges once', async t => {
  const f = await interruptedFixture(t);
  await writeFile(f.statePath + '.lock', JSON.stringify({owner: {pid: 123, bootId: 'fake', startTicks: '1'}}));
  const results = await Promise.allSettled([1, 2].map(() => recoverPending(f.statePath, undefined,
    {processProbe: async () => 'dead'})));
  assert.ok(results.some(result => result.status === 'fulfilled'));
  const state = await recoverPending(f.statePath);
  assert.equal(state.pools['0'].spent, estimateCredits(usage));
  assert.equal(state.history.filter(entry => entry.event === 'usage').length, 1);
});

test('recovery refuses a potentially live worker with completed artifacts', async t => {
  const f = await interruptedFixture(t);
  await writeFile(f.state.pending.processPath, JSON.stringify({status: 'running'}));
  await assert.rejects(recoverPending(f.statePath), /process|live/i);
  assert.deepEqual(JSON.parse(await readFile(f.statePath, 'utf8')), f.state);
});

test('interrupted usage without a response is charged once and retains pending artifacts', async t => {
  const f = await interruptedFixture(t, false);
  const trace = await readFile(f.state.pending.tracePath, 'utf8');
  const first = await recoverPending(f.statePath);
  assert.equal(first.pools['0'].spent, estimateCredits(usage));
  assert.ok(first.pending);
  assert.equal(first.phase, 'paused');
  assert.deepEqual(await recoverPending(f.statePath), first);
  await assert.rejects(runOneStep({statePath: f.statePath, runner: () => assert.fail('duplicate dispatch')}), /pending/i);
  assert.equal(await readFile(f.state.pending.tracePath, 'utf8'), trace);
});

test('recovery without usage evidence retains pending state', async t => {
  const f = await interruptedFixture(t, true, null);
  await assert.rejects(recoverPending(f.statePath), /usage/i);
  assert.deepEqual(JSON.parse(await readFile(f.statePath, 'utf8')), f.state);
});

test('live controller lock blocks concurrent recovery and dispatch', async t => {
  const root = await mkdtemp(path.join(tmpdir(), 'astra-live-lock-'));
  t.after(() => rm(root, {recursive: true, force: true}));
  const statePath = path.join(root, 'state.json');
  let release;
  let started;
  const ready = new Promise(resolve => { started = resolve; });
  const wait = new Promise(resolve => { release = resolve; });
  const running = runOneStep({statePath, runner: async () => {
    started();
    await wait;
    return {result: coordinator('pause'), usage};
  }});
  await ready;
  try {
    const lock = JSON.parse(await readFile(statePath + '.lock', 'utf8'));
    assert.deepEqual(lock.owner, await processIdentity());
    await assert.rejects(recoverPending(statePath), /live|unknown/i);
    await assert.rejects(runOneStep({statePath, runner: () => assert.fail('duplicate')}), /EEXIST/);
  } finally { release(); await running; }
});

test('stale-lock identity distinguishes PID reuse and preserves old lock evidence', async t => {
  const f = await interruptedFixture(t);
  const owner = {...await processIdentity(), startTicks: '0'};
  await writeFile(f.statePath + '.lock', JSON.stringify({owner}));
  const state = await recoverPending(f.statePath);
  assert.equal(state.pools['0'].spent, estimateCredits(usage));
});

test('interrupted worker usage charges its milestone and section only once', async t => {
  const f = await interruptedFixture(t, false);
  const state = applyCoordinatorDecision(createInitialState(), coordinator('worker', {
    allowanceCredits: 20, ownedFiles: ['owned.js'],
  }));
  state.pending = {...f.state.pending, role: 'worker'};
  await writeFile(f.statePath, JSON.stringify(state));
  const first = await recoverPending(f.statePath);
  assert.equal(first.workUnit.spent, estimateCredits(usage));
  assert.equal(first.pools['0'].spent, estimateCredits(usage));
  // Even a later response cannot replay an already-accounted interruption.
  await writeFile(state.pending.responsePath, JSON.stringify({status: 'handoff'}));
  assert.deepEqual(await recoverPending(f.statePath), first);
});

test('unknown worker launch and missing exit receipts remain blocked', async t => {
  const f = await interruptedFixture(t);
  for (const status of ['launching', 'spawning', 'running']) {
    await writeFile(f.state.pending.processPath, JSON.stringify({status}));
    await assert.rejects(recoverPending(f.statePath), /process.*live/i);
  }
  await rm(f.state.pending.processPath);
  await assert.rejects(recoverPending(f.statePath), /exit evidence/i);
  assert.deepEqual(JSON.parse(await readFile(f.statePath, 'utf8')), f.state);
});

test('ambiguous or unfinished trace usage cannot clear pending dispatch protection', async t => {
  const f = await interruptedFixture(t);
  const completed = JSON.stringify({type: 'turn.completed', usage}) + '\n';
  for (const tail of [completed, JSON.stringify({type: 'item.started'}) + '\n']) {
    await writeFile(f.state.pending.tracePath, completed + tail);
    await assert.rejects(recoverPending(f.statePath), /usage evidence/i);
    assert.deepEqual(JSON.parse(await readFile(f.statePath, 'utf8')), f.state);
  }
});


async function usageOnlyWorker(t) {
  const f = await interruptedFixture(t, false);
  const state = applyCoordinatorDecision(createInitialState(), coordinator('worker', {
    allowanceCredits: 20, ownedFiles: ['owned.js'],
  }));
  state.pending = {...f.state.pending, role: 'worker'};
  state.lastWorker = {status: 'complete', tests: ['old']};
  state.lastReview = {verdict: 'pass', gatePassed: true};
  await writeFile(f.statePath, JSON.stringify(state));
  return {...f, state};
}

const continueUsage = f => controller.continueInterruptedUsage(f.statePath, f.state.pending.id);

test('usage-only continuation preserves artifacts and permits dispatch without duplicate charges', async t => {
  const f = await usageOnlyWorker(t);
  const trace = await readFile(f.state.pending.tracePath, 'utf8');
  const exitReceipt = await readFile(f.state.pending.processPath, 'utf8');
  const recovered = await recoverPending(f.statePath);
  assert.deepEqual(await recoverPending(f.statePath), recovered);
  const continued = await continueUsage(f);
  assert.equal(continued.pending, null);
  assert.equal(continued.phase, 'coordinator');
  assert.equal(continued.lastWorker, null);
  assert.equal(continued.lastReview, null);
  assert.equal(continued.assignment, null);
  assert.equal(continued.workerVersion, 1);
  assert.equal(continued.handoff.tracePath, f.state.pending.tracePath);
  assert.equal(continued.workUnit.spent, estimateCredits(usage));
  assert.deepEqual(await continueUsage(f), continued);
  assert.deepEqual(await recoverPending(f.statePath), continued);
  assert.equal(await readFile(f.state.pending.tracePath, 'utf8'), trace);
  assert.equal(await readFile(f.state.pending.processPath, 'utf8'), exitReceipt);
  assert.deepEqual(continued.history.find(e => e.event === 'continue_interrupted_usage').artifacts,
    recovered.pending);
  const next = await runOneStep({statePath: f.statePath, runner: async () => ({
    result: coordinator('worker', {allowanceCredits: 20, ownedFiles: ['owned.js']}), usage,
  })});
  assert.equal(next.phase, 'worker');
  assert.equal(next.pools['0'].spent, 2 * estimateCredits(usage));
  assert.equal(next.history.filter(e => e.interrupted && e.event === 'usage').length, 1);
  assert.deepEqual(await continueUsage(f), next);
});

test('usage-only continuation carries interrupted edits into later review scope', async t => {
  const f = await usageOnlyWorker(t);
  const repo = path.join(path.dirname(f.statePath), 'repo');
  await mkdir(repo);
  execFileSync('git', ['init', '-q', repo]);
  const baselinePath = path.join(path.dirname(f.statePath), 'baseline.json');
  await writeFile(baselinePath, '{}');
  await writeFile(path.join(repo, 'unowned.js'), 'partial edit');
  f.state.pending.repo = repo;
  f.state.pending.baselinePath = baselinePath;
  await writeFile(f.statePath, JSON.stringify(f.state));
  await recoverPending(f.statePath);
  let state = await continueUsage(f);
  assert.deepEqual(state.handoff.changedFiles, ['unowned.js']);
  assert.deepEqual(state.workUnit.changedFiles, ['unowned.js']);
  state = applyCoordinatorDecision(state, coordinator('worker', {
    allowanceCredits: 20, ownedFiles: ['owned.js'],
  }));
  state = applyAgentResult(state, 'worker', {status: 'complete', summary: 'Finished',
    changedFiles: ['owned.js'], tests: ['passed'], nextAction: 'Review'}, meta);
  state = applyCoordinatorDecision(state, coordinator('review', {allowanceCredits: 20}));
  assert.ok(state.assignment.ownedFiles.includes('unowned.js'));
});

test('usage-only continuation refuses missing, live, unknown, or ambiguous evidence', async t => {
  for (const failure of ['unrecovered', 'running', 'launching', 'missing-exit',
    'missing-usage', 'duplicate-usage', 'changed-usage', 'response', 'partial', 'missing-ledger',
    'duplicate-ledger', 'wrong-run', 'wrong-role']) {
    const f = await usageOnlyWorker(t);
    if (failure !== 'unrecovered') await recoverPending(f.statePath);
    const p = f.state.pending;
    if (['running', 'launching'].includes(failure)) {
      await writeFile(p.processPath, JSON.stringify({status: failure}));
    }
    if (failure === 'missing-exit') await rm(p.processPath);
    if (failure === 'missing-usage') await rm(p.tracePath);
    if (failure === 'duplicate-usage') {
      const trace = await readFile(p.tracePath, 'utf8');
      await writeFile(p.tracePath, trace + trace);
    }
    if (failure === 'changed-usage') await writeFile(p.tracePath,
      JSON.stringify({type: 'turn.completed', usage: {...usage, output_tokens: 99}}));
    if (failure === 'response') await writeFile(p.responsePath, '{}');
    if (failure === 'partial') await writeFile(p.responsePath + '.partial', '{}');
    if (['missing-ledger', 'duplicate-ledger', 'wrong-role'].includes(failure)) {
      const state = JSON.parse(await readFile(f.statePath, 'utf8'));
      if (failure === 'missing-ledger') state.history = state.history.filter(e => e.event !== 'usage');
      if (failure === 'duplicate-ledger') state.history.push(state.history.find(e => e.event === 'usage'));
      if (failure === 'wrong-role') state.pending.role = 'coordinator';
      await writeFile(f.statePath, JSON.stringify(state));
    }
    if (failure === 'wrong-run') f.state.pending.id = 'wrong-run';
    const before = await readFile(f.statePath, 'utf8');
    await assert.rejects(async () => continueUsage(f), /evidence|receipt|response|ENOENT|recover|usage/i, failure);
    assert.equal(await readFile(f.statePath, 'utf8'), before);
    await assert.rejects(runOneStep({statePath: f.statePath, runner: () => {
      assert.fail('must not dispatch');
    }}), /pending/);
  }
});

test('usage-only continuation keeps budget overrun paused and next-call budget gates intact', async t => {
  for (const remaining of [-1, 24]) {
    const f = await usageOnlyWorker(t);
    const state = await recoverPending(f.statePath);
    state.pools['0'].limit = state.pools['0'].spent + remaining;
    await writeFile(f.statePath, JSON.stringify(state));
    const continued = await continueUsage(f);
    assert.equal(continued.phase, remaining < 0 ? 'paused' : 'coordinator');
    assert.equal(continued.pools['0'].spent, estimateCredits(usage));
    const options = {statePath: f.statePath, runner: () => assert.fail('must not dispatch')};
    if (remaining < 0) assert.deepEqual(await runOneStep(options), continued);
    else await assert.rejects(runOneStep(options), /credits/i);
  }
});

test('manual retirement of a legacy interrupted worker needs exit and billing evidence', async t => {
  const root = await mkdtemp(path.join(tmpdir(), 'astra-manual-retire-'));
  t.after(() => rm(root, {recursive: true, force: true}));
  const statePath = path.join(root, 'state.json');
  const tracePath = path.join(root, 'events.jsonl');
  const responsePath = path.join(root, 'response.json');
  const state = applyCoordinatorDecision(createInitialState(), coordinator('worker', {
    allowanceCredits: 20, ownedFiles: ['owned.js'],
  }));
  state.pools['0'].spent = 10;
  state.workUnit.spent = 10;
  state.pending = {id: 'interrupted-run', role: 'worker', milestone: 0,
    tracePath, responsePath};
  await writeFile(statePath, JSON.stringify(state));
  await writeFile(tracePath, '{"type":"turn.started"}\n');
  await assert.rejects(retireInterruptedAfterBilling(statePath, 'interrupted-run', 15),
    /observed.*exit/i);
  await assert.rejects(retireInterruptedAfterBilling(statePath, 'wrong-run', 15,
    {exitObserved: true}), /run id/i);
  state.reconciledTotal = 12;
  await writeFile(statePath, JSON.stringify(state));
  await assert.rejects(retireInterruptedAfterBilling(statePath, 'interrupted-run', 11,
    {exitObserved: true}), /prior billed total/i);
  const retired = await retireInterruptedAfterBilling(statePath, 'interrupted-run', 15,
    {exitObserved: true});
  assert.equal(retired.pending, null);
  assert.equal(retired.phase, 'coordinator');
  assert.equal(retired.lastWorker, null);
  assert.equal(retired.pools['0'].spent, 15);
  assert.equal(retired.reconciledTotal, 15);
  assert.ok(retired.history.some(entry => entry.event === 'retire_interrupted'
    && entry.runId === 'interrupted-run'));
  assert.match(await readFile(tracePath, 'utf8'), /turn.started/);
  await assert.rejects(retireInterruptedAfterBilling(statePath, 'interrupted-run', 15,
    {exitObserved: true}), /pending/i);
});

test('manual retirement refuses a live process or a recoverable response', async t => {
  const root = await mkdtemp(path.join(tmpdir(), 'astra-retire-safety-'));
  t.after(() => rm(root, {recursive: true, force: true}));
  const statePath = path.join(root, 'state.json');
  const processPath = path.join(root, 'process.json');
  const responsePath = path.join(root, 'response.json');
  const tracePath = path.join(root, 'events.jsonl');
  const state = applyCoordinatorDecision(createInitialState(), coordinator('worker', {
    allowanceCredits: 20, ownedFiles: ['owned.js'],
  }));
  state.pools['0'].spent = 10;
  state.workUnit.spent = 10;
  state.pending = {id: 'run', role: 'worker', milestone: 0,
    tracePath, responsePath, processPath};
  await writeFile(statePath, JSON.stringify(state));
  await writeFile(tracePath, '{"type":"turn.started"}\n');
  await writeFile(processPath, '{"status":"running"}');
  await assert.rejects(retireInterruptedAfterBilling(statePath, 'run', 15,
    {exitObserved: true}), /exit receipt/i);
  await writeFile(processPath, '{"status":"exited"}');
  await writeFile(responsePath, '{}');
  await assert.rejects(retireInterruptedAfterBilling(statePath, 'run', 15,
    {exitObserved: true}), /response exists/i);
  assert.deepEqual(JSON.parse(await readFile(statePath, 'utf8')), state);
});

test('provisional retirement reserves credits without inventing billed usage', async t => {
  const root = await mkdtemp(path.join(tmpdir(), 'astra-provisional-retire-'));
  t.after(() => rm(root, {recursive: true, force: true}));
  const statePath = path.join(root, 'state.json');
  const tracePath = path.join(root, 'events.jsonl');
  const state = applyCoordinatorDecision(createInitialState(), coordinator('worker', {
    allowanceCredits: 20, ownedFiles: ['owned.js'],
  }));
  state.pools['0'].spent = 126;
  state.workUnit.spent = 80;
  state.reconciledTotal = 30;
  state.reconciledAtEstimated = 30;
  state.pending = {id: 'interrupted-run', role: 'worker', milestone: 0,
    tracePath, responsePath: path.join(root, 'response.json')};
  await writeFile(statePath, JSON.stringify(state));
  await writeFile(tracePath, '{"type":"turn.started"}\n');
  await assert.rejects(retireInterruptedWithReserve(statePath, 'interrupted-run', 100),
    /observed.*exit/i);
  const retired = await retireInterruptedWithReserve(statePath, 'interrupted-run', 100,
    {exitObserved: true});
  assert.equal(retired.pending, null);
  assert.equal(retired.phase, 'coordinator');
  assert.equal(retired.pools['0'].spent, 226);
  assert.equal(retired.pools['0'].limit, 300);
  assert.equal(retired.pools.reserve.limit, 100);
  assert.equal(retired.workUnit.spent, 180);
  assert.equal(retired.reconciledTotal, 30);
  assert.equal(retired.lastWorker, null);
  assert.ok(retired.history.some(entry => entry.event === 'usage'
    && entry.basis === 'provisional_missing_receipt' && entry.credits === 100));
  assert.ok(retired.history.some(entry => entry.event === 'transfer'
    && entry.from === 'reserve' && entry.to === '0'));
  await assert.rejects(retireInterruptedWithReserve(statePath, 'interrupted-run', 100,
    {exitObserved: true}), /pending/i);
});

test('provisional retirement can draw from a future milestone pool', async t => {
  const root = await mkdtemp(path.join(tmpdir(), 'astra-future-retire-'));
  t.after(() => rm(root, {recursive: true, force: true}));
  const statePath = path.join(root, 'state.json');
  const tracePath = path.join(root, 'events.jsonl');
  const state = applyCoordinatorDecision(createInitialState(), coordinator('worker', {
    allowanceCredits: 20, ownedFiles: ['owned.js'],
  }));
  state.pools['0'].spent = 180;
  state.pools.reserve.limit = 0;
  state.pending = {id: 'future-run', role: 'worker', milestone: 0,
    tracePath, responsePath: path.join(root, 'response.json')};
  await writeFile(statePath, JSON.stringify(state));
  await writeFile(tracePath, '{"type":"turn.started"}\n');
  const retired = await retireInterruptedWithReserve(statePath, 'future-run', 60,
    {exitObserved: true, sourcePool: '1'});
  assert.equal(retired.phase, 'coordinator');
  assert.equal(retired.pools['0'].limit, 260);
  assert.equal(retired.pools['0'].spent, 240);
  assert.equal(retired.pools['1'].limit, 60);
  assert.equal(retired.pools.reserve.limit, 0);
  assert.ok(retired.history.some(entry => entry.event === 'transfer'
    && entry.from === '1' && entry.to === '0' && entry.credits === 60));
  await assert.rejects(retireInterruptedWithReserve(statePath, 'future-run', 60,
    {exitObserved: true, sourcePool: '1'}), /pending/i);
});

test('provisional retirement records partial edits for later review', async t => {
  const f = await usageOnlyWorker(t);
  const repo = path.join(path.dirname(f.statePath), 'retired-repo');
  await mkdir(repo);
  execFileSync('git', ['init', '-q', repo]);
  const baselinePath = path.join(path.dirname(f.statePath), 'retired-baseline.json');
  await writeFile(baselinePath, '{}');
  await writeFile(path.join(repo, 'new-module.js'), 'partial');
  f.state.pending.repo = repo;
  f.state.pending.baselinePath = baselinePath;
  await writeFile(f.state.pending.tracePath, '{"type":"turn.started"}\n');
  await writeFile(f.statePath, JSON.stringify(f.state));
  const retired = await retireInterruptedWithReserve(f.statePath, f.state.pending.id, 10,
    {exitObserved: true});
  assert.deepEqual(retired.handoff.changedFiles, ['new-module.js']);
  assert.deepEqual(retired.workUnit.changedFiles, ['new-module.js']);
});

test('retirement CLI parses the run ID, credits, and observed-exit flag', () => {
  assert.deepEqual(parseRetirementArgs(['retire-provisional', 'run-1', '100', '--observed-exit']),
    {runId: 'run-1', credits: 100});
  assert.deepEqual(parseRetirementArgs(['retire-interrupted', 'run-2', '125', '--observed-exit']),
    {runId: 'run-2', credits: 125});
  assert.deepEqual(parseRetirementArgs(['retire-provisional', 'run-3', '60', '--observed-exit', '--from=1']),
    {runId: 'run-3', credits: 60, sourcePool: '1'});
  assert.throws(() => parseRetirementArgs(['retire-provisional', 'run-1', '100']),
    /observed-exit/i);
});

for (const stream of ['stdout', 'stderr', 'combined']) {
  test(`output checkpoint bounds ${stream} without a newline and preserves pending work`, async t => {
    const f = await confinementFixture(t);
    const planPath = path.join(f.repo, 'hackathon_plan.md');
    await writeFile(planPath, '# Fake output checkpoint\n');
    await writeFile(f.pending.statePath, JSON.stringify(f.state));
    await writeFile(f.executable, `#!${process.execPath}
const fs = require('node:fs');
fs.writeFileSync('owned.js', 'partial work');
${stream === 'combined'
    ? "fs.writeSync(1, 'x'.repeat(512)); fs.writeSync(2, 'y'.repeat(513));"
    : `fs.writeSync(${stream === 'stdout' ? 1 : 2}, 'x'.repeat(8192));`}
setInterval(() => {}, 1000);
`);
    const options = {repo: f.repo, planPath, statePath: f.pending.statePath};
    const started = Date.now();
    await assert.rejects(runOneStep({...options, runner: input => codexRunner({
      ...input, maxOutputBytes: 1024, maxRuntimeMs: 500,
    })}), /output checkpoint/i);
    assert.ok(Date.now() - started < 5000);
    const saved = JSON.parse(await readFile(f.pending.statePath, 'utf8'));
    assert.equal(saved.pending.role, 'worker');
    const stdout = await readFile(saved.pending.tracePath);
    const stderr = await readFile(path.join(path.dirname(saved.pending.tracePath), 'stderr.txt'));
    assert.equal(stdout.length + stderr.length, 1024);
    if (stream === 'stdout') assert.equal(stdout.length, 1024);
    if (stream === 'stderr') assert.equal(stderr.length, 1024);
    assert.equal(await readFile(path.join(f.repo, 'owned.js'), 'utf8'), 'partial work');
    assert.equal(saved.pools['0'].spent, 0);
    await assert.rejects(runOneStep({...options, runner: () => assert.fail('duplicate launch')}), /pending/i);
    await assert.rejects(recoverPending(f.pending.statePath, f.repo));
    assert.deepEqual(JSON.parse(await readFile(f.pending.statePath, 'utf8')), saved);
  });
}

for (const extraByte of [0, 1]) {
  test(`response checkpoint ${extraByte ? 'rejects oversized' : 'accepts exact-ceiling'} fake output`, async t => {
    const f = await confinementFixture(t);
    const limit = 1024 * 1024;
    const planPath = path.join(f.repo, 'hackathon_plan.md');
    await writeFile(planPath, '# Fake response checkpoint\n');
    await writeFile(f.pending.statePath, JSON.stringify(f.state));
    const result = {status: 'handoff', summary: 'bounded', changedFiles: ['owned.js'],
      tests: ['fake only'], nextAction: 'Continue'};
    const body = JSON.stringify(result).padEnd(limit + extraByte, ' ');
    await writeFile(path.join(f.root, 'body.json'), body);
    await writeFile(f.executable, `#!${process.execPath}
const fs = require('node:fs');
const args = process.argv.slice(2);
fs.writeFileSync('owned.js', 'partial work');
fs.copyFileSync(${JSON.stringify(path.join(f.root, 'body.json'))}, args[args.indexOf('-o') + 1]);
fs.writeSync(1, JSON.stringify({type:'turn.completed', usage:${JSON.stringify(usage)}}) + '\\n');
`);
    const options = {repo: f.repo, planPath, statePath: f.pending.statePath};
    if (!extraByte) {
      const saved = await runOneStep({...options, runner: codexRunner});
      assert.equal(saved.pending, null);
      assert.ok(saved.pools['0'].spent > 0);
      return;
    }
    await assert.rejects(runOneStep({...options, runner: codexRunner}), /response checkpoint/i);
    const saved = JSON.parse(await readFile(f.pending.statePath, 'utf8'));
    assert.equal(saved.pending.role, 'worker');
    assert.equal(saved.pools['0'].spent, 0);
    assert.equal(await readFile(path.join(f.repo, 'owned.js'), 'utf8'), 'partial work');
    assert.match(await readFile(saved.pending.tracePath, 'utf8'), /turn.completed/);
    assert.equal((await readFile(saved.pending.responsePath + '.partial')).length, limit);
    await assert.rejects(readFile(saved.pending.responsePath), {code: 'ENOENT'});
    await assert.rejects(runOneStep({...options, runner: () => assert.fail('duplicate launch')}), /pending/i);
    await assert.rejects(recoverPending(f.pending.statePath, f.repo));
    await writeFile(saved.pending.responsePath, body);
    await assert.rejects(recoverPending(f.pending.statePath, f.repo), /response checkpoint/i);
    assert.equal(await readFile(saved.pending.responsePath, 'utf8'), body);
    assert.deepEqual(JSON.parse(await readFile(f.pending.statePath, 'utf8')), saved);
  });
}

test('response checkpoint rejects oversized recovery without changing state or artifacts', async t => {
  const f = await confinementFixture(t);
  await mkdir(path.dirname(f.pending.tracePath), {recursive: true});
  const pending = {...f.pending, role: 'coordinator'};
  const state = {...createInitialState(), pending};
  const body = JSON.stringify(coordinator('pause')).padEnd(1024 * 1024 + 1, ' ');
  await writeFile(pending.statePath, JSON.stringify(state));
  await writeFile(pending.responsePath, body);
  await writeFile(pending.tracePath, JSON.stringify({type: 'turn.completed', usage}) + '\n');
  await assert.rejects(recoverPending(pending.statePath, f.repo), /response checkpoint/i);
  assert.deepEqual(JSON.parse(await readFile(pending.statePath, 'utf8')), state);
  assert.equal(await readFile(pending.responsePath, 'utf8'), body);
});

test('CLI runner uses a read-only coordinator and parses the JSONL usage receipt', async () => {
  const root = await mkdtemp(path.join(tmpdir(), 'astra-cli-'));
  const bin = path.join(root, 'bin');
  await mkdir(bin);
  const fake = path.join(bin, 'codex');
  await writeFile(fake, [
    '#!/bin/sh',
    'printf "%s\\n" "$@" >&2',
    'prev=""',
    'for arg in "$@"; do',
    '  if [ "$prev" = "-o" ]; then out="$arg"; fi',
    '  prev="$arg"',
    'done',
    'cat >/dev/null',
    'printf "%s\\n" \'{"action":"pause","milestone":0,"allowanceCredits":0,"instructions":"","ownedFiles":[],"reason":"done","transferFrom":"","transferTo":"","transferCredits":0}\' > "$out"',
    'printf "%s\\n" \'{"type":"thread.started","thread_id":"fake-thread"}\'',
    'printf "%s\\n" \'{"type":"turn.completed","usage":{"input_tokens":1000,"cached_input_tokens":200,"output_tokens":500}}\'',
  ].join('\n') + '\n');
  await chmod(fake, 0o755);
  const previousPath = process.env.PATH;
  process.env.PATH = bin + path.delimiter + previousPath;
  try {
    const pending = {
      tracePath: path.join(root, 'run', 'events.jsonl'),
      responsePath: path.join(root, 'run', 'response.json'),
    };
    const receipt = await codexRunner({
      role: 'coordinator', prompt: 'Inspect only', state: createInitialState(),
      pending, repo: root,
    });
    const args = (await readFile(path.join(root, 'run', 'stderr.txt'), 'utf8')).split('\n');
    assert.ok(args.includes('approval_policy=never'));
    assert.ok(args.includes('multi_agent'));
    assert.equal(receipt.threadId, 'fake-thread');
    assert.deepEqual(receipt.usage, usage);
  } finally {
    process.env.PATH = previousPath;
  }
});

test('runtime checkpoint stops a long agent and preserves its partial trace', async t => {
  const root = await mkdtemp(path.join(tmpdir(), 'astra-timeout-'));
  t.after(() => rm(root, {recursive: true, force: true}));
  const fake = path.join(root, 'codex');
  await writeFile(fake, '#!/bin/sh\ncat >/dev/null\nprintf \'{"type":"thread.started","thread_id":"slow"}\\n\'\nsleep 10\n');
  await chmod(fake, 0o755);
  const previousPath = process.env.PATH;
  process.env.PATH = root + path.delimiter + previousPath;
  t.after(() => { process.env.PATH = previousPath; });
  const pending = {tracePath: path.join(root, 'run', 'events.jsonl'),
    responsePath: path.join(root, 'run', 'response.json')};
  const started = Date.now();
  await assert.rejects(codexRunner({role: 'coordinator', prompt: 'Inspect',
    state: createInitialState(), pending, repo: root, maxRuntimeMs: 100}),
  /runtime checkpoint/i);
  assert.ok(Date.now() - started < 5_000);
  assert.match(await readFile(pending.tracePath, 'utf8'), /thread.started/);
  await assert.rejects(readFile(pending.responsePath), {code: 'ENOENT'});
});

test('runtime checkpoint waits for an active tool item to finish', async t => {
  const root = await mkdtemp(path.join(tmpdir(), 'astra-boundary-'));
  t.after(() => rm(root, {recursive: true, force: true}));
  const fake = path.join(root, 'codex');
  await writeFile(fake, '#!/bin/sh\ncat >/dev/null\nprintf \'{"type":"item.started","item":{"id":"edit","type":"file_change"}}\\n\'\nsleep 0.3\nprintf \'{"type":"item.completed","item":{"id":"edit","type":"file_change"}}\\n\'\nsleep 10\n');
  await chmod(fake, 0o755);
  const previousPath = process.env.PATH;
  process.env.PATH = root + path.delimiter + previousPath;
  t.after(() => { process.env.PATH = previousPath; });
  const pending = {tracePath: path.join(root, 'run', 'events.jsonl'),
    responsePath: path.join(root, 'run', 'response.json')};
  const started = Date.now();
  await assert.rejects(codexRunner({role: 'coordinator', prompt: 'Inspect',
    state: createInitialState(), pending, repo: root, maxRuntimeMs: 100}),
  /runtime checkpoint/i);
  assert.ok(Date.now() - started < 5_000);
  assert.match(await readFile(pending.tracePath, 'utf8'), /item.completed/);
});

test('response file growth stops a running agent and preserves a bounded partial copy', async t => {
  const f = await confinementFixture(t);
  await writeFile(f.executable, `#!${process.execPath}
const fs = require('node:fs');
const output = process.argv[process.argv.indexOf('-o') + 1];
fs.writeFileSync(output, 'x'.repeat(1024 * 1024 + 1));
setInterval(() => {}, 1000);
`);
  await chmod(f.executable, 0o755);
  const started = Date.now();
  await assert.rejects(codexRunner({...f, role: 'coordinator', prompt: 'Inspect',
    maxRuntimeMs: 5_000}), /response checkpoint/i);
  assert.ok(Date.now() - started < 3_000);
  assert.equal((await readFile(f.pending.responsePath + '.partial')).length, 1024 * 1024);
  await assert.rejects(readFile(f.pending.responsePath), {code: 'ENOENT'});
});
const usage = {input_tokens: 1000, cached_input_tokens: 200, output_tokens: 500};
const meta = {threadId: 'thread-1', usage, tracePath: 'trace.jsonl'};

test('credit estimate separates cached input and output', () => {
  assert.equal(estimateCredits(usage), (800 * 250 + 200 * 25 + 500 * 1250) / 1_000_000);
  assert.equal(estimateCredits({input_tokens: 10}), null);
});

test('reconciled account usage remains the baseline for later estimated charges', () => {
  const state = createInitialState();
  state.totalCredits = 200;
  state.milestone = 1;
  state.pools['0'].spent = 100;
  state.reconciledTotal = 150;
  state.reconciledAtEstimated = 100;
  state.pools['1'].spent = 10;
  assert.throws(() => applyCoordinatorDecision(state, {
    ...coordinator('worker', {allowanceCredits: 20, ownedFiles: ['scripts/section.mjs']}),
    milestone: 1,
  }), /global budget/i);
});

test('included plan usage can reconcile to zero billed credits', () => {
  const state = createInitialState();
  state.totalCredits = 100;
  state.milestone = 1;
  state.pools['0'].spent = 90;
  state.reconciledTotal = 0;
  state.reconciledAtEstimated = 90;
  const next = applyCoordinatorDecision(state, {
    ...coordinator('worker', {allowanceCredits: 20, ownedFiles: ['docs/machine.md']}),
    milestone: 1,
  });
  assert.equal(next.assignment.allowanceCredits, 20);
});

test('reconciliation attributes actual billing across pools and the active work unit', () => {
  const state = createInitialState();
  state.pools['0'].spent = 150;
  state.pools.review.spent = 10;
  state.workUnit = {milestone: 0, cap: 75, spent: 120};
  const next = reconcileBilledCredits(state, 32);
  assert.equal(next.pools['0'].spent, 30);
  assert.equal(next.pools.review.spent, 2);
  assert.equal(next.workUnit.spent, 24);
  assert.equal(next.reconciledTotal, 32);
  assert.equal(next.reconciledAtEstimated, 32);
  assert.equal(state.pools['0'].spent, 150);
  assert.equal(next.history.at(-1).event, 'reconcile');
  assert.equal(next.history.at(-1).priorLedgerSpent, 160);
});

test('a budget pause can resume after reconciliation restores valid gates', () => {
  const state = createInitialState();
  state.phase = 'paused';
  state.pauseReason = 'Work unit exceeds its cap.';
  state.workUnit = {milestone: 0, cap: 75, spent: 120};
  state.pools['0'].spent = 150;
  state.lastWorker = {status: 'handoff', changedFiles: [], tests: [], nextAction: ''};
  state.history.push({event: 'decision', action: 'pause', reason: state.pauseReason});
  assert.throws(() => resumeAfterReconciliation(state), /reconcil/i);
  const reconciled = reconcileBilledCredits(state, 30);
  const resumed = resumeAfterReconciliation(reconciled);
  assert.equal(resumed.phase, 'coordinator');
  assert.equal(resumed.workUnit.spent, 24);
  assert.equal(resumed.pauseReason, null);
  assert.equal(state.phase, 'paused');
});

test('prior billing attribution keeps newer estimated usage unreconciled', () => {
  const state = createInitialState();
  state.pools['0'].spent = 170;
  state.workUnit = {milestone: 0, cap: 75, spent: 120};
  state.reconciledTotal = 30;
  state.reconciledAtEstimated = 160;
  state.history.push({event: 'reconcile', billedTotal: 30});
  state.history.push({event: 'usage', role: 'coordinator', pool: '0', credits: 10});
  const next = attributeExistingReconciliation(state);
  assert.equal(next.pools['0'].spent, 40);
  assert.equal(next.workUnit.spent, 22.5);
  assert.equal(next.reconciledAtEstimated, 30);
  assert.equal(next.reconciledTotal, 30);
  assert.equal(next.history.at(-1).afterEstimate, 10);
  assert.equal(state.pools['0'].spent, 170);
  assert.throws(() => attributeExistingReconciliation(next), /already|attribut/i);
});

test('a coordinator estimate above ten credits still dispatches within its larger allowance', () => {
  const state = createInitialState();
  const next = applyAgentResult(state, 'coordinator', coordinator('worker', {
    allowanceCredits: 20, ownedFiles: ['scripts/astra_controller.mjs'],
  }), {...meta, billedCredits: 11.05415});
  assert.equal(next.phase, 'worker');
  assert.equal(next.assignment.allowanceCredits, 20);
});

test('a saved coordinator decision can be replayed after the old allowance paused it', () => {
  const state = createInitialState();
  state.phase = 'paused';
  state.pauseReason = 'Usage exceeded an agent allowance or budget.';
  state.pools['0'].spent = 11.05415;
  state.history.push({event: 'result', role: 'coordinator', milestone: 0,
    result: coordinator('worker', {allowanceCredits: 20,
      ownedFiles: ['scripts/astra_controller.mjs']})});
  const next = resumePausedCoordinatorDecision(state);
  assert.equal(next.phase, 'worker');
  assert.equal(next.pools['0'].spent, 11.05415);
  assert.equal(next.assignment.allowanceCredits, 20);
  assert.equal(state.phase, 'paused');
});

test('worker cost above its call estimate keeps coordination running within the pool', () => {
  let state = createInitialState();
  state = applyCoordinatorDecision(state, coordinator('worker', {
    allowanceCredits: 20, ownedFiles: ['scripts/astra_controller.mjs'],
  }));
  state = applyAgentResult(state, 'worker', {
    status: 'handoff', summary: 'Partial implementation', changedFiles: [],
    tests: ['focused checks passed'], nextAction: 'Bound the next call',
  }, {...meta, billedCredits: 25});
  assert.equal(state.phase, 'coordinator');
  assert.equal(state.pools['0'].spent, 25);
  assert.equal(state.workUnit.spent, 25);
  state.phase = 'paused';
  state.pauseReason = 'Usage exceeded an agent allowance or budget.';
  assert.throws(() => resumePausedWorkerOverrun(state), /reconcil/i);
  state.reconciledTotal = 5;
  state.reconciledAtEstimated = 25;
  const resumed = resumePausedWorkerOverrun(state);
  assert.equal(resumed.phase, 'coordinator');
  assert.equal(resumed.workUnit.spent, 25);
  assert.equal(resumed.pools['0'].spent, 25);
  assert.equal(resumed.lastWorker.status, 'handoff');
  assert.equal(state.phase, 'paused');
  assert.throws(() => resumePausedWorkerOverrun(resumed), /overrun/i);
});

test('milestone pool exhaustion still pauses after a worker call', () => {
  let state = createInitialState();
  state = applyCoordinatorDecision(state, coordinator('worker', {
    allowanceCredits: 20, ownedFiles: ['scripts/astra_controller.mjs'],
  }));
  state.pools['0'].spent = 190;
  state = applyAgentResult(state, 'worker', {
    status: 'handoff', summary: 'Partial', changedFiles: [], tests: [], nextAction: 'Continue',
  }, {...meta, billedCredits: 25});
  assert.equal(state.phase, 'paused');
  assert.match(state.pauseReason, /budget/i);
});

test('coordinator can estimate a worker call within the milestone pool', () => {
  const state = createInitialState();
  const queued = applyCoordinatorDecision(state, coordinator('worker', {
    allowanceCredits: 75, ownedFiles: ['scripts/astra_controller.mjs'],
  }));
  assert.equal(queued.phase, 'worker');
  assert.equal(queued.assignment.allowanceCredits, 75);
  assert.equal(state.phase, 'coordinator');
  assert.equal(applyCoordinatorDecision(state, coordinator('worker', {
    allowanceCredits: 76, ownedFiles: ['scripts/astra_controller.mjs'],
  })).assignment.allowanceCredits, 76);
  assert.throws(() => applyCoordinatorDecision(state, coordinator('worker', {
    allowanceCredits: 201, ownedFiles: ['scripts/astra_controller.mjs'],
  })), /milestone|allowance/i);
  assert.throws(() => applyCoordinatorDecision(state, coordinator('worker', {
    allowanceCredits: 75, ownedFiles: ['../outside'],
  })), /owned file/i);
});

test('credits transfer only from a future milestone or reserve to the active milestone', () => {
  const state = createInitialState();
  const transfer = {...coordinator('transfer'), transferFrom: 'review', transferTo: '0',
    transferCredits: 10};
  assert.throws(() => applyCoordinatorDecision(state, transfer), /transfer/i);
  assert.throws(() => applyCoordinatorDecision(state, {
    ...transfer, transferFrom: '1', transferTo: 'review',
  }), /transfer/i);
  const next = applyCoordinatorDecision(state, {...transfer, transferFrom: 'reserve'});
  assert.equal(next.pools['0'].limit, 210);
  assert.equal(next.pools.reserve.limit, 190);
});

test('operator transfer is logged and remains subject to coordinator transfer gates', () => {
  const state = createInitialState();
  state.pools['0'].spent = 190;
  const transferred = transferForCoordinator(state, '2', 50,
    'Fund independent milestone-0 review dispatch.');
  assert.equal(transferred.pools['0'].limit, 250);
  assert.equal(transferred.pools['2'].limit, 200);
  assert.ok(transferred.history.some(entry => entry.event === 'transfer'
    && entry.from === '2' && entry.to === '0' && entry.credits === 50));
  assert.throws(() => transferForCoordinator(state, '0', 50, 'Invalid'), /transfer/i);
  state.pending = {id: 'active'};
  assert.throws(() => transferForCoordinator(state, '2', 50, 'Concurrent'), /idle/i);
});

test('coordinator dispatch after milestone zero charges the active milestone', async t => {
  const root = await mkdtemp(path.join(tmpdir(), 'astra-later-coordinator-'));
  t.after(() => rm(root, {recursive: true, force: true}));
  const statePath = path.join(root, 'state.json');
  const planPath = path.join(root, 'hackathon_plan.md');
  const state = createInitialState();
  state.milestone = 1;
  state.pools['0'].spent = state.pools['0'].limit;
  await writeFile(statePath, JSON.stringify(state));
  await writeFile(planPath, '# Later milestone\n');
  const next = await runOneStep({statePath, repo: root, planPath,
    runner: async () => ({result: {...coordinator('pause'), milestone: 1}, usage})});
  assert.equal(next.phase, 'paused');
  assert.equal(next.pools['0'].spent, 200);
  assert.equal(next.pools['1'].spent, estimateCredits(usage));
  assert.equal(next.history.findLast(entry => entry.event === 'usage').pool, '1');
});

test('worker records unassigned edits without losing the completed result', () => {
  let state = applyCoordinatorDecision(createInitialState(), coordinator('worker', {
    allowanceCredits: 20, ownedFiles: ['owned.js'],
  }));
  state = applyAgentResult(state, 'worker', {
    status: 'complete', summary: 'Edited another necessary file',
    changedFiles: ['unowned.js'], tests: ['focused check passed'], nextAction: 'Review',
  }, {...meta, actualChangedFiles: ['unowned.js']});
  assert.equal(state.phase, 'coordinator');
  assert.deepEqual(state.lastWorker.changedFiles, ['unowned.js']);
});

test('unfinished work keeps milestone usage without a separate section cap', () => {
  let state = createInitialState();
  state = applyCoordinatorDecision(state, coordinator('worker', {
    allowanceCredits: 75, ownedFiles: ['scripts/astra_controller.mjs'],
  }));
  state = applyAgentResult(state, 'worker', {
    status: 'handoff', summary: 'Test written', changedFiles: ['scripts/astra_controller.mjs'],
    tests: ['node --test: failing as expected'], nextAction: 'Implement budget gate',
  }, {...meta, inputTokens: 530_000, billedCredits: 10});
  assert.equal(state.phase, 'coordinator');
  assert.equal(state.workerThreadId, null);
  assert.match(state.handoff.nextAction, /budget gate/);
  assert.equal(state.pools['0'].spent, 10);
  const next = applyCoordinatorDecision(state, coordinator('worker', {
    allowanceCredits: 75, ownedFiles: ['scripts/astra_controller.mjs'],
  }));
  assert.equal(next.pools['0'].spent, 10);
  assert.equal(next.assignment.allowanceCredits, 75);
});

test('review must pass the latest worker version before acceptance', () => {
  let state = createInitialState();
  state = applyCoordinatorDecision(state, coordinator('worker', {
    allowanceCredits: 75, ownedFiles: ['scripts/astra_controller.mjs'],
  }));
  state = applyAgentResult(state, 'worker', {
    status: 'complete', summary: 'Controller implemented',
    changedFiles: ['scripts/astra_controller.mjs'], tests: ['node --test: passed'],
    nextAction: '',
  }, {...meta, billedCredits: 15});
  assert.throws(() => applyCoordinatorDecision(state, coordinator('accept')), /review/i);
  state = applyCoordinatorDecision(state, coordinator('review', {allowanceCredits: 20}));
  state = applyAgentResult(state, 'reviewer', {
    verdict: 'pass', findings: [], evidence: ['Focused tests passed'],
  }, {...meta, threadId: 'review-1', billedCredits: 3, gatePassed: true,
    contentDigest: 'test-content-digest'});
  assert.throws(() => applyCoordinatorDecision(state, coordinator('accept')), /content/i);
  const accepted = applyCoordinatorDecision(state, coordinator('accept'),
    {contentDigest: 'test-content-digest'});
  assert.equal(accepted.milestone, 1);
  assert.equal(accepted.phase, 'coordinator');
  assert.equal(accepted.pools.review.spent, 3);
});

test('milestone-zero review includes controller implementation after a docs-only final worker', () => {
  const state = createInitialState();
  state.lastWorker = {status: 'complete', changedFiles: ['README.md', 'CHANGELOG.md'],
    tests: ['controller suite passed']};
  state.workerVersion = 1;
  const review = applyCoordinatorDecision(state, coordinator('review', {allowanceCredits: 20}));
  assert.ok(review.assignment.ownedFiles.includes('scripts/astra_controller.mjs'));
  assert.ok(review.assignment.ownedFiles.includes('tests/astra_controller.test.mjs'));
  assert.ok(review.assignment.ownedFiles.includes('README.md'));
});

test('limited review can be invalidated before a full milestone review', () => {
  const state = createInitialState();
  state.lastWorker = {status: 'complete', changedFiles: ['README.md'], tests: ['passed']};
  state.lastReview = {verdict: 'pass', workVersion: 1, gatePassed: true};
  const next = invalidateLimitedReview(state, 'Documentation-only review omitted the controller.');
  assert.equal(next.lastReview, null);
  assert.equal(next.phase, 'coordinator');
  assert.ok(next.history.some(entry => entry.event === 'invalidate_review'));
  state.pending = {id: 'active'};
  assert.throws(() => invalidateLimitedReview(state, 'Concurrent'), /pending/i);
});

test('milestone zero cannot advance from a reviewer claim without the controller test gate', () => {
  let state = createInitialState();
  state = applyCoordinatorDecision(state, coordinator('worker', {
    allowanceCredits: 75, ownedFiles: ['scripts/astra_controller.mjs'],
  }));
  state = applyAgentResult(state, 'worker', {
    status: 'complete', summary: 'Done', changedFiles: [], tests: ['claimed pass'],
    nextAction: '',
  }, {...meta, billedCredits: 1});
  state = applyCoordinatorDecision(state, coordinator('review', {allowanceCredits: 20}));
  state = applyAgentResult(state, 'reviewer', {
    verdict: 'pass', findings: [], evidence: ['Looks fine'],
  }, {...meta, billedCredits: 1});
  assert.throws(() => applyCoordinatorDecision(state, coordinator('accept')), /test gate/i);
});

test('later milestones also need a controller-run acceptance gate', () => {
  let state = createInitialState();
  state.milestone = 1;
  state = applyCoordinatorDecision(state, {
    ...coordinator('worker', {allowanceCredits: 20, ownedFiles: ['docs/machine.md']}),
    milestone: 1,
  });
  state = applyAgentResult(state, 'worker', {
    status: 'complete', summary: 'Contract written', changedFiles: ['docs/machine.md'],
    tests: ['Contract reviewed'], nextAction: '',
  }, {...meta, billedCredits: 1});
  state = applyCoordinatorDecision(state, {
    ...coordinator('review', {allowanceCredits: 10}), milestone: 1,
  });
  state = applyAgentResult(state, 'reviewer', {
    verdict: 'pass', findings: [], evidence: ['Contract checked'],
  }, {...meta, billedCredits: 1});
  assert.throws(() => applyCoordinatorDecision(state, {
    ...coordinator('accept'), milestone: 1,
  }), /gate/i);
});

test('missing usage pauses automatic dispatch rather than inventing a charge', () => {
  let state = createInitialState();
  state = applyCoordinatorDecision(state, coordinator('worker', {
    allowanceCredits: 75, ownedFiles: ['scripts/astra_controller.mjs'],
  }));
  state = applyAgentResult(state, 'worker', {
    status: 'handoff', summary: 'Partial', changedFiles: [], tests: [], nextAction: 'Continue',
  }, {threadId: 'thread-1', usage: null, tracePath: 'trace.jsonl'});
  assert.equal(state.phase, 'paused');
  assert.match(state.pauseReason, /usage/i);
});

test('a pending invocation survives a crash and is never launched twice', async () => {
  const root = await mkdtemp(path.join(tmpdir(), 'astra-controller-'));
  const statePath = path.join(root, 'state.json');
  let calls = 0;
  await assert.rejects(runOneStep({statePath, runner: async () => {
    calls += 1;
    throw new Error('runner crashed');
  }}), /runner crashed/);
  const saved = JSON.parse(await readFile(statePath, 'utf8'));
  assert.equal(saved.pending.role, 'coordinator');
  await assert.rejects(runOneStep({statePath, runner: async () => { calls += 1; }}), /pending/i);
  assert.equal(calls, 1);
});

test('a fake coordinator, worker, and reviewer advance only after the controller gate', async () => {
  const repo = await mkdtemp(path.join(tmpdir(), 'astra-flow-'));
  execFileSync('git', ['init', '-q', repo], {stdio: 'ignore'});
  await mkdir(path.join(repo, 'scripts'));
  const planPath = path.join(repo, 'hackathon_plan.md');
  await writeFile(planPath, '# Current plan\n');
  const statePath = path.join(repo, '.noob-agent', 'state.json');
  const roles = [];
  let decisions = 0;
  let gates = 0;
  const runner = async ({role}) => {
    roles.push(role);
    let result;
    if (role === 'coordinator') {
      decisions += 1;
      result = decisions === 1
        ? coordinator('worker', {allowanceCredits: 20, ownedFiles: ['scripts/section.js']})
        : decisions === 2 ? coordinator('review', {allowanceCredits: 10})
          : coordinator('accept');
    } else if (role === 'worker') {
      await writeFile(path.join(repo, 'scripts', 'section.js'), 'export const ready = true;\n');
      result = {status: 'complete', summary: 'Added section', changedFiles: ['scripts/section.js'],
        tests: ['focused test passed'], nextAction: ''};
    } else {
      result = {verdict: 'pass', findings: [], evidence: ['Inspected file and test']};
    }
    return {result, threadId: role + '-thread', billedCredits: 1,
      usage, inputTokens: 1000, tracePath: 'fake.jsonl'};
  };
  let state;
  for (let i = 0; i < 5; i += 1) {
    state = await runOneStep({
      repo, planPath, statePath, runner,
      gate: async () => { gates += 1; return true; },
    });
  }
  assert.deepEqual(roles, ['coordinator', 'worker', 'coordinator', 'reviewer', 'coordinator']);
  assert.equal(gates, 1);
  assert.equal(state.milestone, 1);
  assert.equal(state.pools['0'].spent, 4);
  assert.equal(state.pools.review.spent, 1);
});

async function reviewFixture(t) {
  const repo = await mkdtemp(path.join(tmpdir(), 'astra-evidence-'));
  t.after(() => rm(repo, {recursive: true, force: true}));
  execFileSync('git', ['init', '-q', repo], {stdio: 'ignore'});
  const planPath = path.join(repo, 'hackathon_plan.md');
  await writeFile(planPath, '# Frozen requirements\n');
  const target = path.join(repo, 'section.js');
  await writeFile(target, 'export const ready = true;\n');
  const statePath = path.join(repo, '.noob-agent', 'state.json');
  await mkdir(path.dirname(statePath));
  let state = applyCoordinatorDecision(createInitialState(), coordinator('worker', {
    allowanceCredits: 20, ownedFiles: ['section.js'],
  }));
  state = applyAgentResult(state, 'worker', {
    status: 'complete', summary: 'Done', changedFiles: ['section.js'],
    tests: ['Behavior passed'], nextAction: '',
  }, meta);
  state = applyCoordinatorDecision(state, coordinator('review', {allowanceCredits: 10}));
  await writeFile(statePath, JSON.stringify(state));
  const review = {verdict: 'pass', findings: [], evidence: ['Inspected section.js']};
  const options = {repo, planPath, statePath, gate: async () => true};
  const runner = async ({role}) => ({...meta,
    result: role === 'reviewer' ? review : coordinator('accept')});
  return {options, runner, target, review};
}

for (const mutation of ['edit', 'add', 'delete', 'mode', 'symlink', 'plan', 'during-accept']) {
  test(`acceptance refuses ${mutation} after review without another worker`, async t => {
    const {options, runner, target} = await reviewFixture(t);
    const reviewed = await runOneStep({...options, runner});
    assert.equal(reviewed.lastReview.gatePassed, true);
    if (mutation === 'edit') await writeFile(target, 'broken();\n');
    if (mutation === 'add') await writeFile(path.join(options.repo, 'new.js'), 'broken();\n');
    if (mutation === 'delete') await rm(target);
    if (mutation === 'mode') await chmod(target, 0o755);
    if (mutation === 'symlink') {
      await rm(target);
      await symlink('hackathon_plan.md', target);
    }
    if (mutation === 'plan') await writeFile(options.planPath, '# Different requirements\n');
    const accepted = await runOneStep({...options, runner: async input => {
      if (mutation === 'during-accept') await writeFile(target, 'broken();\n');
      return runner(input);
    }});
    assert.equal(accepted.milestone, 0);
    assert.equal(accepted.phase, 'paused');
    assert.match(accepted.pauseReason, /content|fresh/i);
  });
}

for (const stage of ['review', 'gate']) {
  test(`changes during ${stage} cannot produce passing acceptance evidence`, async t => {
    const {options, runner, target} = await reviewFixture(t);
    const state = await runOneStep({...options,
      runner: async input => {
        if (stage === 'review') await writeFile(target, 'broken();\n');
        return runner(input);
      },
      gate: async () => {
        if (stage === 'gate') await writeFile(target, 'broken();\n');
        return true;
      },
    });
    assert.equal(state.lastReview.gatePassed, false);
    assert.throws(() => applyCoordinatorDecision(state, coordinator('accept')), /gate|content/i);
  });
}

test('recovery refuses stale review evidence and charges a durable receipt only once', async t => {
  const {options, target, review} = await reviewFixture(t);
  await assert.rejects(runOneStep({...options, runner: async ({pending}) => {
    await writeFile(pending.tracePath, JSON.stringify({type: 'turn.completed', usage}) + '\n');
    await writeFile(pending.responsePath, JSON.stringify(review));
    throw new Error('crashed after receipt');
  }}), /crashed after receipt/);
  await writeFile(target, 'broken();\n');
  let gates = 0;
  const recovered = await recoverPending(options.statePath, options.repo, {
    gate: async () => { gates += 1; return true; },
  });
  assert.equal(gates, 0);
  assert.equal(recovered.lastReview.gatePassed, false);
  assert.equal(recovered.pools.review.spent, estimateCredits(usage));
  assert.deepEqual(await recoverPending(options.statePath, options.repo), recovered);
});

test('unchanged review recovery saves content receipts and accepts without double charging', async t => {
  const {options, runner, review} = await reviewFixture(t);
  await assert.rejects(runOneStep({...options, runner: async ({pending}) => {
    await writeFile(pending.tracePath, JSON.stringify({type: 'turn.completed', usage}) + '\n');
    await writeFile(pending.responsePath, JSON.stringify(review));
    throw new Error('crashed after receipt');
  }}), /crashed after receipt/);
  let gates = 0;
  const recovered = await recoverPending(options.statePath, options.repo, {
    gate: async (_repo, outputPath) => {
      gates += 1;
      await writeFile(outputPath, 'Fake gate passed\n');
      return true;
    },
  });
  assert.equal(gates, 1);
  assert.equal(recovered.lastReview.gatePassed, true);
  const receipt = JSON.parse(await readFile(recovered.lastReview.evidencePath, 'utf8'));
  const baseline = JSON.parse(await readFile(receipt.baselinePath, 'utf8'));
  assert.equal(receipt.contentDigest, baseline.contentDigest);
  assert.ok(baseline.files['section.js']);
  assert.equal(await readFile(receipt.gatePath, 'utf8'), 'Fake gate passed\n');
  assert.deepEqual(await recoverPending(options.statePath, options.repo), recovered);
  const accepted = await runOneStep({...options, runner});
  assert.equal(accepted.milestone, 1);
  assert.equal(accepted.pools.review.spent, estimateCredits(usage));
  assert.equal(accepted.history.find(entry => entry.event === 'accepted').contentDigest,
    baseline.contentDigest);
});

test('legacy recovered reviews without content baselines cannot satisfy the gate', async t => {
  const {options, review} = await reviewFixture(t);
  await assert.rejects(runOneStep({...options, runner: async ({pending}) => {
    await writeFile(pending.tracePath, JSON.stringify({type: 'turn.completed', usage}) + '\n');
    await writeFile(pending.responsePath, JSON.stringify(review));
    throw new Error('crashed after receipt');
  }}), /crashed after receipt/);
  const state = JSON.parse(await readFile(options.statePath, 'utf8'));
  delete state.pending.contentDigest;
  await writeFile(options.statePath, JSON.stringify(state));
  const recovered = await recoverPending(options.statePath, options.repo, {
    gate: async () => assert.fail('Unbound review must not run acceptance tests'),
  });
  assert.equal(recovered.lastReview.gatePassed, false);
});

test('recovered accepting coordinator refuses intervening edits and charges only once', async t => {
  const {options, runner, target} = await reviewFixture(t);
  await runOneStep({...options, runner});
  await assert.rejects(runOneStep({...options, runner: async ({pending}) => {
    await writeFile(pending.tracePath, JSON.stringify({type: 'turn.completed', usage}) + '\n');
    await writeFile(pending.responsePath, JSON.stringify(coordinator('accept')));
    throw new Error('crashed after receipt');
  }}), /crashed after receipt/);
  await writeFile(target, 'broken();\n');
  const recovered = await recoverPending(options.statePath, options.repo);
  assert.equal(recovered.phase, 'paused');
  assert.equal(recovered.milestone, 0);
  assert.match(recovered.pauseReason, /content/i);
  assert.equal(recovered.pools['0'].spent, 2 * estimateCredits(usage));
  assert.deepEqual(await recoverPending(options.statePath, options.repo), recovered);
});


async function confinementFixture(t) {
  const root = await mkdtemp(path.join(tmpdir(), 'astra-confinement-'));
  t.after(() => rm(root, {recursive: true, force: true}));
  const repo = path.join(root, 'repo');
  await mkdir(repo);
  execFileSync('git', ['init', '-q', repo], {stdio: 'ignore'});
  await mkdir(path.join(repo, '.noob-agent'));
  for (const file of ['owned.js', 'unowned.js', '.noob-agent/state.json']) {
    await writeFile(path.join(repo, file), 'original');
  }
  const outside = path.join(root, 'outside');
  await writeFile(outside, 'original');
  await symlink(outside, path.join(repo, 'escape'));
  const executable = path.join(root, 'codex');
  const previousPath = process.env.PATH;
  process.env.PATH = root + path.delimiter + previousPath;
  t.after(() => { process.env.PATH = previousPath; });
  await writeFile(executable, `#!${process.execPath}
const fs = require('node:fs');
const args = process.argv.slice(2);
{
  const results = JSON.parse(fs.readFileSync(${JSON.stringify(path.join(root, 'requests.json'))}, 'utf8')).map(file => {
    try { fs.writeFileSync(file, 'changed'); return 'allowed'; }
    catch { return 'denied'; }
  });
  fs.writeFileSync(args[args.indexOf('-o') + 1], JSON.stringify({results, args}));
  fs.writeSync(1, JSON.stringify({type:'thread.started', thread_id:'fake-thread'}) + '\\n');
  fs.writeSync(1, JSON.stringify({type:'turn.completed', usage:{input_tokens:1,cached_input_tokens:0,output_tokens:1}}) + '\\n');
}
`);
  await chmod(executable, 0o755);
  await writeFile(path.join(root, 'requests.json'), '[]');
  const pending = {tracePath: path.join(root, 'ledger', 'events.jsonl'),
    responsePath: path.join(root, 'ledger', 'response.json'),
    statePath: path.join(repo, '.noob-agent/state.json')};
  const state = applyCoordinatorDecision(createInitialState(), coordinator('worker', {
    allowanceCredits: 20, ownedFiles: ['owned.js'],
  }));
  return {root, repo, outside, executable, pending, state};
}

for (const resumed of [false, true]) {
  test(`worker can use a shell and write outside assigned files (${resumed ? 'saved' : 'new'})`, async t => {
    const f = await confinementFixture(t);
    if (resumed) f.state.workerThreadId = 'legacy-worker';
    await writeFile(f.executable, `#!${process.execPath}\nconst fs = require('node:fs');\nconst cp = require('node:child_process');\nconst args = process.argv.slice(2);\ncp.execFileSync('sh', ['-c', 'printf shell-ok > unowned.js'], {cwd:${JSON.stringify(f.repo)}});\nfs.writeFileSync(${JSON.stringify(f.outside)}, 'changed');\nfs.writeFileSync(args[args.indexOf('-o') + 1], JSON.stringify({shell:fs.readFileSync('unowned.js','utf8')}));\nfs.writeSync(1, JSON.stringify({type:'turn.completed', usage:{input_tokens:1,cached_input_tokens:0,output_tokens:1}}) + '\\n');\n`);
    const receipt = await codexRunner({...f, role: 'worker', prompt: 'Fake worker'});
    assert.equal(receipt.result.shell, 'shell-ok');
    assert.equal(await readFile(path.join(f.repo, 'unowned.js'), 'utf8'), 'shell-ok');
    assert.equal(await readFile(f.outside, 'utf8'), 'changed');
  });

  test(`worker starts fresh with full repository access (${resumed ? 'saved' : 'new'})`, async t => {
    const f = await confinementFixture(t);
    if (resumed) f.state.workerThreadId = 'legacy-worker';
    const targets = ['owned.js', 'unowned.js', '.git/config']
      .map(file => path.resolve(f.repo, file));
    await writeFile(path.join(f.root, 'requests.json'), JSON.stringify(targets));
    const receipt = await codexRunner({...f, role: 'worker', prompt: 'Fake worker'});
    assert.deepEqual(receipt.result.results, ['allowed', 'allowed', 'allowed']);
    assert.equal(await readFile(path.join(f.repo, 'owned.js'), 'utf8'), 'changed');
    assert.equal(await readFile(f.outside, 'utf8'), 'original');
    assert.ok(!receipt.result.args.includes('resume'), 'saved workers start fresh');
    assert.ok(receipt.result.args.includes('approval_policy=never'));
  });
}

for (const role of ['coordinator', 'reviewer']) {
  for (const resumed of [false, true]) {
    test(`confinement makes ${role} read-only (${resumed ? 'saved' : 'new'})`, async t => {
      const f = await confinementFixture(t);
      if (resumed) {
        f.state.coordinatorThreadId = 'legacy-coordinator';
        f.state.workerThreadId = 'legacy-worker';
        f.pending.resumeId = 'legacy-thread';
      }
      await writeFile(path.join(f.root, 'requests.json'),
        JSON.stringify([path.join(f.repo, 'owned.js'), f.outside]));
      const receipt = await codexRunner({...f, role, prompt: 'Fake observer'});
      assert.deepEqual(receipt.result.results, ['denied', 'denied']);
      assert.ok(receipt.result.args.includes('approval_policy=never'));
      assert.ok(!receipt.result.args.includes('resume'));
    });
  }
}

test('confinement failure never falls back to an unconfined fake runner', async t => {
  const f = await confinementFixture(t);
  await assert.rejects(codexRunner({...f, role: 'reviewer', prompt: '[]',
    sandboxExecutable: path.join(f.root, 'missing-bwrap')}), /ENOENT/);
  await assert.rejects(readFile(f.pending.responsePath), {code: 'ENOENT'});
});

test('a requested model-turn bound cannot silently launch an unbounded CLI', async t => {
  const f = await confinementFixture(t);
  await assert.rejects(codexRunner({...f, role: 'worker', prompt: 'Fixture only',
    maxModelTurns: 1}), /model.turn.*unsupported/i);
  await assert.rejects(readFile(f.pending.tracePath), {code: 'ENOENT'});
  await assert.rejects(readFile(f.pending.responsePath), {code: 'ENOENT'});
});

test('real CLI local responses execute worker shell and writes, then recover usage', {
  skip: !process.env.ASTRA_REAL_CODEX,
}, async t => {
  const f = await confinementFixture(t);
  const realCodex = process.env.ASTRA_REAL_CODEX;
  assert.ok(path.isAbsolute(realCodex));
  t.diagnostic(execFileSync(realCodex, ['--version'], {encoding: 'utf8'}).trim());
  // Only the local provider synthesizes Responses SSE; shell, JSONL and patch
  // execution come from the installed Codex CLI without external model billing.
  const providerScript = `
const http = require('node:http');
const {spawn} = require('node:child_process');
let calls = 0;
const server = http.createServer(async (req, res) => {
  const chunks = [];
  for await (const chunk of req) chunks.push(chunk);
  if (req.method !== 'POST' || req.url !== '/responses' || ++calls > 2) {
    res.writeHead(400); res.end('unexpected request'); return;
  }
  const request = JSON.parse(Buffer.concat(chunks).toString());
  let output;
  if (calls === 1) {
    const definitions = JSON.stringify(request.input.filter(i => i.type === 'additional_tools'));
    if (!definitions.includes('apply_patch')) { res.writeHead(400); res.end('missing patch tool'); return; }
    const patch = file => '*** Begin Patch\\n*** Update File: '+${JSON.stringify(f.repo)}+'/'+file+'\\n@@\\n-original\\n+changed\\n*** End Patch';
    const shell = JSON.stringify({cmd:'printf shell-ok', workdir:${JSON.stringify(f.repo)}});
    output = {id:'fc_local', type:'custom_tool_call', call_id:'call_local', name:'exec', namespace:'functions',
      input:'text((await tools.exec_command('+shell+')).output); text(await tools.apply_patch('+JSON.stringify(patch('owned.js'))+')); text(await tools.apply_patch('+JSON.stringify(patch('unowned.js'))+'));', status:'completed'};
  } else {
    output = {id:'msg_local', type:'message', role:'assistant', status:'completed',
      content:[{type:'output_text', text:JSON.stringify({status:'handoff', summary:'Local fixture',
        changedFiles:['owned.js','unowned.js'], tests:['shell and writes'], nextAction:'Inspect evidence'}), annotations:[]}]};
  }
  res.writeHead(200, {'content-type':'text/event-stream'});
  const event = data => res.write('data: ' + JSON.stringify(data) + '\\n\\n');
  event({type:'response.created', response:{id:'resp_'+calls, status:'in_progress', output:[]}});
  event({type:'response.output_item.added', output_index:0, item:output});
  event({type:'response.output_item.done', output_index:0, item:output});
  event({type:'response.completed', response:{id:'resp_'+calls, status:'completed', output:[output],
    usage:{input_tokens:100, output_tokens:20, total_tokens:120, input_tokens_details:{cached_tokens:0}}}});
  res.end();
});
server.listen(0, '127.0.0.1', () => {
  const home = require('node:fs').mkdtempSync(process.env.TMPDIR + '/offline-home-');
  const provider = {name:'local-fixture', base_url:'http://127.0.0.1:'+server.address().port,
    wire_api:'responses', requires_openai_auth:false, request_max_retries:0, stream_max_retries:0};
  const args = [];
  for (const [key,value] of Object.entries(provider)) args.push('-c', 'model_providers.local_fixture.'+key+'='+JSON.stringify(value));
  const child = spawn(${JSON.stringify(realCodex)}, [...process.argv.slice(1),
    '-c', 'model_provider="local_fixture"', ...args],
    {stdio:'inherit', env:{...process.env, CODEX_HOME:home}});
  child.on('exit', code => server.close(() => process.exit(code ?? 1)));
});
`;
  await writeFile(f.executable, `#!${process.execPath}
const {spawnSync} = require('node:child_process');
const result = spawnSync(${JSON.stringify(process.execPath)},
  ['-e', ${JSON.stringify(providerScript)}, '--', ...process.argv.slice(2)],
  {stdio:'inherit'});
process.exit(result.status ?? 1);
`);
  const planPath = path.join(f.repo, 'hackathon_plan.md');
  await writeFile(planPath, 'Milestone 0 deterministic local provider fixture.');
  await writeFile(f.pending.statePath, JSON.stringify(f.state));
  await assert.rejects(runOneStep({repo: f.repo, planPath, statePath: f.pending.statePath,
    runner: async input => {
      await codexRunner({...input, maxRuntimeMs: 15_000}).catch(async error => {
        t.diagnostic(await readFile(path.join(path.dirname(input.pending.tracePath), 'stderr.txt'), 'utf8'));
        t.diagnostic(await readFile(input.pending.tracePath, 'utf8'));
        throw error;
      });
      await rm(input.pending.responsePath);
      throw new Error('fixture lost response after CLI exit');
    }}), /fixture lost response/);
  const saved = JSON.parse(await readFile(f.pending.statePath, 'utf8'));
  const trace = await readFile(saved.pending.tracePath, 'utf8');
  t.diagnostic(trace);
  assert.equal(await readFile(path.join(f.repo, 'owned.js'), 'utf8'), 'changed\n');
  assert.equal(await readFile(path.join(f.repo, 'unowned.js'), 'utf8'), 'changed\n');
  assert.match(trace, /file_change/);
  assert.match(trace, /unowned.js/);
  assert.match(trace, /shell-ok/);
  const recovered = await recoverPending(f.pending.statePath, f.repo);
  assert.ok(recovered.pending.interruptedUsage.credits > 0);
  assert.equal(recovered.phase, 'paused');
  assert.deepEqual(await recoverPending(f.pending.statePath, f.repo), recovered);
  await assert.rejects(runOneStep({statePath: f.pending.statePath,
    runner: () => assert.fail('must not redispatch')}), /pending/i);
});

test('real CLI offline startup failure preserves pending recovery and confirms exit', {
  skip: !process.env.ASTRA_REAL_CODEX,
}, async t => {
  const realCodex = process.env.ASTRA_REAL_CODEX;
  assert.ok(path.isAbsolute(realCodex), 'ASTRA_REAL_CODEX must be an absolute executable path');
  const version = execFileSync(realCodex, ['--version'], {encoding: 'utf8'}).trim();
  assert.match(version, /^codex-cli /);
  t.diagnostic(version + '; real CLI, offline invalid-provider fixture; no model response');
  const f = await confinementFixture(t);
  // This shim only routes to the real CLI. A separate network namespace and
  // unknown provider make this a deterministic, credential-free failure check.
  await writeFile(f.executable, `#!${process.execPath}
const {spawnSync} = require('node:child_process');
const result = spawnSync('bwrap', ['--unshare-net', '--bind', '/', '/', '--', ${JSON.stringify(realCodex)},
  ...process.argv.slice(2), '-c', 'model_provider="astra_offline_missing"'],
  {stdio: 'inherit'});
process.exit(result.status ?? 1);
`);
  const planPath = path.join(f.repo, 'hackathon_plan.md');
  await writeFile(planPath, 'Offline milestone-0 startup fixture.');
  await writeFile(f.pending.statePath, JSON.stringify(f.state));
  await assert.rejects(runOneStep({repo: f.repo, planPath,
    statePath: f.pending.statePath, runner: codexRunner}), /Confined Codex worker exited/);
  const saved = await readFile(f.pending.statePath, 'utf8');
  const state = JSON.parse(saved);
  const processReceipt = JSON.parse(await readFile(state.pending.processPath, 'utf8'));
  assert.equal(processReceipt.status, 'exited');
  assert.notEqual(processReceipt.code, 0);
  assert.match(await readFile(path.join(path.dirname(state.pending.tracePath), 'stderr.txt'), 'utf8'),
    /astra_offline_missing/);
  await assert.rejects(recoverPending(f.pending.statePath, f.repo), /No completed turn/);
  assert.equal(await readFile(f.pending.statePath, 'utf8'), saved);
  await assert.rejects(runOneStep({statePath: f.pending.statePath,
    runner: () => assert.fail('pending failure must block redispatch')}), /pending/i);
  assert.equal(await readFile(path.join(f.repo, 'owned.js'), 'utf8'), 'original');
  assert.equal(await readFile(f.outside, 'utf8'), 'original');
  assert.equal(state.pools['0'].spent, f.state.pools['0'].spent);
});

test('chunk checkpoints retire worker and coordinator threads regardless of token totals', () => {
  for (const inputTokens of [1, 900_000]) {
    const queued = applyAgentResult(createInitialState(), 'coordinator', coordinator('worker', {
      allowanceCredits: 20, ownedFiles: ['owned.js'],
    }), {...meta, inputTokens});
    assert.equal(queued.coordinatorThreadId, null);
    const next = applyAgentResult(queued, 'worker', {status: 'complete', summary: 'Chunk done',
      changedFiles: [], tests: ['fake passed'], nextAction: 'Review'}, {...meta, inputTokens});
    assert.equal(next.workerThreadId, null);
    assert.equal(next.workUnit.cap, 200);
  }
});


test('observer confinement protects relocated Git metadata', async t => {
  const f = await confinementFixture(t);
  execFileSync('git', ['init', '-q', '--separate-git-dir', path.join(f.repo, 'metadata'), f.repo],
    {stdio: 'ignore'});
  await writeFile(path.join(f.root, 'requests.json'),
    JSON.stringify([path.join(f.repo, 'metadata/config')]));
  const receipt = await codexRunner({...f, role: 'reviewer', prompt: 'Fake reviewer'});
  assert.deepEqual(receipt.result.results, ['denied']);
});

test('context checkpoint refuses oversized dispatch before pending state or fake execution', async t => {
  const f = await confinementFixture(t);
  const planPath = path.join(f.repo, 'hackathon_plan.md');
  await writeFile(planPath, 'x'.repeat(65 * 1024));
  await writeFile(f.pending.statePath, JSON.stringify(f.state));
  let launches = 0;
  await assert.rejects(runOneStep({repo: f.repo, planPath, statePath: f.pending.statePath,
    runner: async () => { launches++; throw new Error('should not launch'); }}), /checkpoint/i);
  assert.equal(launches, 0);
  assert.deepEqual(JSON.parse(await readFile(f.pending.statePath, 'utf8')), f.state);
});
