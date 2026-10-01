"""Графики в тёмной палитре. Рисуем через объектный API matplotlib (без pyplot),
поэтому можно безопасно вызывать из потока, не блокируя бота."""
from __future__ import annotations

import asyncio
import io
import re
import threading

from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from matplotlib.patches import FancyBboxPatch
from matplotlib.ticker import FuncFormatter

from .money import fmt_short

BG = "#14161c"
PANEL = "#1b1e26"
TEXT = "#e8eaf0"
MUTED = "#8b91a1"
GRID = "#2a2e3a"
GREEN = "#34d399"
RED = "#f87171"
BLUE = "#6c8cff"
AMBER = "#f59e0b"
PALETTE = ["#6c8cff", "#34d399", "#f59e0b", "#f472b6", "#a78bfa", "#22d3ee", "#f87171", "#94a3b8"]

_lock = threading.Lock()  # шрифтовой кэш matplotlib не любит гонок


def clean(label: str) -> str:
    """Убирает эмодзи: шрифт графиков их не рисует и оставляет квадраты."""
    return re.sub(r"[^\w\s\-&.,/()%+]", "", label, flags=re.UNICODE).strip() or "—"


def _figure(width: float = 8, height: float = 5) -> Figure:
    fig = Figure(figsize=(width, height), dpi=150, facecolor=BG)
    FigureCanvasAgg(fig)
    return fig


def _style(ax) -> None:
    ax.set_facecolor(PANEL)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
    ax.tick_params(colors=MUTED, labelsize=9)
    ax.grid(axis="y", color=GRID, linewidth=0.6, alpha=0.8)
    ax.set_axisbelow(True)
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: fmt_short(int(v))))


def _png(fig: Figure) -> bytes:
    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", facecolor=BG)
    return buffer.getvalue()


def _title(fig: Figure, title: str, subtitle: str = "") -> None:
    """Заголовок в абсолютных отступах: на низких картинках не слипается с подписью."""
    height = fig.get_figheight()
    fig.text(0.05, 1 - 0.28 / height, title, color=TEXT, fontsize=15, fontweight="bold", va="top")
    if subtitle:
        fig.text(0.05, 1 - 0.68 / height, subtitle, color=MUTED, fontsize=10, va="top")


# ---------------------------------------------------------------- рисовалки

def _donut(title: str, subtitle: str, rows: list[tuple[str, int]], total_text: str) -> bytes | None:
    rows = [(name, value) for name, value in rows if value > 0]
    if not rows:
        return None
    top = rows[:7]
    rest = sum(value for _, value in rows[7:])
    if rest:
        top.append(("Другое", rest))
    names = [clean(n) for n, _ in top]
    values = [v for _, v in top]
    total = sum(values)

    fig = _figure(8, 5)
    _title(fig, title, subtitle)
    ax = fig.add_axes([0.02, 0.04, 0.46, 0.78])
    ax.pie(values, colors=PALETTE[:len(values)], startangle=90, counterclock=False,
           wedgeprops={"width": 0.38, "edgecolor": BG, "linewidth": 2})
    ax.text(0, 0.07, total_text, ha="center", va="center", color=TEXT, fontsize=15, fontweight="bold")
    ax.text(0, -0.15, "всего", ha="center", va="center", color=MUTED, fontsize=9)
    ax.set_aspect("equal")

    legend = fig.add_axes([0.52, 0.06, 0.46, 0.76])
    legend.axis("off")
    step = 1 / max(len(values), 6)
    for i, (name, value) in enumerate(zip(names, values)):
        y = 1 - (i + 0.5) * step
        legend.add_patch(FancyBboxPatch(
            (0.0, y - 0.025), 0.045, 0.05, boxstyle="round,pad=0,rounding_size=0.012",
            facecolor=PALETTE[i], edgecolor="none", transform=legend.transAxes))
        legend.text(0.08, y, name[:22], color=TEXT, fontsize=10.5, va="center", transform=legend.transAxes)
        legend.text(1.0, y, f"{value / total * 100:.0f}%  ·  {fmt_short(value)}", color=MUTED,
                    fontsize=10, va="center", ha="right", transform=legend.transAxes)
    return _png(fig)


def _bars(title: str, subtitle: str, series: list[tuple[str, int, int]]) -> bytes | None:
    if not any(income or expense for _, income, expense in series):
        return None
    fig = _figure(8, 5)
    _title(fig, title, subtitle)
    ax = fig.add_axes([0.09, 0.14, 0.88, 0.66])
    _style(ax)
    labels = [name for name, _, _ in series]
    xs = range(len(series))
    width = 0.38
    ax.bar([x - width / 2 for x in xs], [i for _, i, _ in series], width, color=GREEN, label="Доходы")
    ax.bar([x + width / 2 for x in xs], [e for _, _, e in series], width, color=RED, label="Расходы")
    net = [i - e for _, i, e in series]
    ax.plot(list(xs), net, color=AMBER, marker="o", linewidth=2, markersize=5, label="Итог")
    ax.axhline(0, color=GRID, linewidth=1)
    ax.set_xticks(list(xs), labels)
    legend = ax.legend(loc="lower right", frameon=False, ncol=3, fontsize=9, bbox_to_anchor=(1, 1.02))
    for text in legend.get_texts():
        text.set_color(MUTED)
    return _png(fig)


