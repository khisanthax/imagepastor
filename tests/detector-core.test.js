"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const {
  CoalescingScheduler,
  GenerationStateMachine,
  STATES
} = require("../chrome-extension/detector-core.js");

function harness() {
  let now = 1000;
  let nextTimer = 1;
  const timers = new Map();
  let completions = 0;
  const machine = new GenerationStateMachine({
    quietMs: 2500,
    intentTimeoutMs: 30000,
    now: () => now,
    setTimer: (callback, delay) => {
      const id = nextTimer++;
      timers.set(id, { callback, due: now + delay });
      return id;
    },
    clearTimer: (id) => timers.delete(id),
    onComplete: () => completions++
  });

  function advance(milliseconds) {
    now += milliseconds;
    let due;
    do {
      due = [...timers.entries()]
        .filter(([, timer]) => timer.due <= now)
        .sort((left, right) => left[1].due - right[1].due);
      for (const [id, timer] of due) {
        timers.delete(id);
        timer.callback();
      }
    } while (due.length);
  }

  return { machine, advance, completions: () => completions };
}

test("page load with an active marker never chimes", () => {
  const run = harness();
  run.machine.observeGeneration(true);
  run.machine.observeGeneration(false);
  run.advance(5000);
  assert.equal(run.completions(), 0);
  assert.equal(run.machine.state, STATES.IDLE);
});

test("one armed active-to-inactive cycle chimes once", () => {
  const run = harness();
  run.machine.arm("test send");
  run.machine.observeGeneration(true);
  run.machine.observeGeneration(false);
  run.advance(2499);
  assert.equal(run.completions(), 0);
  run.advance(1);
  assert.equal(run.completions(), 1);
  assert.equal(run.machine.state, STATES.COMPLETE);
  run.machine.observeGeneration(false);
  run.advance(5000);
  assert.equal(run.completions(), 1);
});

test("a resumed generation cancels an early completion", () => {
  const run = harness();
  run.machine.arm("test send");
  run.machine.observeGeneration(true);
  run.machine.observeGeneration(false);
  run.advance(2000);
  run.machine.observeGeneration(true);
  run.advance(1000);
  assert.equal(run.completions(), 0);
  run.machine.observeGeneration(false);
  run.advance(2500);
  assert.equal(run.completions(), 1);
});

test("an intent that never generates expires silently", () => {
  const run = harness();
  run.machine.arm("test send");
  run.advance(30000);
  run.machine.observeGeneration(true);
  run.machine.observeGeneration(false);
  run.advance(5000);
  assert.equal(run.completions(), 0);
});

test("navigation reset prevents a stale completion", () => {
  const run = harness();
  run.machine.arm("test send");
  run.machine.observeGeneration(true);
  run.machine.observeGeneration(false);
  run.machine.reset("navigation");
  run.advance(5000);
  assert.equal(run.completions(), 0);
});

test("continuous mutation notifications cannot postpone evaluation", () => {
  let nextTimer = 1;
  const timers = new Map();
  let evaluations = 0;
  const scheduler = new CoalescingScheduler(() => evaluations++, {
    setTimer: (callback, delay) => {
      const id = nextTimer++;
      timers.set(id, { callback, delay });
      return id;
    },
    clearTimer: (id) => timers.delete(id)
  });

  assert.equal(scheduler.schedule(120), true);
  for (let mutation = 0; mutation < 100; mutation++) {
    assert.equal(scheduler.schedule(120), false);
  }
  assert.equal(timers.size, 1);

  const [{ callback, delay }] = timers.values();
  assert.equal(delay, 120);
  callback();
  assert.equal(evaluations, 1);
  assert.equal(scheduler.schedule(120), true);
});
