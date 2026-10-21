"""Generate a decorative heart-shaped PNG while preserving a scannable QR core."""
from __future__ import annotations

import math
from pathlib import Path

import qrcode
from qrcode.constants import ERROR_CORRECT_H
from PIL import Image, ImageDraw, ImageFilter

DEFAULT_SIZE = 1024
DEFAULT_BG = "#FFCCF0"
DEFAULT_FG = "#222222"
DEFAULT_PETAL = "#D90067"
DEFAULT_CENTER_HEART = "#FF0257"

# Smooth outer heart silhouette recovered from the last compiled generator.
BIG_HEART_POINTS = [(0.2773, 0.0342), (0.2512, 0.0361), (0.2266, 0.04), (0.2021, 0.0459), (0.1783, 0.0537),
 (0.1556, 0.0641), (0.1337, 0.0763), (0.1129, 0.0912), (0.0941, 0.1081), (0.0761, 0.127),
 (0.0612, 0.1478), (0.0479, 0.168), (0.0371, 0.1904), (0.0277, 0.2135), (0.0205, 0.2375),
 (0.0146, 0.2621), (0.0112, 0.2876), (0.0089, 0.3136), (0.0088, 0.3398), (0.0117, 0.3655),
 (0.0156, 0.3909), (0.0211, 0.4156), (0.0273, 0.44), (0.0358, 0.4635), (0.0449, 0.4867),
 (0.0547, 0.5096), (0.0664, 0.5317), (0.0782, 0.5538), (0.0918, 0.5752), (0.1055, 0.5965),
 (0.1203, 0.6173), (0.1359, 0.6378), (0.1522, 0.6581), (0.1691, 0.6769), (0.186, 0.6957),
 (0.2037, 0.7154), (0.2222, 0.733), (0.2401, 0.7508), (0.2593, 0.7681), (0.2793, 0.7851),
 (0.2992, 0.8021), (0.3189, 0.8184), (0.3392, 0.8344), (0.3597, 0.85), (0.3805, 0.8649),
 (0.4013, 0.8798), (0.4224, 0.8941), (0.4434, 0.9072), (0.4643, 0.9219), (0.4856, 0.9355),
 (0.5075, 0.9388), (0.5288, 0.9253), (0.5499, 0.9111), (0.571, 0.8968), (0.5917, 0.883),
 (0.6125, 0.8682), (0.6334, 0.8535), (0.654, 0.8382), (0.6739, 0.8223), (0.6944, 0.8066),
 (0.7143, 0.7896), (0.7342, 0.7726), (0.7535, 0.7553), (0.7728, 0.7369), (0.7918, 0.7189),
 (0.809, 0.7008), (0.8265, 0.6822), (0.8434, 0.6634), (0.8597, 0.6432), (0.8761, 0.623),
 (0.8916, 0.6024), (0.9059, 0.5814), (0.9189, 0.5598), (0.9323, 0.5384), (0.9434, 0.516),
 (0.9541, 0.494), (0.9636, 0.471), (0.9717, 0.4473), (0.9785, 0.4232), (0.9844, 0.3986),
 (0.9883, 0.3733), (0.9902, 0.3471), (0.9902, 0.3201), (0.9893, 0.2935), (0.9863, 0.2678),
 (0.9812, 0.2429), (0.9746, 0.2187), (0.9651, 0.1956), (0.9537, 0.1734), (0.9404, 0.1525),
 (0.9249, 0.1319), (0.9062, 0.1133), (0.8867, 0.0967), (0.8658, 0.082), (0.8437, 0.0702),
 (0.8209, 0.0601), (0.7972, 0.0521), (0.7727, 0.0462), (0.7475, 0.042), (0.7209, 0.041),
 (0.6943, 0.042), (0.6685, 0.0449), (0.6436, 0.0498), (0.6198, 0.0576), (0.5969, 0.0674),
 (0.5752, 0.0801), (0.5543, 0.0947), (0.5348, 0.1127), (0.5184, 0.1329), (0.5049, 0.1543),
 (0.4917, 0.1509), (0.4785, 0.1306), (0.462, 0.1104), (0.4426, 0.092), (0.4218, 0.0771),
 (0.4009, 0.064), (0.3781, 0.0537), (0.3547, 0.0451), (0.3302, 0.0392), (0.3045, 0.0361)]


def _hex_to_rgba(hex_color: str, alpha: int = 255) -> tuple[int, int, int, int]:
    value = hex_color.strip().lstrip("#")
    if len(value) == 3:
        value = "".join(char * 2 for char in value)
    if len(value) != 6:
        raise ValueError("La couleur doit être au format hexadécimal #RRGGBB.")
    red, green, blue = (int(value[index:index + 2], 16) for index in (0, 2, 4))
    return red, green, blue, alpha


