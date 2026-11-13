"""Task 2.3: NMF load decomposition ("electrification fingerprint") + exemplar customers per appliance class.

Outputs
  outputs/nmf_mix.parquet                    per customer: share and kWh of each component (gitignored)
  outputs/exemplars.parquet                  exemplar customer ids, for the app (gitignored)
  outputs/summary/nmf_k_selection.csv        error, fit and odd/even reproducibility per k
  outputs/summary/nmf_components.csv         288-cell component shapes + descriptions
  outputs/summary/nmf_associations.csv       AUC of every component weight vs every appliance flag / label
  outputs/summary/nmf_mix_by_segment.csv     mean mix per business segment and label group
  outputs/summary/exemplars.csv              exemplar selection, anonymised (no customer ids)
  docs/decomposition.md, docs/figures/nmf_components.png, nmf_fingerprint.png,
  exemplars_carpet.png, exemplars_heating.png, exemplars_ev.png
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from avangrid import config as C  # noqa: E402
from avangrid import decompose as D  # noqa: E402
from avangrid import io  # noqa: E402
from avangrid import plots as P  # noqa: E402
from avangrid import segment as S  # noqa: E402
from avangrid.features.weather import daily_kwh, daily_weather  # noqa: E402

lines: list[str] = []


def emit(s: str = "") -> None:
    print(s)
    lines.append(s)


def md(df: pd.DataFrame, index: bool = False) -> None:
    emit(df.to_markdown(index=index, floatfmt=".3g"))
    emit()


def month_ticks(ax, axis: str = "x") -> None:
    starts = [0, 30, 61, 91, 122, 153, 181, 212, 242, 273, 303, 334]   # day index of each month start, Sep 1 = 0
    if axis == "x":
        ax.set_xticks(starts)
        ax.set_xticklabels([m[0] for m in D.MONTH_ABBR], fontsize=7)


def main() -> None:
    t0 = time.time()
    P.style()
    f_all = pd.read_parquet(C.FEATURES)
    k_all = pd.read_parquet(C.OUTPUTS / "classify.parquet")
    seg_all = pd.read_parquet(C.OUTPUTS / "segments.parquet")[["customer_id", "segment"]]
    kcols = [c for c in k_all.columns if c not in f_all.columns or c == "customer_id"]
    df_all = f_all.merge(k_all[kcols], on="customer_id", how="left").merge(seg_all, on="customer_id", how="left")
    df = df_all[df_all.in_model].reset_index(drop=True)
    n = len(df)
    pos = io.customer_position()
    rows = df.customer_id.map(pos).to_numpy()

    mat = io.load_matrix()
    hours = io.load_hours()
    sub = np.array(mat[rows], dtype=np.float32)
    prof, filled = D.month_hour_profiles(sub, hours)
    Sx = D.to_shape(prof)
    odd = hours.odd_week.to_numpy()
    halves = {}
    for name, m in (("odd", odd), ("even", ~odd)):
        x = sub.copy()
        x[:, ~m] = np.nan
        halves[name] = D.to_shape(D.month_hour_profiles(x, hours)[0])
        del x

    # ------------------------------------------------------------ choose k (label-free)
    sel = []
    for kk in D.K_GRID:
        W, H, err = D.fit_nmf(Sx, kk)
        _, Ha, _ = D.fit_nmf(halves["odd"], kk)
        _, Hb, _ = D.fit_nmf(halves["even"], kk)
        _, cos_ab = D.match_components(Ha, Hb)
        sel.append({"k": kk, "relative_error": err, "r2_vs_mean_shape": D.r2_vs_mean_shape(Sx, W, H),
                    "odd_even_min_cosine": cos_ab.min(), "odd_even_mean_cosine": cos_ab.mean()})
    sel = pd.DataFrame(sel)
    ok = sel[(sel.odd_even_min_cosine >= D.STABLE_COSINE) & (sel.k <= D.K_MAX_READABLE)]
    K = int(ok.k.max()) if len(ok) else 6
    sel.to_csv(C.OUTPUTS_SUMMARY / "nmf_k_selection.csv", index=False)

    # ------------------------------------------------------------ final fit, ordered by energy share
    W, H, err = D.fit_nmf(Sx, K)
    M = D.mix(W)
    order = np.argsort(-M.mean(axis=0))
    W, H, M = W[:, order], H[order], M[:, order]
    pop_share = (M * df.annual_kwh.to_numpy()[:, None]).sum(axis=0) / df.annual_kwh.sum()
    # reproducibility per component: refit on each half, match to the full fit, compare shapes and weights
    Wa, Ha, _ = D.fit_nmf(halves["odd"], K)
    Wb, Hb, _ = D.fit_nmf(halves["even"], K)
    oa, cos_a = D.match_components(H, Ha)
    ob, cos_b = D.match_components(H, Hb)
    Ma, Mb = D.mix(Wa)[:, oa], D.mix(Wb)[:, ob]
    w_corr = [np.corrcoef(Ma[:, j], Mb[:, j])[0, 1] for j in range(K)]

    desc = [D.describe_component(H[j]) for j in range(K)]
    assoc = D.associations(M, D.appliance_targets(df))
    names = D.name_components(assoc, desc)
    names["energy_share"] = pop_share
    names["mean_customer_share"] = M.mean(axis=0)
    names["cos_vs_odd_fit"] = cos_a
    names["cos_vs_even_fit"] = cos_b
    names["weight_corr_odd_even"] = w_corr
    names = pd.concat([names, pd.DataFrame(desc)], axis=1)
    assoc.to_csv(C.OUTPUTS_SUMMARY / "nmf_associations.csv", index=False)
    comp = pd.DataFrame(H, columns=[f"{m}_{h:02d}" for m in D.MONTH_ABBR for h in range(24)])
    pd.concat([names, comp], axis=1).to_csv(C.OUTPUTS_SUMMARY / "nmf_components.csv", index=False)
    labels = [f"C{j}" for j in range(K)]
    short = [f"{d['season']} {'all day' if d['window_hours'] >= 12 else d['half_energy_window']}" for d in desc]

    mix_df = pd.DataFrame(M, columns=[f"share_{c}" for c in labels])
    for j, c in enumerate(labels):
        mix_df[f"kwh_{c}"] = M[:, j] * df.annual_kwh.to_numpy()
    mix_df.insert(0, "customer_id", df.customer_id.to_numpy())
    mix_df["recon_error"] = np.linalg.norm(Sx - W @ H, axis=1) / np.linalg.norm(Sx, axis=1)
    mix_df.to_parquet(C.OUTPUTS / "nmf_mix.parquet", index=False)

    groups = {"All modeled customers": np.ones(n, bool), "EV label": df.label_ev.to_numpy(),
              "HP label": df.label_hp.to_numpy(), "PV list": df.label_pv.to_numpy()}
    gm = pd.DataFrame({g: M[m].mean(axis=0) for g, m in groups.items()}, index=labels).T
    gm.insert(0, "customers", [int(m.sum()) for m in groups.values()])
    sm = pd.DataFrame(M, columns=labels).assign(segment=df.segment.to_numpy()).groupby("segment")
    seg_mix = sm.mean()
    seg_mix.insert(0, "customers", sm.size())
    seg_mix = seg_mix.reindex([s for s in S.SEGMENT_ORDER if s in seg_mix.index])
    by_seg = pd.concat([gm, seg_mix]).rename_axis("group")
    by_seg.to_csv(C.OUTPUTS_SUMMARY / "nmf_mix_by_segment.csv")

    # ------------------------------------------------------------ exemplars
    ex = pd.DataFrame([D.pick_exemplar(df, spec) for spec in D.exemplar_classes(df)])
    ex.to_parquet(C.OUTPUTS / "exemplars.parquet", index=False)
    ex.drop(columns="customer_id").to_csv(C.OUTPUTS_SUMMARY / "exemplars.csv", index=False)
    ex_rows = ex.customer_id.map(pos).to_numpy()
    ex_mat = np.array(mat[ex_rows], dtype=np.float32)
    del sub

    # ------------------------------------------------------------ report
    emit("# Load decomposition and exemplars (task 2.3)")
    emit()
    emit(f"Generated by `scripts/06b_decompose.py`. Modeling population {n:,} customers.")
    emit()
    emit("## NMF load decomposition")
    emit()
    emit("K-Means archetypes (docs/segmentation.md) force every home into one group, but load shapes form a "
         "continuum. Non-negative matrix factorisation instead writes each home's shape as a **mix of parts**:")
    emit()
    emit("$$S_i \\approx \\sum_j w_{ij} H_j, \\quad w_{ij} \\ge 0, \\quad \\textstyle\\sum H_j = 1$$")
    emit()
    emit("$S_i$ = customer *i*'s mean load in each of 12 months x 24 hours (288 cells), scaled to sum to 1. "
         "Because both $S_i$ and each part $H_j$ sum to 1, $w_{ij}$ is about the share of the home's annual "
         "energy that part *j* explains, and $w_{ij} \\times$ annual kWh is the kWh it explains. "
         f"Months with no readings take the customer's own mean for that hour ({(filled > 0).mean():.1%} of "
         f"customers have any such cell; {filled.mean():.2%} of cells overall).")
    emit()
    emit("**Choice of k (label-free).** NMF is refit separately on odd and even weeks (both halves cover every "
         "season) and the two sets of parts are matched (Hungarian assignment on cosine similarity). The rule, "
         f"set before looking at appliance labels: the largest k ≤ {D.K_MAX_READABLE} at which *every* part is "
         f"found again in the other half (cosine ≥ {D.STABLE_COSINE}).")
    emit()
    md(sel)
    emit(f"→ **k = {K}**: relative error {err:.3f}, reproduces {D.r2_vs_mean_shape(Sx, W, H):.0%} of the "
         "between-customer shape variation. Beyond it a part appears in one half only (split noise, not structure).")
    emit()
    emit("**Parts, described from their own shape first**, then compared with appliance flags and labels. "
         f"A part is named after the flag its weight ranks best, if that AUC ≥ {D.ASSOC_AUC}; otherwise it is "
         "reported as unexplained. Energy share = share of the population's kWh the part explains. Reproducibility: "
         "cosine of the part against the odd-week and even-week refits, and the correlation of customers' weights "
         "between the two halves.")
    emit()
    md(names[["component", "label_free", "name", "best_match", "best_auc", "energy_share", "mean_customer_share",
              "cos_vs_odd_fit", "cos_vs_even_fit", "weight_corr_odd_even"]])
    piv = assoc.pivot(index="target", columns="component", values="auc").round(2)
    piv.columns = labels
    emit("AUC of each part's weight for each flag (0.5 = unrelated, below 0.5 = flagged homes have *less* of it):")
    emit()
    md(piv.reset_index())
    # ---- reading the parts (computed, so it stays true on rerun)
    emit("**Reading the parts.**")
    emit()
    heat = [j for j in range(K) if names.explained[j] and "heat" in names.best_match[j]]
    if heat:
        hs = by_seg[[labels[j] for j in heat]].sum(axis=1)
        emit(f"- **Heating** splits into {len(heat)} part(s) ({', '.join(f'C{j} {short[j]}' for j in heat)}): together "
             f"{hs.get('Electric-heat home', np.nan):.0%} of an electric-heat home's energy vs "
             f"{hs.get('Typical', np.nan):.0%} for a typical home, and {hs.get('HP label', np.nan):.0%} for labeled HP homes.")
    l2_best = assoc[assoc.target == "EV L2 signature"].auc.max()
    emit(f"- **L2 EV charging has no part** (best AUC {l2_best:.2f}): ~2 blocks of ~3 h a week vanish in monthly "
         "averages. L2 detection stays with the event detector (docs/classification.md); NMF complements it, "
         "it does not replace it.")
    pvj = [j for j in range(K) if names.explained[j] and names.best_match[j] == "PV signature"]
    for j in pvj:
        emit(f"- **C{j} ({short[j]}) ranks PV homes (AUC {names.best_auc[j]:.2f}) because it is an evening part**: "
             "delivered-only metering clips PV homes' midday load to 0, which shifts their *measured* energy to the "
             "evening. It is a metering artefact, not an appliance.")
    l1j = [j for j in range(K) if names.explained[j] and names.best_match[j] == "EV L1 signature"]
    for j in l1j:
        emit(f"- **C{j} ({short[j]}) matches L1 EV charging (AUC {names.best_auc[j]:.2f})**, but it peaks in summer, "
             "so overnight cooling probably contributes too; the L1 detector itself only uses mild nights.")
    for j in range(K):
        if not names.explained[j]:
            emit(f"- **C{j} ({short[j]}) is unexplained**: closest flag {names.best_match[j]} at AUC "
                 f"{names.best_auc[j]:.2f}, below the {D.ASSOC_AUC} bar.")
    emg = by_seg.loc["Emerging load", labels] - by_seg.loc["Typical", labels] if "Emerging load" in by_seg.index else None
    if emg is not None:
        up = emg.sort_values(ascending=False).index[:2]
        emit(f"- **Emerging-load homes** carry more of {up[0]} ({short[labels.index(up[0])]}, "
             f"{emg[up[0]]:+.0%}) and {up[1]} ({short[labels.index(up[1])]}, {emg[up[1]]:+.0%}) than typical homes.")
    emit()
    emit("Mean mix (share of each customer's energy) by label group and business segment:")
    emit()
    md(by_seg.reset_index())

    emit("## Exemplar customers per appliance class")
    emit()
    emit("One exemplar per class, picked by a fixed **medoid rule** so the plots show a *typical* member, not the "
         "most extreme one: among class members with ≥ 350 days of data, not very-high-load and (outside the PV "
         "classes) not PV, take the customer closest to the class median on the class's defining features "
         "(robust z-score: distance / IQR). Where labels exist (EV, HP, PV) and at least 3 labeled members qualify, "
         "the exemplar is drawn from the labeled members. Customer ids are not shown in docs or figures; they are "
         "kept in `outputs/exemplars.parquet` (gitignored) for the app.")
    emit()
    show = ex.drop(columns="customer_id")
    md(show[["cls", "class_size", "candidates", "picked_from_labeled", "confidence"]])
    emit("Exemplar vs class median on the defining features:")
    emit()
    for _, r in ex.iterrows():
        feats = [c for c in ex.columns if not c.startswith("class_median_") and f"class_median_{c}" in ex.columns
                 and pd.notna(r[c])]
        emit(f"- **{r.cls}**: " + ", ".join(f"{c} {r[c]:.3g} (median {r[f'class_median_{c}']:.3g})" for c in feats))
    emit()
    emit("Figures: `exemplars_carpet.png` (every exemplar's full year, hour x day), `exemplars_heating.png` "
         "(daily kWh vs temperature: heat pump vs HP + backup vs resistive), `exemplars_ev.png` (two weeks of "
         "L2 vs L1 charging). Water heater and hot tub are tagged **low confidence**: their 20-40 minute cycles "
         "average out at hourly resolution.")

    # ------------------------------------------------------------ figures
    colors = P.PALETTE[:K] if K <= 8 else plt.cm.tab10.colors[:K]
    ncol = 4
    nrow = int(np.ceil(K / ncol))
    fig, axes = plt.subplots(nrow, ncol, figsize=(14, 3.6 * nrow), squeeze=False)
    for j in range(K):
        ax = axes.flat[j]
        im = ax.imshow(H[j].reshape(12, 24), aspect="auto", cmap="viridis", origin="upper")
        ax.set_yticks(range(12))
        ax.set_yticklabels(D.MONTH_ABBR, fontsize=6)
        ax.set_xticks([0, 6, 12, 18, 23])
        ax.set_xlabel("Hour of day", fontsize=8)
        ax.grid(False)
        r = names.iloc[j]
        nm = r["name"] if r.explained else f"unexplained (closest: {r.best_match}, AUC {r.best_auc:.2f})"
        ax.set_title(f"C{j}: {short[j]} · {pop_share[j]:.0%} of energy\n{nm}", fontsize=8)
    for ax in axes.flat[K:]:
        ax.axis("off")
    fig.suptitle(f"NMF load parts (k = {K}): each home = a non-negative mix of these month × hour shapes",
                 fontweight="bold")
    fig.tight_layout()
    P.save(fig, "nmf_components")

    fig, ax = plt.subplots(figsize=(10, 5.5))
    plot_df = by_seg.drop(columns="customers").iloc[::-1]
    left = np.zeros(len(plot_df))
    for j, c in enumerate(labels):
        r = names.iloc[j]
        lab = f"{c} {short[j]}: {r.best_match if r.explained else 'unexplained'}"
        ax.barh(plot_df.index, plot_df[c], left=left, color=colors[j], label=lab)
        left += plot_df[c].to_numpy()
    ytl = [f"{g} (n={int(by_seg.loc[g, 'customers']):,})" for g in plot_df.index]
    ax.set_yticks(range(len(plot_df)))
    ax.set_yticklabels(ytl, fontsize=8)
    ax.set_xlim(0, 1)
    ax.set_xlabel("Mean share of each customer's annual energy")
    ax.grid(axis="y", visible=False)
    ax.legend(loc="lower center", bbox_to_anchor=(0.5, -0.38), ncol=2, fontsize=8)
    ax.set_title("Electrification fingerprint: NMF mix by label group and business segment")
    P.save(fig, "nmf_fingerprint")

    # carpets
    nex = len(ex)
    ncol = 4
    nrow = int(np.ceil(nex / ncol))
    fig, axes = plt.subplots(nrow, ncol, figsize=(14, 2.9 * nrow), squeeze=False)
    for i, r in ex.iterrows():
        ax = axes.flat[i]
        carpet = ex_mat[i].reshape(-1, 24).T
        vmax = max(np.nanpercentile(ex_mat[i], 99.5), 0.5)
        im = ax.imshow(carpet, aspect="auto", cmap="viridis", vmin=0, vmax=vmax, origin="lower",
                       interpolation="nearest")
        ax.grid(False)
        month_ticks(ax)
        ax.set_yticks([0, 6, 12, 18, 23])
        ax.tick_params(labelsize=7)
        tag = []
        if r.picked_from_labeled:
            tag.append("labeled")
        if r.confidence:
            tag.append("LOW confidence")
        ax.set_title(f"{r.cls}" + (f" [{', '.join(tag)}]" if tag else "") + f"\nclass n = {r.class_size:,}",
                     fontsize=8)
        cb = fig.colorbar(im, ax=ax, pad=0.01, fraction=0.04)
        cb.ax.tick_params(labelsize=6)
    for ax in axes.flat[nex:]:
        ax.axis("off")
    for ax in axes[:, 0]:
        ax.set_ylabel("Hour of day", fontsize=8)
    fig.suptitle("Exemplar customers (medoid of each class): hourly kWh, Sep 2025 → Aug 2026", fontweight="bold")
    fig.tight_layout()
    P.save(fig, "exemplars_carpet")

    # heating: HP vs HP+backup vs resistive
    wx = daily_weather(hours)
    temp = wx.temp.to_numpy()
    dk = daily_kwh(ex_mat)
    heat_cls = ["Heat pump", "Heat pump + resistance backup", "Resistive heat"]
    fig, axes = plt.subplots(1, 3, figsize=(13, 3.8))
    bins = np.arange(-5, 96, 5)
    for ax, cls, col in zip(axes, heat_cls, [P.PALETTE[2], P.PALETTE[1], P.PALETTE[3]]):
        i = int(ex.index[ex.cls == cls][0])
        y = dk[i]
        ok_ = ~np.isnan(y) & ~np.isnan(temp)
        ax.scatter(temp[ok_], y[ok_], s=8, alpha=0.45, color=col, edgecolor="none")
        b = pd.Series(y[ok_]).groupby(pd.cut(temp[ok_], bins)).median()
        mids = np.array([iv.mid for iv in b.index])
        ax.plot(mids, b.to_numpy(), color="black", lw=1.6, label="median per 5 °F")
        ax.axvline(20, color=P.NEUTRAL, ls="--", lw=1)
        ax.text(20.5, ax.get_ylim()[1] * 0.95, "20 °F", fontsize=7, color=P.NEUTRAL, va="top")
        r = ex.loc[i]
        ax.set_title(f"{cls}{' [labeled]' if r.picked_from_labeled else ''}\n"
                     f"heating slope {r.heat_slope:.2f} kWh/day/°F, ×{r.heat_break_ratio:.1f} below 20 °F", fontsize=9)
        ax.set_xlabel("Daily mean temperature (°F)")
    axes[0].set_ylabel("Daily kWh")
    axes[0].legend(fontsize=7)
    fig.suptitle("Heating type from the shape of the temperature response (exemplar homes)", fontweight="bold")
    fig.tight_layout()
    P.save(fig, "exemplars_heating")

    # EV: two weeks of L2 vs L1
    fig, axes = plt.subplots(2, 1, figsize=(12, 5.2), sharex=False)
    for ax, cls, col in zip(axes, ["EV, L2 charging", "EV, L1 charging"], [P.PALETTE[0], P.PALETTE[4]]):
        i = int(ex.index[ex.cls == cls][0])
        x = ex_mat[i].reshape(-1, 24)
        if cls.endswith("L2 charging"):
            score = np.nansum(x >= C.EV_L2_PEAK_KWH, axis=1).astype(float)
        else:
            night = np.nanmean(x[:, :6], axis=1)
            ref = np.nanpercentile(night, 20)
            score = ((night - ref >= C.EV_L1_KW[0]) & (np.nanstd(x[:, :6], axis=1) < 0.6)).astype(float)
        mild = (wx.temp.to_numpy() > 45) & (wx.temp.to_numpy() < 75)
        score = np.where(mild, score, 0.0)
        win = np.convolve(score, np.ones(14), mode="valid")
        d0 = int(np.argmax(win))
        seg_ = x[d0:d0 + 14].ravel()
        t = np.arange(len(seg_))
        for d in range(14):          # shade nights 0-6h
            ax.axvspan(d * 24, d * 24 + 6, color=P.NEUTRAL, alpha=0.12, lw=0)
        ax.step(t, seg_, where="post", color=col, lw=1.2)
        start = (C.START + pd.Timedelta(days=d0)).strftime("%b %d")
        r = ex.loc[i]
        extra = (f"median block {r.ev_l2_block_kw:.1f} kW, {r.ev_l2_block_hours:.0f} h, {r.ev_l2_per_week:.1f}/week"
                 if cls.endswith("L2 charging") else
                 f"overnight plateau on {r.ev_l1_night_share:.0%} of mild nights")
        ax.set_title(f"{cls}{' [labeled]' if r.picked_from_labeled else ''}: 14 days from {start} · {extra}",
                     fontsize=9)
        ax.set_ylabel("kWh in hour")
        ax.set_xticks(np.arange(0, 14 * 24 + 1, 24))
        ax.set_xticklabels([f"d{d}" for d in range(15)], fontsize=7)
        ax.set_xlim(0, 14 * 24)
    axes[1].set_xlabel("Day (grey = 00-06 h)")
    fig.suptitle("EV charging signatures: L2 = short high blocks, L1 = long low overnight plateau", fontweight="bold")
    fig.tight_layout()
    P.save(fig, "exemplars_ev")

    (C.DOCS / "decomposition.md").write_text("\n".join(lines) + "\n")
    print(f"\ndone in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
