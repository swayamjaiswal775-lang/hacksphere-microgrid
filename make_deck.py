"""Build the presentation PDF for the submission form (16:9, 8 slides).

    python tools/make_deck.py --team "Team Name" --url https://your-app.catalystappsail.in \
        --repo https://github.com/you/repo --quickml-rmse 11.9 --out Presentation.pdf

All options are optional. A line that needs a value you did not give is simply left out,
so the PDF never contains placeholder text. Needs: pip install reportlab
"""
import argparse
import csv
import os
from collections import defaultdict

from reportlab.lib.utils import ImageReader, simpleSplit
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
W, H = 960, 540

PAPER, PANEL, INK, INK2, LINE = "#F2F4F6", "#FFFFFF", "#13202E", "#4A5A6B", "#D9DFE5"
GRID, STORE, OVER, WARN = "#2F6DB5", "#0B8A62", "#D62839", "#B97F00"
GRID_T, STORE_T, OVER_T = "#E3EDF8", "#DDF3EA", "#FBE4E7"

# ---------------------------------------------------------------- fonts (Poppins if present, else Helvetica)
FONT, FONT_M, FONT_B = "Helvetica", "Helvetica", "Helvetica-Bold"
for d in ("/usr/share/fonts/truetype/google-fonts", os.path.expanduser("~/.fonts"), "C:/Windows/Fonts"):
    try:
        pdfmetrics.registerFont(TTFont("Pop", os.path.join(d, "Poppins-Regular.ttf")))
        pdfmetrics.registerFont(TTFont("PopM", os.path.join(d, "Poppins-Medium.ttf")))
        pdfmetrics.registerFont(TTFont("PopB", os.path.join(d, "Poppins-Bold.ttf")))
        FONT, FONT_M, FONT_B = "Pop", "PopM", "PopB"
        break
    except Exception:
        continue


def hexc(c, h):
    c.setFillColor(h)
    c.setStrokeColor(h)


class Deck:
    def __init__(self, path, meta):
        self.c = canvas.Canvas(path, pagesize=(W, H))
        self.c.setTitle("Microgrid Dispatch Console - HackSphere 3.0")
        self.c.setAuthor(meta.get("team") or "HackSphere 3.0 team")
        self.n = 0
        self.meta = meta

    # -- primitives
    def page(self, title=None, footer=True):
        if self.n:
            self.c.showPage()
        self.n += 1
        self.c.setFillColor(PAPER)
        self.c.rect(0, 0, W, H, stroke=0, fill=1)
        if title:
            self.text(48, H - 78, title, FONT_B, 28, INK)
        if footer:
            self.text(48, 24, "HackSphere 3.0  |  Microgrid Dispatch Console", FONT, 9.5, INK2)
            self.text(W - 48, 24, str(self.n), FONT_M, 9.5, INK2, anchor="r")

    def text(self, x, y, s, font=None, size=14, color=INK, anchor="l"):
        self.c.setFillColor(color)
        self.c.setFont(font or FONT, size)
        {"l": self.c.drawString, "r": self.c.drawRightString, "c": self.c.drawCentredString}[anchor](x, y, s)

    def para(self, x, y, s, width, font=None, size=14, color=INK, lead=None, anchor_top=True):
        """Wrapped paragraph; returns the y below the last line."""
        font = font or FONT
        lead = lead or size * 1.42
        lines = simpleSplit(s, font, size, width)
        self.c.setFillColor(color)
        self.c.setFont(font, size)
        yy = y
        for ln in lines:
            self.c.drawString(x, yy, ln)
            yy -= lead
        return yy

    def box(self, x, y, w, h, fill=PANEL, stroke=LINE, r=10, lw=1):
        self.c.setFillColor(fill)
        self.c.setStrokeColor(stroke or fill)
        self.c.setLineWidth(lw)
        self.c.roundRect(x, y, w, h, r, stroke=1 if stroke else 0, fill=1)

    def chip(self, x, y, s, fill, fg="#FFFFFF", size=11, padx=10, h=22, font=None):
        font = font or FONT_M
        w = pdfmetrics.stringWidth(s, font, size) + 2 * padx
        self.c.setFillColor(fill)
        self.c.roundRect(x, y, w, h, h / 2, stroke=0, fill=1)
        self.text(x + padx, y + (h - size) / 2 + 2, s, font, size, fg)
        return w

    def arrow(self, x1, y1, x2, y2, color=INK2, dashed=False):
        c = self.c
        c.setStrokeColor(color)
        c.setFillColor(color)
        c.setLineWidth(1.6)
        c.setDash(5, 4) if dashed else c.setDash()
        c.line(x1, y1, x2, y2)
        c.setDash()
        import math
        a = math.atan2(y2 - y1, x2 - x1)
        p = c.beginPath()
        p.moveTo(x2, y2)
        p.lineTo(x2 - 9 * math.cos(a - .4), y2 - 9 * math.sin(a - .4))
        p.lineTo(x2 - 9 * math.cos(a + .4), y2 - 9 * math.sin(a + .4))
        p.close()
        c.drawPath(p, stroke=0, fill=1)

    def save(self):
        self.c.showPage()
        self.c.save()


