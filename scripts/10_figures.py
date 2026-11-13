"""Task 3.1: deck-only figures built from existing outputs (no model is refit here).

Outputs
  docs/figures/classification_summary.png   labels vs label-implied totals vs physics-detected counts
  docs/figures/exemplars_carpet_deck.png    six exemplar homes, full year, readable at slide size
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

from avangrid import config as C  # noqa: E402
from avangrid import plots as P  # noqa: E402


def classification_summary() -> None:
    k = pd.read_parquet(C.OUTPUTS / "classify.parquet")
    k = k[k.in_model]
    hp_type = k.heat_type.isin(["HP", "HP+backup"])
    rows = [
        # appliance, labeled, implied total if labels were the brief's random 25%, detected, labeled with signature
        ("EV home charging", k.label_ev.sum(), C.IMPLIED_COUNTS["ev"], k.ev_physics.sum(),
         (k.label_ev & k.ev_physics).sum() / k.label_ev.sum(), "show home charging"),
        ("Heat pump", k.label_hp.sum(), C.IMPLIED_COUNTS["hp"], hp_type.sum(),
         (k.label_hp & k.electric_heat).sum() / k.label_hp.sum(), "show electric heat"),
        ("Solar PV", k.label_pv.sum(), k.label_pv.sum(), k.pv.sum(),
         (k.label_pv & k.pv).sum() / k.label_pv.sum(), "show midday zeros"),
    ]
    fig, axes = plt.subplots(1, 3, figsize=(14, 4.4))
    for ax, (name, lab, implied, det, recall, lab_name) in zip(axes, rows):
        vals = [lab, implied, det]
        names = ["Labeled", "Implied if a\nrandom 25%" if name != "Solar PV" else "Implied\n(full list)",
                 "Detected\n(physics)"]
        bars = ax.bar(names, vals, color=[P.NEUTRAL, "#56B4E9", P.WINTER])
        for b, v in zip(bars, vals):
            ax.text(b.get_x() + b.get_width() / 2, v, f"{v:,.0f}", ha="center", va="bottom", fontsize=10)
        ax.set_title(f"{name}\n{recall:.0%} of labeled homes {lab_name}", fontsize=11)
        ax.tick_params(axis="x", labelsize=9)
        ax.set_ylim(0, max(vals) * 1.15)
        ax.grid(axis="x", visible=False)
    axes[0].set_ylabel("Customers")
    ev_ratio = rows[0][3] / rows[0][2]
    fig.suptitle(f"Labels are not a random sample: physics finds {ev_ratio:.1f}x the EV chargers the labels imply, "
                 "while PV labels match", fontweight="bold")
    fig.tight_layout()
    P.save(fig, "classification_summary")


DECK_CLASSES = ["EV, L2 charging", "Heat pump + resistance backup", "Resistive heat", "Central AC", "Pool pump",
                "Solar PV"]


def exemplars_carpet_deck() -> None:
    """The six clearest appliance signatures from the task 2.3 exemplars, sized for a slide."""
    import numpy as np
    from avangrid import app_data as A
    ex = pd.read_parquet(C.OUTPUTS / "exemplars.parquet").set_index("cls")
    fig, axes = plt.subplots(2, 3, figsize=(13, 6.4))
    starts = [0, 30, 61, 91, 122, 153, 181, 212, 242, 273, 303, 334]
    for ax, cls in zip(axes.flat, DECK_CLASSES):
        x = A.load_row(int(ex.at[cls, "customer_id"])).reshape(-1, 24).T
        im = ax.imshow(x, aspect="auto", origin="lower", cmap="viridis", vmin=0,
                       vmax=max(float(np.nanpercentile(x, 99.5)), 0.5), interpolation="nearest")
        ax.grid(False)
        ax.set_xticks(starts, ["S", "O", "N", "D", "J", "F", "M", "A", "M", "J", "J", "A"], fontsize=10)
        ax.set_yticks([0, 6, 12, 18, 23])
        ax.tick_params(labelsize=10)
        lab = " [labeled]" if bool(ex.at[cls, "picked_from_labeled"]) else ""
        ax.set_title(f"{cls}{lab}\n{int(ex.at[cls, 'class_size']):,} homes in class", fontsize=12)
        cb = fig.colorbar(im, ax=ax, pad=0.02, fraction=0.045)
        cb.ax.tick_params(labelsize=9)
        cb.set_label("kWh", fontsize=9)
    for ax in axes[:, 0]:
        ax.set_ylabel("Hour of day", fontsize=11)
    fig.tight_layout()
    P.save(fig, "exemplars_carpet_deck")


def main() -> None:
    P.style()
    classification_summary()
    exemplars_carpet_deck()


if __name__ == "__main__":
    main()
