import base64
import io as _io
import re
from PIL import Image, ImageOps, ImageTk
import numpy as np
import os
import threading
import tkinter as tk
from tkinter import colorchooser, filedialog, messagebox, ttk
from scipy import ndimage
import theme
from sklearn.cluster import KMeans

try:
    import fitz as _fitz        # PyMuPDF — SVG preview / rasterisation
    from lxml import etree as _etree  # namespace-aware SVG serialisation
    _SVG_SUPPORT = True
except ImportError:
    _fitz = None
    _etree = None
    _SVG_SUPPORT = False

THUMB = 200  # max thumbnail dimension in pixels

_SVG_NS = "http://www.w3.org/2000/svg"
_SVG_SHAPE_TAGS = frozenset(
    f"{{{_SVG_NS}}}{t}" for t in
    ("path", "circle", "rect", "ellipse", "polygon", "polyline",
     "line", "text", "tspan", "use")
)
_NAMED_COLORS = {
    "black": (0, 0, 0), "white": (255, 255, 255), "red": (255, 0, 0),
    "green": (0, 128, 0), "blue": (0, 0, 255), "yellow": (255, 255, 0),
    "orange": (255, 165, 0), "purple": (128, 0, 128), "pink": (255, 192, 203),
    "gray": (128, 128, 128), "grey": (128, 128, 128), "brown": (165, 42, 42),
    "cyan": (0, 255, 255), "magenta": (255, 0, 255), "lime": (0, 255, 0),
    "navy": (0, 0, 128), "teal": (0, 128, 128), "silver": (192, 192, 192),
    "maroon": (128, 0, 0), "olive": (128, 128, 0), "aqua": (0, 255, 255),
    "fuchsia": (255, 0, 255),
}


def _svg_style_get(style, prop):
    if not style:
        return None
    m = re.search(
        rf"(?:^|;)\s*{re.escape(prop)}\s*:\s*([^;]+)",
        style,
        flags=re.IGNORECASE,
    )
    if not m:
        return None
    value = m.group(1).strip()
    if value in ("inherit", ""):
        return None
    return value


def _svg_style_set(style, prop, value):
    parts = []
    for raw in (style or "").split(";"):
        item = raw.strip()
        if not item:
            continue
        if ":" not in item:
            parts.append(item)
            continue
        key, val = item.split(":", 1)
        if key.strip().lower() == prop.lower():
            continue
        parts.append(f"{key.strip()}:{val.strip()}")
    parts.append(f"{prop}:{value}")
    return ";".join(parts)


def _svg_get_fill(elem):
    style_val = _svg_style_get(elem.get("style") or "", "fill")
    if style_val is not None:
        return style_val
    fill_attr = elem.get("fill")
    if fill_attr and fill_attr != "inherit":
        return fill_attr.strip()
    return None


def _svg_get_stroke(elem):
    style_val = _svg_style_get(elem.get("style") or "", "stroke")
    if style_val is not None:
        return style_val
    stroke_attr = elem.get("stroke")
    if stroke_attr and stroke_attr != "inherit":
        return stroke_attr.strip()
    return None


def _parse_svg_color(value):
    if not value:
        return None
    value = value.strip()
    if value in ("none", "transparent", "inherit", "currentColor") or value.startswith("url("):
        return None

    # light-dark(light_val, dark_val) — take the light-mode value
    if value.startswith("light-dark(") and value.endswith(")"):
        inner = value[len("light-dark("):-1]
        depth = 0
        for idx, ch in enumerate(inner):
            if ch in "([":
                depth += 1
            elif ch in ")]":
                depth -= 1
            elif ch == "," and depth == 0:
                return _parse_svg_color(inner[:idx].strip())
        return _parse_svg_color(inner.strip())

    # var(--name, fallback) — use fallback
    if value.startswith("var(") and value.endswith(")"):
        inner = value[4:-1]
        idx = inner.find(",")
        if idx >= 0:
            return _parse_svg_color(inner[idx + 1:].strip())
        return None

    if value.startswith("#"):
        hx = value[1:]
        if len(hx) == 3:
            hx = hx[0] * 2 + hx[1] * 2 + hx[2] * 2
        if len(hx) == 6:
            try:
                return (int(hx[0:2], 16), int(hx[2:4], 16), int(hx[4:6], 16))
            except ValueError:
                return None

    m = re.match(
        r"rgb\(\s*(\d+(?:\.\d+)?)\s*,\s*(\d+(?:\.\d+)?)\s*,\s*(\d+(?:\.\d+)?)\s*\)",
        value,
    )
    if m:
        return (int(float(m.group(1))), int(float(m.group(2))), int(float(m.group(3))))

    return _NAMED_COLORS.get(value.lower())


