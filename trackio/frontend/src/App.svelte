<script>
  import { onMount } from "svelte";
  import Navbar from "./components/Navbar.svelte";
  import Sidebar from "./components/Sidebar.svelte";
  import AlertPanel from "./components/AlertPanel.svelte";
  import Metrics from "./pages/Metrics.svelte";
  import Traces from "./pages/Traces.svelte";
  import SystemMetrics from "./pages/SystemMetrics.svelte";
  import Media from "./pages/Media.svelte";
  import Reports from "./pages/Reports.svelte";
  import Runs from "./pages/Runs.svelte";
  import RunDetail from "./pages/RunDetail.svelte";
  import Files from "./pages/Files.svelte";
  import {
    getRunsForProject,
    getRunConfigs,
    getAlerts,
    getTabAvailability,
    getSettings,
    setMediaDir,
  } from "./lib/api.js";
  import {
    getAppPollIntervalMs,
    isRateLimitCooldownActive,
    isTabHidden,
  } from "./lib/hostPolling.js";
  import { setColorPalette } from "./lib/stores.js";
  import { reconcileSelectedRuns } from "./lib/selection.js";
  import { getPageFromPath, navigateTo, getQueryParam } from "./lib/router.js";
  import Settings from "./pages/Settings.svelte";
  import { initTheme, isDark, onThemeChange } from "./lib/theme.js";

  function metricFilterFromLegacyMetricsParam(metricsParam) {
    if (!metricsParam) return "";
    const trimmed = metricsParam.trim();
    const parts = trimmed.split(",").map((s) => s.trim()).filter(Boolean);
    if (parts.length === 0) return "";
    const isSimpleToken = (p) => /^[\w./-]+$/.test(p);
    if (parts.every(isSimpleToken)) {
      return parts
        .map((p) => {
          const esc = p.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
          return `^${esc}$`;
        })
        .join("|");
    }
    return trimmed;
  }

  function xAxisParamFromUrl() {
    return getQueryParam("x_axis") || getQueryParam("x-axis");
  }

  initTheme();

  let darkMode = $state(isDark());
  onThemeChange((dark) => { darkMode = dark; });

  let currentPage = $state("metrics");
  let projectName = $state(null);
  let runs = $state([]);
  let selectedRuns = $state([]);
  let smoothing = $state(10);
  let xAxis = $state("step");
  let logScaleX = $state(false);
  let logScaleY = $state(false);
  let outlierFilterEnabled = $state(false);
  let outlierFilterHead = $state(0.1);
  let outlierFilterTail = $state(0.1);
  let metricFilter = $state("");
  let realtimeEnabled = $state(true);
  let showHeaders = $state(true);
  let filterText = $state("");
  let metricColumns = $state([]);
  let requestedUrlXAxis = $state(null);
  let urlXAxisApplied = $state(false);
  let sidebarOpen = $state(true);
  let sidebarHidden = $state(false);
  let navbarHidden = $state(false);
  let hideEmptyTabs = $state(false);
  let urlTick = $state(0);
  let alerts = $state([]);
  let pollTimer = $state(null);
  let appBootstrapReady = $state(false);
  let logoUrls = $state({ light: "/static/trackio/trackio_logo_type_light_transparent.png", dark: "/static/trackio/trackio_logo_type_dark_transparent.png" });
  let plotOrder = $state([]);
  let tableTruncateLength = $state(250);
  let availableSystemDevices = $state([]);
  let selectedSystemDevices = $state([]);
  let tabAvailability = $state({});
  let tabAvailabilityRequestId = 0;
  let lastTabAvailabilityRefreshAt = 0;
  let shouldOpenFirstNonEmptyTab = false;
  let openedFirstNonEmptyTab = false;
  const TAB_AVAILABILITY_POLL_INTERVAL_MS = 15000;

  const OPTIONAL_EMPTY_TABS = new Set([
    "system",
    "traces",
    "media",
    "reports",
    "files",
  ]);
  const AUTO_OPEN_TAB_ORDER = [
    "metrics",
    "system",
    "traces",
    "media",
    "reports",
    "runs",
    "files",
  ];
  let runConfigs = $state({});

  function runKey(run) {
    return run?.id ?? run?.name;
  }

  function isXAxisAvailable(axis) {
    return axis === "step" || axis === "time" || metricColumns.includes(axis);
  }

  let selectedRunRecords = $derived(
    runs.filter((run) => selectedRuns.includes(runKey(run))),
  );

  function handleNavigate(page) {
    openedFirstNonEmptyTab = true;
    currentPage = page;
    navigateTo(page);
  }

  function isBareDashboardPath() {
    const base = window.__trackio_base || "";
    let pathname = window.location.pathname;
    if (base && pathname.startsWith(base)) {
      pathname = pathname.slice(base.length) || "/";
    }
    pathname = pathname.replace(/\/+$/, "") || "/";
    return pathname === "/";
  }

  async function refreshRuns() {
    try {
      const [data, configs] = await Promise.all([
        getRunsForProject(),
        getRunConfigs().catch(() => null),
      ]);
      const newRuns = [...(data || [])].reverse();

      if (JSON.stringify(runs) !== JSON.stringify(newRuns)) {
        const prevSelected = selectedRuns;
        const prevOrdered = runs.map(runKey);
        runs = newRuns;
        selectedRuns = reconcileSelectedRuns(
          prevSelected,
          newRuns.map(runKey),
          prevOrdered,
        );
      }
      if (configs != null) {
        runConfigs = configs;
      }
    } catch (e) {
      console.error("Failed to load runs:", e);
    }
  }

  async function refreshAlerts() {
    try {
      const data = await getAlerts(null, null, null);
      alerts = (data || []).slice(-20);
    } catch {
      // ignore
    }
  }

  function initialAvailability() {
    return {
      metrics: false,
      system: false,
      traces: false,
      media: false,
      reports: false,
      runs: false,
      files: false,
    };
  }

  async function refreshTabAvailability({ force = false } = {}) {
    const now = Date.now();
    if (
      !force &&
      now - lastTabAvailabilityRefreshAt < TAB_AVAILABILITY_POLL_INTERVAL_MS
    ) {
      return;
    }
    lastTabAvailabilityRefreshAt = now;
    const requestId = ++tabAvailabilityRequestId;

    try {
      const flags = await getTabAvailability();
      if (requestId !== tabAvailabilityRequestId) return;
      const availability = {
        ...initialAvailability(),
        ...flags,
        runs: runs.length > 0,
      };
      tabAvailability = availability;

      if (shouldOpenFirstNonEmptyTab && !openedFirstNonEmptyTab && isBareDashboardPath()) {
        const first = AUTO_OPEN_TAB_ORDER.find((page) => availability[page]);
        if (first && first !== currentPage) {
          currentPage = first;
          navigateTo(first);
        }
        openedFirstNonEmptyTab = true;
      }
    } catch (e) {
      if (requestId !== tabAvailabilityRequestId) return;
      console.error("Failed to load tab availability:", e);
      tabAvailability = initialAvailability();
    }
  }

  function startPolling() {
    if (pollTimer) clearInterval(pollTimer);
    pollTimer = setInterval(async () => {
      if (!realtimeEnabled) return;
      if (isTabHidden()) return;
      if (isRateLimitCooldownActive()) return;
      await refreshRuns();
      await refreshAlerts();
      await refreshTabAvailability();
    }, getAppPollIntervalMs());
  }

  $effect(() => {
    urlTick;
    navbarHidden = getQueryParam("navbar") === "hidden";
  });

  onMount(() => {
    const sidebarParam = getQueryParam("sidebar");
    if (sidebarParam === "hidden") {
      sidebarHidden = true;
      sidebarOpen = false;
    } else if (sidebarParam === "collapsed") {
      sidebarHidden = false;
      sidebarOpen = false;
    } else {
      sidebarHidden = false;
    }

    const smoothingParam = getQueryParam("smoothing");
    if (smoothingParam) {
      const s = parseInt(smoothingParam, 10);
      if (!Number.isNaN(s)) smoothing = s;
    }

    const xAxisParam = xAxisParamFromUrl();
    if (xAxisParam && xAxisParam.trim()) {
      requestedUrlXAxis = xAxisParam.trim();
      xAxis = requestedUrlXAxis;
    } else {
      urlXAxisApplied = true;
    }

    const metricFilterParam = getQueryParam("metric_filter");
    const metricsLegacyParam = getQueryParam("metrics");
    if (metricFilterParam) {
      metricFilter = metricFilterParam;
    } else if (metricsLegacyParam) {
      metricFilter = metricFilterFromLegacyMetricsParam(metricsLegacyParam);
    }

    if (getQueryParam("accordion") === "hidden") {
      showHeaders = false;
    }

    hideEmptyTabs = getQueryParam("hide_empty_tabs") === "true";

    shouldOpenFirstNonEmptyTab = isBareDashboardPath();
    currentPage = getPageFromPath();

    window.addEventListener("popstate", () => {
      currentPage = getPageFromPath();
      urlTick++;
    });

    (async () => {
      try {
        try {
          const settings = await getSettings();
          if (settings) {
            if (settings.logo_urls) logoUrls = settings.logo_urls;
            if (settings.color_palette) setColorPalette(settings.color_palette);
            if (settings.plot_order) plotOrder = settings.plot_order;
            if (settings.table_truncate_length) tableTruncateLength = settings.table_truncate_length;
            if (settings.media_dir) setMediaDir(settings.media_dir);
            if (settings.project_name) projectName = settings.project_name;
          }
        } catch {
          // settings endpoint may not be available
        }
        await refreshRuns();

        await refreshAlerts();
        await refreshTabAvailability({ force: true });
      } catch (e) {
        console.error("Failed to load dashboard data:", e);
      } finally {
        appBootstrapReady = true;
      }

      startPolling();
    })();

    return () => {
      if (pollTimer) clearInterval(pollTimer);
    };
  });

  $effect(() => {
    runs;
    if (appBootstrapReady) refreshTabAvailability({ force: true });
  });

  $effect(() => {
    if (urlXAxisApplied || !requestedUrlXAxis) return;
    if (isXAxisAvailable(requestedUrlXAxis)) {
      xAxis = requestedUrlXAxis;
      urlXAxisApplied = true;
    } else if (metricColumns.length > 0) {
      xAxis = "step";
      urlXAxisApplied = true;
    }
  });

  let urlRunsFromQueryApplied = $state(false);

  $effect(() => {
    if (urlRunsFromQueryApplied) return;
    if (!appBootstrapReady) return;
    const runIdsParam = getQueryParam("run_ids");
    const runsParam = getQueryParam("runs");
    if (!runIdsParam && !runsParam) {
      urlRunsFromQueryApplied = true;
      return;
    }
    if (!runs.length) {
      urlRunsFromQueryApplied = true;
      return;
    }
    let wanted;
    if (runIdsParam) {
      const ids = runIdsParam.split(",").map((s) => s.trim()).filter(Boolean);
      const validKeys = new Set(runs.map((r) => runKey(r)));
      wanted = ids.filter((id) => validKeys.has(id));
    } else {
      const names = runsParam.split(",").map((s) => s.trim()).filter(Boolean);
      const wantedNames = new Set(names);
      wanted = runs.filter((r) => wantedNames.has(r.name)).map((r) => runKey(r));
    }
    if (wanted.length) {
      selectedRuns = wanted;
    }
    urlRunsFromQueryApplied = true;
  });

  let showSidebar = $derived(
    currentPage === "metrics" ||
      currentPage === "traces" ||
      currentPage === "system" ||
      currentPage === "media" ||
      currentPage === "reports" ||
      currentPage === "runs" ||
      currentPage === "run-detail" ||
      currentPage === "files"
  );

  let sidebarVariant = $derived(
    currentPage === "runs" || currentPage === "files" ? "compact" : "full"
  );
