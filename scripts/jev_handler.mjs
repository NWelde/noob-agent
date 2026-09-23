import {experimental_evaluate as evaluate} from 'ai';
import {resolve} from 'node:path';
import {fileURLToPath} from 'node:url';

export const ticketRequest = {
  state: {ticketText: 'My billing invoice is incorrect and double-charged.'},
  questions: {
    department: {
      type: 'choice',
      instructions: 'Select the department responsible for resolving this ticket.',
      criteria: {
        billing: 'Invoices, payments, incorrect charges, or refunds.',
        technical: 'Product errors, outages, or technical troubleshooting.',
        sales: 'Purchasing, pricing inquiries, or upgrades.',
      },
    },
    urgent: {
      type: 'boolean',
      instructions: 'Does this ticket require urgent attention because it describes '
        + 'an active financial loss, security issue, outage, or time-critical deadline?',
    },
  },
};

/** Server-side evaluation. The caller owns call budgets and action execution. */
export async function evaluateJev({state, questions}, {
  timeoutMs = 30_000,
  abortSignal,
  evaluate: runEvaluation = evaluate,
} = {}) {
  const deadline = AbortSignal.timeout(timeoutMs);
  return runEvaluation({
    model: 'typesafe-ai/jev',
    state,
    questions,
    maxRetries: 0,
    abortSignal: abortSignal ? AbortSignal.any([abortSignal, deadline]) : deadline,
  });
}

async function main() {
  if (!process.env.AI_GATEWAY_API_KEY?.trim()) {
    throw new Error('AI_GATEWAY_API_KEY is required; configure it in the local .env.');
  }
  const args = process.argv.slice(2);
  if (args.length > 1 || (args.length === 1 && args[0] !== '--example')) {
    throw new Error('Use --example or provide a JSON {state, questions} object on stdin.');
  }
  let request = ticketRequest;
  if (args.length === 0) {
    if (process.stdin.isTTY) {
      throw new Error('Provide JSON on stdin, or use --example.');
    }
    let input = '';
    for await (const chunk of process.stdin) input += chunk;
    try { request = JSON.parse(input); } catch {
      throw new Error('stdin must contain one JSON {state, questions} object.');
    }
  }
  try {
    const result = await evaluateJev(request);
    // Keep answers, usage and model identity; omit raw provider headers/body.
    process.stdout.write(JSON.stringify({
      answers: result.answers,
      usage: result.usage,
      response: {modelId: result.response.modelId, timestamp: result.response.timestamp},
    }) + '\n');
  } catch {
    // Provider errors may contain request headers or sensitive state.
    throw new Error('Jev evaluation failed. Check the request schema, Gateway key/credits '
      + 'and connectivity. No automatic retry was made.');
  }
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main().catch(error => {
    process.stderr.write(error.message + '\n');
    process.exitCode = 1;
  });
}