# ---------------------------------------------------------------- data for slide 4
def hourly_means():
    s, n = defaultdict(float), defaultdict(int)
    with open(os.path.join(ROOT, "train_telemetry.csv"), newline="") as fh:
        for r in csv.DictReader(fh):
            h = int(r["timestamp"][11:13])
            s[h] += float(r["target_demand_kw"])
            n[h] += 1
    return [s[h] / n[h] for h in range(24)]


# ---------------------------------------------------------------- slides
def s_title(d):
    m = d.meta
    d.page(footer=False)
    c = d.c
    d.text(56, H - 150, "Microgrid", FONT_B, 54, INK)
    d.text(56, H - 208, "Dispatch Console", FONT_B, 54, INK)
    d.para(58, H - 252, "Smart energy microgrid and demand forecasting engine: predicts the next 24 hours "
           "of demand with Zoho Catalyst QuickML and decides when to run on the main grid or on battery storage.",
           560, FONT, 15, INK2, 22)
    y = 118
    if m.get("team"):
        d.text(58, y, m["team"], FONT_B, 17, INK)
        y -= 26
    d.text(58, y, "HackSphere 3.0  |  CSA, BITS Pilani Hyderabad Campus", FONT, 12.5, INK2)
    y -= 22
    if m.get("url"):
        d.text(58, y, "Live app:  " + m["url"], FONT_M, 12, GRID)
        y -= 20
    if m.get("repo"):
        d.text(58, y, "Code:  " + m["repo"], FONT_M, 12, GRID)
    # signature element: the dispatch ribbon for a real hot day (pattern from the sample data)
    modes = "GGGGGGSSSSSSSSSSSSSSSSSG"
    x0, cw, y0 = 56, 36.5, 34
    for i, ch in enumerate(modes):
        c.setFillColor(STORE if ch == "S" else GRID)
        c.roundRect(x0 + i * cw, y0, cw - 3, 34, 4, stroke=0, fill=1)
        d.text(x0 + i * cw + (cw - 3) / 2, y0 + 12, ch, FONT_B, 12, "#FFFFFF", "c")
    d.text(x0, y0 + 46, "One day, hour by hour: S = renewable storage, G = main grid", FONT, 10.5, INK2)


def s_flow(d):
    d.page("What the app does")
    steps = [
        ("Ingest and clean", "Hourly weather from a form or a CSV. Timestamps parsed, duplicates removed, "
                             "impossible values rejected, gaps filled, every repair reported.", GRID),
        ("Forecast", "One feature row per hour goes to the published QuickML endpoint. "
                     "One demand prediction (kW) comes back per row.", GRID),
        ("Apply the rules", "Threshold and time-of-day rules choose Renewable Storage or Main Grid "
                            "for each hour and raise critical overload alerts.", STORE),
        ("Dispatch and show", "Switching signals, battery state of charge and alerts appear on a live "
                              "dashboard. Predictions export as predictions.csv.", STORE),
    ]
    x, bw, gap, y, bh = 48, 204, 20, 190, 205
    for i, (t, body, col) in enumerate(steps):
        bx = x + i * (bw + gap)
        d.box(bx, y, bw, bh)
        d.c.setFillColor(col)
        d.c.roundRect(bx, y + bh - 8, bw, 8, 4, stroke=0, fill=1)
        d.text(bx + 16, y + bh - 44, t, FONT_B, 16, INK)
        d.para(bx + 16, y + bh - 72, body, bw - 32, FONT, 12, INK2, 17.5)
        if i < 3:
            d.arrow(bx + bw + 3, y + bh / 2, bx + bw + gap - 3, y + bh / 2)
    d.para(48, 150, "Built to run without surprises: the app needs no third-party packages, the rules are "
           "unit-tested at every boundary, and the dashboard always says which model produced the forecast.",
           860, FONT, 13, INK, 19)


