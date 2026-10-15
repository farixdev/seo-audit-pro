# -*- coding: utf-8 -*-
"""
Charts — premium, brand-consistent matplotlib visuals rendered to PNG (for the
.docx) and base64 (for the in-app HTML tabs).

Every renderer is defensive: on any failure it returns None so the report still
generates without that chart. matplotlib uses the Agg backend (no display); all
PNGs live in one temp directory owned by a ChartRenderer instance and are cleaned
up after the report is written.
"""

import base64
import io
import os
import tempfile

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import Wedge, Circle, FancyBboxPatch  # noqa: E402

# ---- brand palette ---------------------------------------------------------
BRAND = "#2E7DF2"
BRAND_DK = "#1E5FD0"
INK = "#1B2430"
SLATE = "#46515F"
MUTED = "#8B95A3"
GOOD = "#1A9D55"
WARN = "#E8A400"
BAD = "#E5484D"
TRACK = "#EDF1F6"
GRID = "#E6EAF0"

PRIORITY_COLORS = {"Critical": BAD, "High": "#F0703A", "Medium": WARN,
                   "Low": BRAND}

plt.rcParams.update({
    "font.family": "Segoe UI, DejaVu Sans, sans-serif",
    "font.size": 11,
    "axes.edgecolor": GRID,
    "axes.labelcolor": SLATE,
    "text.color": INK,
    "xtick.color": MUTED,
    "ytick.color": INK,
    "figure.dpi": 160,
    "savefig.dpi": 160,
    "axes.linewidth": 0.8,
})


def score_color(v):
    if v is None:
        return MUTED
    if v >= 90:
        return GOOD
    if v >= 50:
        return WARN
    return BAD


def _rating_color(rating):
    return {"good": GOOD, "needs-improvement": WARN, "poor": BAD}.get(
        rating, MUTED)


