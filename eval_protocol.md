# Evaluation Protocol

## Question being tested

noob-agent evaluates a model-powered agent inside a frozen harness:

> Given ordinary new-player controls, an unfamiliar rule, public feedback, and
> a bounded learning budget, how well does a model turn experience into a
> reusable capability that transfers to unseen situations?

It does not measure the connector's ability to solve the task, raw keyboard and
screen control, model-weight training, or broad mastery of a game.

## Unit of comparison

An evaluation target is the exact tuple of provider model ID, model settings,
system and task prompts, Action-agent code, Builder prompt, and skill runtime
version. We call this a **model configuration**. Changing any field creates a
new target.

Within a game, the connector version, primitive manifest, scenario build,
grader, stop rules, and budgets are identical for every target. Results are not
aggregated across games as though their primitive controls were identical.

## Experimental conditions

### 1. Cold baseline

The model attempts each held-out scenario with primitives only. It receives no
training trace, written notes, or generated skill. This measures initial ability.
Its held-out limits are identical to the other conditions.

### 2. Self-improving

The model receives one bounded training attempt, then the Builder may author one
skill and repair it once. After validation, the game and conversation reset.
Only the accepted skill metadata and code-backed tool survive into held-out
episodes.

Cold-to-adaptive lift is the clearest demo result, but adaptive learning consumes
extra resources. Those learning costs must be shown next to the lift.

### 3. Budget-matched notes control

This control is required for a competition-quality claim and optional for the
first BDP. It receives the same training trace and consolidation-call allowance
as the self-improving condition. Instead of Python, it produces a plain-text
playbook of at most 4 KiB. After reset, the playbook is supplied to the Action
agent, but no generated tool is added.

The notes and skill conditions have the same training decisions, held-out
decisions, maximum model calls, token ceiling, and wall-time ceiling. Validation
does not call a model, so its compute is reported separately rather than filled
with artificial calls. This comparison asks whether executable, validated
skills add value beyond reflection and persistent text.

## Budgets

Per model configuration and training seed:

| Phase | Decisions/model calls | Primitive calls | Token ceiling | Wall time |
| --- | ---: | ---: | ---: | ---: |
| Training action | 20 | 40 | 40,000 | 180 s |
| Build or notes authoring | 1 | 0 | 12,000 | 60 s |
| Repair or notes revision | 1 | 0 | 8,000 | 60 s |
| Each held-out episode | 12 | 24 | 24,000 | 90 s |
| Defect reproduction | no model calls | 16 | 0 | 60 s |

The self-improving and notes conditions may use at most 22 learning model calls
and 60,000 learning tokens before held-out evaluation. Unused budget is not
reallocated. Actual calls, tokens, latency, and cost are always reported.

### Multi-round self-improving condition

This separately named condition (`hackathon_plan.md` section 22, step 22.D)
refines the skill before held-out evaluation. It is never pooled with
single-pass results.

- **Round 0.** Round 0 is the single-pass training attempt and build above.
- **Practice.** The accepted skill plays a fixed set of training-split practice
  seeds (`scenarios/doom/basic-v3`: four `practice_seeds`, two targets on each side). Each practice episode has
  the held-out episode budget and a fresh Action conversation.
- **Refinement.** Each later round makes one refinement call, up to 12,000
  tokens, plus its repairs, up to 8,000 tokens each. A validated refinement plays
  the same practice seeds.
- **Keep or reject.** A refinement is accepted, and the incumbent retired, only
  if its public practice score is higher: the fraction of practice episodes ending
  in a terminal state, with fewer primitive actions breaking ties. Otherwise it is
  rejected, and the next refinement again starts from the incumbent.
- **Stopping.** The loop stops after 3 refinement rounds, 2 consecutive rounds
  without a kept version (a refinement that fails validation counts as one), a
  perfect practice score, or the learning budget (section 24, step 24b).
- **Learning budget.** At most 140 learning model calls, 300,000 learning tokens
  (training, Builder, and practice calls), and 900 seconds of learning wall time.
- **Held-out.** Held-out cells run once, after the loop has ended, with the final
  version. A learning curve, if reported, evaluates earlier kept versions only
  after the loop has ended.
- **Grades.** Private grades of practice episodes are recorded only to report how
  often the public practice score agrees with them. They never select a version or
  reach a model.

Skills do not receive free actions: nested primitives count toward the same
held-out limit. Cold results are compared under equal held-out budgets, while
total adaptation cost is disclosed separately.

## Models, settings, and prompts

The BDP may use one W&B Inference model to prove the pipeline. The comparison
target is two models available through W&B Inference at the event. Exact model
IDs are selected once, written to the experiment manifest, and never aliased or
changed mid-campaign.

Use the same temperature, sampling settings, maximum response tokens, tool
schemas, and prompt templates when provider support permits. If a provider
cannot honor a setting, record the difference and avoid claiming a perfectly
controlled comparison.

For the headline result, the same model configuration powers both Action and
Builder roles. A separate Builder model is allowed only as a clearly labeled
ablation.

## Scenarios, seeds, and split

Minecraft uses one training layout, one validation variation, and two held-out
layouts. Each held-out layout has matched clean and faulty builds and three
precommitted seeds. Doom uses one training scenario and two held-out positional
variations with three seeds; a Doom defect is not required for the BDP.

