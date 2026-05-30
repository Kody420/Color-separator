"""Dark + blue-accent theme for the Color Separator app.

Import this module and call ``apply(root)`` once before building the UI.
All colour constants are available at module level for use in tk widget kwargs.
"""
import tkinter as tk
from tkinter import ttk

# ── Palette ──────────────────────────────────────────────────────────────────
BG       = "#1a1a1a"   # root window / main background
PANEL    = "#222222"   # settings sidebar fill
CARD     = "#282828"   # output thumbnail card
BORDER   = "#3a3a3a"   # widget borders & separator lines
INPUT_BG = "#2d2d2d"   # text entry / spinbox field
TEXT     = "#e0e0e0"   # primary text
TEXT_DIM = "#666666"   # secondary / hint text
ACCENT   = "#0078d4"   # Windows-blue accent
ACCENT_H = "#1a8ae4"   # accent hover shade
BTN_BG   = "#2d2d2d"   # regular button fill
BTN_H    = "#383838"   # regular button hover fill


def apply(root: tk.Tk) -> None:
    """Apply dark + blue-accent ttk styles and set root background."""
    root.configure(bg=BG)
    s = ttk.Style(root)
    s.theme_use("clam")          # clam is the most customisable cross-platform base

    # ── Universal defaults ────────────────────────────────────────────────
    s.configure(".",
        background=BG, foreground=TEXT,
        fieldbackground=INPUT_BG,
        troughcolor="#1e1e1e",
        bordercolor=BORDER, darkcolor=PANEL, lightcolor=PANEL,
        selectbackground=ACCENT, selectforeground="#ffffff",
        font=("Segoe UI", 9))

    # ── Frames ────────────────────────────────────────────────────────────
    s.configure("TFrame", background=BG)

    # ── Labels ────────────────────────────────────────────────────────────
    s.configure("TLabel", background=BG, foreground=TEXT)

    # ── Buttons ──────────────────────────────────────────────────────────
    s.configure("TButton",
        background=BTN_BG, foreground=TEXT,
        bordercolor=BORDER, focuscolor="none",
        padding=(8, 4), relief="flat")
    s.map("TButton",
        background=[("active", BTN_H), ("disabled", "#1e1e1e")],
        foreground=[("disabled", "#444444")],
        relief=[("active", "flat"), ("pressed", "flat")])

    # Blue accent button (Run)
    s.configure("Accent.TButton",
        background=ACCENT, foreground="#ffffff",
        bordercolor=ACCENT, focuscolor="none",
        padding=(10, 5), relief="flat",
        font=("Segoe UI", 9, "bold"))
    s.map("Accent.TButton",
        background=[("active", ACCENT_H), ("disabled", "#1a3a5a")],
        foreground=[("disabled", "#6699bb")],
        relief=[("active", "flat"), ("pressed", "flat")])

    # ── Combobox ─────────────────────────────────────────────────────────
    s.configure("TCombobox",
        fieldbackground=INPUT_BG, foreground=TEXT,
        bordercolor=BORDER, arrowcolor=TEXT_DIM,
        selectbackground=INPUT_BG, selectforeground=TEXT,
        padding=(4, 2))
    s.map("TCombobox",
        fieldbackground=[("readonly", INPUT_BG)],
        selectbackground=[("readonly", INPUT_BG)],
        selectforeground=[("readonly", TEXT)],
        bordercolor=[("focus", ACCENT)],
        arrowcolor=[("active", TEXT)])

    # ── Progressbar ──────────────────────────────────────────────────────
    s.configure("TProgressbar",
        troughcolor="#1e1e1e", background=ACCENT,
        bordercolor=BG, thickness=8)

    # ── Scrollbar ────────────────────────────────────────────────────────
    s.configure("TScrollbar",
        troughcolor=BG, background=BTN_BG,
        bordercolor=BG, arrowcolor=TEXT_DIM, relief="flat")
    s.map("TScrollbar",
        background=[("active", BTN_H)])

    # ── Separator ────────────────────────────────────────────────────────
    s.configure("TSeparator", background=BORDER)