def _line(title: str, subtitle: str, xs: list, ys: list[int], color: str,
          marker: bool = False, fill: bool = True, guide: float | None = None) -> bytes | None:
    if not ys or not any(ys):
        return None
    fig = _figure(8, 5)
    _title(fig, title, subtitle)
    ax = fig.add_axes([0.09, 0.14, 0.88, 0.66])
    _style(ax)
    positions = list(range(len(xs)))
    ax.plot(positions, ys, color=color, linewidth=2.4, marker="o" if marker else None, markersize=5)
    if fill:
        ax.fill_between(positions, ys, min(0, min(ys)), color=color, alpha=0.16)
    if guide:
        ax.axhline(guide, color=AMBER, linewidth=1.4, linestyle="--")
        ax.text(len(xs) - 1, guide, "  лимит", color=AMBER, fontsize=9, va="bottom", ha="right")
    step = max(1, len(xs) // 8)
    ax.set_xticks(positions[::step], [str(x) for x in xs][::step])
    return _png(fig)


def _hbars_frame(fig: Figure, count: int):
    ax = fig.add_axes([0.27, 0.06, 0.70, 0.72])
    ax.set_facecolor(BG)
    for side in ("top", "right", "bottom", "left"):
        ax.spines[side].set_visible(False)
    ax.tick_params(colors=TEXT, labelsize=10.5, length=0)
    ax.set_xticks([])
    ax.set_ylim(-0.6, count - 0.4)
    return ax


def _shares(title: str, subtitle: str, rows: list[tuple[str, int]]) -> bytes | None:
    """Полосы в общем масштабе: видно, какой источник тянет весь доход."""
    rows = [(n, v) for n, v in rows if v > 0][:8]
    if not rows:
        return None
    total = sum(v for _, v in rows)
    fig = _figure(8, max(3.2, 1.5 + 0.55 * len(rows)))
    _title(fig, title, subtitle)
    ax = _hbars_frame(fig, len(rows))
    peak = max(v for _, v in rows)
    ys = list(range(len(rows)))[::-1]
    for i, (y, (name, value)) in enumerate(zip(ys, rows)):
        ax.barh(y, value, color=PALETTE[i % len(PALETTE)], height=0.55)
        ax.text(value + peak * 0.02, y, f"{fmt_short(value)}  ·  {value / total * 100:.0f}%",
                color=MUTED, fontsize=10, va="center")
    ax.set_yticks(ys, [clean(n)[:20] for n, _ in rows])
    ax.set_xlim(0, peak * 1.45)
    return _png(fig)


def _progress(title: str, subtitle: str, rows: list[tuple[str, int, int]]) -> bytes | None:
    """Копилки с целью: одинаковые дорожки, заполнение в процентах от цели."""
    rows = [(n, v, t) for n, v, t in rows if t][:8]
    if not rows:
        return None
    fig = _figure(8, max(3.2, 1.5 + 0.6 * len(rows)))
    _title(fig, title, subtitle)
    ax = _hbars_frame(fig, len(rows))
    ys = list(range(len(rows)))[::-1]
    for y, (name, value, target) in zip(ys, rows):
        ratio = max(0.0, value / target)
        done = ratio >= 1
        ax.barh(y, 1, color=GRID, height=0.5)
        ax.barh(y, min(ratio, 1), color=GREEN if done else BLUE, height=0.5)
        ax.text(0.02, y, f"{min(ratio * 100, 999):.0f}%", color=TEXT, fontsize=10,
                fontweight="bold", va="center")
        ax.text(1.03, y, f"{fmt_short(value)} из {fmt_short(target)}", color=MUTED,
                fontsize=9.5, va="center")
    ax.set_yticks(ys, [clean(n)[:20] for n, _, _ in rows])
    ax.set_xlim(0, 1.42)
    return _png(fig)


# ---------------------------------------------------------------- публичный API

async def _run(func, *args, **kwargs) -> bytes | None:
    def work():
        with _lock:
            return func(*args, **kwargs)
    return await asyncio.to_thread(work)


async def expense_donut(title, subtitle, rows, total_text):
    return await _run(_donut, title, subtitle, rows, total_text)


async def income_bars(title, subtitle, rows):
    return await _run(_shares, title, subtitle, rows)


async def months_bars(title, subtitle, series):
    return await _run(_bars, title, subtitle, series)


async def cumulative_line(title, subtitle, series, limit=None):
    xs = [day for day, _ in series]
    ys = [value for _, value in series]
    return await _run(_line, title, subtitle, xs, ys, RED, False, True, limit)


async def capital_line(title, subtitle, series):
    xs = [label for label, _ in series]
    ys = [value for _, value in series]
    return await _run(_line, title, subtitle, xs, ys, BLUE, True, True)


async def goals_bars(title, subtitle, rows):
    return await _run(_progress, title, subtitle, rows)