class ChartRenderer:
    """Renders charts to a private temp dir; returns {'png':path,'b64':str}."""

    def __init__(self):
        self.dir = tempfile.mkdtemp(prefix="seoaudit_charts_")

    def _save(self, fig, name, transparent=False):
        try:
            path = os.path.join(self.dir, "%s.png" % name)
            fc = "none" if transparent else "white"
            fig.savefig(path, bbox_inches="tight", facecolor=fc, pad_inches=0.06)
            buf = io.BytesIO()
            fig.savefig(buf, format="png", bbox_inches="tight", facecolor=fc,
                        pad_inches=0.06)
            b64 = base64.b64encode(buf.getvalue()).decode("ascii")
            return {"png": path, "b64": b64}
        except Exception:
            return None
        finally:
            plt.close(fig)

    # -- overall score gauge (zoned semicircle + needle) -------------------
    def gauge(self, score, grade, transparent=False):
        try:
            score = 0 if score is None else score
            fig, ax = plt.subplots(figsize=(4.6, 2.9))
            ax.set_xlim(-1.25, 1.25)
            ax.set_ylim(-0.32, 1.18)
            ax.axis("off")
            ax.set_aspect("equal")
            R, w = 1.0, 0.26
            # colored zones (red 0-50, amber 50-90, green 90-100), soft
            zones = [(0.0, 0.5, BAD), (0.5, 0.9, WARN), (0.9, 1.0, GOOD)]
            for a, b, col in zones:
                ax.add_patch(Wedge((0, 0), R, 180 - 180 * b, 180 - 180 * a,
                                   width=w, facecolor=col, alpha=0.22,
                                   edgecolor="none"))
            # active zone highlighted at full strength (value arc)
            frac = max(0.0, min(1.0, score / 100.0))
            col = score_color(score)
            ax.add_patch(Wedge((0, 0), R, 180 - 180 * frac, 180, width=w,
                               facecolor=col, edgecolor="none"))
            # ticks
            for t in (0, 25, 50, 75, 100):
                ang = np.radians(180 - 180 * t / 100.0)
                x0, y0 = (R + 0.02) * np.cos(ang), (R + 0.02) * np.sin(ang)
                x1, y1 = (R + 0.08) * np.cos(ang), (R + 0.08) * np.sin(ang)
                ax.plot([x0, x1], [y0, y1], color=MUTED, lw=1.1)
            # needle
            ang = np.radians(180 - 180 * frac)
            ax.plot([0, (R - w - 0.04) * np.cos(ang)],
                    [0, (R - w - 0.04) * np.sin(ang)], color=INK, lw=2.4,
                    solid_capstyle="round", zorder=5)
            ax.add_patch(Circle((0, 0), 0.045, facecolor=INK, zorder=6))
            # labels
            ax.text(0, 0.40, "%d" % score, ha="center", va="center",
                    fontsize=44, fontweight="bold", color=col)
            ax.text(0, 0.14, "out of 100", ha="center", va="center",
                    fontsize=11, color=MUTED)
            ax.text(0, 0.86, "GRADE %s" % grade, ha="center", va="center",
                    fontsize=16, fontweight="bold", color=INK)
            return self._save(fig, "gauge", transparent)
        except Exception:
            return None

    # -- per-category horizontal bars with track ---------------------------
    def category_bars(self, dimensions):
        try:
            dims = list(dimensions)
            if not dims:
                return None
            labels = [d["label"] for d in dims]
            scores = [d["score"] for d in dims]
            colors = [score_color(s) for s in scores]
            fig, ax = plt.subplots(figsize=(7.0, 0.56 * len(dims) + 1.0))
            y = np.arange(len(dims))
            ax.barh(y, [100] * len(dims), color=TRACK, height=0.60, zorder=1)
            ax.barh(y, scores, color=colors, height=0.60, zorder=3)
            ax.set_xlim(0, 108)
            ax.invert_yaxis()
            ax.set_yticks(y)
            ax.set_yticklabels(labels, fontsize=11, color=INK)
            ax.set_xticks([])
            for s in ("top", "right", "left", "bottom"):
                ax.spines[s].set_visible(False)
            for i, sc in enumerate(scores):
                ax.text(102, i, "%d" % sc, va="center", ha="left", fontsize=11,
                        fontweight="bold", color=colors[i])
            ax.tick_params(length=0)
            return self._save(fig, "category_bars")
        except Exception:
            return None

    # -- issue severity donut ----------------------------------------------
    def severity_donut(self, counts):
        try:
            order = ["Critical", "High", "Medium", "Low"]
            vals = [counts.get(k, 0) for k in order]
            if sum(vals) == 0:
                # all-clear donut
                fig, ax = plt.subplots(figsize=(4.0, 3.2))
                ax.pie([1], colors=[GOOD], startangle=90,
                       wedgeprops=dict(width=0.42, edgecolor="white"))
                ax.text(0, 0.08, "0", ha="center", va="center", fontsize=30,
                        fontweight="bold", color=GOOD)
                ax.text(0, -0.24, "issues", ha="center", va="center",
                        fontsize=11, color=MUTED)
                ax.set_aspect("equal")
                return self._save(fig, "severity_donut")
            labels = [k for k, v in zip(order, vals) if v]
            data = [v for v in vals if v]
            colors = [PRIORITY_COLORS[k] for k, v in zip(order, vals) if v]
            fig, ax = plt.subplots(figsize=(4.6, 3.2))
            wedges, _t = ax.pie(data, colors=colors, startangle=90,
                                counterclock=False,
                                wedgeprops=dict(width=0.42, edgecolor="white",
                                                linewidth=2))
            ax.text(0, 0.12, "%d" % sum(data), ha="center", va="center",
                    fontsize=30, fontweight="bold", color=INK)
            ax.text(0, -0.24, "issues", ha="center", va="center", fontsize=11,
                    color=MUTED)
            ax.legend(wedges, ["%s  (%d)" % (l, d) for l, d in zip(labels, data)],
                      loc="center left", bbox_to_anchor=(1.02, 0.5),
                      frameon=False, fontsize=11, handlelength=1.1,
                      labelspacing=0.7)
            ax.set_aspect("equal")
            return self._save(fig, "severity_donut")
        except Exception:
            return None

    # -- PSI mobile vs desktop grouped bars --------------------------------
    def psi_grouped(self, psi):
        try:
            mob = (psi.get("mobile") or {}).get("scores") or {}
            desk = (psi.get("desktop") or {}).get("scores") or {}
            keys = ["performance", "seo", "accessibility", "best_practices"]
            labels = ["Performance", "SEO", "Accessibility", "Best\nPractices"]
            m = [mob.get(k) for k in keys]
            d = [desk.get(k) for k in keys]
            if not any(v is not None for v in m + d):
                return None
            m = [v or 0 for v in m]
            d = [v or 0 for v in d]
            x = np.arange(len(keys))
            wbar = 0.38
            fig, ax = plt.subplots(figsize=(7.0, 3.5))
            b1 = ax.bar(x - wbar / 2, m, wbar, label="Mobile", color=BRAND,
                        zorder=3)
            b2 = ax.bar(x + wbar / 2, d, wbar, label="Desktop", color="#8FBBF8",
                        zorder=3)
            ax.set_ylim(0, 108)
            ax.set_xticks(x)
            ax.set_xticklabels(labels, fontsize=10, color=INK)
            ax.set_yticks([0, 50, 90, 100])
            ax.axhline(90, color=GOOD, lw=0.8, ls="--", alpha=0.6, zorder=1)
            ax.axhline(50, color=WARN, lw=0.8, ls="--", alpha=0.5, zorder=1)
            ax.yaxis.grid(True, color=GRID, zorder=0)
            ax.set_axisbelow(True)
            for s in ("top", "right"):
                ax.spines[s].set_visible(False)
            for bars in (b1, b2):
                for r in bars:
                    h = r.get_height()
                    if h:
                        ax.text(r.get_x() + r.get_width() / 2, h + 1.5,
                                "%d" % h, ha="center", va="bottom", fontsize=8.5,
                                color=INK, fontweight="bold")
            ax.legend(frameon=False, fontsize=10, loc="lower center", ncol=2,
                      bbox_to_anchor=(0.5, -0.28))
            return self._save(fig, "psi_grouped")
        except Exception:
            return None

    # -- Core Web Vitals (field) -------------------------------------------
    def cwv(self, field):
        try:
            if not field or not field.get("available"):
                return None
            fm = field.get("metrics") or {}
            order = [("lcp", "LCP", 4000), ("inp", "INP", 500),
                     ("cls", "CLS", 0.25), ("fcp", "FCP", 3000),
                     ("ttfb", "TTFB", 1800)]
            rows = [(k, lbl, poor, fm.get(k)) for k, lbl, poor in order
                    if fm.get(k) and fm[k].get("p75") is not None]
            if not rows:
                return None
            fig, ax = plt.subplots(figsize=(7.0, 0.62 * len(rows) + 1.1))
            y = np.arange(len(rows))
            ax.barh(y, [1.0] * len(rows), color=TRACK, height=0.55, zorder=1)
            labels, texts = [], []
            for i, (key, lbl, poor, m) in enumerate(rows):
                val = m["p75"]
                length = min(val / (poor * 1.3), 1.0)
                ax.barh(i, length, color=_rating_color(m["rating"]),
                        height=0.55, zorder=3)
                labels.append(lbl)
                txt = ("%.2f" % val) if key == "cls" else ("%.0f ms" % val)
                texts.append("%s  ·  %s" % (txt, m["rating"]))
            ax.set_xlim(0, 1.28)
            ax.invert_yaxis()
            ax.set_yticks(y)
            ax.set_yticklabels(labels, fontsize=11, color=INK)
            ax.set_xticks([])
            for s in ("top", "right", "left", "bottom"):
                ax.spines[s].set_visible(False)
            ax.tick_params(length=0)
            for i, t in enumerate(texts):
                ax.text(1.02, i, t, va="center", ha="left", fontsize=9.5,
                        color=SLATE)
            ax.set_title("Real-user Core Web Vitals (p75)", fontsize=11.5,
                         color=INK, loc="left", pad=8, fontweight="bold")
            return self._save(fig, "cwv")
        except Exception:
            return None

    # -- top issues by impact ----------------------------------------------
    def top_issues(self, items, n=8):
        try:
            top = list(items)[:n]
            if not top:
                return None
            top = list(reversed(top))
            labels = [("%s" % it["label"])[:44] for it in top]
            vals = [it["impact"] for it in top]
            colors = [PRIORITY_COLORS.get(it["priority"], BRAND) for it in top]
            fig, ax = plt.subplots(figsize=(7.0, 0.5 * len(top) + 1.1))
            y = np.arange(len(top))
            mx = max(vals) * 1.15 or 1
            ax.barh(y, [mx] * len(top), color=TRACK, height=0.62, zorder=1)
            ax.barh(y, vals, color=colors, height=0.62, zorder=3)
            ax.set_yticks(y)
            ax.set_yticklabels(labels, fontsize=9.5, color=INK)
            ax.set_xticks([])
            ax.set_xlim(0, mx)
            for s in ("top", "right", "left", "bottom"):
                ax.spines[s].set_visible(False)
            ax.tick_params(length=0)
            for i, it in enumerate(top):
                ax.text(vals[i] + mx * 0.01, i, it["priority"], va="center",
                        ha="left", fontsize=8, color=colors[i],
                        fontweight="bold")
            ax.set_title("Top priorities (by estimated impact)", fontsize=11.5,
                         color=INK, loc="left", pad=8, fontweight="bold")
            return self._save(fig, "top_issues")
        except Exception:
            return None

    # -- category radar ----------------------------------------------------
    def radar(self, dimensions):
        try:
            dims = list(dimensions)
            if len(dims) < 3:
                return None
            labels = [d["label"].replace(" SEO", "").replace(" Quality", "")
                      for d in dims]
            vals = [d["score"] for d in dims]
            n = len(dims)
            angles = np.linspace(0, 2 * np.pi, n, endpoint=False).tolist()
            vals_c = vals + vals[:1]
            angles_c = angles + angles[:1]
            fig, ax = plt.subplots(figsize=(5.2, 5.0),
                                   subplot_kw=dict(polar=True))
            ax.set_theta_offset(np.pi / 2)
            ax.set_theta_direction(-1)
            ax.set_ylim(0, 100)
            ax.set_yticks([25, 50, 75, 100])
            ax.set_yticklabels(["25", "50", "75", "100"], color=MUTED,
                               fontsize=8)
            ax.set_xticks(angles)
            ax.set_xticklabels(labels, fontsize=9.5, color=INK)
            ax.plot(angles_c, vals_c, color=BRAND, linewidth=2, zorder=3)
            ax.fill(angles_c, vals_c, color=BRAND, alpha=0.18, zorder=2)
            for ang, v in zip(angles, vals):
                ax.plot(ang, v, "o", color=score_color(v), markersize=6,
                        zorder=4)
            ax.grid(color=GRID)
            ax.spines["polar"].set_color(GRID)
            return self._save(fig, "radar")
        except Exception:
            return None

    # -- competitor comparison --------------------------------------------
    def competitor_bars(self, target, competitors):
        try:
            rows = [target] + [c for c in competitors if c.get("score")
                               is not None]
            if len(rows) < 2:
                return None
            names = [(r.get("label") or r.get("domain") or "")[:22]
                     for r in rows]
            scores = [r.get("score") or 0 for r in rows]
            colors = [BRAND] + ["#9CB8E8"] * (len(rows) - 1)
            fig, ax = plt.subplots(figsize=(7.0, 0.6 * len(rows) + 1.0))
            y = np.arange(len(rows))
            ax.barh(y, [100] * len(rows), color=TRACK, height=0.6, zorder=1)
            ax.barh(y, scores, color=colors, height=0.6, zorder=3)
            ax.set_xlim(0, 108)
            ax.invert_yaxis()
            ax.set_yticks(y)
            ax.set_yticklabels(names, fontsize=10, color=INK)
            ax.set_xticks([])
            for s in ("top", "right", "left", "bottom"):
                ax.spines[s].set_visible(False)
            ax.tick_params(length=0)
            for i, sv in enumerate(scores):
                ax.text(102, i, "%d" % sv, va="center", fontsize=10,
                        fontweight="bold", color=colors[i] if i == 0 else INK)
            ax.set_title("Overall score vs competitors", fontsize=11.5,
                         color=INK, loc="left", pad=8, fontweight="bold")
            return self._save(fig, "competitor_bars")
        except Exception:
            return None

    def cleanup(self):
        try:
            for f in os.listdir(self.dir):
                try:
                    os.remove(os.path.join(self.dir, f))
                except OSError:
                    pass
            os.rmdir(self.dir)
        except OSError:
            pass
