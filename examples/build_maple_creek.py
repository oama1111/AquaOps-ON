"""Build the fictional Township of Maple Creek distribution network (EPANET .inp).

Design intent (documented for the Unit 3 design report):
- Small Ontario town, population ~4,800 => avg demand ~22 L/s (~1,900 m3/day at 400 L/capita/day)
- One groundwater source + treatment (chlorination) + one elevated tank
- Looped trunk with two branch loops (typical of small towns) plus three dead-end spurs;
  dead ends and far-from-source points are where chlorine decays most and where O. Reg. 170/03
  sampling plans concentrate.
- 18 of the 25 network nodes are designated sampling points SP01..SP18.

Outputs:
- data/networks/maple_creek.inp        (committed — the canonical case network)
- data/networks/maple_creek_points.csv (sampling-point registry used by M1/M3)
"""
import csv
from pathlib import Path

import wntr

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data/networks"
OUT.mkdir(parents=True, exist_ok=True)

wn = wntr.network.WaterNetworkModel()
wn.options.hydraulic.inpfile_units = "LPS"
wn.options.time.duration = 48 * 3600
wn.options.time.hydraulic_timestep = 300
wn.options.time.quality_timestep = 300
wn.options.time.report_timestep = 3600
wn.options.quality.parameter = "CHEMICAL"

# ---- sources -------------------------------------------------------------
# Groundwater well field west of town; water is chlorinated at the pump discharge.
wn.add_reservoir("WELLFIELD", base_head=100.0, coordinates=(0, 500))
wn.add_junction("TP", base_demand=0.0, elevation=101.0, coordinates=(150, 500))  # treatment plant / Cl2 injection point
wn.add_curve("PCURVE", "HEAD", [(0.0, 42.0), (0.025, 35.0), (0.050, 21.0)])  # flow m3/s, head m
wn.add_pump("P1", "WELLFIELD", "TP", pump_type="HEAD", pump_parameter="PCURVE")

# ---- junctions: (name, x, y, elevation, base_demand LPS, sampling_point?) ----
J = [
    # trunk (east-west spine)
    ("A1", 400, 500, 102, 1.2, "SP01"),
    ("A2", 800, 500, 103, 1.0, "SP02"),
    ("A3", 1200, 500, 104, 1.1, "SP03"),
    ("A4", 1600, 500, 106, 0.9, "SP04"),
    # north branch + loop back
    ("B1", 800, 900, 105, 0.8, "SP05"),
    ("B2", 1200, 900, 106, 0.9, "SP06"),
    ("B3", 1600, 900, 108, 0.7, "SP07"),   # north-east dead end
    # south branch + loop back
    ("C1", 400, 100, 103, 1.0, "SP08"),
    ("C2", 800, 100, 104, 1.1, "SP09"),
    ("C3", 1200, 100, 105, 0.9, "SP10"),
    # dead-end spurs (high water age — regulatory sampling hotspots)
    ("D1", 400, -250, 104, 0.5, "SP11"),
    ("D2", 2000, 100, 108, 0.6, "SP12"),
    ("D3", 2000, 900, 109, 0.4, "SP13"),
    # small north-west pocket
    ("E1", 400, 900, 104, 0.7, "SP14"),
    ("E2", 400, 1250, 106, 0.5, "SP15"),   # far north-west dead end
    # south-east pocket
    ("F1", 1600, 100, 106, 0.8, "SP16"),
    ("F2", 1600, -250, 107, 0.5, "SP17"),
    ("F3", 2000, -250, 109, 0.4, "SP18"),  # far south-east dead end
    # non-sampled junctions (pipe topology only)
    ("J1", 800, 1300, 108, 0.6, ""),
    ("J2", 1200, 1300, 109, 0.6, ""),
    ("J3", 2000, 500, 108, 0.7, ""),
    ("J4", 2350, 500, 110, 0.5, ""),
    ("J5", 2350, 100, 111, 0.4, ""),
    ("J6", 2350, 900, 111, 0.4, ""),
]
sp_registry = []
for name, x, y, elev, demand, sp in J:
    wn.add_junction(name, base_demand=demand / 1000.0, elevation=elev, coordinates=(x, y))  # demand: L/s -> m3/s (SI)
    if sp:
        sp_registry.append((sp, name, x, y, elev))

