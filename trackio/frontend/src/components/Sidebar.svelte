<script>
  import ColoredCheckbox from "./ColoredCheckbox.svelte";
  import Dropdown from "./Dropdown.svelte";
  import GradioCheckbox from "./GradioCheckbox.svelte";
  import GradioSlider from "./GradioSlider.svelte";
  import GradioTextbox from "./GradioTextbox.svelte";
  import LogPercentSlider from "./LogPercentSlider.svelte";
  import { buildColorMap, getColorForIndex } from "../lib/stores.js";
  import { latestOnlySelection } from "../lib/selection.js";
  import { filterMetricsByRegex } from "../lib/dataProcessing.js";
  import { computeGroupByOptions, computeGroupedRuns } from "../lib/grouping.js";

  let {
    open = $bindable(true),
    variant = "full",
    currentPage = "metrics",
    projectName = null,
    runs = [],
    selectedRuns = $bindable([]),
    availableSystemDevices = [],
    selectedSystemDevices = $bindable([]),
    smoothing = $bindable(10),
    xAxis = $bindable("step"),
    logScaleX = $bindable(false),
    logScaleY = $bindable(false),
    outlierFilterEnabled = $bindable(false),
    outlierFilterHead = $bindable(0.1),
    outlierFilterTail = $bindable(0.1),
    metricFilter = $bindable(""),
    realtimeEnabled = $bindable(true),
    showHeaders = $bindable(true),
    filterText = $bindable(""),
    runConfigs = {},
    metricColumns = [],
    logoUrls = { light: "/static/trackio/trackio_logo_type_light_transparent.png", dark: "/static/trackio/trackio_logo_type_dark_transparent.png" },
    darkMode = false,
  } = $props();

  let availableXAxes = $derived.by(() => {
    let axes = ["step", "time", ...metricColumns];
    return axes;
  });

  function toggleSidebar() {
    open = !open;
  }

  function setIndeterminate(node, value) {
    node.indeterminate = value;
    return {
      update(newValue) {
        node.indeterminate = newValue;
      },
    };
  }

  let filteredRuns = $derived.by(() => {
    if (!filterText || !filterText.trim()) return runs;
    const matches = new Set(filterMetricsByRegex(runs.map((r) => r.name), filterText));
    return runs.filter((r) => matches.has(r.name));
  });

  let runColorMap = $derived(buildColorMap(runs));
  let filteredRunIds = $derived(filteredRuns.map((r) => r.id ?? r.name));

  let groupByRaw = $state(null);

  $effect(() => {
    if (!groupByOptions.some((o) => o.value === groupByRaw)) {
      groupByRaw = null;
    }
  });

  let groupByOptions = $derived(computeGroupByOptions(runConfigs));
  let groupedRuns = $derived(computeGroupedRuns(filteredRuns, runConfigs, groupByRaw));

  let latestOnly = $state(false);

  function toggleLatestOnly() {
    latestOnly = !latestOnly;
    if (latestOnly && filteredRuns.length > 0) {
      selectedRuns = latestOnlySelection(filteredRunIds);
    } else if (!latestOnly) {
      selectedRuns = [...filteredRunIds];
    }
  }

  $effect(() => {
    if (!latestOnly || filteredRunIds.length === 0) return;
    const desired = latestOnlySelection(filteredRunIds);
    if (
      selectedRuns.length !== desired.length ||
      selectedRuns[0] !== desired[0]
    ) {
      selectedRuns = desired;
    }
  });

  function toggleDevice(device) {
    if (selectedSystemDevices.includes(device)) {
      selectedSystemDevices = selectedSystemDevices.filter((d) => d !== device);
    } else {
      selectedSystemDevices = [...selectedSystemDevices, device];
    }
  }
</script>

