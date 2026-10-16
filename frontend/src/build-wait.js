/**
 * Wait for the server's current build to finish — the "poll health until
 * !building" loop that App's instance mutations (update / duplicate /
 * rename / delete) each used to carry by hand.
 *
 * Plain function (no React) so the node-only spec can drive it with a
 * scripted health().
 */
import { api } from './api.js';

export const BUILD_WAIT_TIMEOUT_MS = 60 * 1000;
export const BUILD_WAIT_INTERVAL_MS = 1000;

/**
 * Resolves true once health reports `building: false`, false when the
 * wait times out — the caller carries on either way (the 2 s health poll
 * reloads the font whenever the build does land). Polling errors are
 * ignored and the wait continues, as the hand-written loops did.
 */
export async function waitForBuildIdle({
  health = () => api.health(),
  timeoutMs = BUILD_WAIT_TIMEOUT_MS,
  intervalMs = BUILD_WAIT_INTERVAL_MS,
} = {}) {
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    try {
      const h = await health();
      if (!h.building) return true;
    } catch {
      // Ignore polling errors, continue waiting
    }
    if (Date.now() >= deadline) return false;
    await new Promise(resolve => setTimeout(resolve, intervalMs));
  }
}