The scenario manifest, seed list, connector hash, and grader hash are committed
before the first scored run. Held-out content is inaccessible to Action and
Builder agents until the relevant episode begins. Run order is randomized by a
precommitted ordering seed so provider drift or machine warm-up does not always
favor one condition.

The smallest publishable comparison is:

```text
2 models x 2 core conditions x 2 games x 2 held-out variations x 3 seeds
= 48 held-out episodes
```

The notes control adds 24 episodes. If only the BDP is complete, publish all
individual runs and label the sample as a demonstration, not a model ranking.

## Metrics

All outcome metrics come from the independent grader.

- **Verified success rate:** successful held-out episodes divided by eligible
  held-out episodes.
- **Adaptation lift:** self-improving success rate minus cold success rate on
  the same model, game, layouts, and seeds.
- **Skill-over-notes lift:** self-improving success rate minus notes-control
  success rate under matched budgets.
- **Progress:** mean completed ordered scenario predicates for failed episodes.
- **Skill acceptance rate:** accepted final candidates divided by learning runs
  that proposed a candidate.
- **Skill invocation success:** invocations whose claimed postcondition and
  grader outcome agree, divided by all invocations.
- **Held-out skill reliability:** held-out episodes in which the invoked skill
  returns the correct status and evidence, divided by episodes invoking it.
- **Learning cost:** calls, input/output tokens, dollars, and wall time through
  accepted skill or exhausted build budget.
- **Successful-run efficiency:** median decisions and primitives among verified
  successful held-out episodes. Failures are never dropped from success rate.
- **Fault detection rate:** verified reports divided by eligible faulty episodes.
- **Clean false-alarm rate:** unverified fault reports divided by eligible clean
  episodes.
- **Reproduction rate:** independently reproduced verified findings divided by
  findings sent to reproduction.

Report means with raw numerators and denominators. With three seeds, show every
point and avoid confidence claims that the sample cannot support. Do not combine
success, defect detection, and cost into one opaque score.

## Pairing and analysis

Primary comparisons are paired by model, game, layout, and seed. A slope chart
shows cold and self-improving success; a second marker shows notes control when
available. Also publish a scenario heatmap, learning-cost versus success plot,
and table of infrastructure and model failures.

An adaptive episode counts as skill-assisted only if an accepted skill was
available and invoked. Keep accepted-but-unused cases visible; do not silently
move them into the cold condition.

## Failure handling

Failures are classified before looking at aggregate scores.

### Counted as model or agent outcomes

- Invalid tool names or arguments generated by the model.
- Repeated ineffective actions, budget exhaustion, voluntary stopping, or an
  incorrect success claim.
- Builder syntax or policy violations, rejected skills, and exhausted repair
  allowance.
- A skill's confirmed primitive failure or incorrect postcondition.

These remain in the denominator as failures.

### Infrastructure failures

- Game or connector crash unrelated to a valid player action.
- Reset fingerprint mismatch.
- W&B Inference service error before an acknowledged response.
- Sandbox service outage before candidate execution.
- An action with unknown delivery status.

Infrastructure-failed episodes are excluded from capability denominators but
listed in a separate reliability table. Retry the same condition and seed at
most once, at the end of the randomized run queue. Never automatically retry an
unknown-usage model call or unknown-delivery game action. Both the original and
replacement records remain visible.

If more than 10% of planned episodes hit infrastructure failures, stop the
campaign and fix or disclose the system problem before ranking models.

### Model API errors

A valid provider refusal, malformed tool call, or context-limit error caused by
the model request counts as a model outcome. Rate limits and provider 5xx errors
are infrastructure failures. Unknown token usage is stored as unknown, never
zero. Models cannot be switched within an episode.

## Exclusions

Predeclare exclusions in the experiment manifest. Allowed exclusions are only:

- Failed reset or scenario fingerprint before the model sees observation zero.
- Confirmed connector, game, provider, or sandbox outage.
- Evidence that the scenario itself was unsolvable or leaked private data.
- Human operator error that selected the wrong frozen manifest.

Do not exclude surprising model behavior, low scores, costly runs, unused
skills, false alarms, or rejected candidates. Any post-hoc scenario exclusion
invalidates all models and conditions for the paired game/layout/seed cell.

## Defect evaluation

The public prompt is identical for clean and faulty Minecraft scenarios. A
finding must specify expected behavior, actual behavior, and evidence references.
Private verification runs only after the episode.

The reproduction runner starts from a fresh reset and uses ordinary primitives,
not private state mutation. It confirms the behavior on faulty and matched clean
builds. The evaluated model does not receive reproduction or grader results.

## Reproducibility and publication

Every result stores the experiment manifest, exact model ID and settings,
prompt hashes, scenario and connector hashes, seed, skill or notes artifact,
exact observations and actions, usage, timing, stop reason, grader version, and
software commit.

SQLite is the local source of truth. Weave mirrors trace and evaluation views;
W&B Artifacts may publish frozen manifests and accepted skills. Upload failure
must not lose or alter a local episode.

The final report must distinguish live runs, exact replays, scripted oracle
checks, and mocked tests. A replay is evidence of reproducibility, not a new
independent sample.

## BDP decision rule

The loop is demo-ready when one live or honestly labeled recorded learning run
produces an accepted skill, a fresh held-out variation invokes it and achieves a
verified success within budget, the same trace is inspectable in Weave, and the
Minecraft faulty behavior is independently reproduced. Claims of model
superiority require the full precommitted comparison, not this single success.
