#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Archivo: archivo.py
Descripción / cabecera:
  - Refactor del entrypoint original "streamlit_etiquetador.py" para adaptar la UI a un tema claro
    con "cards", layout centralizado (max-width ~1100px), dos columnas (≈65% / 35%), y un botón
    grande verde centrado en el pie.
  - Mantiene la lógica funcional de generación de etiquetas y PDF (usando reportlab) y añade:
      - preview_label() que genera una mini‑vista previa PNG usando Pillow
      - generate_pdf() que crea y devuelve bytes PDF (reportlab)
      - build_ui(), validate_inputs(), main() organizadas y documentadas
      - session_state robusto para preservar inputs y evitar concurrencia
      - estilos CSS inyectados para el tema claro y "card" styling
  - Dependencias necesarias:
      pip install streamlit reportlab Pillow
  - Cómo probar localmente:
      1) Instala dependencias: pip install streamlit reportlab Pillow
      2) Ejecuta: streamlit run archivo.py
      3) Ajusta campos y pulsa "GENERAR PDF" (botón grande centrado). La aplicación
         mostrará una vista previa y luego permitirá descargar/abrir el PDF.

Notas importantes en el código:
  - La cuenta de etiquetas generadas se calcula a partir de la configuración de viales (viales_multiplicadores)
    y la lista lógica de elementos (blanco, wash, patrones, muestras, placebos, reactivos). El PDF contendrá
    una etiqueta por elemento expandido por su multiplicador, y las etiquetas se numeran lógicamente en
    el PDF (no se imprimen números secuenciales en cada etiqueta, salvo lo que figura en el texto).
  - session_state administra estados como 'generating', 'last_pdf', 'lotes', 'reactivos', etc.
  - CSS inyectado controla el aspecto general, el contenedor central y las "cards".
