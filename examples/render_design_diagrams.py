"""Render AquaOps ON design diagrams (architecture + ERD) as PNG evidence.

Outputs:
- docs/diagrams/architecture.png  (module/container view, M1-M6)
- docs/diagrams/erd.png           (data model overview)
"""
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs/diagrams"
OUT.mkdir(parents=True, exist_ok=True)

C_APP = "#1f77b4"; C_MOD = "#e8f1fb"; C_DATA = "#e8f7e8"; C_EXT = "#f7eee8"


def box(ax, x, y, w, h, label, fc, fontsize=9, ec="#333333", weight="normal", tc="black"):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.02,rounding_size=0.06",
                                fc=fc, ec=ec, lw=1.1))
    ax.text(x + w / 2, y + h / 2, label, ha="center", va="center",
            fontsize=fontsize, weight=weight, color=tc)


def arrow(ax, x1, y1, x2, y2, label="", dashed=False, fs=7.5):
    ax.add_patch(FancyArrowPatch((x1, y1), (x2, y2), arrowstyle="-|>", mutation_scale=12,
                                 color="#444444", lw=1.1,
                                 linestyle="--" if dashed else "-"))
    if label:
        ax.text((x1 + x2) / 2, (y1 + y2) / 2 + 0.12, label, fontsize=fs,
                ha="center", color="#444444", style="italic")


# ---------------------------------------------------------------- architecture
fig, ax = plt.subplots(figsize=(12, 8))
ax.set_xlim(0, 12); ax.set_ylim(0, 8); ax.axis("off")
ax.set_title("AquaOps ON — container & module view (Unit 3 design)", fontsize=13, pad=14)

box(ax, 0.3, 5.6, 1.9, 1.0, "Operator\n(browser)", C_EXT, weight="bold")
box(ax, 0.3, 3.4, 1.9, 1.0, "Nightly batch\n(APScheduler)", C_EXT, weight="bold")
box(ax, 0.3, 1.2, 1.9, 1.0, "Lab CSV /\nAPI feeds", C_EXT, weight="bold")

box(ax, 3.0, 6.6, 6.4, 0.9, "FastAPI app core (Uvicorn) — REST /api/v1 + auth + idempotency", C_APP, weight="bold", tc="white")

mods = [
    ("M5 Dashboard\nJinja2 + HTMX", 3.0, 5.2),
    ("M4 Rule engine\nO. Reg. 170/03 YAML", 5.2, 5.2),
    ("M3 Scheduler\ngreedy + 2-opt", 7.4, 5.2),
    ("M2 Anomaly det.\nEWMA + IsoForest", 3.0, 3.6),
    ("M1 Ingestion &\nregistry", 5.2, 3.6),
    ("M6 Evaluation\nfrozen metrics", 7.4, 3.6),
]
for label, x, y in mods:
    box(ax, x, y, 2.0, 1.2, label, C_MOD, weight="bold")

box(ax, 10.0, 5.2, 1.7, 1.2, "rules/\noreg170.yaml", C_DATA)
box(ax, 10.0, 3.4, 1.7, 1.2, "SQLAlchemy\nSQLite / PG", C_DATA)
box(ax, 10.0, 1.6, 1.7, 1.2, "WNTR/EPANET\nsim (offline)", C_DATA)
box(ax, 3.0, 1.6, 6.4, 1.0, "data/networks: maple_creek.inp · points registry · 48 h chlorine ground truth", C_DATA)

arrow(ax, 2.2, 6.1, 3.0, 6.9, "HTMX\nrequests")
arrow(ax, 2.2, 3.9, 3.0, 4.2, "nightly\nbatch")
arrow(ax, 2.2, 1.7, 3.0, 2.0, "lab\nupload")
arrow(ax, 6.2, 6.6, 6.2, 6.4)
for x in (4.0, 6.2, 8.4):
    arrow(ax, x, 5.2, x, 4.8)
    arrow(ax, x, 3.6, x, 2.6, dashed=True)
