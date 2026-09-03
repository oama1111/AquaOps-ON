"""Unit 2 evidence: run a WNTR chlorine water-quality simulation on the BattLeDIM
L-TOWN network to confirm the simulation stack works end to end.

Outputs (committed as evidence):
- docs/evidence/2026-09-03-wntr-ltown-chlorine.png  (chlorine time series, sample nodes)
- docs/evidence/2026-09-03-wntr-ltown-check.log    (run summary numbers)
"""
from pathlib import Path
import datetime
import wntr
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
INP = ROOT / "data/raw/battledim/L-TOWN.inp"
EV = ROOT / "docs/evidence"
EV.mkdir(parents=True, exist_ok=True)

log = []
def say(msg):
    print(msg)
    log.append(str(msg))

say(f"# WNTR L-TOWN chlorine check — {datetime.datetime.now().isoformat(timespec='seconds')}")
say(f"wntr version: {wntr.__version__}")
say(f"network file: {INP.name}")

wn = wntr.network.WaterNetworkModel(str(INP))
say(f"nodes: {wn.num_nodes} | links: {wn.num_links} | junctions: {wn.num_junctions} | tanks: {wn.num_tanks}")

# 3-day simulation, 5-minute quality step; trace chlorine (chemical) from sources
wn.options.time.duration = 3 * 24 * 3600
wn.options.time.report_timestep = 3600
wn.options.quality.parameter = "CHEMICAL"

# add a chlorine source at every reservoir so decay can be observed downstream
for rname in wn.reservoir_name_list:
    wn.add_pattern(f"{rname}_clpat", [1.0])
    wn.add_source(f"CL_{rname}", rname, "CONCENTRATION", 1.0, f"{rname}_clpat")
say(f"chlorine sources added at reservoirs: {wn.reservoir_name_list}")

sim = wntr.sim.EpanetSimulator(wn)
results = sim.run_sim()

quality = results.node["quality"]  # mg/L chlorine at nodes, hourly
say(f"quality results shape: {quality.shape} (hours x nodes)")

# summary over junctions only
junc = [j for j in wn.junction_name_list if j in quality.columns]
q = quality[junc]
say(f"chlorine mg/L across {len(junc)} junctions: "
    f"mean={q.mean().mean():.3f}, median={q.median().median():.3f}, "
    f"p5={q.stack().quantile(0.05):.3f}, p95={q.stack().quantile(0.95):.3f}, "
    f"min={q.min().min():.3f}, max={q.max().max():.3f}")

# distance proxy: nodes with lowest mean chlorine (longest travel time)
lowest = q.mean().sort_values().head(5)
say("5 junctions with lowest mean chlorine (decay tail):")
for n, v in lowest.items():
    say(f"  {n}: {v:.3f} mg/L")

# plot a handful of nodes across the network
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
fig, ax = plt.subplots(figsize=(9, 4.5))
pick = list(q.mean().sort_values().index[[0, len(junc)//4, len(junc)//2, 3*len(junc)//4, -1]])
for n in pick:
    ax.plot(q.index / 3600, q[n], label=n, linewidth=1.2)
ax.set_xlabel("hour")
ax.set_ylabel("chlorine (mg/L)")
ax.set_title("WNTR L-TOWN chlorine simulation — sample junctions (source = 1.0 mg/L)")
ax.legend(fontsize=7, ncol=2)
ax.grid(alpha=0.3)
fig.tight_layout()
png = EV / "2026-09-03-wntr-ltown-chlorine.png"
fig.savefig(png, dpi=150)
say(f"plot saved: {png.name}")

(EV / "2026-09-03-wntr-ltown-check.log").write_text("\n".join(log) + "\n", encoding="utf-8")
print("\nDONE")