<div class="sidebar" class:collapsed={!open}>
  <button class="toggle-btn" onclick={toggleSidebar}>
    {#if open}
      <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
        <path d="M10 12L6 8L10 4" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
      </svg>
    {:else}
      <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
        <path d="M6 4L10 8L6 12" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
      </svg>
    {/if}
  </button>

  {#if open}
    <div class="sidebar-content">
      <div class="sidebar-scroll">
      <div class="logo-section">
        <img
          src={darkMode ? logoUrls.dark : logoUrls.light}
          alt="Trackio"
          class="logo"
        />
      </div>

      <div class="section">
        <div class="section-label">Project</div>
        <div class="locked-project">{projectName ?? "—"}</div>
      </div>

      {#if variant === "compact" && currentPage === "runs"}
        <div class="section">
          <GradioTextbox
            label="Run Filter"
            info="Filter runs using regex patterns. Leave empty to show all runs."
            placeholder="e.g., baseline|exp-.*|v2"
            bind:value={filterText}
          />
        </div>
      {/if}

      {#if variant === "full"}
        <div class="section">
          <div class="runs-header">
            <label class="select-all-label">
              <input
                type="checkbox"
                class="select-all-cb"
                checked={selectedRuns.length === filteredRunIds.length && filteredRunIds.length > 0}
                use:setIndeterminate={selectedRuns.length > 0 && selectedRuns.length < filteredRunIds.length}
                onchange={() => {
                  if (selectedRuns.length === filteredRunIds.length) {
                    selectedRuns = [];
                  } else {
                    selectedRuns = [...filteredRunIds];
                  }
                  latestOnly = false;
                }}
              />
              <span class="section-label">Runs ({filteredRunIds.length})</span>
            </label>
            <label class="latest-toggle">
              <span>Latest only</span>
              <input
                type="checkbox"
                checked={latestOnly}
                onchange={toggleLatestOnly}
              />
            </label>
          </div>
          <GradioTextbox
            bind:value={filterText}
            placeholder="Type to filter..."
            showLabel={false}
          />
          <div class="group-by-row">
            <Dropdown
              label="Group by"
              choices={groupByOptions}
              bind:value={groupByRaw}
              filterable={false}
            />
          </div>
          {#if groupedRuns}
            <div class="grouped-runs">
              {#each [...groupedRuns.entries()] as [groupLabel, groupRunList]}
                {@const groupIds = groupRunList.map((r) => r.id ?? r.name)}
                {@const groupSelected = groupIds.filter((id) => selectedRuns.includes(id))}
                <div class="run-group-section">
                  <div class="run-group-header">
                    <label class="select-all-label">
                      <input
                        type="checkbox"
                        class="select-all-cb"
                        checked={groupSelected.length === groupIds.length && groupIds.length > 0}
                        use:setIndeterminate={groupSelected.length > 0 && groupSelected.length < groupIds.length}
                        onchange={() => {
                          if (groupSelected.length === groupIds.length) {
                            selectedRuns = selectedRuns.filter((id) => !groupIds.includes(id));
                          } else {
                            const toAdd = groupIds.filter((id) => !selectedRuns.includes(id));
                            selectedRuns = [...selectedRuns, ...toAdd];
                          }
                          latestOnly = false;
                        }}
                      />
                      <span class="run-group-label" title={groupLabel}>{groupLabel}</span>
                      <span class="run-group-count">({groupRunList.length})</span>
                    </label>
                  </div>
                  <div class="checkbox-list group-checkbox-list">
                    <ColoredCheckbox
                      choices={groupRunList}
                      bind:selected={selectedRuns}
                      getKey={(run) => run.id ?? run.name}
                      getLabel={(run) => run.name}
                      colors={groupRunList.map(
                        (r) => runColorMap[r.id ?? r.name] ?? getColorForIndex(Math.max(0, runs.indexOf(r))),
                      )}
                      ontoggle={() => { latestOnly = false; }}
                    />
                  </div>
                </div>
              {/each}
            </div>
          {:else}
            <div class="checkbox-list">
              <ColoredCheckbox
                choices={filteredRuns}
                bind:selected={selectedRuns}
                getKey={(run) => run.id ?? run.name}
                getLabel={(run) => run.name}
                colors={filteredRuns.map(
                (r) => runColorMap[r.id ?? r.name] ?? getColorForIndex(Math.max(0, runs.indexOf(r))),
              )}
                ontoggle={() => { latestOnly = false; }}
              />
            </div>
          {/if}
          {#if currentPage === "system" && availableSystemDevices.length > 0}
            <div class="device-group">
              <label class="select-all-label">
                <input
                  type="checkbox"
                  class="select-all-cb"
                  checked={selectedSystemDevices.length === availableSystemDevices.length && availableSystemDevices.length > 0}
                  use:setIndeterminate={selectedSystemDevices.length > 0 && selectedSystemDevices.length < availableSystemDevices.length}
                  onchange={() => {
                    if (selectedSystemDevices.length === availableSystemDevices.length) {
                      selectedSystemDevices = [];
                    } else {
                      selectedSystemDevices = [...availableSystemDevices];
                    }
                  }}
                />
                <span class="section-sublabel">Devices ({availableSystemDevices.length})</span>
              </label>
              <div class="checkbox-group">
                {#each availableSystemDevices as device}
                  <label class="checkbox-item">
                    <input
                      type="checkbox"
                      checked={selectedSystemDevices.includes(device)}
                      onchange={() => toggleDevice(device)}
                    />
                    <span class="run-name" title={device}>{device}</span>
                  </label>
                {/each}
              </div>
            </div>
          {/if}
        </div>

        {#if currentPage === "metrics" || currentPage === "system"}
          <span class="section-label">Display Settings</span>

          <div class="section">
            <GradioCheckbox
              label="Refresh metrics realtime"
              bind:checked={realtimeEnabled}
            />
            <GradioCheckbox
              label="Show section headers"
              bind:checked={showHeaders}
            />
          </div>

          <div class="section">
            <GradioSlider
              label="Smoothing Factor (0 = no smoothing)"
              bind:value={smoothing}
              min={0}
              max={20}
              step={1}
            />
          </div>

          <div class="section">
            <Dropdown
              label="X-axis"
              choices={availableXAxes}
              bind:value={xAxis}
              filterable={false}
            />
            <GradioCheckbox
              label="Log scale X-axis"
              bind:checked={logScaleX}
            />
            <GradioCheckbox
              label="Log scale Y-axis"
              bind:checked={logScaleY}
            />
          </div>

          <div class="section">
            <GradioTextbox
              label="Metric Filter"
              info="Filter metrics using regex patterns. Leave empty to show all metrics."
              placeholder="e.g., loss|ndcg@10|gpu"
              bind:value={metricFilter}
            />
          </div>

          {#if currentPage === "metrics"}
            <div class="section">
              <GradioCheckbox
                label="Outlier filter"
                bind:checked={outlierFilterEnabled}
              />
              {#if outlierFilterEnabled}
                <LogPercentSlider label="Top (head)" bind:value={outlierFilterHead} />
                <LogPercentSlider label="Bottom (tail)" bind:value={outlierFilterTail} />
              {/if}
            </div>
          {/if}
        {/if}
      {/if}
      </div>

    </div>
  {/if}
</div>

<style>
  .sidebar {
    width: 290px;
    min-width: 290px;
    background: var(--background-fill-primary, white);
    border-right: 1px solid var(--border-color-primary, #e5e7eb);
    display: flex;
    flex-direction: column;
    position: relative;
    overflow: hidden;
    transition: width 0.2s, min-width 0.2s;
  }
  .sidebar.collapsed {
    width: 40px;
    min-width: 40px;
  }
  .toggle-btn {
    position: absolute;
    top: 12px;
    right: 8px;
    z-index: 10;
    border: none;
    background: none;
    color: var(--body-text-color-subdued, #9ca3af);
    cursor: pointer;
    padding: 4px;
    display: flex;
    align-items: center;
    justify-content: center;
    border-radius: var(--radius-sm, 4px);
    transition: color 0.15s, background-color 0.15s;
  }
  .toggle-btn:hover {
    color: var(--body-text-color, #1f2937);
    background-color: var(--background-fill-secondary, #f9fafb);
  }
  .sidebar-content {
    padding: 16px;
    flex: 1;
    min-height: 0;
    display: flex;
    flex-direction: column;
  }
  .sidebar-scroll {
    overflow-y: auto;
    flex: 1;
    min-height: 0;
  }
  .logo-section {
    margin-bottom: 20px;
  }
  .logo {
    width: 80%;
    max-width: 200px;
  }
  .section {
    margin-top: 2px;
    margin-bottom: 18px;
  }
  .section-label {
    font-size: 13px;
    font-weight: 500;
    color: var(--body-text-color-subdued, #6b7280);
  }
  .locked-project {
    margin-top: 4px;
    font-size: 13px;
    font-weight: 500;
    color: var(--body-text-color, #1f2937);
    padding: 8px 10px;
    border: 1px solid var(--border-color-primary, #e5e7eb);
    border-radius: var(--radius-md, 6px);
    background: var(--background-fill-secondary, #f9fafb);
  }
  .runs-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 6px;
  }
  .select-all-label {
    display: flex;
    align-items: center;
    gap: 6px;
    cursor: pointer;
  }
  .select-all-cb {
    appearance: none;
    -webkit-appearance: none;
    width: 16px;
    height: 16px;
    border: 1px solid var(--checkbox-border-color, #d1d5db);
    border-radius: var(--checkbox-border-radius, 4px);
    background-color: var(--checkbox-background-color, white);
    cursor: pointer;
    flex-shrink: 0;
    position: relative;
    transition: background-color 0.15s, border-color 0.15s;
  }
  .select-all-cb:checked {
    background-color: var(--checkbox-background-color-selected, var(--color-accent, #f97316));
    border-color: var(--checkbox-background-color-selected, var(--color-accent, #f97316));
    background-image: var(--checkbox-check);
  }
  .select-all-cb:indeterminate {
    background-color: var(--checkbox-background-color-selected, var(--color-accent, #f97316));
    border-color: var(--checkbox-background-color-selected, var(--color-accent, #f97316));
    background-image: url("data:image/svg+xml,%3Csvg viewBox='0 0 16 16' fill='white' xmlns='http://www.w3.org/2000/svg'%3E%3Crect x='3' y='7' width='10' height='2' rx='1'/%3E%3C/svg%3E");
    background-size: 12px;
    background-position: center;
    background-repeat: no-repeat;
  }
  .latest-toggle {
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 12px;
    color: var(--body-text-color-subdued, #6b7280);
    cursor: pointer;
  }
  .latest-toggle input[type="checkbox"] {
    appearance: none;
    -webkit-appearance: none;
    width: 16px;
    height: 16px;
    margin: 0;
    border: 1px solid var(--checkbox-border-color, #d1d5db);
    border-radius: var(--checkbox-border-radius, 4px);
    background-color: var(--checkbox-background-color, white);
    box-shadow: var(--checkbox-shadow);
    cursor: pointer;
    flex-shrink: 0;
    transition: background-color 0.15s, border-color 0.15s;
  }
  .latest-toggle input[type="checkbox"]:checked {
    background-image: var(--checkbox-check);
    background-color: var(--checkbox-background-color-selected, #f97316);
    border-color: var(--checkbox-border-color-selected, #f97316);
  }
  .checkbox-list {
    max-height: 300px;
    overflow-y: auto;
    margin-top: 8px;
  }
  .device-group {
    margin-top: 14px;
    padding-top: 12px;
    border-top: 1px solid var(--border-color-primary, #e5e7eb);
  }
  .section-sublabel {
    font-size: 12px;
    font-weight: 600;
    color: var(--body-text-color-subdued, #6b7280);
  }
  .checkbox-group {
    display: flex;
    flex-direction: column;
    margin-top: 8px;
  }
  .checkbox-item {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 3px 0;
    cursor: pointer;
    font-size: 13px;
  }
  .checkbox-item input[type="checkbox"] {
    appearance: none;
    -webkit-appearance: none;
    width: 16px;
    height: 16px;
    margin: 0;
    border: 1px solid var(--checkbox-border-color, #d1d5db);
    border-radius: var(--checkbox-border-radius, 4px);
    background-color: var(--checkbox-background-color, white);
    box-shadow: var(--checkbox-shadow);
    cursor: pointer;
    flex-shrink: 0;
    transition: background-color 0.15s, border-color 0.15s;
  }
  .checkbox-item input[type="checkbox"]:checked {
    background-image: var(--checkbox-check);
    background-color: var(--checkbox-background-color-selected, #f97316);
    border-color: var(--checkbox-border-color-selected, #f97316);
  }
  .checkbox-item input[type="checkbox"]:hover {
    border-color: var(--checkbox-border-color-hover, #d1d5db);
  }
  .run-name {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    color: var(--body-text-color, #1f2937);
  }
  .group-by-row {
    margin-top: 8px;
  }
  .grouped-runs {
    margin-top: 8px;
    display: flex;
    flex-direction: column;
    gap: 6px;
  }
  .run-group-section {
    border: 1px solid var(--border-color-primary, #e5e7eb);
    border-radius: var(--radius-md, 6px);
    overflow: hidden;
  }
  .run-group-header {
    display: flex;
    align-items: center;
    padding: 6px 10px;
    background: var(--background-fill-secondary, #f9fafb);
    border-bottom: 1px solid var(--border-color-primary, #e5e7eb);
  }
  .run-group-label {
    font-size: 12px;
    font-weight: 600;
    color: var(--body-text-color, #1f2937);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    max-width: 160px;
  }
  .run-group-count {
    font-size: 11px;
    color: var(--body-text-color-subdued, #9ca3af);
    margin-left: 4px;
    flex-shrink: 0;
  }
  .group-checkbox-list {
    padding: 4px 10px;
    max-height: none;
  }
</style>
