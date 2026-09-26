(function (root, factory) {
  const api = factory();
  root.ImagePastorDetectorCore = api;
  if (typeof module === "object" && module.exports) {
    module.exports = api;
  }
})(globalThis, function () {
  "use strict";

  const STATES = Object.freeze({
    IDLE: "IDLE",
    GENERATING: "GENERATING",
    COMPLETE: "COMPLETE"
  });

  class CoalescingScheduler {
    constructor(callback, options = {}) {
      this.callback = callback;
      // Browser timer functions must not receive this scheduler as their receiver.
      this.setTimer = options.setTimer ?? ((callback, delay) => setTimeout(callback, delay));
      this.clearTimer = options.clearTimer ?? ((id) => clearTimeout(id));
      this.timer = null;
    }

    schedule(delay = 0) {
      if (this.timer !== null) {
        return false;
      }
      this.timer = this.setTimer(() => {
        this.timer = null;
        this.callback();
      }, delay);
      return true;
    }

    cancel() {
      if (this.timer !== null) {
        this.clearTimer(this.timer);
        this.timer = null;
      }
    }
  }

  class GenerationStateMachine {
    constructor(options = {}) {
      this.quietMs = options.quietMs ?? 2500;
      this.intentTimeoutMs = options.intentTimeoutMs ?? 30000;
      this.now = options.now ?? Date.now;
      this.setTimer = options.setTimer ?? ((callback, delay) => setTimeout(callback, delay));
      this.clearTimer = options.clearTimer ?? ((id) => clearTimeout(id));
      this.onComplete = options.onComplete ?? (() => {});
      this.onStateChange = options.onStateChange ?? (() => {});
      this.state = STATES.IDLE;
      this.armed = false;
      this.indicatorActive = false;
      this.lastIntentAt = 0;
      this.completionTimer = null;
      this.intentTimer = null;
    }

    arm(reason = "user action") {
      this.resetTimers();
      this.state = STATES.IDLE;
      this.armed = true;
      this.indicatorActive = false;
      this.lastIntentAt = this.now();
      this.onStateChange(this.state, `armed by ${reason}`);
      this.intentTimer = this.setTimer(() => {
        if (this.armed && this.state === STATES.IDLE) {
          this.armed = false;
          this.intentTimer = null;
          this.onStateChange(this.state, "intent expired");
        }
      }, this.intentTimeoutMs);
    }

    observeGeneration(isActive) {
      this.indicatorActive = Boolean(isActive);

      if (this.indicatorActive) {
        this.cancelCompletionTimer();
        if (!this.armed || this.state === STATES.COMPLETE) {
          return;
        }
        if (this.state !== STATES.GENERATING) {
          this.state = STATES.GENERATING;
          this.cancelIntentTimer();
          this.onStateChange(this.state, "generation indicator appeared");
        }
        return;
      }

      if (this.state !== STATES.GENERATING || this.completionTimer !== null) {
        return;
      }

      this.completionTimer = this.setTimer(() => {
        this.completionTimer = null;
        if (this.state !== STATES.GENERATING || this.indicatorActive) {
          return;
        }
        this.state = STATES.COMPLETE;
        this.armed = false;
        this.onStateChange(this.state, "generation remained inactive");
        this.onComplete();
      }, this.quietMs);
    }

    isPending() {
      return this.armed || this.state === STATES.GENERATING;
    }

    reset(reason = "reset") {
      this.resetTimers();
      this.state = STATES.IDLE;
      this.armed = false;
      this.indicatorActive = false;
      this.lastIntentAt = 0;
      this.onStateChange(this.state, reason);
    }

    cancelCompletionTimer() {
      if (this.completionTimer !== null) {
        this.clearTimer(this.completionTimer);
        this.completionTimer = null;
      }
    }

    cancelIntentTimer() {
      if (this.intentTimer !== null) {
        this.clearTimer(this.intentTimer);
        this.intentTimer = null;
      }
    }

    resetTimers() {
      this.cancelCompletionTimer();
      this.cancelIntentTimer();
    }
  }

  return { CoalescingScheduler, GenerationStateMachine, STATES };
});