def generate_heart_qr(
    data: str,
    output_path: str | Path | None = None,
    size: int = DEFAULT_SIZE,
    bg_hex: str = DEFAULT_BG,
    fg_hex: str = DEFAULT_FG,
    petal_hex: str = DEFAULT_PETAL,
    center_heart_hex: str = DEFAULT_CENTER_HEART,
) -> Image.Image:
    """Render a heart of decorative modules around an intact square QR code.

    The 4-module quiet zone is kept clear and blends into the heart background.
    ``petal_hex`` and ``center_heart_hex`` remain accepted for API compatibility.
    """
    del petal_hex, center_heart_hex
    if not isinstance(data, str) or not data.strip():
        raise ValueError("Les données ou l'URL à encoder ne peuvent pas être vides.")
    if not isinstance(size, int) or size < 1:
        raise ValueError("La taille de sortie doit être un entier positif.")

    bg_color = _hex_to_rgba(bg_hex)
    fg_color = _hex_to_rgba(fg_hex)

    # Keep a full QR matrix at the center; error correction H tolerates the
    # decorative frame, while the required quiet zone stays free of modules.
    qr = qrcode.QRCode(error_correction=ERROR_CORRECT_H, border=0)
    qr.add_data(data.strip())
    qr.make(fit=True)
    core_matrix = qr.get_matrix()
    core_n = qr.modules_count
    quiet_zone_modules = 4

    required_grid = math.ceil((core_n + quiet_zone_modules * 2) / 0.48)
    required_version = math.ceil((required_grid - 17) / 4)
    decorative_version = min(40, max(qr.version, 21, required_version))
    decorative = qrcode.QRCode(
        version=decorative_version,
        error_correction=ERROR_CORRECT_H,
        border=0,
    )
    decorative.add_data("DEDIKKA-HEART-QR-LOVE-FOREVER-TEXTURE-PATTERN")
    decorative.make(fit=True)
    decorative_matrix = decorative.get_matrix()
    decorative_n = len(decorative_matrix)

    col_offset = (decorative_n - core_n) // 2
    row_offset = (decorative_n - core_n) // 2
    scale = 2
    canvas_size = size * scale
    cell_size = canvas_size / float(decorative_n)
    grid = [list(row) for row in decorative_matrix]

    def clear_zone(r0: int, r1: int, c0: int, c1: int) -> None:
        for row_index in range(max(0, r0), min(decorative_n, r1)):
            for col_index in range(max(0, c0), min(decorative_n, c1)):
                grid[row_index][col_index] = False

    clear_zone(
        row_offset - quiet_zone_modules,
        row_offset + core_n + quiet_zone_modules,
        col_offset - quiet_zone_modules,
        col_offset + core_n + quiet_zone_modules,
    )

    for row_index in range(core_n):
        for col_index in range(core_n):
            grid[row_offset + row_index][col_offset + col_index] = core_matrix[row_index][col_index]

    core_r1 = row_offset + core_n
    core_c1 = col_offset + core_n
    mask_img = Image.new("L", (canvas_size, canvas_size), 0)
    mask_draw = ImageDraw.Draw(mask_img)
    big_pts = [
        (x * canvas_size, y * canvas_size)
        for x, y in BIG_HEART_POINTS
    ]
    mask_draw.polygon(big_pts, fill=255)
    filter_size = max(3, int(scale * 2) + 1)
    if filter_size % 2 == 0:
        filter_size += 1
    eroded_mask = mask_img.filter(ImageFilter.MinFilter(filter_size))

    canvas = Image.new("RGBA", (canvas_size, canvas_size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    draw.polygon(big_pts, fill=bg_color)
    width_ratio = 0.74
    height_ratio = 0.85
    radius = int(3 * scale)

    for row_index in range(decorative_n):
        for col_index in range(decorative_n):
            if row_offset <= row_index < core_r1 and col_offset <= col_index < core_c1:
                continue
            if not grid[row_index][col_index]:
                continue

            center_x = int(round((col_index + 0.5) * cell_size))
            center_y = int(round((row_index + 0.5) * cell_size))
            if eroded_mask.getpixel((center_x, center_y)) == 0:
                continue

            top_connected = (
                row_index > 0
                and grid[row_index - 1][col_index]
                and eroded_mask.getpixel((center_x, int(round((row_index - 0.5) * cell_size)))) > 0
            )
            bottom_connected = (
                row_index < decorative_n - 1
                and grid[row_index + 1][col_index]
                and eroded_mask.getpixel((center_x, int(round((row_index + 1.5) * cell_size)))) > 0
            )

            module_width = cell_size * width_ratio
            module_height = cell_size * height_ratio
            x0 = col_index * cell_size + (cell_size - module_width) / 2
            x1 = x0 + module_width
            y0 = (
                row_index * cell_size
                if top_connected
                else row_index * cell_size + (cell_size - module_height) / 2
            )
            y1 = (
                (row_index + 1) * cell_size
                if bottom_connected
                else (row_index + 1) * cell_size - (cell_size - module_height) / 2
            )
            draw.rounded_rectangle(
                (x0, y0, x1, y1),
                radius=radius,
                fill=fg_color,
            )

    # Draw the QR core last, as crisp square modules; do not reshape or cover it.
    for row_index, row in enumerate(core_matrix):
        for col_index, is_dark in enumerate(row):
            if not is_dark:
                continue
            x0 = round((col_offset + col_index) * cell_size)
            y0 = round((row_offset + row_index) * cell_size)
            x1 = round((col_offset + col_index + 1) * cell_size) - 1
            y1 = round((row_offset + row_index + 1) * cell_size) - 1
            draw.rectangle((x0, y0, x1, y1), fill=fg_color)

    final_img = canvas.resize((size, size), Image.Resampling.NEAREST)
    if output_path:
        final_img.save(output_path, "PNG")
    return final_img


if __name__ == "__main__":
    import sys

    url = sys.argv[1] if len(sys.argv) > 1 else "https://dedikka.web.app/"
    destination = sys.argv[2] if len(sys.argv) > 2 else "heart_qr.png"
    print(f"Génération du code QR pour : {url} → {destination}")
    generate_heart_qr(url, output_path=destination)
    print("Terminé avec succès !")
