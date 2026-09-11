"""Validate the Maple Creek case network: 48-h hydraulic + chlorine simulation.

Checks (acceptance criteria for the case data used in Units 3-8):
- junction pressures within 25-60 m head at all times (typical small-system band)
- free chlorine within 0-1.1 mg/L everywhere, with visible decay at dead ends
- tank level cycles within min/max (no draining or overflow)
- sampling-point chlorine series exported for the anomaly-detection module (M2)

Outputs (committed as dated evidence for the design report):
- docs/evidence/2026-09-11-maple-creek-network.png   (topology + sampling points)
- docs/evidence/2026-09-11-maple-creek-chlorine.png  (48h chlorine at selected points)
- docs/evidence/2026-09-11-maple-creek-sim.log       (numeric validation summary)
- data/networks/maple_creek_chlorine_48h.csv         (SP01..SP18 hourly chlorine)
"""
from pathlib import Path
import sys

import pandas as pd
import wntr

ROOT = Path(__file__).resolve().parents[1]
EV = ROOT / "docs/evidence"
EV.mkdir(parents=True, exist_ok=True)
LOG = EV / "2026-09-11-maple-creek-sim.log"

lines = []


def log(msg):
    print(msg)
    lines.append(msg)


wn = wntr.network.WaterNetworkModel(str(ROOT / "data/networks/maple_creek.inp"))
sim = wntr.sim.EpanetSimulator(wn)
res = sim.run_sim()

pressure = res.node["pressure"]          # m, all nodes hourly
quality = res.node["quality"] * 1000.0   # wntr returns kg/m3 (SI); x1000 -> mg/L
junctions = wn.junction_name_list
jp = pressure[junctions]
jq = quality[junctions + ["T1"]]

tank_level = res.node["pressure"]["T1"]  # tank pressure ~ water depth above elev

log(f"simulation: {pressure.shape[0]} hourly steps x {pressure.shape[1]} nodes")
log(f"pressure at junctions: min={jp.min().min():.1f} m  max={jp.max().max():.1f} m  (accept 25-60)")
log(f"chlorine all nodes:    min={jq.min().min():.2f} mg/L max={jq.max().max():.2f} mg/L  (accept 0-1.1)")
log(f"tank T1 level:         min={tank_level.min():.2f} m  max={tank_level.max():.2f} m  (accept 2-12)")

# chlorine contrast: source-adjacent vs dead ends at final hour
final = quality.iloc[-1]
log(f"chlorine at final hour: TP={final['TP']:.2f}  A1(SP01)={final['A1']:.2f}  "
    f"D1(SP11)={final['D1']:.2f}  E2(SP15)={final['E2']:.2f}  F3(SP18)={final['F3']:.2f} mg/L")

ok = (
    25 <= jp.min().min() and jp.max().max() <= 60
    and jq.min().min() >= 0 and jq.max().max() <= 1.11
    and tank_level.min() >= 2 and tank_level.max() <= 12
    and final["TP"] > final["F3"]  # decay gradient exists
)
log(f"VALIDATION {'PASSED' if ok else 'FAILED'}")

# ---- export sampling-point chlorine series for M2 ---------------------------
pts = pd.read_csv(ROOT / "data/networks/maple_creek_points.csv")
sp_nodes = dict(zip(pts.sampling_point_id, pts.node_id))
sp_q = quality[list(sp_nodes.values())].copy()
sp_q.columns = list(sp_nodes.keys())  # dict preserves SP01..SP18 order
sp_q.index = pd.to_timedelta(sp_q.index, unit="s")
sp_q.to_csv(ROOT / "data/networks/maple_creek_chlorine_48h.csv", index_label="time")
log(f"exported chlorine series for {sp_q.shape[1]} sampling points -> data/networks/maple_creek_chlorine_48h.csv")

# ---- figures -----------------------------------------------------------------
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

fig, ax = plt.subplots(figsize=(11, 8))
wntr.graphics.plot_network(wn, ax=ax, node_size=25, link_width=1.4, show_plot=False)
coords = {n: wn.get_node(n).coordinates for n in wn.node_name_list}
dead = set(pts.loc[pts.location_type == "dead_end", "node_id"])
for sp, node in sp_nodes.items():
    x, y = coords[node]
    ax.scatter(x, y, s=200, c="#d62728" if node in dead else "#1f77b4", zorder=5,
               edgecolors="white", linewidths=1.2)
    ax.annotate(sp, (x, y), textcoords="offset points", xytext=(8, 8), fontsize=8)
for n, lbl, c, m in [("TP", "TP", "#2ca02c", "s"), ("T1", "T1", "#9467bd", "^"),
                     ("WELLFIELD", "WELL", "#8c564b", "D")]:
    x, y = coords[n]
    ax.scatter(x, y, s=300, c=c, marker=m, zorder=6, edgecolors="black")
    ax.annotate(lbl, (x, y), textcoords="offset points", xytext=(10, -14), fontsize=9, weight="bold")
ax.set_title("Township of Maple Creek — case distribution network\n"
             "blue/red = sampling points SP01–SP18 (red = dead ends), square = treatment plant, triangle = tank",
             fontsize=11)
fig.savefig(EV / "2026-09-11-maple-creek-network.png", bbox_inches="tight", dpi=150)
plt.close(fig)

sel = ["TP", "A1", "B3", "D1", "E2", "F3"]
fig, ax = plt.subplots(figsize=(10, 5))
for n in sel:
    ax.plot(quality.index / 3600, quality[n], label={"TP": "TP (dose)", "A1": "SP01/A1",
            "B3": "SP07/B3", "D1": "SP11/D1", "E2": "SP15/E2", "F3": "SP18/F3"}[n])
ax.set_xlabel("hour")
ax.set_ylabel("free chlorine (mg/L)")
ax.set_title("Maple Creek — 48 h chlorine decay at representative points")
ax.legend(ncol=3, fontsize=9)
ax.grid(alpha=0.3)
fig.savefig(EV / "2026-09-11-maple-creek-chlorine.png", bbox_inches="tight", dpi=150)
plt.close(fig)

LOG.write_text("\n".join(lines) + "\n")
log(f"log written: {LOG.name}")
sys.exit(0 if ok else 1)
