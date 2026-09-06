import { describe, expect, test } from "vitest";
import {
  computeMetricPlotData,
  filterOutlierExtent,
  mergeMetricCatalogs,
  metricCatalogChanged,
  pinLatestAxisTicks,
  processRunData,
} from "./dataProcessing.js";

describe("processRunData smoothing", () => {
  test("does not fabricate values for rows that did not log the metric", () => {
    const logs = [
      { step: 0, "train/loss": 1.0 },
      { step: 0, "train/loss": 1.1 },
      { step: 0, "train/loss": 0.9 },
      { step: 0, epoch: 0, "train/avg_loss": 1.0 },
      { step: 1, "train/loss": 0.8 },
      { step: 1, "train/loss": 0.7 },
      { step: 1, "train/loss": 0.75 },
      { step: 1, epoch: 1, "train/avg_loss": 0.75 },
    ];

    const { rows } = processRunData(logs, "run-1", 10, "step", false, false);

    const epochSmoothed = rows.filter(
      (r) => r.data_type === "smoothed" && r.epoch != null,
    );
    expect(epochSmoothed.length).toBe(2);
    expect(epochSmoothed.map((r) => r.step).sort()).toEqual([0, 1]);
  });

  test("computeMetricPlotData returns one point per logged epoch", () => {
    const logs = [
      { step: 0, "train/loss": 1.0 },
      { step: 0, "train/loss": 1.1 },
      { step: 0, epoch: 0 },
      { step: 1, "train/loss": 0.8 },
      { step: 1, "train/loss": 0.7 },
      { step: 1, epoch: 1 },
      { step: 2, "train/loss": 0.6 },
      { step: 2, epoch: 2 },
    ];
    const { rows } = processRunData(logs, "run-1", 10, "step", false, false);
    const { data } = computeMetricPlotData(rows, "step", "epoch", null);
    const originals = data.filter((r) => r.data_type === "original");
    const smoothed = data.filter((r) => r.data_type === "smoothed");
    expect(originals.length).toBe(3);
    expect(smoothed.length).toBe(3);
  });

  test("dense metrics are still smoothed across all rows", () => {
    const logs = [
      { step: 0, loss: 1.0 },
      { step: 1, loss: 0.9 },
      { step: 2, loss: 0.8 },
      { step: 3, loss: 0.7 },
      { step: 4, loss: 0.6 },
    ];
    const { rows } = processRunData(logs, "run-1", 3, "step", false, false);
    const smoothed = rows.filter((r) => r.data_type === "smoothed");
    expect(smoothed.length).toBe(5);
    expect(smoothed.every((r) => typeof r.loss === "number")).toBe(true);
  });

  test("falls back to step when a requested custom x-axis is unavailable", () => {
    const logs = [
      { step: 0, loss: 1.0 },
      { step: 1, loss: 0.9 },
    ];

    const result = processRunData(
      logs,
      "run-1",
      0,
      "missing_axis",
      false,
      false,
    );

    expect(result.xColumn).toBe("step");
  });
});

describe("filterOutlierExtent", () => {
  const values = [1, 2, 3, 4, 5, 6, 7, 8, 9, 1000];

  test("returns full extent when filtering is off", () => {
    expect(filterOutlierExtent(values, { head: 0, tail: 0 })).toEqual([1, 1000]);
    expect(filterOutlierExtent(values)).toEqual([1, 1000]);
  });

  test("filters the top head fraction, rounding the removed count up", () => {
    // n=10, head=0.01 -> ceil(0.1)=1 point removed from the top
    expect(filterOutlierExtent(values, { head: 0.01 })).toEqual([1, 9]);
  });

  test("filters the bottom tail fraction, rounding the removed count up", () => {
    // n=10, tail=0.1 -> ceil(1)=1 point removed from the bottom
    expect(filterOutlierExtent(values, { tail: 0.1 })).toEqual([2, 1000]);
  });

  test("filters head and tail independently", () => {
    // n=10: ceil(10*0.1)=1 removed from each end
    expect(filterOutlierExtent(values, { head: 0.1, tail: 0.1 })).toEqual([2, 9]);
  });

  test("keeps at least one point when the filter would remove everything", () => {
    expect(filterOutlierExtent([5, 6], { head: 1, tail: 1 })).toEqual([5, 6]);
  });

  test("returns undefined for empty input", () => {
    expect(filterOutlierExtent([], { head: 0.01 })).toBeUndefined();
  });

  test("computeMetricPlotData applies outlier filtering to yExtent only", () => {
    const logs = [];
    for (let i = 0; i < 100; i++) logs.push({ step: i, loss: i });
    logs.push({ step: 100, loss: 100000 });

    const { rows } = processRunData(logs, "run-1", 0, "step", false, false);
    const unfiltered = computeMetricPlotData(rows, "step", "loss", null);
    expect(unfiltered.yExtent).toEqual([0, 100000]);

    // n=101 originals, head=0.01 -> ceil(1.01)=2 points removed from the top
    const filtered = computeMetricPlotData(rows, "step", "loss", null, {
      head: 0.01,
    });
    expect(filtered.yExtent).toEqual([0, 98]);
    // data points themselves are kept (only the axis range changes)
    expect(filtered.data.length).toBe(unfiltered.data.length);
  });
});

describe("mergeMetricCatalogs", () => {
  test("uses the database catalog even when subsampled logs omit a key", () => {
    expect(
      mergeMetricCatalogs(
        [["train/loss", "eval/recon_loss", "eval/disc_accuracy"]],
        ["train/loss"],
      ),
    ).toEqual(["train/loss", "eval/recon_loss", "eval/disc_accuracy"]);
  });

  test("falls back to log columns when no catalog is present", () => {
    expect(mergeMetricCatalogs([undefined], ["train/loss", "run"])).toEqual([
      "train/loss",
    ]);
  });
});

describe("metricCatalogChanged", () => {
  test("detects newly appeared metric names", () => {
    expect(metricCatalogChanged(["train/loss"], ["train/loss", "eval/recon_loss"])).toBe(
      true,
    );
    expect(metricCatalogChanged(["train/loss"], ["train/loss"])).toBe(false);
  });
});

describe("pinLatestAxisTicks", () => {
  test("always includes the latest value", () => {
    const ticks = pinLatestAxisTicks(0, 38421, 400);
    expect(ticks[ticks.length - 1]).toBe(38421);
    expect(ticks[0]).toBe(0);
    expect(ticks).toContain(30000);
  });

  test("drops the last even tick when it collides with the latest label", () => {
    const ticks = pinLatestAxisTicks(0, 31000, 400);
    expect(ticks[ticks.length - 1]).toBe(31000);
    expect(ticks).not.toContain(30000);
  });

  test("keeps a single tick when min equals max", () => {
    expect(pinLatestAxisTicks(12, 12, 400)).toEqual([12]);
  });

  test("does not duplicate the latest value when it already sits on an even tick", () => {
    const ticks = pinLatestAxisTicks(0, 10000, 400);
    expect(ticks.filter((value) => value === 10000)).toHaveLength(1);
    expect(ticks[ticks.length - 1]).toBe(10000);
  });
});
