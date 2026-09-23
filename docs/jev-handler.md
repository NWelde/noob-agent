# Jev request handler

Install with `npm ci` (Node 22+). Put your Vercel AI Gateway key in the
Git-ignored root `.env` as `AI_GATEWAY_API_KEY=...`. The existing local key
is used without copying it into source. An exported environment variable
takes precedence over `.env`.

Run the billing example from the repository root:

```sh
npm run jev -- --example
```

For project requests, pass one JSON object containing `state` and `questions`
on stdin. The process returns one JSON result on stdout and exits; errors go
to stderr with a nonzero exit code. Use npm's silent mode when parsing stdout:

```sh
printf '%s\n' '{"state":{"ticketText":"My invoice was double-charged."},"questions":{"department":{"type":"choice","instructions":"Choose the responsible department.","criteria":{"billing":"Invoices and payments","technical":"Product errors","sales":"New purchases"}}}}' | npm run --silent jev
```

JavaScript callers can import `evaluateJev` from `scripts/jev_handler.mjs`.
They must load `.env` themselves, for example by starting Node with
`--env-file-if-exists=.env`. The function returns the full SDK evaluation result;
the CLI returns `answers`, `usage`, and response model/timestamp only.

The pinned AI SDK uses `criteria`, not `options`, for choices and requires
`instructions` on each question. Read a choice from
`result.answers.department.choice`. Boolean answers contain `probability`,
not a true/false decision; callers choose their decision threshold.
See [Vercel's Jev integration guide](https://vercel.com/i/jev-integrations).

Calls use `typesafe-ai/jev`, a 30-second deadline, and zero automatic retries.
Import callers may override `timeoutMs` or supply an `abortSignal`. The caller
must count each attempted invocation against its budget, including failures.
This handler evaluates supplied state; Minecraft action execution and the
planner/trial loop still need to call it and enforce their own contracts.

Run offline handler checks with `npm run test:jev`.
