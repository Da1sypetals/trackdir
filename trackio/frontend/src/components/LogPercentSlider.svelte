<script>
  let {
    label = "",
    value = $bindable(0.1),
  } = $props();

  const LOG_MIN = Math.log10(0.01);
  const LOG_MAX = Math.log10(1);

  let logValue = $derived(Math.log10(value));

  function setFromLog(lv) {
    value = Math.pow(10, lv);
  }

  function formatPercent(p) {
    if (p >= 0.1) return `${(+p.toFixed(2))}%`;
    return `${(+p.toFixed(3))}%`;
  }
</script>

<div class="lp-slider">
  <div class="lp-head">
    {#if label}
      <span class="lp-label">{label}</span>
    {/if}
    <span class="lp-value">{formatPercent(value)}</span>
  </div>
  <div class="lp-row">
    <span class="bound">0.01%</span>
    <input
      type="range"
      class="lp-range"
      min={LOG_MIN}
      max={LOG_MAX}
      step="0.005"
      value={logValue}
      oninput={(e) => setFromLog(parseFloat(e.currentTarget.value))}
    />
    <span class="bound">1%</span>
  </div>
</div>

<style>
  .lp-slider {
    margin-top: 8px;
  }
  .lp-head {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
  }
  .lp-label {
    font-size: 12px;
    color: var(--body-text-color-subdued, #6b7280);
  }
  .lp-value {
    font-size: 12px;
    font-weight: 600;
    color: var(--body-text-color, #1f2937);
  }
  .lp-row {
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .lp-row .bound {
    font-size: 11px;
    color: var(--body-text-color-subdued, #6b7280);
    white-space: nowrap;
  }
  .lp-range {
    flex: 1;
    min-width: 0;
    accent-color: var(--color-accent, #f97316);
  }
</style>