# elevated storage tank east of town centre
wn.add_tank("T1", elevation=130.0, init_level=8.0, min_level=2.0, max_level=12.0,
            diameter=15.0, coordinates=(2050, 520))

# ---- pipes: (name, start, end, length m, diameter mm, roughness) ----------
def L(a, b):
    ax, ay = wn.get_node(a).coordinates
    bx, by = wn.get_node(b).coordinates
    return round(((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5)

P = [
    ("PTP", "TP", "A1", None, 250, 110),
    ("P01", "A1", "A2", None, 250, 110),
    ("P02", "A2", "A3", None, 250, 110),
    ("P03", "A3", "A4", None, 200, 120),
    ("P04", "A4", "J3", None, 200, 120),
    ("P05", "J3", "T1", 80, 200, 120),
    ("P06", "A2", "B1", None, 150, 130),
    ("P07", "B1", "B2", None, 150, 130),
    ("P08", "B2", "A3", None, 150, 130),   # north loop closes
    ("P09", "B2", "B3", None, 100, 130),   # NE dead end
    ("P10", "A1", "C1", None, 150, 130),
    ("P11", "C1", "C2", None, 150, 130),
    ("P12", "C2", "C3", None, 150, 130),
    ("P13", "C3", "A3", None, 150, 130),   # south loop closes
    ("P14", "C1", "D1", None, 100, 140),
    ("P15", "C3", "F1", None, 100, 130),
    ("P16", "F1", "F2", None, 100, 140),
    ("P17", "F2", "F3", None, 100, 140),
    ("P18", "F1", "D2", None, 100, 130),
    ("P19", "D2", "J5", None, 100, 140),
    ("P20", "B1", "E1", None, 100, 130),
    ("P21", "E1", "E2", None, 100, 140),
    ("P22", "E2", "J1", None, 100, 140),
    ("P23", "J1", "J2", None, 100, 140),
    ("P24", "J2", "B3", None, 100, 140),   # far north loop
    ("P25", "J3", "J4", None, 150, 130),
    ("P26", "J4", "J5", None, 100, 140),
    ("P27", "J4", "J6", None, 100, 140),
    ("P28", "J6", "B3", None, 100, 140),   # NE loop
    ("P29", "B3", "D3", None, 100, 140),   # NE dead-end extension (SP13)
]
for name, s, e, length, d, c in P:
    wn.add_pipe(name, s, e, length=length or L(s, e), diameter=d / 1000.0, roughness=c)

# ---- demand pattern: small-town diurnal curve ----------------------------
diurnal = [0.45, 0.40, 0.38, 0.38, 0.42, 0.60, 0.85, 1.10, 1.25, 1.20, 1.10, 1.05,
           1.00, 1.00, 1.02, 1.05, 1.15, 1.30, 1.35, 1.25, 1.05, 0.85, 0.65, 0.52]
wn.add_pattern("DIURNAL", diurnal)
for j in wn.junction_name_list:
    wn.get_node(j).demand_timeseries_list[0].pattern_name = "DIURNAL"

# ---- chlorine: 1.1 mg/L injected at the treatment plant ------------------
wn.add_pattern("CL_DOSE", [1.0])
wn.add_source("CL2", "TP", "SETPOINT", 0.0011, "CL_DOSE")  # 1.1 mg/L = 0.0011 kg/m3 (SI)
wn.options.reaction.bulk_coeff = -0.5 / 86400.0   # first-order decay, 0.5 1/day -> SI 1/s
wn.options.reaction.wall_coeff = 0.0

inp = OUT / "maple_creek.inp"
wntr.network.write_inpfile(wn, str(inp))
print(f"network written: {inp.name}  | junctions={wn.num_junctions} pipes={wn.num_pipes} tank=1 pump=1")
print(f"total base demand: {sum(j[4] for j in J):.1f} L/s "
      f"(~{sum(j[4] for j in J) * 86400 / 1000:.0f} m3/day)")

with open(OUT / "maple_creek_points.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["sampling_point_id", "node_id", "x_m", "y_m", "elevation_m", "location_type"])
    for sp, node, x, y, elev in sp_registry:
        dead = node.startswith(("B3", "D", "E2", "F3"))
        w.writerow([sp, node, x, y, elev, "dead_end" if dead else "looped"])
print(f"sampling points registered: {len(sp_registry)}")
