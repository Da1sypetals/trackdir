export function createDashboardState() {
  let runs = $state([]);
  let selectedRuns = $state([]);
  let runColors = $state({});
  let runConfigs = $state({});
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
  let sidebarOpen = $state(true);
  let currentPage = $state("metrics");
  let loading = $state(false);
  let theme = $state("default");

  return {
    get runs() {
      return runs;
    },
    set runs(v) {
      runs = v;
    },
    get selectedRuns() {
      return selectedRuns;
    },
    set selectedRuns(v) {
      selectedRuns = v;
    },
    get runColors() {
      return runColors;
    },
    set runColors(v) {
      runColors = v;
    },
    get runConfigs() {
      return runConfigs;
    },
    set runConfigs(v) {
      runConfigs = v;
    },
    get smoothing() {
      return smoothing;
    },
    set smoothing(v) {
      smoothing = v;
    },
    get xAxis() {
      return xAxis;
    },
    set xAxis(v) {
      xAxis = v;
    },
    get logScaleX() {
      return logScaleX;
    },
    set logScaleX(v) {
      logScaleX = v;
    },
    get logScaleY() {
      return logScaleY;
    },
    set logScaleY(v) {
      logScaleY = v;
    },
    get outlierFilterEnabled() {
      return outlierFilterEnabled;
    },
    set outlierFilterEnabled(v) {
      outlierFilterEnabled = v;
    },
    get outlierFilterHead() {
      return outlierFilterHead;
    },
    set outlierFilterHead(v) {
      outlierFilterHead = v;
    },
    get outlierFilterTail() {
      return outlierFilterTail;
    },
    set outlierFilterTail(v) {
      outlierFilterTail = v;
    },
    get metricFilter() {
      return metricFilter;
    },
    set metricFilter(v) {
      metricFilter = v;
    },
    get realtimeEnabled() {
      return realtimeEnabled;
    },
    set realtimeEnabled(v) {
      realtimeEnabled = v;
    },
    get showHeaders() {
      return showHeaders;
    },
    set showHeaders(v) {
      showHeaders = v;
    },
    get filterText() {
      return filterText;
    },
    set filterText(v) {
      filterText = v;
    },
    get sidebarOpen() {
      return sidebarOpen;
    },
    set sidebarOpen(v) {
      sidebarOpen = v;
    },
    get currentPage() {
      return currentPage;
    },
    set currentPage(v) {
      currentPage = v;
    },
    get loading() {
      return loading;
    },
    set loading(v) {
      loading = v;
    },
    get theme() {
      return theme;
    },
    set theme(v) {
      theme = v;
    },
  };
}

export const DEFAULT_COLORS = [
  "#A8769B",
  "#E89957",
  "#3B82F6",
  "#10B981",
  "#EF4444",
  "#8B5CF6",
  "#14B8A6",
  "#F59E0B",
  "#EC4899",
  "#06B6D4",
];

let _colorPalette = DEFAULT_COLORS;

export function setColorPalette(palette) {
  if (Array.isArray(palette) && palette.length > 0) {
    _colorPalette = palette;
  }
}

export function getColorForIndex(i) {
  return _colorPalette[i % _colorPalette.length];
}

export function buildColorMap(runs) {
  const map = {};
  runs.forEach((run, i) => {
    const key =
      typeof run === "string" ? run : (run?.id ?? run?.name ?? String(i));
    map[key] = getColorForIndex(i);
  });
  return map;
}
