import { registerRateLimitHit } from "./hostPolling.js";

const BASE = window.__trackio_base || "";

let _mediaDir = "";

export async function callApi(apiName, params = {}) {
  const cleanApiName = apiName.startsWith("/") ? apiName.slice(1) : apiName;
  const url = `${BASE}/api/${cleanApiName}`;
  const resp = await fetch(url, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(params),
  });
  if (resp.status === 429) {
    registerRateLimitHit();
  }
  if (!resp.ok) {
    throw new Error(`API call ${apiName} failed: ${resp.status}`);
  }
  const json = await resp.json();
  if (json.error) {
    throw new Error(json.error);
  }
  return json.data;
}

export async function getRunsForProject() {
  return await callApi("/get_runs_for_project");
}

export async function getRunConfigs() {
  return await callApi("/get_run_configs");
}

function normalizeRun(run) {
  if (run == null) return { run: null, run_id: null };
  if (typeof run === "string") return { run, run_id: null };
  return { run: run.name ?? null, run_id: run.id ?? null };
}

export async function getMetricsForRun(run) {
  return await callApi("/get_metrics_for_run", normalizeRun(run));
}

export async function getLogs(run, options = {}) {
  return await callApi("/get_logs", { ...normalizeRun(run), ...options });
}

export async function getLogsBatch(runs, options = {}) {
  const payload = {
    runs: runs.map((run) => normalizeRun(run)),
    ...options,
  };
  return await callApi("/get_logs_batch", payload);
}

export async function getTraces(run, options = {}) {
  return await callApi("/get_traces", { ...normalizeRun(run), ...options });
}

export async function getTraceSteps(run) {
  return await callApi("/get_trace_steps", normalizeRun(run));
}

export async function getProjectSummary() {
  return await callApi("/get_project_summary");
}

export async function getRunSummary(run) {
  return await callApi("/get_run_summary", normalizeRun(run));
}

export async function getAlerts(run, level, since) {
  return await callApi("/get_alerts", { ...normalizeRun(run), level, since });
}

export async function getSystemMetricsForRun(run) {
  return await callApi("/get_system_metrics_for_run", normalizeRun(run));
}

export async function getSystemLogs(run) {
  return await callApi("/get_system_logs", normalizeRun(run));
}

export async function getSystemLogsBatch(runs) {
  return await callApi("/get_system_logs_batch", {
    runs: runs.map((run) => normalizeRun(run)),
  });
}

export async function getSnapshot(run, step) {
  return await callApi("/get_snapshot", {
    ...normalizeRun(run),
    step,
    around_step: null,
    at_time: null,
    window: null,
  });
}

export async function getMetricValues(run, metricName) {
  return await callApi("/get_metric_values", {
    ...normalizeRun(run),
    metric_name: metricName,
    step: null,
    around_step: null,
    at_time: null,
    window: null,
  });
}

export async function getSettings() {
  return await callApi("/get_settings");
}

export async function getProjectFiles() {
  return await callApi("/get_project_files");
}

export async function getTabAvailability() {
  return await callApi("/get_tab_availability");
}

export async function deleteRun(run) {
  return await callApi("/delete_run", normalizeRun(run));
}

export async function renameRun(oldRun, newName) {
  const run = normalizeRun(oldRun);
  return await callApi("/rename_run", {
    old_name: run.run,
    run_id: run.run_id,
    new_name: newName,
  });
}

export function setMediaDir(dir) {
  _mediaDir = dir ? dir + "/" : "";
}

export function getAssetUrl(path) {
  return `${BASE}/file?path=${encodeURIComponent(`${_mediaDir}${path}`)}`;
}

export function getMediaUrl(path) {
  return `${BASE}/file?path=${encodeURIComponent(`${_mediaDir}${path}`)}`;
}

export function getFileUrl(path) {
  return `${BASE}/file?path=${encodeURIComponent(path)}`;
}

export async function fetchMediaBlob(path) {
  return getMediaUrl(path);
}