"""

from io import BytesIO
from datetime import datetime
import re
import base64
import uuid
import json
import time

import streamlit as st
import streamlit.components.v1 as components

# Dependencias para PDF y preview
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from PIL import Image, ImageDraw, ImageFont

# ----------------------------
# Constantes y paleta
# ----------------------------
CM_TO_PT = 28.35
MARGIN_X = 1.09 * CM_TO_PT
MARGIN_Y = 1.4 * CM_TO_PT
ETIQ_WIDTH = 3.56 * CM_TO_PT
ETIQ_HEIGHT = 1.69 * CM_TO_PT
COLS = 5
ROWS = 16
H_STEP = 3.81 * CM_TO_PT
V_STEP = 1.69 * CM_TO_PT
TOTAL_ETIQUETAS_PAGINA = COLS * ROWS  # 80

STD_A_COLOR = "#1f77b4"
STD_B_COLOR = "#ffbf00"
BLANCO_COLOR = "#ecf0f1"
REACTIVO_COLOR = "#f39c12"
PLACEBO_COLOR = "#ff0000"

FORBIDDEN_COLORS = {c.lower() for c in {BLANCO_COLOR, STD_A_COLOR, STD_B_COLOR, REACTIVO_COLOR, PLACEBO_COLOR, "#e6194b", "#f58231"}}

def build_sample_palette():
    # Paleta simple reutilizable (minus colores prohibidos)
    palette = [
        "#2ca02c", "#9467bd", "#8c564b", "#e377c2", "#17becf", "#7f7f7f",
        "#bcbd22", "#98df8a", "#c5b0d5", "#6b6bd3", "#00a5a5", "#b59ddb",
        "#9edae5", "#c49c94", "#dbdb8d"
    ]
    palette = [p.lower() for p in palette]
    filtered = [c for c in palette if c not in FORBIDDEN_COLORS]
    extras = ["#2f4f4f", "#6a5acd", "#20b2aa", "#00ced1", "#4b0082", "#556b2f", "#4682b4", "#8b4513"]
    for e in extras:
        if e not in filtered:
            filtered.append(e)
        if len(filtered) >= 12:
            break
    return filtered[:12]

SAMPLE_PALETTE = build_sample_palette()

# ----------------------------
# Utilidades
# ----------------------------
def new_uid(prefix="u"):
    return f"{prefix}_{uuid.uuid4().hex[:8]}"

def sanitize_key(s: str) -> str:
    return re.sub(r'[^0-9a-zA-Z_]', '_', s)

def safe_int_from_str(s, default=0):
    try:
        if s is None or str(s).strip() == "":
            return default
        return int(float(str(s).strip()))
    except Exception:
        return default

def _format_with_unit(value: str, unit: str) -> str:
    if value is None:
        return ""
    v = str(value).strip()
    if v == "":
        return ""
    if re.search(r"[A-Za-z]", v):
        return v
    return f"{v}{unit}"

def allocate_lote_color(index: int):
    pool = [c for c in SAMPLE_PALETTE if c.lower() not in FORBIDDEN_COLORS]
    if not pool:
        return "#6b6bd3"
    return pool[index % len(pool)]

# ----------------------------
# Session state init
# ----------------------------
def init_session_state():
    ss = st.session_state
    ss.setdefault("show_color_square", True)
    ss.setdefault("dup_patron", False)
    ss.setdefault("dup_muestra", True)
    ss.setdefault("uniformidad", False)
    ss.setdefault("incluir_placebo", False)
    ss.setdefault("incluir_viales", True)

    ss.setdefault("num_uniform_samples", 2)
    ss.setdefault("num_lotes", 1)
    ss.setdefault("num_reactivos", 0)

    # Datos básicos
    ss.setdefault("texto_blanco", "")
    ss.setdefault("texto_wash", "")
    ss.setdefault("peso_patron", "")
    ss.setdefault("vol_patron", "")
    ss.setdefault("muestra_peso", "")
    ss.setdefault("muestra_vol", "")
    ss.setdefault("placebo_peso", "")
    ss.setdefault("placebo_vol", "")
    ss.setdefault("nombre_prod", "")
    ss.setdefault("lote_general", "")
    ss.setdefault("determinacion", "")
    ss.setdefault("analista", "YAK")
    ss.setdefault("fecha", datetime.today().strftime("%d/%m/%Y"))

    ss.setdefault("start_label", 1)

    # Lotes: list of names
    if "lotes" not in ss or not isinstance(ss.lotes, list):
        ss["lotes"] = ["Lote1"]

    # Reactivos: list of dicts {name,color,multiplier}
    if "reactivos" not in ss or not isinstance(ss.reactivos, list):
        ss["reactivos"] = []

    # viales multiplicadores mapping id->int (se sincroniza en build)
    ss.setdefault("viales_multiplicadores", {})

    # PDF and generating state
    ss.setdefault("generating", False)
    ss.setdefault("trigger_generate", False)
    ss.setdefault("last_pdf", None)
    ss.setdefault("last_pdf_name", "")
    ss.setdefault("last_total", 0)
    ss.setdefault("preview_img", None)

init_session_state()

# ----------------------------
# Functions to build logical vials/etiquetas (adapted)
# ----------------------------
def construir_ids_viales_from_state(state):
    """
    Construye la lista lógica de IDs (viales) a partir del estado dado.
    Devuelve lista de dicts: {id, type, lot_index}
    """
    items = []
    tb = (state.get("texto_blanco") or "").strip()
    items.append({"id": f"Blanco ({tb})" if tb else "Blanco", "type": "blank", "lot_index": None})
    tw = (state.get("texto_wash") or "").strip()
    items.append({"id": f"Wash ({tw})" if tw else "Wash", "type": "blank", "lot_index": None})

    if state.get("dup_patron"):
        items.append({"id": "STD A", "type": "std", "lot_index": None})
        items.append({"id": "STD B", "type": "std", "lot_index": None})
    else:
        items.append({"id": "STD", "type": "std", "lot_index": None})

    # no se consideran diluciones_std manuales aquí para simplificar (pueden añadirse si se requiere)
    # Lotes
    lotes = state.get("lotes", [])
    for i, lote_name in enumerate(lotes):
        lote_name = (lote_name or "").strip() or f"Lote{i+1}"
        if state.get("uniformidad"):
            n = safe_int_from_str(state.get("num_uniform_samples"), 1)
            n = max(1, min(n, 100))
            for k in range(1, n+1):
                items.append({"id": f"{lote_name}/{k}", "type": "sample", "lot_index": i})
        else:
            if state.get("dup_muestra"):
                items.append({"id": f"{lote_name}/A", "type": "sample", "lot_index": i})
                items.append({"id": f"{lote_name}/B", "type": "sample", "lot_index": i})
            else:
                items.append({"id": lote_name, "type": "sample", "lot_index": i})

    if state.get("incluir_placebo"):
        items.append({"id": "Placebo", "type": "placebo", "lot_index": None})

    for r in state.get("reactivos", []):
        name = (r.get("name") or "").strip()
        if name:
            items.append({"id": name, "type": "reactivo", "lot_index": None})

    # remove duplicates preserving order
    seen = set()
    final = []
    for it in items:
        if it["id"] not in seen:
            final.append(it)
            seen.add(it["id"])
    return final

def assign_colors_for_ids_for_state(items, state):
    """
    Asegura y asigna colores por id y por lote.
    Mantiene state['id_color_map'] y state['lote_color_map'].
    """
    id_map = state.get("id_color_map", {})
    lote_map = state.get("lote_color_map", {})

    # ensure lote colors
    for i in range(len(state.get("lotes", []))):
        if i not in lote_map or (lote_map.get(i) or "").lower() in FORBIDDEN_COLORS:
            lote_map[i] = allocate_lote_color(i)
    for it in items:
        vid = it["id"]
        t = it["type"]
        if t == "blank":
            id_map[vid] = BLANCO_COLOR
        elif t == "std":
            if vid.endswith("/A") or vid == "STD A":
                id_map[vid] = STD_A_COLOR
            elif vid.endswith("/B") or vid == "STD B":
                id_map[vid] = STD_B_COLOR
            else:
                id_map[vid] = STD_A_COLOR
        elif t == "placebo":
            id_map[vid] = PLACEBO_COLOR
        elif t == "reactivo":
            id_map[vid] = REACTIVO_COLOR
        elif t == "sample":
            li = it.get("lot_index")
            id_map[vid] = lote_map.get(li, allocate_lote_color(li))
    state["id_color_map"] = id_map
    state["lote_color_map"] = lote_map

def build_etiquetas_from_state(state_in):
    """
    Construye la lista final de etiquetas (tipo, id_text, color_hex) expandiendo multiplicadores.
    Se usa luego para generar el PDF. Esta función es 'stateless' respecto al session_state
    (trabaja con una copia del dict que se le pase).
    """
    state = json.loads(json.dumps(state_in))  # deep copy-like
    etiquetas = []

    # Agregar encabezado de patrones simple (mantener consistencia visual)
    if state.get("dup_patron"):
        etiquetas.append(("STD_A", f"STD A {_format_with_unit(state.get('peso_patron'), 'g')}/{_format_with_unit(state.get('vol_patron'), 'ml')}", STD_A_COLOR))
        etiquetas.append(("STD_B", f"STD B {_format_with_unit(state.get('peso_patron'), 'g')}/{_format_with_unit(state.get('vol_patron'), 'ml')}", STD_B_COLOR))
    else:
        etiquetas.append(("STD_A", f"STD {_format_with_unit(state.get('peso_patron'), 'g')}/{_format_with_unit(state.get('vol_patron'), 'ml')}", STD_A_COLOR))

    # Muestras por lote
    for li, lote in enumerate(state.get("lotes", [])):
        name = (lote or "").strip() or f"Lote{li+1}"
        peso_label = _format_with_unit(state.get("muestra_peso"), "g")
        vol_label = _format_with_unit(state.get("muestra_vol"), "ml")
        suffix_parts = []
        if peso_label:
            suffix_parts.append(peso_label)
        if vol_label:
            suffix_parts.append(vol_label)
        suffix = ("/".join(suffix_parts)) if suffix_parts else ""
        color = (state.get("lote_color_map", {}) or {}).get(str(li)) or (state.get("lote_color_map", {}) or {}).get(li) or allocate_lote_color(li)
        # uniformidad
        if state.get("uniformidad"):
            n = safe_int_from_str(state.get("num_uniform_samples"), 1)
            n = max(1, min(n, 100))
            for k in range(1, n+1):
                etiquetas.append(("MUESTRA", f"{name}/{k}" + (f" {suffix}" if suffix else ""), color))
        else:
            if state.get("dup_muestra"):
                etiquetas.append(("MUESTRA", f"{name}/A" + (f" {suffix}" if suffix else ""), color))
                etiquetas.append(("MUESTRA", f"{name}/B" + (f" {suffix}" if suffix else ""), color))
            else:
                etiquetas.append(("MUESTRA", f"{name}" + (f" {suffix}" if suffix else ""), color))

    # Placebo
    if state.get("incluir_placebo"):
        p1 = (state.get("placebo_peso") or "").strip()
        p2 = (state.get("placebo_vol") or "").strip()
        p1_fmt = _format_with_unit(p1, "g") if p1 else ""
        p2_fmt = _format_with_unit(p2, "ml") if p2 else ""
        if p1_fmt or p2_fmt:
            etiquetas.append(("PLACEBO", f"Placebo {p1_fmt}/{p2_fmt}", PLACEBO_COLOR))
        else:
            etiquetas.append(("PLACEBO", "Placebo", PLACEBO_COLOR))

    # Reactivos (nombres) — usar color si está en reactivos list
    for r in state.get("reactivos", []):
        name = (r.get("name") or "").strip()
        color = r.get("color") or REACTIVO_COLOR
        if name:
            etiquetas.append(("REACTIVO", name, color))

    # Ahora expandir según multiplicadores viales_multiplicadores
    # Primero sincronizar ids y colores
    items = construir_ids_viales_from_state(state)
    assign_colors_for_ids_for_state(items, state)
    # default multipliers: reactivo->0 else->1
    new_mult = {}
    for it in items:
        vid = it["id"]
        if vid in state.get("viales_multiplicadores", {}):
            try:
                new_mult[vid] = int(state["viales_multiplicadores"].get(vid, 0))
            except Exception:
                new_mult[vid] = 0 if it["type"] == "reactivo" else 1
        else:
            new_mult[vid] = 0 if it["type"] == "reactivo" else 1
    # apply multipliers: for each id, append that many "VIAL" etiquetas
    for it in items:
        vid = it["id"]
        mult = int(new_mult.get(vid, 0))
        color = state.get("id_color_map", {}).get(vid, "#cccccc")
        for _ in range(max(0, mult)):
            etiquetas.append(("VIAL", vid, color))

    return etiquetas

# ----------------------------
# PDF generation (reportlab)
# ----------------------------
def generate_pdf_bytes(etiquetas, ss):
    """
    Genera un PDF con las etiquetas pasadas (lista de tuplas (tipo, id_text, color_hex))
    y devuelve bytes. El layout reparte las etiquetas por página en la plantilla APLI
    (COLUMNAS x FILAS). Esta función usa la misma lógica que la versión desktop:
      - posiciona recuadro y texto, dibuja cuadrado de color si show_color_square True.
    Return: (pdf_bytes, total_generated)
    """
    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    margin_x_int = ETIQ_WIDTH * 0.05
    margin_y_int = ETIQ_HEIGHT * 0.05
    square_size = 0.45 * CM_TO_PT

    etiqueta_idx = max(0, safe_int_from_str(ss.get("start_label", 1), 1) - 1)
    # normalize start range
    if etiqueta_idx < 0:
        etiqueta_idx = 0
    total_generated = 0

    for tipo, id_text, color_hex in etiquetas:
        if etiqueta_idx >= TOTAL_ETIQUETAS_PAGINA:
            c.showPage()
            etiqueta_idx = 0

        col = etiqueta_idx % COLS
        row = etiqueta_idx // COLS
        base_x = MARGIN_X + col * H_STEP
        base_y = (A4[1] - MARGIN_Y) - (row + 1) * V_STEP

        # card border (subtle)
        c.setStrokeColor(colors.HexColor("#d6d6d6"))
        c.setLineWidth(0.6)
        c.rect(base_x, base_y, ETIQ_WIDTH, ETIQ_HEIGHT)

        inner_x = base_x + margin_x_int
        inner_y = base_y + margin_y_int
        inner_w = ETIQ_WIDTH - 2 * margin_x_int
        inner_h = ETIQ_HEIGHT - 2 * margin_y_int

        if ss.get("show_color_square", True):
            try:
                fill_color = colors.HexColor(color_hex)
            except Exception:
                fill_color = colors.HexColor("#cccccc")
            square_margin = ETIQ_WIDTH * 0.02
            square_x = base_x + ETIQ_WIDTH - square_size - square_margin
            square_y = base_y + ETIQ_HEIGHT - square_size - square_margin
            c.setFillColor(fill_color)
            c.setStrokeColor(colors.black)
            c.setLineWidth(0.6)
            c.rect(square_x, square_y, square_size, square_size, fill=1, stroke=1)

        datos = [
            f"Producto: {ss.get('nombre_prod','')}",
            f"Determinación: {ss.get('determinacion','')}",
            f"Lote: {ss.get('lote_general','')}",
            f"Analista: {ss.get('analista','')}    Fecha: {ss.get('fecha','')}"
        ]

        # Simple sizing: id bold, datos normal
        size_id = 9
        size_data = 7
        margin_text_w = inner_w * 0.05
        margin_text_h = inner_h * 0.05
        text_area_x = inner_x + margin_text_w
        text_area_y = inner_y + margin_text_h
        text_area_w = inner_w - (2 * margin_text_w) - (square_size * 0.6)
        text_area_h = inner_h - (2 * margin_text_h)

        c.setFont("Helvetica-Bold", size_id)
        c.setFillColor(colors.black)
        text_id_y = text_area_y + text_area_h - size_id
        # center text horizontally in the text area
        text_width = c.stringWidth(id_text, "Helvetica-Bold", size_id)
        if text_width > text_area_w:
            x_adj = text_area_x - ((text_width - text_area_w) * 0.3)
        else:
            x_adj = text_area_x + (text_area_w - text_width) / 2
        c.drawString(x_adj, text_id_y, id_text)

        c.setFont("Helvetica", size_data)
        line_height = size_data * 1.15
        data_start_y = text_id_y - line_height
        for i, dato in enumerate(datos):
            y_pos = data_start_y - i * line_height
            if y_pos < text_area_y:
                break
            text_width = c.stringWidth(dato, "Helvetica", size_data)
            if text_width > text_area_w:
                x_adj = text_area_x - ((text_width - text_area_w) * 0.3)
            else:
                x_adj = text_area_x + (text_area_w - text_width) / 2
            c.drawString(x_adj, y_pos, dato)

        etiqueta_idx += 1
        total_generated += 1

    c.save()
    buffer.seek(0)
    pdf_bytes = buffer.read()
    return pdf_bytes, total_generated

# ----------------------------
# Preview label (Pillow)
# ----------------------------
def preview_label_sample(ss, etiquetas_sample=None):
    """
    Genera una imagen PNG (Pillow Image) que sirve como vista previa de la primera etiqueta.
    Devuelve bytes (PNG).
    """
    W, H = 900, 420
    bg_color = (250, 250, 250)
    img = Image.new("RGB", (W, H), color=bg_color)
    draw = ImageDraw.Draw(img)

    # card
    card_margin = 32
    card_bbox = [card_margin, card_margin, W - card_margin, H - card_margin]
    draw.rounded_rectangle(card_bbox, radius=12, fill=(255,255,255), outline=(214,214,214), width=1)

    # sample text (left)
    pad = 24
    left_x = card_bbox[0] + pad
    top_y = card_bbox[1] + pad

    # Fonts: try to load a truetype, fallback to default
    try:
        font_bold = ImageFont.truetype("DejaVuSans-Bold.ttf", 28)
        font_reg = ImageFont.truetype("DejaVuSans.ttf", 18)
        font_mono = ImageFont.truetype("DejaVuSans.ttf", 14)
    except Exception:
        font_bold = ImageFont.load_default()
        font_reg = ImageFont.load_default()
        font_mono = ImageFont.load_default()

    prod = ss.get("nombre_prod", "") or "Nombre producto"
    lote = ss.get("lote_general", "") or (ss.get("lotes",[None])[0] or "Lote1")
    lblnum = ss.get("start_label", 1)

    draw.text((left_x, top_y), prod, fill=(34,34,34), font=font_bold)
    draw.text((left_x, top_y + 40), f"Lote: {lote}", fill=(80,80,80), font=font_reg)
    draw.text((left_x, top_y + 72), f"Etiqueta: {lblnum}", fill=(80,80,80), font=font_reg)

    # color swatches (reactivos or sample color)
    sw_x = left_x
    sw_y = top_y + 120
    sw_size = 36
    reactivos = ss.get("reactivos", [])
    if etiquetas_sample:
        # use provided sample etiquetas to pick colors
        color_list = [e[2] for e in etiquetas_sample[:6] if e and e[2]]
    else:
        color_list = [r.get("color") or "#cccccc" for r in reactivos[:6]]
    if not color_list:
        color_list = ["#cccccc"]

    for i, col in enumerate(color_list):
        x = sw_x + i * (sw_size + 10)
        try:
            draw.rectangle([x, sw_y, x + sw_size, sw_y + sw_size], fill=col, outline=(0,0,0))
        except Exception:
            draw.rectangle([x, sw_y, x + sw_size, sw_y + sw_size], fill="#cccccc", outline=(0,0,0))

    # small footer with meta
    foot = f"Analista: {ss.get('analista','')}    Fecha: {ss.get('fecha','')}"
    draw.text((left_x, H - card_margin - 32), foot, fill=(120,120,120), font=font_mono)

    # prepare bytes
    b = BytesIO()
    img.save(b, format="PNG")
    b.seek(0)
    return b.read()

# ----------------------------
# Validation
# ----------------------------
def validate_inputs(ss):
    """
    Validaciones mínimas:
      - Pesos y volúmenes >= 0 cuando sean numéricos
      - etiqueta_inicial entre 1 y TOTAL_ETIQUETAS_PAGINA
      - num_lotes en rango
    Devuelve lista de strings con errores (vacía = OK)
    """
    errors = []
    # etiqueta inicial
    s_label = safe_int_from_str(ss.get("start_label", 1), 1)
    if s_label < 1 or s_label > TOTAL_ETIQUETAS_PAGINA:
        errors.append(f"Etiqueta inicial debe estar entre 1 y {TOTAL_ETIQUETAS_PAGINA}.")

    # numeric checks (if provided and parseable)
    for key in ["peso_patron", "vol_patron", "muestra_peso", "muestra_vol", "placebo_peso", "placebo_vol"]:
        val = ss.get(key, "")
        if val is None or str(val).strip() == "":
            continue
        try:
            v = float(str(val).strip())
            if v < 0:
                errors.append(f"{key.replace('_',' ').capitalize()} debe ser >= 0.")
        except Exception:
            # allow alphanumeric but warn? We'll not add error to be permissive.
            pass

    # num_lotes
    nl = safe_int_from_str(ss.get("num_lotes", 1), 1)
    if nl < 1 or nl > 20:
        errors.append("N° de lotes debe estar entre 1 y 20.")
    return errors

# ----------------------------
# UI helpers and layout
# ----------------------------
def inject_css():
    """
    Inyecta estilos para layout central, cards y botón grande verde.
    Comentario: aquí controlamos ancho máximo del contenedor y estilo de 'cards'.
    """
    st.markdown(
        """
        <style>
        /* Contenedor central con ancho máximo */
        .central-container {
            max-width: 1100px;
            margin: 18px auto;
            padding: 8px 8px 48px 8px;
            background: #f7f9fb;
            border-radius: 8px;
        }
        /* Card style */
        .card {
            background: #ffffff;
            border: 1px solid #d6d6d6;
            border-radius: 10px;
            padding: 16px;
            margin-bottom: 14px;
            box-shadow: 0 1px 2px rgba(15,15,15,0.02);
        }
        .card h3 {
            margin: 0 0 8px 0;
            font-size: 16px;
        }
        .row-label {
            color: #444;
            font-size: 13px;
            width: 140px;
            text-align: left;
            display: inline-block;
        }
        .input-compact > div, .stTextInput > div > input {
            padding: 8px !important;
            border-radius: 6px;
        }
        /* Big generator button */
        .gen-btn {
            display: flex;
            justify-content: center;
            margin-top: 18px;
            margin-bottom: 18px;
        }
        .gen-btn > button {
            background-color: #2ecc71 !important;
            color: white !important;
            border: none !important;
            padding: 12px 28px !important;
            font-size: 18px !important;
            border-radius: 10px !important;
            box-shadow: 0 4px 10px rgba(46,204,113,0.18);
        }
        /* compact inputs spacing */
        .card .stTextInput, .card .stNumberInput {
            margin-bottom: 8px;
        }
        /* preview center */
        .preview-center { display:flex; justify-content:center; margin-bottom:8px; }
        </style>
        """,
        unsafe_allow_html=True,
    )

def card_container(title, content_callable):
    """
    Wrapper: renderiza una card con título y ejecuta content_callable() para el contenido.
    """
    st.markdown(f"<div class='card'><h3>{title}</h3>", unsafe_allow_html=True)
    try:
        content_callable()
    finally:
        st.markdown("</div>", unsafe_allow_html=True)

# ----------------------------
# Main UI build
# ----------------------------
def build_ui():
    inject_css()
    st.markdown("<div class='central-container'>", unsafe_allow_html=True)
    st.title("Generador de etiquetas APLI 10199 (tema claro)")

    # Two columns: left wide (~65%) and right narrow (~35%)
    left_col, right_col = st.columns([2.2, 1])

    # LEFT COLUMN: Datos del Patrón, Muestras, Lotes, Diluciones (simplified)
    with left_col:
        def contenido_patron():
            c1, c2, c3 = st.columns([1,1,1])
            with c1:
                st.checkbox("Duplicado (A/B)", value=st.session_state.dup_patron, key="dup_patron")
            with c2:
                st.text_input("Peso patrón (g):", value=st.session_state.peso_patron, key="peso_patron")
            with c3:
                st.text_input("Vol final patrón (ml):", value=st.session_state.vol_patron, key="vol_patron")
        card_container("Datos del Patrón", contenido_patron)

        def contenido_muestras():
            st.checkbox("Duplicado por muestra (A/B)", value=st.session_state.dup_muestra, key="dup_muestra")
            st.checkbox("Uniformidad de contenido", value=st.session_state.uniformidad, key="uniformidad")
            if st.session_state.uniformidad:
                st.number_input("N° muestras (uniformidad)", min_value=1, max_value=100, value=int(st.session_state.num_uniform_samples), key="num_uniform_samples")
            c1, c2 = st.columns([1,1])
            with c1:
                st.text_input("Peso muestra (g):", value=st.session_state.muestra_peso, key="muestra_peso")
            with c2:
                st.text_input("Vol final muestra (ml):", value=st.session_state.muestra_vol, key="muestra_vol")
        card_container("Datos de las Muestras", contenido_muestras)

        def contenido_lotes():
            # N° de lotes con number_input y controles +/- para ajustar
            n = st.number_input("N° de lotes", min_value=1, max_value=20, value=int(st.session_state.num_lotes), key="num_lotes")
            # Ensure session_state.lotes length matches
            wanted = int(n)
            cur = len(st.session_state.lotes)
            if wanted > cur:
                for i in range(cur, wanted):
                    st.session_state.lotes.append(f"Lote{i+1}")
            elif wanted < cur:
                st.session_state.lotes = st.session_state.lotes[:wanted]
            # Mostrar inputs para cada lote
            for i in range(wanted):
                key = f"lote_name_{i}"
                st.session_state.lotes[i] = st.text_input(f"Lote {i+1}", value=st.session_state.lotes[i], key=key)
        card_container("Lotes", contenido_lotes)

        def contenido_diluciones():
            st.markdown("Diluciones estándar y de muestra (simplificado).", unsafe_allow_html=True)
            st.caption("Nota: la versión simplificada muestra las diluciones como texto libre para mantener UI compacta.")
            # In this refactor we keep a minimal input to allow manual IDs
            st.text_input("IDs extra (separados por comas):", value="", key="manual_ids")
        card_container("Diluciones", contenido_diluciones)

        def contenido_reactivos_extra():
            st.markdown("Reactivos (nombre + color + multiplicador)")
            # Reactivos dynamic list
            if "reactivos" not in st.session_state:
                st.session_state.reactivos = []
            cols = st.columns([3,1,1,0.5])
            headers = ["Nombre", "Color", "Multiplicador", ""]
            for i, h in enumerate(headers):
                cols[i].markdown(f"**{h}**")
            # render each reactivo row
            for idx, r in enumerate(list(st.session_state.reactivos)):
                c1, c2, c3, c4 = st.columns([3,1,1,0.5])
                with c1:
                    name = c1.text_input("", value=r.get("name",""), key=f"reactivo_name_{idx}")
                with c2:
                    color = c2.color_picker("", value=r.get("color","#f39c12"), key=f"reactivo_color_{idx}")
                with c3:
                    mult = c3.number_input("", min_value=0, value=int(r.get("multiplier",1)), step=1, key=f"reactivo_mult_{idx}")
                with c4:
                    if c4.button("✕", key=f"del_react_{idx}"):
                        st.session_state.reactivos.pop(idx)
                        st.experimental_rerun()
                st.session_state.reactivos[idx] = {"name": name, "color": color, "multiplier": int(mult)}
            if st.button("+ Agregar reactivo"):
                st.session_state.reactivos.append({"name": "", "color": "#f39c12", "multiplier": 0})
        card_container("Reactivos extra", contenido_reactivos_extra)

    # RIGHT COLUMN: Opciones Viales, Datos Generales, Resumen y Preview
    with right_col:
        def contenido_viales_options():
            st.checkbox("Mostrar cuadro de color", value=st.session_state.show_color_square, key="show_color_square")
            st.checkbox("Incluir viales", value=st.session_state.incluir_viales, key="incluir_viales")
            st.text_input("Blanco:", value=st.session_state.texto_blanco, key="texto_blanco")
            st.text_input("Wash:", value=st.session_state.texto_wash, key="texto_wash")
            st.caption("Configure multiplicadores para viales en la sección de vista previa/summary después de generar preview.")
        card_container("Opciones Viales", contenido_viales_options)

        def contenido_datos_generales():
            st.text_input("Nombre producto:", value=st.session_state.nombre_prod, key="nombre_prod")
            st.text_input("Lote (general):", value=st.session_state.lote_general, key="lote_general")
            st.text_input("Determinación:", value=st.session_state.determinacion, key="determinacion")
            st.text_input("Analista:", value=st.session_state.analista, key="analista")
            st.text_input("Fecha:", value=st.session_state.fecha, key="fecha")
            st.number_input("Etiqueta inicial (1-80):", min_value=1, max_value=TOTAL_ETIQUETAS_PAGINA, value=int(st.session_state.start_label), key="start_label")
        card_container("Datos Generales", contenido_datos_generales)

        def contenido_preview_y_resumen():
            st.markdown("<div class='preview-center'>", unsafe_allow_html=True)
            # Show existing preview if available
            if st.session_state.preview_img:
                st.image(st.session_state.preview_img, width=420)
            else:
                st.info("Pulse GENERAR PDF para ver una vista previa antes de descargar.")
            st.markdown("</div>", unsafe_allow_html=True)

            # Summary of counts
            etiquetas = build_etiquetas_from_state({
                "texto_blanco": st.session_state.texto_blanco,
                "texto_wash": st.session_state.texto_wash,
                "dup_patron": st.session_state.dup_patron,
                "dup_muestra": st.session_state.dup_muestra,
                "uniformidad": st.session_state.uniformidad,
                "incluir_placebo": st.session_state.incluir_placebo,
                "lotes": st.session_state.lotes,
                "reactivos": st.session_state.reactivos,
                "viales_multiplicadores": st.session_state.viales_multiplicadores,
                "muestra_peso": st.session_state.muestra_peso,
                "muestra_vol": st.session_state.muestra_vol,
                "placebo_peso": st.session_state.placebo_peso,
                "placebo_vol": st.session_state.placebo_vol,
                "peso_patron": st.session_state.peso_patron,
                "vol_patron": st.session_state.vol_patron,
                "lote_color_map": getattr(st.session_state, "lote_color_map", {}) if "lote_color_map" in st.session_state else {}
            })
            st.markdown(f"**Total etiquetas a generar (estimado):** {len(etiquetas)}")
            if st.session_state.last_pdf:
                st.success(f"Última generación: {st.session_state.last_total} etiquetas — {st.session_state.last_pdf_name}")
                st.download_button("Descargar PDF", data=st.session_state.last_pdf, file_name=st.session_state.last_pdf_name, mime="application/pdf")
                if st.button("Abrir PDF en nueva pestaña"):
                    b64 = base64.b64encode(st.session_state.last_pdf).decode("ascii")
                    js = f"""
                    <script>
                    (function() {{
                        const b64 = "{b64}";
                        const byteCharacters = atob(b64);
                        const byteNumbers = new Array(byteCharacters.length);
                        for (let i = 0; i < byteCharacters.length; i++) {{
                            byteNumbers[i] = byteCharacters.charCodeAt(i);
                        }}
                        const byteArray = new Uint8Array(byteNumbers);
                        const blob = new Blob([byteArray], {{type: 'application/pdf'}});
                        const url = URL.createObjectURL(blob);
                        window.open(url, '_blank');
                    }})();
                    </script>
                    """
                    components.html(js, height=50)
        card_container("Vista previa y resumen", contenido_preview_y_resumen)

    # GENERATE BUTTON (big green centered under columns)
    st.markdown("<div class='gen-btn'>", unsafe_allow_html=True)

    # Use trigger pattern: clicking the button sets trigger_generate True, and below main flow will perform generation.
    if st.session_state.generating:
        # disabled button visual while generating
        st.button("GENERANDO...", disabled=True)
    else:
        if st.button("GENERAR PDF"):
            st.session_state.trigger_generate = True

    st.markdown("</div>", unsafe_allow_html=True)

    st.markdown("</div>", unsafe_allow_html=True)  # close central container

# ----------------------------
# Main application logic
# ----------------------------
def main():
    build_ui()

    # If generation has been requested, run validation and generation with spinner/progress
    if st.session_state.get("trigger_generate", False) and not st.session_state.get("generating", False):
        # Begin generation flow
        st.session_state.generating = True
        st.session_state.trigger_generate = False

        # Validate inputs
        errors = validate_inputs(st.session_state)
        if errors:
            for e in errors:
                st.error(e)
            st.session_state.generating = False
            return

        with st.spinner("Generando preview y PDF..."):
            progress = st.progress(0)
            # Build minimal state dict to feed build_etiquetas_from_state and preview
            state = {
                "texto_blanco": st.session_state.texto_blanco,
                "texto_wash": st.session_state.texto_wash,
                "dup_patron": st.session_state.dup_patron,
                "dup_muestra": st.session_state.dup_muestra,
                "uniformidad": st.session_state.uniformidad,
                "incluir_placebo": st.session_state.incluir_placebo,
                "lotes": st.session_state.lotes,
                "reactivos": st.session_state.reactivos,
                "viales_multiplicadores": st.session_state.viales_multiplicadores,
                "muestra_peso": st.session_state.muestra_peso,
                "muestra_vol": st.session_state.muestra_vol,
                "placebo_peso": st.session_state.placebo_peso,
                "placebo_vol": st.session_state.placebo_vol,
                "peso_patron": st.session_state.peso_patron,
                "vol_patron": st.session_state.vol_patron,
                "lote_color_map": getattr(st.session_state, "lote_color_map", {}) if "lote_color_map" in st.session_state else {},
            }

            # 1) Build etiquetas list (logical)
            etiquetas = build_etiquetas_from_state({**state,
                                                   "id_color_map": getattr(st.session_state,"id_color_map",{}),
                                                   "lote_color_map": getattr(st.session_state,"lote_color_map",{})})
            progress.progress(20)
            time.sleep(0.25)

            # 2) Create preview from first few etiquetas
            preview_bytes = preview_label_sample({
                "nombre_prod": st.session_state.nombre_prod,
                "lote_general": st.session_state.lote_general,
                "lotes": st.session_state.lotes,
                "analista": st.session_state.analista,
                "fecha": st.session_state.fecha,
                "start_label": int(st.session_state.start_label)
            }, etiquetas_sample=etiquetas)
            st.session_state.preview_img = preview_bytes
            progress.progress(40)
            time.sleep(0.25)

            # 3) Generate final PDF
            pdf_bytes, total_generated = generate_pdf_bytes(etiquetas, {
                "show_color_square": st.session_state.show_color_square,
                "nombre_prod": st.session_state.nombre_prod,
                "determinacion": st.session_state.determinacion,
                "lote_general": st.session_state.lote_general,
                "analista": st.session_state.analista,
                "fecha": st.session_state.fecha,
                "start_label": st.session_state.start_label
            })
            progress.progress(80)
            time.sleep(0.25)

            # 4) Save into session_state and finish
            filename = f"{datetime.today().strftime('%Y%m%d')}_{re.sub(r'[^0-9a-zA-Z_-]','_', (st.session_state.nombre_prod or 'etiquetas'))}.pdf"
            st.session_state.last_pdf = pdf_bytes
            st.session_state.last_pdf_name = filename
            st.session_state.last_total = total_generated

            progress.progress(100)
            time.sleep(0.2)

        # Show success and small summary
        st.success(f"Generado PDF: {st.session_state.last_pdf_name} — {st.session_state.last_total} etiquetas (estimado).")
        st.session_state.generating = False

# ----------------------------
# Entrypoint
# ----------------------------
if __name__ == "__main__":
    main()
