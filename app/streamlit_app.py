"""Avangrid AMI demo (task 2.5): overview, customer lookup, segment explorer, BESS ranking with SOC dispatch.

Run:  streamlit run app/streamlit_app.py
Reads only cached pipeline outputs (outputs/, data_processed/, docs/insights/); nothing leaves the laptop.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import plotly.graph_objects as go  # noqa: E402
import streamlit as st  # noqa: E402
from plotly.subplots import make_subplots  # noqa: E402

from avangrid import app_data as A  # noqa: E402
from avangrid import config as C  # noqa: E402
from avangrid import insights as I  # noqa: E402
from avangrid import segment as S  # noqa: E402

# Okabe-Ito, same as the report figures (avangrid.plots)
BLUE, ORANGE, GREEN, VERMILION, PINK, SKY, YELLOW, GREY = ("#0072B2", "#E69F00", "#009E73", "#D55E00",
                                                           "#CC79A7", "#56B4E9", "#F0E442", "#999999")
SEASON_COLOR = {"summer": VERMILION, "winter": BLUE}

st.set_page_config(page_title="Avangrid AMI: electrification & batteries", layout="wide")


# --------------------------------------------------------------------------- cached loaders
@st.cache_data(show_spinner=False)
def table() -> pd.DataFrame:
    return A.customer_table()


@st.cache_data(show_spinner=False)
def gfacts() -> dict:
    return I.global_facts()


@st.cache_data(show_spinner=False)
def nmf_names() -> dict[str, str]:
    p = C.OUTPUTS_SUMMARY / "nmf_components.csv"
    if not p.exists():
        return {}
    c = pd.read_csv(p, usecols=["component", "name", "label_free"])
    return {f"C{r.component}": f"C{r.component}: {r['name']} ({r.label_free})" for _, r in c.iterrows()}


def alias(cid: int) -> str:
    if st.session_state.get("hide_ids", True):
        return "Home " + hashlib.sha1(f"avangrid-{int(cid)}".encode()).hexdigest()[:6].upper()
    return f"Customer {int(cid)}"


# --------------------------------------------------------------------------- charts
def dispatch_figure(df: pd.DataFrame, season: str, title: str) -> go.Figure:
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    ymax = float(max(df.load_kw.max(), df.net_kw.max())) * 1.08 + 0.1
    for flag, color, name in (("system_top_hour", YELLOW, "System top hours"), ("event_hour", GREY, "Called event hours")):
        on = df[flag].to_numpy()
        if on.any():
            fig.add_trace(go.Bar(x=df.time, y=np.where(on, ymax, 0), marker_color=color, opacity=0.35 if flag == "system_top_hour" else 0.18,
                                 name=name, hoverinfo="skip", width=3.6e6), secondary_y=False)
    fig.add_trace(go.Scatter(x=df.time, y=df.load_kw, name="Load without battery", line=dict(color=GREY, width=1.6, shape="hv")),
                  secondary_y=False)
    fig.add_trace(go.Scatter(x=df.time, y=df.net_kw, name="Net load with battery",
                             line=dict(color=SEASON_COLOR[season], width=2.2, shape="hv")), secondary_y=False)
    fig.add_trace(go.Scatter(x=df.time, y=df.soc_kwh, name="State of charge (kWh)",
                             line=dict(color="black", width=1.2, dash="dash", shape="hv")), secondary_y=True)
    fig.update_layout(title=title, barmode="overlay", height=380, margin=dict(l=10, r=10, t=50, b=10),
                      legend=dict(orientation="h", y=-0.18), bargap=0)
    fig.update_yaxes(title_text="kW (hourly kWh)", range=[0, ymax], secondary_y=False)
    fig.update_yaxes(title_text="kWh stored", range=[0, C.BATTERY.kwh * 1.1], dtick=5, secondary_y=True, showgrid=False)
    return fig


def show_dispatch(cid: int, key: str) -> None:
    r = table().loc[cid]
    c1, c2 = st.columns([1, 1])
    season = c1.radio("Season", ["summer", "winter"], horizontal=True, key=f"season_{key}")
    units = c2.radio("Battery", [2, 1], horizontal=True, format_func=lambda u: f"{u} x IQ 10C ({u * 10.08:.2f} kWh)",
                     key=f"units_{key}")
    df, sm = A.dispatch(cid, season, units=units)
    st.plotly_chart(dispatch_figure(df, season, f"{alias(cid)}: realistic dispatch around the {season} system peak"),
                    width="stretch")
    stored = r[f"relief_kw_{season}"]
    m = st.columns(4)
    m[0].metric(f"Relief in {season} top hours", f"{sm['relief_kw']:.2f} kW",
                help="Average kW the battery delivered across the system's top 88 hours of the season (missed hours count as 0)")
    m[1].metric("Firm kW (claimed)", f"{r[f'firm_kw_{season}']:.2f} kW" if pd.notna(r[f"firm_kw_{season}"]) else "n/a",
                help="P10 of the flat level held through the whole event window: what we claim, not nameplate")
    m[2].metric("SOC range over the year", f"{sm['soc_min']:.1f}-{sm['soc_max']:.1f} kWh",
                help=f"Limits 0-{sm['battery_kwh']:.2f} kWh; discharge ≤ {sm['battery_kw']:.2f} kW; recharge 00-05h only")
    m[3].metric("Avoided buildout (mid)", f"${r.avoided_usd_yr_mid:,.0f}/yr" if pd.notna(r.avoided_usd_yr_mid) else "n/a",
                help="Placeholder $/kW-yr (config.py) pending Avangrid's values; 2 units")
    if units == 2 and pd.notna(stored):
        st.caption(f"Re-simulated here with the task 1.6 code: {sm['relief_kw']:.3f} kW vs {stored:.3f} kW stored in "
                   f"outputs/bess_results.parquet. {sm['events_called']} events called per season on forecast-hottest/coldest days.")


def carpet_figure(cid: int) -> go.Figure:
    cp = A.carpet(cid)
    fig = go.Figure(go.Heatmap(z=cp.to_numpy(), x=cp.columns, y=cp.index, colorscale="Viridis",
                               zmax=float(np.nanpercentile(cp.to_numpy(), 99.5)), colorbar=dict(title="kWh")))
    fig.update_layout(height=330, margin=dict(l=10, r=10, t=30, b=10), yaxis_title="Hour of day",
                      title="Every hour of the year")
    return fig


def temp_figure(cid: int) -> go.Figure:
    d = A.daily_vs_temp(cid).dropna()
    fig = go.Figure(go.Scatter(x=d.temp_f, y=d.kwh, mode="markers", marker=dict(size=6, color=d.temp_f,
                                                                                 colorscale="RdBu_r", opacity=0.7),
                               text=d.date.dt.strftime("%b %d"), hovertemplate="%{text}: %{y:.1f} kWh at %{x:.0f} °F"))
    fig.add_vline(x=20, line_dash="dash", line_color=GREY, annotation_text="20 °F")
    fig.update_layout(height=330, margin=dict(l=10, r=10, t=30, b=10), xaxis_title="Daily mean temperature (°F)",
                      yaxis_title="Daily kWh", title="Weather response (heating left, cooling right)")
    return fig


def profile_figure(prof: pd.DataFrame, rows: list[str]) -> go.Figure:
    fig = make_subplots(rows=1, cols=3, subplot_titles=("Winter", "Shoulder", "Summer"), shared_yaxes=True)
    colors = [BLUE, GREY, ORANGE, GREEN]
    for i, name in enumerate(rows):
        for j, season in enumerate(("winter", "shoulder", "summer")):
            y = prof.loc[name, [f"prof_{season}_{h:02d}" for h in range(24)]].to_numpy()
            fig.add_trace(go.Scatter(x=list(range(24)), y=y, name=name, legendgroup=name, showlegend=j == 0,
                                     line=dict(color=colors[i % 4], width=2.4 if i == 0 else 1.6)), row=1, col=j + 1)
    fig.update_layout(height=300, margin=dict(l=10, r=10, t=40, b=10), legend=dict(orientation="h", y=-0.25))
    fig.update_yaxes(title_text="Load / own annual mean", row=1, col=1)
    fig.update_xaxes(title_text="Hour of day")
    return fig


def nmf_bar(values: pd.Series, title: str) -> go.Figure:
    names = nmf_names()
    fig = go.Figure(go.Bar(x=values.to_numpy(), y=[names.get(c.replace("share_", ""), c) for c in values.index],
                           orientation="h", marker_color=BLUE))
    fig.update_layout(height=300, margin=dict(l=10, r=10, t=40, b=10), title=title, xaxis_tickformat=".0%",
                      yaxis=dict(autorange="reversed"))
    return fig


# --------------------------------------------------------------------------- views
def view_overview() -> None:
    g = gfacts()
    st.title("AMI electrification & behind-the-meter batteries")
    st.caption(f"{g['customers_model']:,} residential customers in the model, one year of hourly AMI "
               "(Sep 2025 - Aug 2026) plus weather. All numbers below come from the pipeline outputs.")
    m = st.columns(5)
    m[0].metric("Summer system peak", f"{g['peak_summer_mw']:.1f} MW", g["peak_summer_date"], delta_color="off", delta_arrow="off")
    m[1].metric("Winter system peak", f"{g['peak_winter_mw']:.1f} MW", f"{g['winter_over_summer_peak']:.0%} of summer",
                delta_color="off", delta_arrow="off")
    m[2].metric("Winter-peaking at", f"{g['crossover_B']:.0%} HP",
                f"range {g['crossover_B']:.0%}-{g['crossover_A']:.0%}", delta_color="off", delta_arrow="off")
    m[3].metric("Tier 1 battery homes", f"{g['tier1_homes']:,}",
                f"{g['tier1_mw_s']:.1f} / {g['tier1_mw_w']:.1f} MW (S / W)", delta_color="off", delta_arrow="off")
    m[4].metric("Avoided buildout, Tier 1 home", f"${g['tier1_usd']:,.0f}/yr", "mid placeholder $/kW-yr",
                delta_color="off", delta_arrow="off")
    pc = A.summary_csv("peak_contribution_by_segment").set_index("group")
    pc = pc.reindex([s for s in S.SEGMENT_ORDER if s in pc.index])
    fig = go.Figure()
    for col, name, color in (("share_customers", "Share of customers", GREY),
                             ("share_cp_kw_summer", "Share of summer peak", VERMILION),
                             ("share_cp_kw_winter", "Share of winter peak", BLUE)):
        fig.add_trace(go.Bar(y=pc.index, x=pc[col], name=name, orientation="h", marker_color=color))
    fig.update_layout(barmode="group", height=520, xaxis_tickformat=".0%", yaxis=dict(autorange="reversed"),
                      title="Who drives the system peak (coincident-peak share by business segment)",
                      margin=dict(l=10, r=10, t=50, b=10), legend=dict(orientation="h", y=-0.12))
    st.plotly_chart(fig, width="stretch")
    pb = C.INSIGHTS / "playbook.md"
    if pb.exists():
        text = pb.read_text().split("## Claim checks")[0]
        text = text.replace("# Program playbook (task 2.4)", "### Program playbook")
        with st.expander("Program playbook", expanded=True):
            st.markdown(text)


def pick_customer() -> int | None:
    d = table()
    mode = st.radio("Find a home by", ["Exemplar", "Battery rank", "Customer ID"], horizontal=True)
    if mode == "Exemplar":
        ex = A.exemplars()
        if ex.empty:
            st.warning("Run scripts/06b_decompose.py to build exemplars.")
            return None
        cls = st.selectbox("Appliance class (typical member, medoid rule)", ex.cls.tolist())
        return int(ex.loc[ex.cls == cls, "customer_id"].iloc[0])
    if mode == "Battery rank":
        n = int(d.bess_rank.max())
        rank = st.number_input(f"Priority rank (1 = best of {n:,} candidates)", 1, n, 1)
        return int(d.index[d.bess_rank == rank][0])
    raw = st.text_input("Customer ID")
    if not raw:
        return None
    try:
        cid = int(raw)
    except ValueError:
        st.error("IDs are integers.")
        return None
    if cid not in d.index:
        st.error("Not in the AMI data.")
        return None
    return cid


def view_customer() -> None:
    st.title("Customer lookup")
    cid = pick_customer()
    if cid is None:
        return
    r = table().loc[cid]
    tier = r.tier if isinstance(r.tier, str) else "not a battery candidate"
    st.subheader(f"{alias(cid)} · {r.segment}")
    st.caption(f"Archetype: {r.archetype_name}" + (f" · exemplar of: {r.exemplar_of}" if isinstance(r.get("exemplar_of"), str) else "")
               + (f" · labels: {', '.join(n for n, f in (('EV', r.label_ev), ('HP', r.label_hp), ('PV', r.label_pv)) if f)}"
                  if (r.label_ev or r.label_hp or r.label_pv) else "")
               + ("" if r.in_model else f" · outside the model population ({r.exclude_reason})"))
    m = st.columns(5)
    m[0].metric("Annual use", f"{r.annual_kwh:,.0f} kWh")
    m[1].metric("Peak hour", f"{r.peak_kw:.1f} kWh")
    m[2].metric("At summer system peak", f"{r.cp_kw_summer:.2f} kW")
    m[3].metric("At winter system peak", f"{r.cp_kw_winter:.2f} kW")
    m[4].metric("Battery", tier, r["size"] if isinstance(r["size"], str) else None, delta_color="off", delta_arrow="off")
    tabs = st.tabs(["Load carpet", "Weather response", "Why flagged", "Battery dispatch", "Load mix (NMF)"])
    with tabs[0]:
        st.plotly_chart(carpet_figure(cid), width="stretch")
    with tabs[1]:
        st.plotly_chart(temp_figure(cid), width="stretch")
        st.caption(f"Heating slope {r.heat_slope:.2f} kWh/day/°F (×{r.heat_break_ratio:.2f} below 20 °F), "
                   f"cooling slope {r.cool_slope:.2f} kWh/day/°F. Heat type: {r.heat_type}.")
    with tabs[2]:
        ev = A.appliance_evidence(cid)
        ev["evidence"] = ev.evidence.str.replace("\n", "<br>", regex=False)
        st.markdown(ev.to_html(index=False, escape=False, border=0, classes="evidence"), unsafe_allow_html=True)
        st.markdown("<style>table.evidence{width:100%;font-size:0.85rem;border-collapse:collapse}"
                    "table.evidence td,table.evidence th{padding:4px 8px;border-bottom:1px solid #ddd;"
                    "text-align:left;vertical-align:top}</style>", unsafe_allow_html=True)
        st.caption(f"PU ranking percentiles (ranking only, labels are not a random sample): EV {r.ev_pu_pct:.0%}, "
                   f"HP {r.hp_pu_pct:.0%}. Water heater and hot tub are low confidence at hourly resolution.")
    with tabs[3]:
        if isinstance(r.tier, str):
            show_dispatch(cid, "lookup")
        else:
            st.info("Not screened for a battery (low use / seasonal, or under 1.5 kW at both system peaks).")
    with tabs[4]:
        shares = r[[c for c in r.index if str(c).startswith("share_C")]].astype(float)
        if shares.notna().any():
            st.plotly_chart(nmf_bar(shares, "Share of this home's annual energy explained by each NMF part"),
                            width="stretch")
        else:
            st.info("Outside the modeling population: no NMF mix.")


def view_segments() -> None:
    st.title("Segment explorer")
    cards = A.cards()
    segs = [s for s in S.SEGMENT_ORDER if s in cards]
    seg = st.selectbox("Business segment", segs, index=segs.index("Electric-heat home") if "Electric-heat home" in segs else 0)
    c = cards[seg]
    st.subheader(c["headline"])
    left, right = st.columns([3, 2])
    with left:
        st.markdown(f"**Who.** {c['who']}\n\n**Why it matters.** {c['why']}\n\n**Program.** {c['program']}\n\n"
                    f"**Watch out.** {c['watch']}")
    with right:
        mt = c["metrics"]
        a, b = st.columns(2)
        a.metric("Customers", f"{mt['customers']:,}", f"{mt['share_customers']:.1%} of homes", delta_color="off", delta_arrow="off")
        b.metric("Tier 1 battery homes", f"{mt['tier1']:,}", f"+{mt['tier2']:,} in Tier 2", delta_color="off",
                 delta_arrow="off")
        a.metric("Summer peak share", f"{mt['share_summer_cp']:.1%}",
                 f"CI {mt['share_summer_cp_ci'][0]:.1%}-{mt['share_summer_cp_ci'][1]:.1%}", delta_color="off", delta_arrow="off")
        b.metric("Winter peak share", f"{mt['share_winter_cp']:.1%}",
                 f"CI {mt['share_winter_cp_ci'][0]:.1%}-{mt['share_winter_cp_ci'][1]:.1%}", delta_color="off", delta_arrow="off")
        if mt["battery_relief_kw_summer"] is not None:
            a.metric("Battery kW (S / W)", f"{mt['battery_relief_kw_summer']:.2f} / {mt['battery_relief_kw_winter']:.2f}")
            b.metric("Avoided $/home/yr (mid)", f"${mt['avoided_usd_per_home_yr']['mid']:,}")
    prof = A.segment_profiles()
    st.plotly_chart(profile_figure(prof, [seg, "All modeled customers"]), width="stretch")
    mix = A.summary_csv("nmf_mix_by_segment").set_index("group")
    if seg in mix.index:
        parts = [col for col in mix.columns if col.startswith("C")]
        comp = pd.DataFrame({"segment": mix.loc[seg, parts], "all": mix.loc["All modeled customers", parts]})
        fig = go.Figure()
        names = nmf_names()
        ylab = [names.get(p, p) for p in parts]
        fig.add_trace(go.Bar(y=ylab, x=comp["segment"], name=seg, orientation="h", marker_color=BLUE))
        fig.add_trace(go.Bar(y=ylab, x=comp["all"], name="All modeled customers", orientation="h", marker_color=GREY))
        fig.update_layout(barmode="group", height=330, xaxis_tickformat=".0%", yaxis=dict(autorange="reversed"),
                          title="Electrification fingerprint (NMF mix)", margin=dict(l=10, r=10, t=40, b=10),
                          legend=dict(orientation="h", y=-0.15))
        st.plotly_chart(fig, width="stretch")


def view_bess() -> None:
    st.title("Battery ranking")
    t = A.summary_csv("bess_tiers").set_index("tier")
    show = pd.DataFrame({
        "homes": t.customers.astype(int),
        "relief MW summer": t.battery_relief_mw_summer, "relief MW winter": t.battery_relief_mw_winter,
        "kW/home summer": t.kw_per_home_summer, "kW/home winter": t.kw_per_home_winter,
        "$ /home/yr (mid)": t.avoided_usd_per_home_yr_mid, "electrified": t.share_electrified,
        "need 2 units + DR": t.share_2units_plus_dr,
    })
    st.dataframe(show.style.format({"homes": "{:,}", "relief MW summer": "{:.2f}", "relief MW winter": "{:.2f}",
                                    "kW/home summer": "{:.2f}", "kW/home winter": "{:.2f}",
                                    "$ /home/yr (mid)": "${:,.0f}", "electrified": "{:.0%}",
                                    "need 2 units + DR": "{:.0%}"}), width="stretch")
    st.caption("Realistic dispatch, 2 x IQ 10C (20.16 kWh, 7.68 kW, 89.4% RTE), SOC tracked hour by hour across "
               "multi-day events. $ = avoided buildout at mid placeholder $/kW-yr (config.py).")
    d = table()
    d = d[d.tier.notna()]
    c1, c2, c3 = st.columns(3)
    tiers = c1.multiselect("Tier", ["Tier 1", "Tier 2", "Tier 3", "Not prioritised"], default=["Tier 1"])
    segs = c2.multiselect("Segment", [s for s in S.SEGMENT_ORDER if s in set(d.segment)])
    sizes = c3.multiselect("Size", sorted(d["size"].dropna().unique()))
    if tiers:
        d = d[d.tier.isin(tiers)]
    if segs:
        d = d[d.segment.isin(segs)]
    if sizes:
        d = d[d["size"].isin(sizes)]
    d = d.sort_values("bess_rank").head(300)
    view = pd.DataFrame({"rank": d.bess_rank.astype(int).to_numpy(), "home": [alias(i) for i in d.index],
                         "segment": d.segment.to_numpy(), "tier": d.tier.to_numpy(),
                         "size": d["size"].str.replace(" (battery alone insufficient)", "", regex=False).to_numpy(),
                         "kW summer": d.relief_kw_summer.to_numpy(), "kW winter": d.relief_kw_winter.to_numpy(),
                         "firm kW winter": d.firm_kw_winter.to_numpy(), "$ /yr mid": d.avoided_usd_yr_mid.to_numpy()})
    st.caption(f"{len(view):,} homes shown (top 300 by priority after filters). Select a row to see its dispatch.")
    ev = st.dataframe(view, hide_index=True, width="stretch", height=280, on_select="rerun",
                      selection_mode="single-row",
                      column_config={"kW summer": st.column_config.NumberColumn(format="%.2f"),
                                     "kW winter": st.column_config.NumberColumn(format="%.2f"),
                                     "firm kW winter": st.column_config.NumberColumn(format="%.2f"),
                                     "$ /yr mid": st.column_config.NumberColumn(format="$%.0f")})
    if view.empty:
        return
    sel = ev.selection.rows if ev and ev.selection else []
    cid = int(d.index[sel[0] if sel else 0])
    show_dispatch(cid, "bess")


# --------------------------------------------------------------------------- layout
with st.sidebar:
    st.header("Avangrid AMI demo")
    view = st.radio("View", ["Overview", "Customer lookup", "Segment explorer", "Battery ranking"])
    st.toggle("Hide customer IDs (for screenshots)", value=True, key="hide_ids")
    st.caption("Local only: reads cached outputs of `scripts/run_all.sh`. No data leaves this machine.")

{"Overview": view_overview, "Customer lookup": view_customer, "Segment explorer": view_segments,
 "Battery ranking": view_bess}[view]()
