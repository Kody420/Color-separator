from PIL import Image, ImageOps, ImageTk
import numpy as np
import os
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from scipy import ndimage
from sklearn.cluster import KMeans

THUMB = 200  # max thumbnail dimension in pixels


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
    input_path      : PNG image
    num_colors      : number of dominant colors to extract (k-means clusters)
    tolerance       : 0.0 = strict clustering, 1.0 = more smoothing (less sensitive)
    min_island_size : remove connected pixel islands smaller than this (0 = off)
    Returns list of (filename, PIL.Image) — one entry per color cluster.
    """

    img = Image.open(input_path).convert("RGBA")
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


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Color Separator")
        self.minsize(960, 540)
        self.result_images = []   # list of (filename, PIL.Image)
        self._orig_images  = []   # pre-transform originals
        self._photo_refs   = []   # keep PhotoImage refs alive
        self._mono_var   = tk.BooleanVar(value=False)
        self._invert_var = tk.BooleanVar(value=False)
        self.format_var  = tk.StringVar(value="PNG")
        self._build_ui()

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        pad = {"padx": 6, "pady": 4}

        # ── Left: controls ────────────────────────────────────────────────
        ctrl = tk.LabelFrame(self, text="Settings", padx=8, pady=8)
        ctrl.pack(side="left", fill="y", padx=8, pady=8)

        tk.Label(ctrl, text="Input PNG:").grid(row=0, column=0, sticky="e", **pad)
        self.input_var = tk.StringVar()
        tk.Entry(ctrl, textvariable=self.input_var, width=30).grid(row=0, column=1, **pad)
        tk.Button(ctrl, text="Browse…", command=self._browse_input).grid(row=0, column=2, **pad)

        tk.Label(ctrl, text="Output folder:").grid(row=1, column=0, sticky="e", **pad)
        self.output_var = tk.StringVar(value="output_colors")
        tk.Entry(ctrl, textvariable=self.output_var, width=30).grid(row=1, column=1, **pad)
        tk.Button(ctrl, text="Browse…", command=self._browse_output).grid(row=1, column=2, **pad)

        tk.Label(ctrl, text="Number of colors:").grid(row=2, column=0, sticky="e", **pad)
        self.num_colors_var = tk.IntVar(value=3)
        tk.Spinbox(ctrl, from_=1, to=20, textvariable=self.num_colors_var, width=6).grid(
            row=2, column=1, sticky="w", **pad)

        tk.Label(ctrl, text="Tolerance (0–1):").grid(row=3, column=0, sticky="e", **pad)
        self.tolerance_var = tk.DoubleVar(value=0.2)
        sf = tk.Frame(ctrl)
        sf.grid(row=3, column=1, sticky="w", **pad)
        tk.Scale(sf, variable=self.tolerance_var, from_=0.0, to=1.0,
                 resolution=0.05, orient="horizontal", length=170).pack(side="left")
        tk.Label(sf, textvariable=self.tolerance_var, width=4).pack(side="left")

        tk.Label(ctrl, text="Min island size (px):").grid(row=4, column=0, sticky="e", **pad)
        isf = tk.Frame(ctrl)
        isf.grid(row=4, column=1, sticky="w", **pad)
        self.min_island_var = tk.IntVar(value=0)
        tk.Spinbox(isf, from_=0, to=100000, textvariable=self.min_island_var, width=8).pack(side="left")
        tk.Label(isf, text="  (0 = off)", fg="gray").pack(side="left")

        tk.Label(ctrl, text="Output format:").grid(row=5, column=0, sticky="e", **pad)
        ttk.Combobox(ctrl, textvariable=self.format_var, values=["PNG", "JPEG", "BMP"],
                     state="readonly", width=8).grid(row=5, column=1, sticky="w", **pad)

        btn_row = tk.Frame(ctrl)
        btn_row.grid(row=6, column=0, columnspan=3, pady=10)
        self.run_btn = tk.Button(btn_row, text="Run", width=10, command=self._run)
        self.run_btn.pack(side="left", padx=4)
        self.save_btn = tk.Button(btn_row, text="Save", width=10, command=self._save, state="disabled")
        self.save_btn.pack(side="left", padx=4)

        self.progress = ttk.Progressbar(ctrl, mode="indeterminate", length=270)
        self.progress.grid(row=7, column=0, columnspan=3, pady=4)

        self.status_var = tk.StringVar(value="Ready.")
        tk.Label(ctrl, textvariable=self.status_var, fg="gray",
                 wraplength=270, justify="left").grid(row=8, column=0, columnspan=3, pady=(0, 4))

        # ── Right: preview ────────────────────────────────────────────────
        preview = tk.Frame(self, padx=8, pady=8)
        preview.pack(side="left", fill="both", expand=True)

        tk.Label(preview, text="Input image", font=("", 9, "bold")).pack(anchor="w")
        self.input_lbl = tk.Label(preview, text="(no image selected)",
                                   relief="groove", bg="#f0f0f0", width=28, height=4)
        self.input_lbl.pack(anchor="w", pady=(2, 8))

        ttk.Separator(preview, orient="horizontal").pack(fill="x", pady=4)

        hdr_row = tk.Frame(preview)
        hdr_row.pack(fill="x", pady=(2, 4))
        tk.Label(hdr_row, text="Output colors", font=("", 9, "bold")).pack(side="left")
        xf_frame = tk.Frame(hdr_row)
        xf_frame.pack(side="right")
        self._mono_cb = tk.Checkbutton(xf_frame, text="Monochromatic",
                                        variable=self._mono_var, command=self._apply_transforms,
                                        state="disabled")
        self._mono_cb.pack(side="left", padx=6)
        self._invert_cb = tk.Checkbutton(xf_frame, text="Invert",
                                          variable=self._invert_var, command=self._apply_transforms,
                                          state="disabled")
        self._invert_cb.pack(side="left", padx=6)

        # Scrollable output area
        out_outer = tk.Frame(preview)
        out_outer.pack(fill="both", expand=True, pady=(4, 0))

        self.out_canvas = tk.Canvas(out_outer, highlightthickness=0, bg="#f8f8f8")
        vsb = ttk.Scrollbar(out_outer, orient="vertical", command=self.out_canvas.yview)
        self.out_canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        self.out_canvas.pack(side="left", fill="both", expand=True)

        self.out_frame = tk.Frame(self.out_canvas, bg="#f8f8f8")
        self._out_win = self.out_canvas.create_window((0, 0), window=self.out_frame, anchor="nw")

        self.out_frame.bind("<Configure>", lambda e: self.out_canvas.configure(
            scrollregion=self.out_canvas.bbox("all")))
        self.out_canvas.bind("<Configure>", lambda e: self.out_canvas.itemconfig(
            self._out_win, width=e.width))
        self.out_canvas.bind("<MouseWheel>", lambda e: self.out_canvas.yview_scroll(
            -1 if e.delta > 0 else 1, "units"))

    # --------------------------------------------------------- Browse helpers
    def _apply_transforms(self):
        """Re-derive result_images from originals applying current mono/invert flags."""
        if not self._orig_images:
            return
        images = []
        for filename, img in self._orig_images:
            out = img.copy()
            if self._mono_var.get():
                r, g, b, a = out.split()
                bw = out.convert("L").point(lambda x: 255 if x >= 128 else 0)
                out = Image.merge("RGBA", (bw, bw, bw, a))
            if self._invert_var.get():
                r, g, b, a = out.split()
                out = Image.merge("RGBA", (
                    ImageOps.invert(r),
                    ImageOps.invert(g),
                    ImageOps.invert(b),
                    a,
                ))
            images.append((filename, out))
        self.result_images = images
        self._show_output_thumbs(images)

    def _browse_input(self):
        path = filedialog.askopenfilename(filetypes=[("PNG files", "*.png"), ("All files", "*.*")])
        if path:
            self.input_var.set(path)
            if self.output_var.get() in ("", "output_colors"):
                self.output_var.set(os.path.join(os.path.dirname(path), "output_colors"))
            self._update_input_thumb(path)

    def _browse_output(self):
        path = filedialog.askdirectory()
        if path:
            self.output_var.set(path)

    def _update_input_thumb(self, path):
        try:
            img = Image.open(path)
            img.thumbnail((THUMB, THUMB), Image.LANCZOS)
            photo = ImageTk.PhotoImage(img)
            self.input_lbl.configure(image=photo, text="", relief="flat",
                                      width=img.width, height=img.height, bg=self.cget("bg"))
            self.input_lbl._photo = photo  # prevent GC
        except Exception:
            self.input_lbl.configure(image="", text="(preview error)", relief="groove")

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
                images = separate_colors(
                    input_path,
                    num_colors=self.num_colors_var.get(),
                    tolerance=self.tolerance_var.get(),
                    min_island_size=self.min_island_var.get(),
                )
                self.after(0, self._on_done, images, None)
            except Exception as exc:
                self.after(0, self._on_done, None, str(exc))

        threading.Thread(target=worker, daemon=True).start()

    def _on_done(self, images, error):
        self.progress.stop()
        self.run_btn.config(state="normal")
        if error:
            self.status_var.set("Error.")
            messagebox.showerror("Error", error)
        else:
            self._orig_images = images
            self._mono_var.set(False)
            self._invert_var.set(False)
            self.result_images = images
            self._show_output_thumbs(images)
            self.save_btn.config(state="normal")
            self._mono_cb.config(state="normal")
            self._invert_cb.config(state="normal")
            self.status_var.set(f"{len(images)} color(s) extracted. Press Save to write files.")

    def _show_output_thumbs(self, images):
        for widget in self.out_frame.winfo_children():
            widget.destroy()
        self._photo_refs = []

        cols = max(1, self.out_canvas.winfo_width() // (THUMB + 24))

        for idx, (filename, img) in enumerate(images):
            thumb = img.copy()
            thumb.thumbnail((THUMB, THUMB), Image.LANCZOS)
            photo = ImageTk.PhotoImage(thumb)
            self._photo_refs.append(photo)

            cell = tk.Frame(self.out_frame, relief="groove", bd=1, padx=4, pady=4,
                            bg="#f8f8f8")
            cell.grid(row=idx // cols, column=idx % cols, padx=6, pady=6, sticky="n")

            tk.Label(cell, image=photo, bg="#f8f8f8").pack()
            tk.Label(cell, text=filename, font=("", 7), wraplength=THUMB,
                     bg="#f8f8f8").pack()

    # ----------------------------------------------------------------- Save
    def _save(self):
        if not self.result_images:
            return
        output_dir = self.output_var.get().strip()
        if not output_dir:
            messagebox.showwarning("Missing output folder", "Please set an output folder.")
            return
        fmt = self.format_var.get()
        ext_map = {"PNG": ".png", "JPEG": ".jpg", "BMP": ".bmp"}
        ext = ext_map.get(fmt, ".png")
        try:
            os.makedirs(output_dir, exist_ok=True)
            prefix = "inv_" if self._invert_var.get() else ""
            for filename, img in self.result_images:
                base = os.path.splitext(filename)[0]
                save_path = os.path.join(output_dir, prefix + base + ext)
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