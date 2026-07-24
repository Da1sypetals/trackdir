let rateLimitCooldownUntil = 0;

export function registerRateLimitHit() {
  const until = Date.now() + 12000;
  rateLimitCooldownUntil = Math.max(rateLimitCooldownUntil, until);
}

export function isRateLimitCooldownActive() {
  return Date.now() < rateLimitCooldownUntil;
}

export function getAppPollIntervalMs() {
  return 1000;
}

export function getMetricsPollIntervalMs() {
  return 1000;
}

export function isTabHidden() {
  return typeof document !== "undefined" && document.hidden;
}