def s_arch(d):
    d.page("Architecture")
    c = d.c
    # browser
    d.box(48, 270, 190, 130, PANEL)
    d.text(64, 372, "Browser", FONT_B, 15)
    d.para(64, 350, "Dashboard in static/index.html. Chart.js and the font are bundled, so no CDN is needed.",
           160, FONT, 11, INK2, 15)
    # appsail
    d.box(300, 190, 330, 270, PANEL, stroke=GRID, lw=1.6)
    d.text(318, 432, "Catalyst AppSail (Python 3.9)", FONT_B, 15, GRID)
    rows = [("app.py", "HTTP server, JSON API, serves the UI"),
            ("engine.py", "Cleaning, rules, battery model, signals"),
            ("predict.py", "Features, QuickML client, fallback")]
    yy = 396
    for name, desc in rows:
        d.box(318, yy - 40, 294, 50, PAPER, stroke=None, r=8)
        d.text(332, yy - 14, name, FONT_B, 12.5, INK)
        d.text(332, yy - 31, desc, FONT, 10.5, INK2)
        yy -= 60
    d.text(318, 210, "Standard library only. Nothing to install.", FONT, 10.5, INK2)
    # quickml
    d.box(700, 290, 212, 110, PANEL, stroke=STORE, lw=1.6)
    d.text(716, 372, "Catalyst QuickML", FONT_B, 15, STORE)
    d.para(716, 350, "Regression model trained on the 4000-hour dataset, published as an endpoint.",
           180, FONT, 11, INK2, 15)
    # fallback
    d.box(700, 190, 212, 76, PAPER, stroke=LINE)
    d.text(716, 242, "Fallback model", FONT_B, 13.5)
    d.para(716, 224, "Local boosted trees, used only if QuickML is unreachable.", 180, FONT, 10.5, INK2, 14)
    d.arrow(238, 325, 298, 325)
    d.arrow(298, 305, 238, 305)
    d.arrow(630, 345, 698, 345, STORE)
    d.arrow(630, 322, 698, 322, STORE)
    d.arrow(630, 228, 698, 228, INK2, dashed=True)
    notes = ["Calls to QuickML use an OAuth refresh token: access tokens renew automatically, a 401 is retried once, "
             "and server errors back off and retry.",
             "Secrets live in AppSail environment variables, never in the repository.",
             "Batch and one-row-per-call request styles are both supported, so the client adapts to the QuickML request format."]
    yy = 150
    for n in notes:
        yy = d.para(48, yy, n, 864, FONT, 11.5, INK2, 16) - 6


