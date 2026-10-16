/**
 * Unit tests for src/build-wait.js — waitForBuildIdle, the one "poll
 * health until !building" loop that replaced App.jsx's four hand-written
 * copies (sprint T2.6). Pins the copied semantics: resolve as soon as a
 * poll reports idle, keep waiting through polling errors, give up (false,
 * never throw) at the timeout.
 *
 * Usage: node e2e/build-wait.spec.mjs
 */

import { waitForBuildIdle, BUILD_WAIT_TIMEOUT_MS, BUILD_WAIT_INTERVAL_MS } from '../src/build-wait.js';

let failures = 0;
const check = (name, cond, extra = '') => {
  if (cond) console.log(`  ok  ${name}`);
  else { failures++; console.error(`FAIL  ${name}${extra ? ` — ${extra}` : ''}`); }
};
const section = (t) => console.log(`\n${t}`);

const scripted = (answers) => {
  let calls = 0;
  const health = async () => {
    calls++;
    const a = answers[Math.min(calls - 1, answers.length - 1)];
    if (a instanceof Error) throw a;
    return a;
  };
  return { health, polls: () => calls };
};

section('defaults match the hand-written loops');
check('60 s budget', BUILD_WAIT_TIMEOUT_MS === 60000);
check('1 s between polls', BUILD_WAIT_INTERVAL_MS === 1000);

section('resolves on the first idle answer');
{
  const s = scripted([{ building: true }, { building: true }, { building: false }]);
  const idle = await waitForBuildIdle({ health: s.health, intervalMs: 5 });
  check('true once idle', idle === true);
  check('stopped at the idle poll', s.polls() === 3, `${s.polls()} polls`);
}

section('polling errors are ignored and the wait continues');
{
  const s = scripted([new Error('server restarting'), new Error('still'), { building: false }]);
  const idle = await waitForBuildIdle({ health: s.health, intervalMs: 5 });
  check('resolves after the errors', idle === true);
  check('three polls', s.polls() === 3, `${s.polls()} polls`);
}

section('times out quietly');
{
  const s = scripted([{ building: true }]);
  const started = Date.now();
  const idle = await waitForBuildIdle({ health: s.health, timeoutMs: 60, intervalMs: 10 });
  check('false at the deadline, no throw', idle === false);
  check('respected the budget', Date.now() - started >= 55 && Date.now() - started < 1000, `${Date.now() - started} ms`);
  check('kept polling until then', s.polls() >= 4, `${s.polls()} polls`);
}

console.log(failures ? `\n${failures} FAILURE(S)` : '\nall passed');
process.exit(failures ? 1 : 0);