def _apply_svg_output_transforms(svg_str, color_mode="", bg_color=None):
    """Apply output color/background transforms directly to serialized SVG text."""
    if not svg_str or _etree is None:
        return svg_str
    if color_mode not in ("black", "white") and bg_color is None:
        return svg_str

    target = None
    if color_mode == "black":
        target = "#000000"
    elif color_mode == "white":
        target = "#ffffff"

    try:
        root = _etree.fromstring(svg_str.encode("utf-8"))
    except Exception:
        # If parsing fails for any reason, keep original SVG so save still succeeds.
        return svg_str

    def _walk(elem, inh_fill=None, inh_stroke=None, hidden=False):
        own_fill = _svg_get_fill(elem)
        own_stroke = _svg_get_stroke(elem)
        resolved_fill = own_fill if own_fill is not None else inh_fill
        resolved_stroke = own_stroke if own_stroke is not None else inh_stroke

        style = elem.get("style") or ""
        display_attr = (elem.get("display") or "").strip().lower()
        display_style = (_svg_style_get(style, "display") or "").strip().lower()
        hidden_here = hidden or display_attr == "none" or display_style == "none"

        if target is not None and not hidden_here and elem.tag in _SVG_SHAPE_TAGS:
            has_fill = resolved_fill is not None and _parse_svg_color(resolved_fill) is not None
            has_stroke = resolved_stroke is not None and _parse_svg_color(resolved_stroke) is not None

            style_changed = False
            if has_fill:
                elem.set("fill", target)
                if _svg_style_get(style, "fill") is not None:
                    style = _svg_style_set(style, "fill", target)
                    style_changed = True
            if has_stroke:
                elem.set("stroke", target)
                if _svg_style_get(style, "stroke") is not None:
                    style = _svg_style_set(style, "stroke", target)
                    style_changed = True
            if style_changed:
                elem.set("style", style)

        for child in elem:
            _walk(child, resolved_fill, resolved_stroke, hidden_here)

    _walk(root)

    if bg_color is not None:
        bg_hex = "#{:02x}{:02x}{:02x}".format(*bg_color)
        bg = _etree.Element(f"{{{_SVG_NS}}}rect")
        bg.set("x", "0")
        bg.set("y", "0")
        bg.set("width", "100%")
        bg.set("height", "100%")
        bg.set("fill", bg_hex)
        bg.set("data-csep-background", "1")
        root.insert(0, bg)

    output = _etree.tostring(root, encoding="unicode", xml_declaration=False)
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + output


def remove_small_islands(labels_2d, min_size):
    """
    Reassign pixel islands smaller than min_size pixels to the nearest
    larger cluster (by Euclidean distance to the nearest non-island pixel).
    """
    num_colors = int(labels_2d.max()) + 1
    small_mask = np.zeros(labels_2d.shape, dtype=bool)

    for color_idx in range(num_colors):
        cluster_mask = (labels_2d == color_idx)
        labeled, num_features = ndimage.label(cluster_mask)
        for comp_idx in range(1, num_features + 1):
            if (labeled == comp_idx).sum() < min_size:
                small_mask |= (labeled == comp_idx)

    if not small_mask.any():
        return labels_2d

    # For each small-island pixel find the nearest non-island pixel and adopt its label.
    nearest = ndimage.distance_transform_edt(
        small_mask, return_distances=False, return_indices=True
    )
    result = labels_2d.copy()
    result[small_mask] = labels_2d[nearest[0][small_mask], nearest[1][small_mask]]
    return result


def separate_colors(input_path, num_colors=3, tolerance=0.2, min_island_size=0):
    """
    input_path      : raster image path (PNG, JPEG, BMP …) or a PIL Image object
    num_colors      : number of dominant colors to extract (k-means clusters)
    tolerance       : 0.0 = strict clustering, 1.0 = more smoothing (less sensitive)
    min_island_size : remove connected pixel islands smaller than this (0 = off)
    Returns list of (filename, PIL.Image) — one entry per color cluster.
    """

    if isinstance(input_path, str):
        img = Image.open(input_path).convert("RGBA")
    else:
        img = input_path if input_path.mode == "RGBA" else input_path.convert("RGBA")
    data = np.array(img)

    rgb = data[:, :, :3]
    h, w = rgb.shape[:2]

    # reshape pixels for clustering
    pixels = rgb.reshape(-1, 3).astype(np.float32)

    # normalize tolerance into clustering strength
    # higher tolerance = more blur before clustering (reduces sensitivity)
    if tolerance > 0:
        noise = np.random.normal(0, tolerance * 5, pixels.shape)
        pixels_for_cluster = np.clip(pixels + noise, 0, 255)
    else:
        pixels_for_cluster = pixels

    # k-means clustering for dominant colors
    kmeans = KMeans(n_clusters=num_colors, n_init=10, random_state=42)
    labels = kmeans.fit_predict(pixels_for_cluster)

    centers = kmeans.cluster_centers_.astype(np.uint8)

    # optionally remove small isolated pixel islands
    if min_island_size > 1:
        labels_2d = labels.reshape(h, w)
        labels_2d = remove_small_islands(labels_2d, min_island_size)
        labels = labels_2d.reshape(-1)

    result_images = []

    # create one image per cluster
    for i in range(num_colors):
        mask = (labels == i)

        out = np.zeros_like(data)
        out[:, :, 3] = 0  # fully transparent

        out_pixels = out[:, :, :3].reshape(-1, 3)
        out_alpha = out[:, :, 3].reshape(-1)

        cluster_color = centers[i]

        out_pixels[mask] = cluster_color
        out_alpha[mask] = 255

        out[:, :, :3] = out_pixels.reshape(h, w, 3)
        out[:, :, 3] = out_alpha.reshape(h, w)

        img_out = Image.fromarray(out, "RGBA")
        hex_color = "#{:02x}{:02x}{:02x}".format(*cluster_color)
        result_images.append((f"cluster_{i}_{hex_color}.png", img_out))

    return result_images