def s_model(d):
    d.page("The model: what the data says")
    means = hourly_means()
    # bar chart
    cx, cy, cw, ch = 60, 118, 470, 236
    d.box(cx - 14, cy - 46, cw + 28, ch + 118, PANEL)
    d.text(cx, cy + ch + 40, "Average demand by hour of day (training data)", FONT_M, 12.5, INK)
    top = 240.0
    bw = cw / 24
    for i, v in enumerate(means):
        hgt = ch * v / top
        peak = 18 <= i <= 22
        d.c.setFillColor(STORE if peak else GRID)
        d.c.rect(cx + i * bw + 1.5, cy, bw - 3, hgt, stroke=0, fill=1)
        if i % 3 == 0:
            d.text(cx + i * bw + bw / 2, cy - 15, "%02d" % i, FONT, 9, INK2, "c")
    d.c.setStrokeColor(LINE)
    d.c.line(cx, cy, cx + cw, cy)
    for v in (100, 200):
        yy = cy + ch * v / top
        d.c.setDash(3, 3)
        d.c.line(cx, yy, cx + cw, yy)
        d.c.setDash()
        d.text(cx - 4, yy + 3, str(v), FONT, 9, INK2, "r")
    d.text(cx + cw, cy + ch + 18, "kW", FONT, 9.5, INK2, "r")
    d.text(cx, cy - 34, "Green bars: the 18:00-22:00 peak window", FONT, 10, INK2)
    # findings
    x = 570
    items = [
        ("Hour of day dominates", "About 92 kW overnight against about 215 kW in the evening peak."),
        ("Temperature acts through the hour", "Demand rises about 0.2 kW per degree at 03:00 but about 3.6 kW per degree at 13:00."),
        ("Features", "hour, temperature, humidity, solar irradiance, cooling degree (temp above 18 C), peak-window flag. Same code builds the training file and every request."),
        ("Honest validation", "Time-blocked folds: train on the past, score the next 600 hours, because the test set is a later period."),
    ]
    y = H - 118
    for t, b in items:
        d.text(x, y, t, FONT_B, 12.5, INK)
        y = d.para(x, y - 18, b, 340, FONT, 11, INK2, 15) - 8
    rmse = d.meta.get("rmse")
    d.box(x, 52, 342, 62, GRID_T, stroke=None)
    d.text(x + 14, 92, "Benchmark: local boosted trees 11.2 kW RMSE (blocked CV)", FONT_M, 10.5, "#17406F")
    d.text(x + 14, 74, "vs about 49 kW for the hour-of-day average" +
           ("" if not rmse else ".  QuickML: %s kW" % rmse), FONT, 10.5, "#17406F")


