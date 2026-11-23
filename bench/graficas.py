#!/usr/bin/env python3
"""
graficas.py — Representación visual de las métricas medidas.
Lee bench/resultados/metricas.json y produce los PNG del informe.

Uso:  python3 bench/graficas.py
"""
import json
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter

BASE = os.path.dirname(os.path.abspath(__file__))
RES = os.path.join(BASE, "resultados")
OUT = os.path.join(os.path.dirname(BASE), "docs", "figuras")
os.makedirs(OUT, exist_ok=True)

# Paleta categórica validada (separación suficiente para daltonismo deutan/tritan)
AZUL = "#2a78d6"    # PostgreSQL  (capa ACID)
NARANJA = "#eb6834"  # MongoDB     (capa BASE)
AQUA = "#1baf7a"    # Híbrido / vía optimizada
TINTA = "#0b0b0b"
TINTA2 = "#52514e"
SUP = "#fcfcfb"

plt.rcParams.update({
    "figure.facecolor": SUP, "axes.facecolor": SUP,
    "font.size": 10, "font.family": "DejaVu Sans",
    "axes.edgecolor": "#d8d7d2", "axes.labelcolor": TINTA2,
    "xtick.color": TINTA2, "ytick.color": TINTA2,
    "axes.spines.top": False, "axes.spines.right": False,
    "axes.titlecolor": TINTA, "axes.grid": True,
    "grid.color": "#ececea", "grid.linewidth": 0.8,
})

with open(os.path.join(RES, "metricas.json"), encoding="utf-8") as f:
    D = json.load(f)
M = {(m["operacion"], m["motor"]): m for m in D["mediciones"]}


def p50(op, motor):
    return M[(op, motor)]["p50_ms"]


def p95(op, motor):
    return M[(op, motor)]["p95_ms"]


def etiquetar(ax, barras, fmt="{:.2f}", dy=1.04, color=TINTA):
    for b in barras:
        v = b.get_width() if b.get_width() > 0 and ax.get_yscale() != "log" and False else b.get_height()
        ax.annotate(fmt.format(v), (b.get_x() + b.get_width() / 2, b.get_height() * dy),
                    ha="center", va="bottom", fontsize=8.5, color=color)


# =====================================================================
# Figura 1 — Latencia p50 por operación (escala logarítmica)
# =====================================================================
ops = [
    ("Ingesta lote 5.000 lecturas", "Ingesta\nlote 5.000"),
    ("Últimas 50 lecturas de una admisión", "Lectura puntual\n50 lecturas"),
    ("Alarmas últimas 6 h (índice parcial)", "Alarmas\núltimas 6 h"),
    ("Tendencia horaria de una admisión", "Tendencia horaria\n1 admisión"),
    ("Top-10 admisiones por alarmas (dataset completo)", "Top-10 alarmas\n1.008.000 reg."),
]
fig, ax = plt.subplots(figsize=(9.2, 4.6))
x = range(len(ops))
w = 0.38
b1 = ax.bar([i - w / 2 for i in x], [p50(o[0], "PostgreSQL") for o in ops], w,
            label="PostgreSQL (ACID)", color=AZUL, zorder=3)
b2 = ax.bar([i + w / 2 for i in x], [p50(o[0], "MongoDB") for o in ops], w,
            label="MongoDB (BASE)", color=NARANJA, zorder=3)
ax.set_yscale("log")
ax.set_ylabel("Latencia mediana p50 (ms, escala logarítmica)")
ax.set_title("Latencia por operación sobre el mismo conjunto de 1.008.000 lecturas",
             fontsize=12, pad=14, loc="left")
ax.set_xticks(list(x))
ax.set_xticklabels([o[1] for o in ops])
ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:g}"))
etiquetar(ax, b1)
etiquetar(ax, b2)
ax.legend(frameon=False, loc="upper left", ncols=2)
ax.set_ylim(0.05, 3000)
fig.tight_layout()
fig.savefig(os.path.join(OUT, "fig1_latencia_operaciones.png"), dpi=200)
plt.close(fig)

# =====================================================================
# Figura 2 — Ingesta: rendimiento en registros por segundo
# =====================================================================
LOTE = 5000
casos = [
    ("PostgreSQL\nINSERT por lotes", LOTE / (p50("Ingesta lote 5.000 lecturas", "PostgreSQL") / 1000), AZUL),
    ("MongoDB\ninsert_many", LOTE / (p50("Ingesta lote 5.000 lecturas", "MongoDB") / 1000), NARANJA),
    ("PostgreSQL\nCOPY nativo", LOTE / (p50("Ingesta lote 5.000 (COPY nativo)", "PostgreSQL") / 1000), AQUA),
]
fig, ax = plt.subplots(figsize=(7.2, 4.2))
bs = ax.bar([c[0] for c in casos], [c[1] for c in casos],
            color=[c[2] for c in casos], width=0.55, zorder=3)
for b, c in zip(bs, casos):
    ax.annotate(f"{c[1]:,.0f}".replace(",", "."), (b.get_x() + b.get_width() / 2, b.get_height()),
                ha="center", va="bottom", fontsize=10, color=TINTA, xytext=(0, 3),
                textcoords="offset points")
ax.set_ylabel("Registros insertados por segundo")
ax.set_title("Rendimiento de ingesta (lote de 5.000 lecturas, p50)",
             fontsize=12, pad=14, loc="left")