def separate_colors_svg(input_path, num_colors=3, tolerance=0.2):
    """
    SVG-native color separation: groups vector elements by fill color without
    rasterising.  Works on SVGs with solid fill colors (hex, rgb(), named).
    Returns list of (filename, svg_str) — one entry per color cluster.
    """
    import re, copy

    SVG_NS = "http://www.w3.org/2000/svg"
    SHAPE_TAGS = frozenset(
        f"{{{SVG_NS}}}{t}" for t in
        ("path", "circle", "rect", "ellipse", "polygon", "polyline",
         "line", "text", "tspan", "use")
    )
    NAMED_COLORS = {
        "black": (0,0,0), "white": (255,255,255), "red": (255,0,0),
        "green": (0,128,0), "blue": (0,0,255), "yellow": (255,255,0),
        "orange": (255,165,0), "purple": (128,0,128), "pink": (255,192,203),
        "gray": (128,128,128), "grey": (128,128,128), "brown": (165,42,42),
        "cyan": (0,255,255), "magenta": (255,0,255), "lime": (0,255,0),
        "navy": (0,0,128), "teal": (0,128,128), "silver": (192,192,192),
        "maroon": (128,0,0), "olive": (128,128,0), "aqua": (0,255,255),
        "fuchsia": (255,0,255),
    }

    def _get_fill(elem):
        style = elem.get("style") or ""
        m = re.search(r"(?:^|;)\s*fill\s*:\s*([^;]+)", style)
        if m:
            v = m.group(1).strip()
            if v not in ("inherit", ""):
                return v
        f = elem.get("fill")
        if f and f != "inherit":
            return f
        return None

    def _get_stroke(elem):
        style = elem.get("style") or ""
        m = re.search(r"(?:^|;)\s*stroke\s*:\s*([^;]+)", style)
        if m:
            v = m.group(1).strip()
            if v not in ("inherit", ""):
                return v
        s = elem.get("stroke")
        if s and s != "inherit":
            return s
        return None

    def _parse_color(s):
        if not s:
            return None
        s = s.strip()
        if s in ("none", "transparent", "inherit", "currentColor") or s.startswith("url("):
            return None
        # light-dark(light_val, dark_val) — take the light-mode value
        if s.startswith("light-dark(") and s.endswith(")"):
            inner = s[len("light-dark("):-1]
            depth = 0
            for i, ch in enumerate(inner):
                if ch in "([":
                    depth += 1
                elif ch in ")]":
                    depth -= 1
                elif ch == "," and depth == 0:
                    return _parse_color(inner[:i].strip())
            return _parse_color(inner.strip())
        # var(--name, fallback) — use the fallback value
        if s.startswith("var(") and s.endswith(")"):
            inner = s[4:-1]
            idx = inner.find(",")
            if idx >= 0:
                return _parse_color(inner[idx + 1:].strip())
            return None
        if s.startswith("#"):
            h = s[1:]
            if len(h) == 3:
                h = h[0]*2 + h[1]*2 + h[2]*2
            if len(h) == 6:
                try:
                    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
                except ValueError:
                    return None
        m = re.match(
            r"rgb\(\s*(\d+(?:\.\d+)?)\s*,\s*(\d+(?:\.\d+)?)\s*,\s*(\d+(?:\.\d+)?)\s*\)", s)
        if m:
            return (int(float(m.group(1))), int(float(m.group(2))), int(float(m.group(3))))
        return NAMED_COLORS.get(s.lower())

    tree = _etree.parse(input_path)
    root = tree.getroot()

    # Collect shape elements and their resolved fill or stroke colors
    shaped = []  # [(element, (r, g, b))]

    def _collect(elem, inh_fill=None, inh_stroke=None):
        f_str = _get_fill(elem) or inh_fill
        s_str = _get_stroke(elem) or inh_stroke
        # Prefer fill; fall back to stroke (handles <line>, <polyline>, etc.)
        color = _parse_color(f_str) if f_str else None
        if color is None:
            color = _parse_color(s_str) if s_str else None
        if elem.tag in SHAPE_TAGS and color is not None:
            shaped.append((elem, color))
        for child in elem:
            _collect(child, f_str, s_str)

    _collect(root)

    if not shaped:
        raise ValueError(
            "No solid-color shapes found in this SVG.\n"
            "SVG-native separation requires elements with solid fill or stroke colors.\n"
            "(Gradients, patterns, and fully inherited colors are not supported.)"
        )

    # K-means cluster on element fill colors
    colors_arr = np.array([c for _, c in shaped], dtype=np.float32)
    unique_count = len({tuple(int(x) for x in c) for c in colors_arr.tolist()})
    k = min(num_colors, unique_count)

    if k <= 1:
        labels = np.zeros(len(shaped), dtype=int)
        centers = np.array([colors_arr.mean(axis=0)], dtype=np.uint8)
    else:
        km = KMeans(n_clusters=k, n_init=10, random_state=42)
        labels = km.fit_predict(colors_arr)
        centers = km.cluster_centers_.astype(np.uint8)

    # Tag each matched element with its cluster index (temporary attribute)
    _ATTR = "_csep_cluster"
    for (elem, _), label in zip(shaped, labels):
        elem.set(_ATTR, str(int(label)))

    results = []
    try:
        for ci in range(k):
            new_root = copy.deepcopy(root)

            def _process(elem, _ci=ci):
                tag_val = elem.get(_ATTR)
                if tag_val is not None:
                    del elem.attrib[_ATTR]
                    if int(tag_val) != _ci:
                        style = (elem.get("style") or "").rstrip(";")
                        elem.set("style", (style + ";display:none").lstrip(";"))
                for child in elem:
                    _process(child)

            _process(new_root)

            svg_str = _etree.tostring(
                new_root, encoding="unicode", xml_declaration=False
            )
            svg_str = '<?xml version="1.0" encoding="UTF-8"?>\n' + svg_str

            color = tuple(int(x) for x in centers[ci])
            hex_color = "#{:02x}{:02x}{:02x}".format(*color)
            results.append((f"cluster_{ci}_{hex_color}.svg", svg_str))
    finally:
        # Remove temp attributes from original tree
        for (elem, _) in shaped:
            elem.attrib.pop(_ATTR, None)

    return results


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Color Separator")
        self.minsize(960, 540)
        self.result_images = []   # list of (filename, PIL.Image)
        self._orig_images  = []   # pre-transform originals
        self._photo_refs   = []   # keep PhotoImage refs alive
        self._color_mode_var = tk.StringVar(value="")  # "" | "black" | "white"
        self._bg_color       = None                     # None = transparent | (r,g,b)
        self.format_var      = tk.StringVar(value="PNG")
        self._input_is_svg   = False
        self._orig_svg_data  = None   # list of (filename, svg_str) when input is SVG
        self._setup_style()
        self._build_ui()

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        pad = {"padx": 6, "pady": 4}

        # ── Left: settings panel ──────────────────────────────────────────
        ctrl = tk.LabelFrame(self, text=" Settings ",
                             bg=theme.PANEL, fg=theme.ACCENT,
                             font=("Segoe UI", 9, "bold"),
                             bd=1, relief="solid", padx=10, pady=8)
        ctrl.pack(side="left", fill="y", padx=(10, 4), pady=10)

        def _lbl(parent, txt):
            return tk.Label(parent, text=txt, bg=theme.PANEL, fg=theme.TEXT,
                            font=("Segoe UI", 9))

        def _entry(parent, var, w=30):
            return tk.Entry(parent, textvariable=var, width=w,
                            bg=theme.INPUT_BG, fg=theme.TEXT,
                            insertbackground=theme.TEXT, relief="flat",
                            highlightthickness=1, highlightbackground=theme.BORDER,
                            highlightcolor=theme.ACCENT)

        def _browse(parent, cmd):
            return tk.Button(parent, text="Browse…", command=cmd,
                             bg=theme.BTN_BG, fg=theme.TEXT,
                             activebackground=theme.BTN_H, activeforeground=theme.TEXT,
                             relief="flat", bd=0, cursor="hand2",
                             font=("Segoe UI", 9), padx=6, pady=2)

        _lbl(ctrl, "Input PNG:").grid(row=0, column=0, sticky="e", **pad)
        self.input_var = tk.StringVar()
        _entry(ctrl, self.input_var).grid(row=0, column=1, **pad)
        _browse(ctrl, self._browse_input).grid(row=0, column=2, **pad)

        _lbl(ctrl, "Output folder:").grid(row=1, column=0, sticky="e", **pad)
        self.output_var = tk.StringVar(value="output_colors")
        _entry(ctrl, self.output_var).grid(row=1, column=1, **pad)
        _browse(ctrl, self._browse_output).grid(row=1, column=2, **pad)

        _lbl(ctrl, "Number of colors:").grid(row=2, column=0, sticky="e", **pad)
        self.num_colors_var = tk.IntVar(value=3)
        tk.Spinbox(ctrl, from_=1, to=20, textvariable=self.num_colors_var, width=6,
                   bg=theme.INPUT_BG, fg=theme.TEXT, insertbackground=theme.TEXT,
                   relief="flat", highlightthickness=1,
                   highlightbackground=theme.BORDER, highlightcolor=theme.ACCENT).grid(
            row=2, column=1, sticky="w", **pad)

        _lbl(ctrl, "Tolerance (0–1):").grid(row=3, column=0, sticky="e", **pad)
        self.tolerance_var = tk.DoubleVar(value=0.2)
        sf = tk.Frame(ctrl, bg=theme.PANEL)
        sf.grid(row=3, column=1, sticky="w", **pad)
        tk.Scale(sf, variable=self.tolerance_var, from_=0.0, to=1.0,
                 resolution=0.05, orient="horizontal", length=155,
                 bg=theme.PANEL, fg=theme.TEXT, troughcolor=theme.INPUT_BG,
                 activebackground=theme.ACCENT, highlightthickness=0,
                 bd=0, sliderrelief="flat").pack(side="left")
        tk.Label(sf, textvariable=self.tolerance_var, width=4,
                 bg=theme.PANEL, fg=theme.TEXT, font=("Segoe UI", 9)).pack(side="left")

        _lbl(ctrl, "Min island size (px):").grid(row=4, column=0, sticky="e", **pad)
        isf = tk.Frame(ctrl, bg=theme.PANEL)
        isf.grid(row=4, column=1, sticky="w", **pad)
        self.min_island_var = tk.IntVar(value=0)
        tk.Spinbox(isf, from_=0, to=100000, textvariable=self.min_island_var, width=8,
                   bg=theme.INPUT_BG, fg=theme.TEXT, insertbackground=theme.TEXT,
                   relief="flat", highlightthickness=1,
                   highlightbackground=theme.BORDER, highlightcolor=theme.ACCENT).pack(side="left")
        tk.Label(isf, text="  (0 = off)", bg=theme.PANEL, fg=theme.TEXT_DIM,
                 font=("Segoe UI", 9)).pack(side="left")

        _lbl(ctrl, "Output format:").grid(row=5, column=0, sticky="e", **pad)
        self.fmt_combo = ttk.Combobox(ctrl, textvariable=self.format_var,
                                       values=["PNG", "JPEG", "BMP"],
                                       state="readonly", width=8)
        self.fmt_combo.grid(row=5, column=1, sticky="w", **pad)

        tk.Frame(ctrl, height=1, bg=theme.BORDER).grid(
            row=6, column=0, columnspan=3, sticky="ew", padx=4, pady=8)

        btn_row = tk.Frame(ctrl, bg=theme.PANEL)
        btn_row.grid(row=7, column=0, columnspan=3, pady=(0, 6))
        self.run_btn = tk.Button(btn_row, text="▶  Run", command=self._run,
                                  bg=theme.ACCENT, fg="#ffffff",
                                  activebackground=theme.ACCENT_H, activeforeground="#ffffff",
                                  disabledforeground="#5599bb",
                                  relief="flat", bd=0, cursor="hand2",
                                  font=("Segoe UI", 9, "bold"), padx=14, pady=5)
        self.run_btn.pack(side="left", padx=4)
        self.save_btn = tk.Button(btn_row, text="💾  Save", command=self._save, state="disabled",
                                   bg=theme.BTN_BG, fg=theme.TEXT,
                                   activebackground=theme.BTN_H, activeforeground=theme.TEXT,
                                   disabledforeground=theme.TEXT_DIM,
                                   relief="flat", bd=0, cursor="hand2",
                                   font=("Segoe UI", 9), padx=10, pady=5)
        self.save_btn.pack(side="left", padx=4)

        self.progress = ttk.Progressbar(ctrl, mode="indeterminate", length=280)
        self.progress.grid(row=8, column=0, columnspan=3, pady=4, padx=4)

        self.status_var = tk.StringVar(value="Ready.")
        tk.Label(ctrl, textvariable=self.status_var, bg=theme.PANEL, fg=theme.TEXT_DIM,
                 font=("Segoe UI", 9), wraplength=280, justify="left").grid(
            row=9, column=0, columnspan=3, pady=(0, 4))

        # ── Right: preview ────────────────────────────────────────────────
        preview = ttk.Frame(self, padding=(4, 10, 10, 10))
        preview.pack(side="left", fill="both", expand=True)

        ttk.Label(preview, text="Input image", font=("Segoe UI", 9, "bold")).pack(anchor="w")
        self.input_lbl = tk.Label(preview, text="(no image selected)",
                                   bg=theme.CARD, fg=theme.TEXT_DIM, font=("Segoe UI", 9),
                                   width=28, height=4,
                                   highlightbackground=theme.BORDER, highlightthickness=1)
        self.input_lbl.pack(anchor="w", pady=(2, 8))

        ttk.Separator(preview, orient="horizontal").pack(fill="x", pady=4)

        hdr_row = ttk.Frame(preview)
        hdr_row.pack(fill="x", pady=(2, 4))
        ttk.Label(hdr_row, text="Output colors", font=("Segoe UI", 9, "bold")).pack(side="left")
        xf_frame = ttk.Frame(hdr_row)
        xf_frame.pack(side="right")

        self._black_btn = tk.Button(
            xf_frame, text="⬛ Black", width=8,
            command=lambda: self._set_color_mode("black"), state="disabled",
            bg="#1e1e1e", fg=theme.TEXT, relief="flat",
            activebackground="#2a2a2a", activeforeground=theme.TEXT,
            cursor="hand2", bd=0, highlightthickness=1,
            highlightbackground=theme.BORDER)
        self._black_btn.pack(side="left", padx=2)

        self._white_btn = tk.Button(
            xf_frame, text="⬜ White", width=8,
            command=lambda: self._set_color_mode("white"), state="disabled",
            bg="#f0f0f0", fg="#1a1a1a", relief="flat",
            activebackground="#dcdcdc", activeforeground="#1a1a1a",
            cursor="hand2", bd=0, highlightthickness=1,
            highlightbackground=theme.BORDER)
        self._white_btn.pack(side="left", padx=2)

        ttk.Separator(xf_frame, orient="vertical").pack(side="left", fill="y", padx=8)
        ttk.Label(xf_frame, text="Background:").pack(side="left")
        self._bg_btn = tk.Button(
            xf_frame, text="  ", width=3,
            command=self._pick_bg_color, state="disabled",
            bg=theme.BG, relief="flat",
            highlightbackground=theme.BORDER, highlightthickness=1, cursor="hand2")
        self._bg_btn.pack(side="left", padx=2)
        self._bg_transparent_btn = ttk.Button(
            xf_frame, text="Transparent",
            command=self._set_bg_transparent, state="disabled")
        self._bg_transparent_btn.pack(side="left", padx=2)

        # ── Scrollable output area ────────────────────────────────────────
        out_outer = ttk.Frame(preview)
        out_outer.pack(fill="both", expand=True, pady=(4, 0))

        self.out_canvas = tk.Canvas(out_outer, highlightthickness=0, bg=theme.BG)
        vsb = ttk.Scrollbar(out_outer, orient="vertical", command=self.out_canvas.yview)
        self.out_canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        self.out_canvas.pack(side="left", fill="both", expand=True)

        self.out_frame = tk.Frame(self.out_canvas, bg=theme.BG)
        self._out_win = self.out_canvas.create_window((0, 0), window=self.out_frame, anchor="nw")

        self.out_frame.bind("<Configure>", lambda e: self.out_canvas.configure(
            scrollregion=self.out_canvas.bbox("all")))
        self.out_canvas.bind("<Configure>", lambda e: self.out_canvas.itemconfig(
            self._out_win, width=e.width))
        self.out_canvas.bind("<MouseWheel>", lambda e: self.out_canvas.yview_scroll(
            -1 if e.delta > 0 else 1, "units"))

    # --------------------------------------------------------- Browse helpers
    def _setup_style(self):
        theme.apply(self)

    def _apply_transforms(self):
        """Re-derive result_images from originals applying current color mode and background."""
        if not self._orig_images:
            return
        try:
            images = []
            for filename, img in self._orig_images:
                out = img.copy()

                # Color mode: override all visible pixels to black or white
                mode = self._color_mode_var.get()
                if mode in ("black", "white"):
                    target = (0, 0, 0, 255) if mode == "black" else (255, 255, 255, 255)
                    arr = np.array(out)
                    arr[arr[:, :, 3] > 0] = target
                    out = Image.fromarray(arr, "RGBA")

                # Background: composite visible pixels over chosen solid color
                if self._bg_color is not None:
                    bg = Image.new("RGBA", out.size, self._bg_color + (255,))
                    bg.paste(out, mask=out.split()[3])
                    out = bg

                images.append((filename, out))
            self.result_images = images
            self._show_output_thumbs(images)
        except Exception as exc:
            messagebox.showerror("Transform error", str(exc))

    def _set_color_mode(self, mode):
        """Toggle color mode; clicking the active button turns it off."""
        self._color_mode_var.set("" if self._color_mode_var.get() == mode else mode)
        m = self._color_mode_var.get()
        self._black_btn.config(bg="#555555" if m == "black" else "#1e1e1e")
        self._white_btn.config(bg="#c8c8c8" if m == "white" else "#f0f0f0")
        self._apply_transforms()

    def _pick_bg_color(self):
        initial = "#%02x%02x%02x" % self._bg_color if self._bg_color else "#ffffff"
        result = colorchooser.askcolor(color=initial, title="Pick background color")
        if result[1]:  # None if user cancelled
            self._bg_color = tuple(int(c) for c in result[0])
            self._bg_btn.config(bg=result[1], activebackground=result[1])
            self._apply_transforms()

    def _set_bg_transparent(self):
        self._bg_color = None
        self._bg_btn.config(bg=theme.BG, activebackground=theme.BG)
        self._apply_transforms()

    def _browse_input(self):
        if _SVG_SUPPORT:
            ftypes = [("Image files", "*.png *.svg"), ("PNG files", "*.png"),
                      ("SVG files", "*.svg"), ("All files", "*.*")]
        else:
            ftypes = [("PNG files", "*.png"), ("All files", "*.*")]
        path = filedialog.askopenfilename(filetypes=ftypes)
        if path:
            self.input_var.set(path)
            if self.output_var.get() in ("", "output_colors"):
                self.output_var.set(os.path.join(os.path.dirname(path), "output_colors"))
            self._input_is_svg = path.lower().endswith(".svg")
            self._update_format_options()
            self._update_input_thumb(path)

    def _browse_output(self):
        path = filedialog.askdirectory()
        if path:
            self.output_var.set(path)

    def _update_format_options(self):
        """Show SVG in the format dropdown only when the input is SVG and support is available."""
        if self._input_is_svg and _SVG_SUPPORT:
            self.fmt_combo.config(values=["PNG", "JPEG", "BMP", "SVG"])
        else:
            self.fmt_combo.config(values=["PNG", "JPEG", "BMP"])
            if self.format_var.get() == "SVG":
                self.format_var.set("PNG")

    def _update_input_thumb(self, path):
        try:
            if path.lower().endswith(".svg"):
                if not _SVG_SUPPORT:
                    self.input_lbl.configure(image="", text="(install PyMuPDF\nfor SVG preview)")
                    return
                with open(path, "rb") as f:
                    svg_data = f.read()
                doc = _fitz.Document(stream=svg_data, filetype="svg")
                page = doc[0]
                zoom = (THUMB * 4) / max(page.rect.width, page.rect.height, 1)
                pix = page.get_pixmap(matrix=_fitz.Matrix(zoom, zoom))
                img = Image.open(_io.BytesIO(pix.tobytes("png")))
            else:
                img = Image.open(path)
            img.thumbnail((THUMB, THUMB), Image.LANCZOS)
            photo = ImageTk.PhotoImage(img)
            self.input_lbl.configure(image=photo, text="", relief="flat",
                                      width=img.width, height=img.height, bg=theme.BG)
            self.input_lbl._photo = photo  # prevent GC
        except Exception:
            self.input_lbl.configure(image="", text="(preview error)")

    # ----------------------------------------------------------------- Run
    def _run(self):
        input_path = self.input_var.get().strip()
        if not input_path:
            messagebox.showwarning("Missing input", "Please select an input PNG file.")
            return
        if not os.path.isfile(input_path):
            messagebox.showerror("File not found", f"Cannot find:\n{input_path}")
            return

        self.run_btn.config(state="disabled")
        self.save_btn.config(state="disabled")
        self.result_images = []
        self.progress.start(10)
        self.status_var.set("Processing…")

        def worker():
            try:
                if self._input_is_svg:
                    # Vector-native pipeline — used when saving as SVG
                    svg_data = separate_colors_svg(
                        input_path,
                        num_colors=self.num_colors_var.get(),
                        tolerance=self.tolerance_var.get(),
                    )
                    # Raster pipeline — rasterise the whole input SVG then run
                    # pixel K-means; used for preview and PNG/JPEG/BMP saves
                    with open(input_path, "rb") as _f:
                        _svg_bytes = _f.read()
                    _doc = _fitz.Document(stream=_svg_bytes, filetype="svg")
                    _pix = _doc[0].get_pixmap()
                    _input_img = Image.open(_io.BytesIO(_pix.tobytes("png"))).convert("RGBA")
                    images = separate_colors(
                        _input_img,
                        num_colors=self.num_colors_var.get(),
                        tolerance=self.tolerance_var.get(),
                        min_island_size=self.min_island_var.get(),
                    )
                    self.after(0, self._on_done, images, None, svg_data)
                else:
                    images = separate_colors(
                        input_path,
                        num_colors=self.num_colors_var.get(),
                        tolerance=self.tolerance_var.get(),
                        min_island_size=self.min_island_var.get(),
                    )
                    self.after(0, self._on_done, images, None, None)
            except Exception as exc:
                self.after(0, self._on_done, None, str(exc), None)

        threading.Thread(target=worker, daemon=True).start()

    def _on_done(self, images, error, svg_data=None):
        self.progress.stop()
        self.run_btn.config(state="normal")
        if error:
            self.status_var.set("Error.")
            messagebox.showerror("Error", error)
        else:
            self._orig_images = images
            self._orig_svg_data = svg_data
            self._color_mode_var.set("")
            self._bg_color = None
            self._bg_btn.config(bg=theme.BG, activebackground=theme.BG)
            self._black_btn.config(state="normal", bg="#1e1e1e")
            self._white_btn.config(state="normal", bg="#f0f0f0")
            self._bg_btn.config(state="normal")
            self._bg_transparent_btn.config(state="normal")
            self.result_images = images
            self._show_output_thumbs(images)
            self.save_btn.config(state="normal")
            self.status_var.set(f"{len(images)} color(s) extracted. Press Save to write files.")

    def _show_output_thumbs(self, images):
        for widget in self.out_frame.winfo_children():
            widget.destroy()
        self._photo_refs = []

        cols = max(1, self.out_canvas.winfo_width() // (THUMB + 24))

        for idx, (filename, img) in enumerate(images):
            thumb = img.copy()
            thumb.thumbnail((THUMB, THUMB), Image.LANCZOS)

            # Composite onto a checkered background so transparent areas and
            # black/white transformed pixels are always clearly visible.
            if thumb.mode == "RGBA":
                th, tw = thumb.height, thumb.width
                ys, xs = np.mgrid[0:th, 0:tw]
                checker = ((xs // 10 + ys // 10) % 2 == 0)
                arr_bg = np.empty((th, tw, 3), dtype=np.uint8)
                arr_bg[ checker] = (220, 220, 220)
                arr_bg[~checker] = (180, 180, 180)
                bg_img = Image.fromarray(arr_bg, "RGB")
                bg_img.paste(thumb.convert("RGB"), mask=thumb.split()[3])
                disp = bg_img
            else:
                disp = thumb.convert("RGB") if thumb.mode != "RGB" else thumb

            photo = ImageTk.PhotoImage(disp)
            self._photo_refs.append(photo)

            cell = tk.Frame(self.out_frame, relief="flat", padx=4, pady=4,
                            bg=theme.CARD, highlightbackground=theme.BORDER, highlightthickness=1)
            cell.grid(row=idx // cols, column=idx % cols, padx=6, pady=6, sticky="n")

            tk.Label(cell, image=photo, bg=theme.CARD).pack()
            tk.Label(cell, text=filename, font=("Segoe UI", 7), wraplength=THUMB,
                     bg=theme.CARD, fg=theme.TEXT_DIM).pack()

        self.out_canvas.update_idletasks()

    # ----------------------------------------------------------------- Save
    def _save(self):
        if not self.result_images:
            return
        output_dir = self.output_var.get().strip()
        if not output_dir:
            messagebox.showwarning("Missing output folder", "Please set an output folder.")
            return
        fmt = self.format_var.get()
        ext_map = {"PNG": ".png", "JPEG": ".jpg", "BMP": ".bmp", "SVG": ".svg"}
        ext = ext_map.get(fmt, ".png")
        try:
            os.makedirs(output_dir, exist_ok=True)
            if fmt == "SVG":
                if not self._orig_svg_data:
                    raise ValueError("No SVG output data available. Run separation on an SVG input first.")
                mode = self._color_mode_var.get()
                for filename, svg_str in self._orig_svg_data:
                    svg_out = _apply_svg_output_transforms(
                        svg_str,
                        color_mode=mode,
                        bg_color=self._bg_color,
                    )
                    base = os.path.splitext(filename)[0]
                    save_path = os.path.join(output_dir, base + ".svg")
                    with open(save_path, "w", encoding="utf-8") as fh:
                        fh.write(svg_out)
            else:
                for filename, img in self.result_images:
                    base = os.path.splitext(filename)[0]
                    save_path = os.path.join(output_dir, base + ext)
                    if fmt in ("JPEG", "BMP"):
                        # flatten transparent areas onto white for formats without alpha
                        bg = Image.new("RGB", img.size, (255, 255, 255))
                        bg.paste(img, mask=img.split()[3])
                        bg.save(save_path, format=fmt)
                    else:
                        img.save(save_path, format=fmt)
            self.status_var.set(f"Saved {len(self.result_images)} image(s) to: {output_dir}")
            messagebox.showinfo("Saved", f"Saved {len(self.result_images)} images to:\n{output_dir}")
        except Exception as exc:
            messagebox.showerror("Save error", str(exc))


if __name__ == "__main__":
    App().mainloop()