</script>

<div class="app">
  {#if showSidebar && !sidebarHidden}
    <Sidebar
      bind:open={sidebarOpen}
      variant={sidebarVariant}
      {currentPage}
      {projectName}
      {runs}
      {runConfigs}
      bind:selectedRuns
      bind:smoothing
      bind:xAxis
      bind:logScaleX
      bind:logScaleY
      bind:outlierFilterEnabled
      bind:outlierFilterHead
      bind:outlierFilterTail
      bind:metricFilter
      bind:realtimeEnabled
      bind:showHeaders
      bind:filterText
      {metricColumns}
      {availableSystemDevices}
      bind:selectedSystemDevices
      {logoUrls}
      {darkMode}
    />
  {/if}

  <div class="main">
    {#if !navbarHidden}
      <Navbar
        {currentPage}
        {tabAvailability}
        optionalEmptyTabs={OPTIONAL_EMPTY_TABS}
        {hideEmptyTabs}
        onNavigate={handleNavigate}
      />
    {/if}

    <div class="page-content">
      {#if currentPage === "metrics"}
        <Metrics
          selectedRuns={selectedRunRecords}
          allRuns={runs}
          {smoothing}
          {xAxis}
          {logScaleX}
          {logScaleY}
          {outlierFilterEnabled}
          {outlierFilterHead}
          {outlierFilterTail}
          {metricFilter}
          {showHeaders}
          {appBootstrapReady}
          {plotOrder}
          {realtimeEnabled}
          bind:metricColumns
        />
      {:else if currentPage === "traces"}
        <Traces
          selectedRuns={selectedRunRecords}
        />
      {:else if currentPage === "system"}
        <SystemMetrics
          selectedRuns={selectedRunRecords}
          allRuns={runs}
          {smoothing}
          {appBootstrapReady}
          {realtimeEnabled}
          bind:availableDevices={availableSystemDevices}
          bind:selectedDevices={selectedSystemDevices}
        />
      {:else if currentPage === "media"}
        <Media
          selectedRuns={selectedRunRecords}
          allRuns={runs}
          {tableTruncateLength}
        />
      {:else if currentPage === "reports"}
        <Reports selectedRuns={selectedRunRecords} />
      {:else if currentPage === "runs"}
        <Runs
          {runs}
          {filterText}
          onRunsChanged={refreshRuns}
        />
      {:else if currentPage === "run-detail"}
        <RunDetail />
      {:else if currentPage === "files"}
        <Files />
      {:else if currentPage === "settings"}
        <Settings {projectName} />
      {/if}
    </div>
  </div>

  <AlertPanel {alerts} />
</div>

<style>
  :global(*) {
    margin: 0;
    padding: 0;
    box-sizing: border-box;
  }

  :global(body) {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
      "Helvetica Neue", Arial, sans-serif;
    background: var(--background-fill-primary, #fff);
    color: var(--body-text-color, #1f2937);
    font-size: var(--text-md, 14px);
    -webkit-font-smoothing: antialiased;
  }

  .app {
    display: flex;
    height: 100vh;
    overflow: hidden;
  }

  .main {
    flex: 1;
    display: flex;
    flex-direction: column;
    overflow: hidden;
    min-width: 0;
  }

  .page-content {
    flex: 1;
    overflow: hidden;
    display: flex;
    background: var(--bg-primary);
  }
</style>