arrow(ax, 9.4, 5.8, 10.0, 5.8, "loads")
arrow(ax, 9.4, 4.2, 10.0, 4.0, "persist")
arrow(ax, 10.85, 2.8, 10.85, 3.4, dashed=True)
ax.text(10.72, 3.05, "ground truth", fontsize=7.5, ha="right", color="#444444", style="italic")
ax.text(3.0, 1.02, "Legend:    ──►  synchronous request/response          ┈┈►  scheduled or batch flow",
        fontsize=8, style="italic", color="#444444")
ax.text(3.0, 0.62, "All persistence is mediated by the owning module (M1–M6); ingest is idempotent on "
                   "(sampling point, measured time).",
        fontsize=8, style="italic", color="#444444")

fig.savefig(OUT / "architecture.png", bbox_inches="tight", dpi=150)
plt.close(fig)

# ------------------------------------------------------------------------- ERD
fig, ax = plt.subplots(figsize=(12, 8))
ax.set_xlim(0, 12); ax.set_ylim(0, 8); ax.axis("off")
ax.set_title("AquaOps ON — data model overview (Unit 3 design)", fontsize=13, pad=14)

ent = [
    ("WATER_SYSTEM\nid · name · population\nnetwork_inp", 0.4, 6.2, C_APP, "white"),
    ("SAMPLING_POINT\nid · node_id · x,y,elev\nlocation_type", 3.2, 6.2, C_MOD, "black"),
    ("CHLORINE_READING\npoint_id · measured_at\nfree_chlorine_mg_l · source", 6.4, 6.2, C_MOD, "black"),
    ("ANOMALY_ALERT\nmodel · severity\nreason_codes · context", 9.6, 6.2, C_MOD, "black"),
    ("REG_RULE\nid · version · payload\nverified", 0.4, 3.4, C_DATA, "black"),
    ("REQUIRED_TASK\nrule_id · window\nstatus", 3.2, 3.4, C_MOD, "black"),
    ("ROUTE_PLAN\nweek · status\nsolver_ms · total_km", 6.4, 3.4, C_MOD, "black"),
    ("ROUTE_STOP\nseq · task_id · eta\ntravel_km", 9.6, 3.4, C_MOD, "black"),
    ("VEHICLE / OPERATOR\nlabel · depot\nshift minutes", 0.4, 0.8, C_DATA, "black"),
    ("LAB_RESULT_UPLOAD\nfile · rows\naccepted/rejected", 3.2, 0.8, C_DATA, "black"),
    ("EVAL_RUN / EVAL_METRIC\ngit_sha · metric\nvalue · target · pass", 6.4, 0.8, C_DATA, "black"),
]
for label, x, y, fc, tc in ent:
    box(ax, x, y, 2.2, 1.5, label, fc, fontsize=8.5, tc=tc)

rel = [
    ((2.6, 7.0), (3.2, 7.0), "1 : *"),
    ((5.4, 7.0), (6.4, 7.0), "1 : *"),
    ((8.6, 7.0), (9.6, 7.0), "1 : *"),
    ((1.5, 6.2), (1.5, 4.9), "loads"),
    ((2.6, 4.2), (3.2, 4.2), "1 : *"),
    ((5.4, 4.2), (6.4, 4.2), "* : 1"),
    ((8.6, 4.2), (9.6, 4.2), "1 : *"),
    ((7.5, 3.4), (7.5, 2.3), "asserts"),
]
for (x1, y1), (x2, y2), lbl in rel:
    arrow(ax, x1, y1, x2, y2, lbl)
# provenance/crew links noted textually to keep the overview uncluttered
ax.text(0.4, 0.42, "LAB_RESULT_UPLOAD 1───* CHLORINE_READING (provenance)", fontsize=7.5, style="italic", color="#555555")
ax.text(0.4, 0.12, "VEHICLE / OPERATOR 1───* ROUTE_PLAN (crew assignment)", fontsize=7.5, style="italic", color="#555555")

fig.savefig(OUT / "erd.png", bbox_inches="tight", dpi=150)
plt.close(fig)

print(f"diagrams written: {OUT}/architecture.png, {OUT}/erd.png")
