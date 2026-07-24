<script>
  import {
    getThemePreference,
    setThemePreference,
  } from "../lib/theme.js";

  let { projectName = null } = $props();

  let themeChoice = $state(getThemePreference());
  let copiedIdx = $state(null);

  function switchTheme(value) {
    themeChoice = value;
    setThemePreference(value);
  }

  let commands = $derived.by(() => {
    const dir = projectName ? `<dir>` : `<dir>`;
    return [
      { title: "Launch dashboard", cmd: `python -m trackio.show ${dir}` },
      { title: "List runs", cmd: `trackio list runs --dir "${dir}"` },
      { title: "List metrics", cmd: `trackio list metrics --dir "${dir}" --run <run>` },
      { title: "Project summary", cmd: `trackio get project --dir "${dir}"` },
      { title: "Run summary", cmd: `trackio get run --dir "${dir}" --run <run>` },
      { title: "SQL query", cmd: `trackio query "${dir}" "SELECT * FROM metrics LIMIT 10"` },
      { title: "Delete a run", cmd: `trackio delete-run --dir "${dir}" --run <run>` },
      { title: "Rename a run", cmd: `trackio rename-run --dir "${dir}" --old-name <old> --new-name <new>` },
    ];
  });

  async function copyCommand(cmd, idx) {
    try {
      await navigator.clipboard.writeText(cmd);
      copiedIdx = idx;
      setTimeout(() => {
        if (copiedIdx === idx) copiedIdx = null;
      }, 1500);
    } catch {}
  }
</script>

<div class="settings-page">
  <h2 class="page-title">Settings</h2>

  <div class="two-col">
    <div class="col col-left">
      <section class="settings-section">
        <h3 class="section-title">Appearance</h3>
        <p class="section-desc">Choose how the dashboard looks to you.</p>
        <div class="theme-switcher">
          {#each [
            { value: "system", label: "System" },
            { value: "light", label: "Light" },
            { value: "dark", label: "Dark" },
          ] as opt}
            <button
              class="theme-option"
              class:selected={themeChoice === opt.value}
              onclick={() => switchTheme(opt.value)}
            >
              {#if opt.value === "system"}
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                  <rect x="2" y="3" width="20" height="14" rx="2" />
                  <path d="M8 21h8" />
                  <path d="M12 17v4" />
                </svg>
              {:else if opt.value === "light"}
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                  <circle cx="12" cy="12" r="4" />
                  <path d="M12 2v2" />
                  <path d="M12 20v2" />
                  <path d="M4.93 4.93l1.41 1.41" />
                  <path d="M17.66 17.66l1.41 1.41" />
                  <path d="M2 12h2" />
                  <path d="M20 12h2" />
                  <path d="M6.34 17.66l-1.41 1.41" />
                  <path d="M19.07 4.93l-1.41 1.41" />
                </svg>
              {:else}
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                  <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" />
                </svg>
              {/if}
              {opt.label}
            </button>
          {/each}
        </div>
      </section>

      <section class="settings-section">
        <h3 class="section-title">CLI Reference</h3>
        <p class="section-desc">
          Common Trackio CLI commands. Each Trackio project is a directory on disk
          containing a <code>trackio.db</code> database.
        </p>
        <div class="commands-table">
          {#each commands as cmd, i}
            <div class="command-row">
              <span class="command-label">{cmd.title}</span>
              <div class="command-value">
                <code>{cmd.cmd}</code>
                <button
                  class="copy-btn"
                  class:copied={copiedIdx === i}
                  onclick={() => copyCommand(cmd.cmd, i)}
                  title="Copy"
                >
                  {#if copiedIdx === i}
                    <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                      <path d="M3.5 8.5l3 3 6-7" />
                    </svg>
                  {:else}
                    <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
                      <rect x="5" y="5" width="8" height="8" rx="1.5" />
                      <path d="M11 5V3.5A1.5 1.5 0 009.5 2h-6A1.5 1.5 0 002 3.5v6A1.5 1.5 0 003.5 11H5" />
                    </svg>
                  {/if}
                </button>
              </div>
            </div>
          {/each}
        </div>
      </section>
    </div>
  </div>
</div>

<style>
  .settings-page {
    padding: 24px 32px;
    overflow-y: auto;
    flex: 1;
  }
  .page-title {
    color: var(--body-text-color, #1f2937);
    font-size: 18px;
    font-weight: 700;
    margin: 0 0 24px;
  }
  .two-col {
    display: grid;
    grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
    gap: 32px;
    align-items: start;
  }
  @media (max-width: 900px) {
    .two-col {
      grid-template-columns: 1fr;
    }
  }
  .settings-section {
    margin-bottom: 32px;
  }
  .section-title {
    color: var(--body-text-color, #1f2937);
    font-size: 15px;
    font-weight: 600;
    margin: 0 0 4px;
  }
  .section-desc {
    color: var(--body-text-color-subdued, #6b7280);
    font-size: var(--text-sm, 12px);
    margin: 0 0 12px;
    line-height: 1.5;
  }
  .section-desc code {
    background: var(--background-fill-secondary, #f3f4f6);
    padding: 1px 5px;
    border-radius: var(--radius-sm, 3px);
    font-size: 11px;
  }

  .theme-switcher {
    display: inline-flex;
    border: 1px solid var(--border-color-primary, #e5e7eb);
    border-radius: var(--radius-lg, 8px);
    overflow: hidden;
  }
  .theme-option {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 8px 20px;
    border: none;
    background: var(--background-fill-primary, white);
    color: var(--body-text-color-subdued, #6b7280);
    font-size: var(--text-md, 14px);
    cursor: pointer;
    transition: all 0.15s;
    border-right: 1px solid var(--border-color-primary, #e5e7eb);
  }
  .theme-option:last-child {
    border-right: none;
  }
  .theme-option:hover {
    color: var(--body-text-color, #1f2937);
    background: var(--background-fill-secondary, #f9fafb);
  }
  .theme-option.selected {
    background: var(--color-accent, #f97316);
    color: white;
    font-weight: 500;
  }

  .commands-table {
    border: 1px solid var(--border-color-primary, #e5e7eb);
    border-radius: var(--radius-lg, 8px);
    overflow: hidden;
  }
  .command-row {
    display: flex;
    align-items: center;
    gap: 16px;
    padding: 10px 14px;
    border-bottom: 1px solid var(--border-color-primary, #e5e7eb);
  }
  .command-row:last-child {
    border-bottom: none;
  }
  .command-label {
    width: 180px;
    flex-shrink: 0;
    font-size: var(--text-sm, 12px);
    color: var(--body-text-color-subdued, #6b7280);
  }
  .command-value {
    flex: 1;
    display: flex;
    align-items: center;
    gap: 8px;
    min-width: 0;
  }
  .command-value code {
    flex: 1;
    font-family: "SFMono-Regular", "Consolas", "Liberation Mono", "Menlo", monospace;
    font-size: 12px;
    color: var(--body-text-color, #1f2937);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .copy-btn {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 26px;
    height: 26px;
    flex-shrink: 0;
    border: none;
    background: none;
    border-radius: var(--radius-md, 4px);
    color: var(--body-text-color-subdued, #6b7280);
    cursor: pointer;
    transition: background-color 0.15s, color 0.15s;
  }
  .copy-btn:hover {
    background: var(--background-fill-secondary, #f3f4f6);
    color: var(--body-text-color, #1f2937);
  }
  .copy-btn.copied {
    color: var(--color-accent, #f97316);
  }
</style>