ax.set_ylim(0, max(c[1] for c in casos) * 1.18)
ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:,.0f}".replace(",", ".")))
fig.tight_layout()
fig.savefig(os.path.join(OUT, "fig2_ingesta_throughput.png"), dpi=200)
plt.close(fig)

# =====================================================================
# Figura 3 — Efecto de los índices sugeridos por IA (antes / después)
# =====================================================================
grupos = [
    ("Alarmas 6 h\nMongoDB", p50("Alarmas últimas 6 h (índice parcial)", "MongoDB"),
     p50("Alarmas últimas 6 h — índice cobertura v1", "MongoDB")),
    ("Top-10 alarmas\nMongoDB v1", p50("Top-10 admisiones por alarmas (dataset completo)", "MongoDB"),
     p50("Top-10 por alarmas — índice cobertura v1", "MongoDB")),
    ("Top-10 alarmas\nMongoDB v2", p50("Top-10 admisiones por alarmas (dataset completo)", "MongoDB"),
     p50("Top-10 por alarmas — índice cobertura v2", "MongoDB")),
    ("Top-10 alarmas\nPostgreSQL", p50("Top-10 admisiones por alarmas (dataset completo)", "PostgreSQL"),
     p50("Top-10 por alarmas — índice cobertura v1", "PostgreSQL")),
    ("Tablero UCI\nhíbrido", p50("Tablero UCI: 20 camas con último signo vital", "Híbrido PG+Mongo"),
     p50("Tablero UCI — orquestación optimizada", "Híbrido PG+Mongo")),
]
fig, ax = plt.subplots(figsize=(9.2, 4.6))
x = range(len(grupos))
w = 0.38
b1 = ax.bar([i - w / 2 for i in x], [g[1] for g in grupos], w,
            label="Antes de optimizar", color="#b9b8b2", zorder=3)
# Color por resultado: verde si la optimización mejoró, rojo si empeoró o no
# cambió. Pintar de verde una barra que creció sería mentir con el color.
colores = [AQUA if g[2] < g[1] * 0.95 else "#e34948" for g in grupos]
b2 = ax.bar([i + w / 2 for i in x], [g[2] for g in grupos], w,
            label="Después de optimizar", color=colores, zorder=3)
ax.set_yscale("log")
ax.set_ylabel("Latencia mediana p50 (ms, escala logarítmica)")
ax.set_title("Efecto de las optimizaciones: no toda sugerencia de índice mejora el plan",
             fontsize=12, pad=14, loc="left")
ax.set_xticks(list(x))
ax.set_xticklabels([g[0] for g in grupos])
ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:g}"))
etiquetar(ax, b1)
etiquetar(ax, b2)
from matplotlib.patches import Patch
ax.legend(handles=[Patch(facecolor="#b9b8b2", label="Antes de optimizar"),
                   Patch(facecolor=AQUA, label="Después: mejoró"),
                   Patch(facecolor="#e34948", label="Después: igual o peor")],
          frameon=False, loc="upper left", ncols=3, fontsize=9)
ax.set_ylim(1, 9000)
# Marca el caso en que la optimización EMPEORÓ el resultado
peor = 1
ax.annotate("+44 %", (peor + w / 2, grupos[peor][2] * 1.9), ha="center",
            fontsize=9, color="#c0392b", fontweight="bold")
fig.tight_layout()
fig.savefig(os.path.join(OUT, "fig3_efecto_indices.png"), dpi=200)
plt.close(fig)

# =====================================================================
# Figura 4 — Costo de almacenamiento del mismo millón de registros
# =====================================================================
alm = D["almacenamiento"]
fig, ax = plt.subplots(figsize=(7.2, 4.2))
motores = ["PostgreSQL", "MongoDB"]
datos = [alm["PostgreSQL"]["datos_MB"], alm["MongoDB"]["datos_comprimidos_MB"]]
indices = [alm["PostgreSQL"]["indices_MB"], alm["MongoDB"]["indices_MB"]]
b1 = ax.bar(motores, datos, 0.5, label="Datos en disco", color=[AZUL, NARANJA], zorder=3)
b2 = ax.bar(motores, indices, 0.5, bottom=datos, label="Índices",
            color=["#a9c9ee", "#f6b79c"], zorder=3, edgecolor=SUP, linewidth=2)
for i, m in enumerate(motores):
    ax.annotate(f"{datos[i]:.0f} MB", (i, datos[i] / 2), ha="center", va="center",
                fontsize=9, color="white", fontweight="bold")
    ax.annotate(f"{indices[i]:.0f} MB", (i, datos[i] + indices[i] / 2), ha="center",
                va="center", fontsize=9, color=TINTA)
    ax.annotate(f"total {datos[i] + indices[i]:.0f} MB", (i, datos[i] + indices[i]),
                ha="center", va="bottom", fontsize=9.5, color=TINTA,
                xytext=(0, 4), textcoords="offset points")
ax.set_ylabel("Espacio ocupado (MB)")
ax.set_title("Costo en disco de 1.008.000 lecturas idénticas\n"
             "(MongoDB con compresión WiredTiger; PostgreSQL sin compresión de tabla)",
             fontsize=11.5, pad=14, loc="left")
ax.legend(frameon=False, loc="upper right")
ax.set_ylim(0, max(d + i for d, i in zip(datos, indices)) * 1.25)
fig.tight_layout()
fig.savefig(os.path.join(OUT, "fig4_almacenamiento.png"), dpi=200)
plt.close(fig)

print("✓ Figuras generadas en", OUT)
for f in sorted(os.listdir(OUT)):
    print("   ", f)