def s_rules(d):
    d.page("Transition controller")
    d.box(48, 300, 864, 128)
    rows = [("Renewable Storage Mode", STORE, "Demand above 160 kW, or the hour is 18:00 to 22:00 inclusive"),
            ("Main Grid Mode", GRID, "Otherwise: demand at or below 160 kW and outside the peak window"),
            ("Critical Overload Alert", OVER, "Demand of 200 kW or more, in either mode")]
    y = 396
    for name, col, desc in rows:
        d.chip(66, y - 4, name, col, size=12, padx=12, h=26)
        d.text(300, y + 3, desc, FONT, 13, INK)
        y -= 40
    d.text(48, 270, "Worked examples (all covered by unit tests)", FONT_B, 13.5, INK)
    ex = [("150 kW at 17:00", "Main Grid", GRID, ""), ("150 kW at 18:00", "Renewable Storage", STORE, ""),
          ("160.00 kW at 10:00", "Main Grid", GRID, ""), ("160.01 kW at 10:00", "Renewable Storage", STORE, ""),
          ("150 kW at 23:00", "Main Grid", GRID, ""), ("205 kW at 03:00", "Renewable Storage", STORE, "+ overload")]
    for i, (cond, mode, col, extra) in enumerate(ex):
        cx = 48 + (i % 3) * 292
        cy = 205 - (i // 3) * 64
        d.box(cx, cy, 276, 52, PANEL)
        d.text(cx + 14, cy + 31, cond, FONT_M, 12, INK)
        w = d.chip(cx + 14, cy + 7, mode, col, size=10, padx=8, h=18)
        if extra:
            d.chip(cx + 14 + w + 6, cy + 7, "Overload", OVER, size=10, padx=8, h=18)
    d.para(48, 92, "A signal (SWITCH_TO_RENEWABLE_STORAGE or SWITCH_TO_MAIN_GRID) is emitted whenever the mode "
           "changes. The battery model only reports charge and warns when the reserve is reached: it never "
           "changes the mode the rules chose.", 864, FONT, 11.5, INK2, 16.5)


def s_dash(d):
    d.page("Live dashboard")
    img = d.meta.get("shot") or os.path.join(ROOT, "docs", "dashboard.png")
    from PIL import Image
    with Image.open(img) as im:
        iw, ih = 640, 640 * im.size[1] / im.size[0]
    d.box(44, 82, iw + 8, ih + 8, PANEL)
    d.c.drawImage(ImageReader(img), 48, 86, iw, ih)
    x = 706
    pts = [("Load curve", "Predicted demand with the 160 kW and 200 kW lines and the peak window."),
           ("Dispatch strip", "Grid mode for every hour; red marks critical overload."),
           ("State badge", "Click an hour or press Play to step through the day."),
           ("Alerts", "Overload and low-battery banners, plus the model in use."),
           ("In and out", "Form or CSV in, predictions.csv out, system log below.")]
    y = H - 118
    for t, b in pts:
        d.text(x, y, t, FONT_B, 12.5, INK)
        y = d.para(x, y - 17, b, 206, FONT, 10.5, INK2, 14.5) - 9


def s_quality(d):
    d.page("Data handling and reliability")
    cols = [
        ("Cleaning before every forecast", GRID, [
            "Accepts common timestamp formats and header names",
            "Sorts into time order, drops duplicate hours",
            "Out-of-range readings treated as missing",
            "Gaps filled by interpolation in time",
            "Each repair counted on the dashboard",
        ]),
        ("Safeguards", STORE, [
            "Real validation errors, never a stack trace",
            "QuickML calls retried, token refreshed on 401",
            "Fallback model is labelled in the UI and log",
            "Secrets only in environment variables",
            "Grid mode never depends on the battery model",
        ]),
        ("Tests that run in one command", OVER, [
            "15 rule boundaries, overload independent of mode",
            "Battery never below reserve, energy balances",
            "QuickML client against a mock OAuth server",
            "Official test_eval.py prints SUCCESS",
            "Standard library only, written for Python 3.9",
        ]),
    ]
    for i, (t, col, items) in enumerate(cols):
        x = 48 + i * 292
        d.box(x, 130, 276, 290)
        d.c.setFillColor(col)
        d.c.roundRect(x, 412, 276, 8, 4, stroke=0, fill=1)
        d.text(x + 16, 384, t, FONT_B, 13.5, INK)
        y = 354
        for it in items:
            d.c.setFillColor(col)
            d.c.circle(x + 20, y + 4, 2.8, stroke=0, fill=1)
            y = d.para(x + 32, y, it, 232, FONT, 11.5, INK, 16) - 10


def s_next(d):
    d.page("Assumptions and next steps")
    d.box(48, 250, 420, 190)
    d.text(66, 412, "Assumptions we made", FONT_B, 15)
    y = 386
    for t in ["The brief gives no battery spec. Defaults (4000 kWh, 300 kW discharge, 200 kW charge, 10% reserve) are editable in the UI.",
              "The model sees only the clock and the weather: no past demand is available when the forecast is made.",
              "Run history is kept in memory and a temp file, so it resets when the app restarts."]:
        y = d.para(66, y, t, 384, FONT, 11.5, INK2, 16) - 9
    d.box(492, 250, 420, 190)
    d.text(510, 412, "What we would build next", FONT_B, 15)
    y = 386
    for t in ["Store telemetry and run history in Catalyst Data Store, and uploaded CSVs in Stratus.",
              "Add lag features if live demand history becomes available, and retrain on a schedule.",
              "Replace the battery assumptions with real equipment limits and add solar generation."]:
        y = d.para(510, y, t, 384, FONT, 11.5, INK2, 16) - 9
    m = d.meta
    y = 190
    if m.get("url") or m.get("repo"):
        d.text(48, y, "Links", FONT_B, 14)
        y -= 24
        if m.get("url"):
            d.text(48, y, "Live app:  " + m["url"], FONT_M, 12.5, GRID)
            y -= 22
        if m.get("repo"):
            d.text(48, y, "Repository:  " + m["repo"], FONT_M, 12.5, GRID)
    else:
        d.text(48, y, "Thank you.", FONT_B, 22)


def build(out, meta):
    d = Deck(out, meta)
    for fn in (s_title, s_flow, s_arch, s_model, s_rules, s_dash, s_quality, s_next):
        fn(d)
    d.save()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--team")
    ap.add_argument("--url")
    ap.add_argument("--repo")
    ap.add_argument("--quickml-rmse", dest="rmse")
    ap.add_argument("--screenshot", help="PNG of your LIVE dashboard (recommended)")
    ap.add_argument("--out", default="Presentation.pdf")
    a = ap.parse_args()
    build(a.out, {"team": a.team, "url": a.url, "repo": a.repo, "rmse": a.rmse, "shot": a.screenshot})
    print("wrote", a.out)
