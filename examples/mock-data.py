"""Generate mock Trackio data for manual testing.

Usage:
    python examples/mock-data.py [dir]

Then launch the dashboard:
    python -m trackio.show [dir]
"""

import math
import random
import sys
from pathlib import Path

import trackio

random.seed(42)

PROJECT_DIR = Path(sys.argv[1] if len(sys.argv) > 1 else "mock-project")
STEPS = 500


def log_training_run(name: str, base_loss: float, lr: float):
    run = trackio.init(
        dir=PROJECT_DIR,
        name=name,
        config={"lr": lr, "optimizer": "adam", "epochs": STEPS},
    )
    for step in range(STEPS):
        progress = step / STEPS
        loss = base_loss * math.exp(-3 * progress) + random.gauss(0, 0.02)
        acc = 1.0 - base_loss * math.exp(-3 * progress) + random.gauss(0, 0.01)

        # occasional wild outliers to exercise the outlier filter
        if step in (100, 250, 400):
            loss += 50.0
        if step == 320:
            acc -= 30.0

        trackio.log(
            {
                "train/loss": loss,
                "train/acc": acc,
                "val/loss": loss * 1.1 + 0.05,
                "reward": 100.0 + 50.0 * math.sin(progress * 6) + random.gauss(0, 2),
                "explained_variance": -2.0 + 1.8 * progress + random.gauss(0, 0.05),
            },
            step=step,
        )
    trackio.finish()
    print(f"  run '{name}' done ({STEPS} steps)")


def main():
    print(f"* writing mock data to: {PROJECT_DIR.resolve()}")
    log_training_run("baseline", base_loss=2.0, lr=1e-3)
    log_training_run("high-lr", base_loss=3.0, lr=5e-3)
    log_training_run("low-lr", base_loss=1.5, lr=1e-4)
    print(f"* done. view with: python -m trackio.show {PROJECT_DIR}")


if __name__ == "__main__":
    main()
