#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Streamlit port corregido y pulido (alineado con etiquetador_Version21_Version4.py)

Cambios principales (2025-08-16):
- Añadida función build_etiquetas_from_state(state) (stateless) para poder probar y comparar
  la lista de etiquetas sin generar el PDF.
- Reemplazado el comportamiento de apertura automática por un botón "Abrir en nueva pestaña".
- Reducción visual del 30% de la UI mediante CSS (escalado).
- Lotes y campos dinámicos usan UID estables para no perder foco.
- Las etiquetas "Lote X ID" pasan a mostrar el nombre actual del lote (actualización en tiempo real).
- Sincronización de viales_multiplicadores para evitar duplicados y repetición de Blanco/Wash.
- El archivo contiene además helpers para pruebas y depuración.
"""
from io import BytesIO
from datetime import datetime
import re
import base64
import random
import uuid
import json
import streamlit as st
import streamlit.components.v1 as components
import html

from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors

# ---------- Constantes ----------
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

# ---------- Paleta ----------
def build_sample_palette():
    try:
        import matplotlib.cm as cm
        import matplotlib.colors as mcolors
        cmap = cm.get_cmap("tab20")
        palette = [mcolors.to_hex(cmap(i % cmap.N)) for i in range(20)]
    except Exception:
        palette = [
            "#2ca02c", "#9467bd", "#8c564b", "#e377c2", "#17becf", "#7f7f7f",
            "#bcbd22", "#98df8a", "#c5b0d5", "#6b6bd3", "#00a5a5", "#b59ddb",
            "#9edae5", "#c49c94", "#dbdb8d"
        ]
    palette = [p.lower() for p in palette]
    filtered = [c for c in palette if c not in FORBIDDEN_COLORS and c not in {STD_A_COLOR.lower(), STD_B_COLOR.lower(), BLANCO_COLOR.lower(), REACTIVO_COLOR.lower()}]
    extras = ["#2f4f4f", "#6a5acd", "#20b2aa", "#00ced1", "#4b0082", "#556b2f", "#4682b4", "#8b4513"]
    for e in extras:
        if e not in filtered:
            filtered.append(e)
        if len(filtered) >= 12:
            break
    return filtered[:12]

SAMPLE_PALETTE = build_sample_palette()

# ---------- Utilidades ----------
def limpiar_nombre_archivo(nombre: str) -> str:
    nombre = (nombre or "").strip()
    nombre = re.sub(r"[\\/*?\"<>|:]", "", nombre)
    nombre = re.sub(r"\s+", "_", nombre)
    return nombre or "etiquetas"

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

def new_uid(prefix="u"):
    return f"{prefix}_{uuid.uuid4().hex[:8]}"

def sanitize_key(s: str) -> str:
    return re.sub(r'[^0-9a-zA-Z_]', '_', s)

# ---------- Session init ----------
def init_session_state():
    ss = st.session_state
    ss.setdefault("show_color_square", True)
    ss.setdefault("dup_patron", False)
    ss.setdefault("dup_muestra", True)
    ss.setdefault("uniformidad", False)
    ss.setdefault("incluir_placebo", False)
    ss.setdefault("incluir_viales", True)

    ss.setdefault("num_uniform_samples", "2")
    ss.setdefault("num_lotes", "1")
    ss.setdefault("num_reactivos", "0")

    for k, v in [
        ("texto_blanco", ""),
        ("texto_wash", ""),
        ("peso_patron", ""),
        ("vol_patron", ""),
        ("muestra_peso", ""),
        ("muestra_vol", ""),
        ("placebo_peso", ""),
        ("placebo_vol", ""),
        ("nombre_prod", ""),
        ("lote", ""),
        ("determinacion", ""),
        ("analista", "YAK"),
        ("fecha", datetime.today().strftime("%d/%m/%Y")),
    ]:
        ss.setdefault(k, v)

    ss.setdefault("start_label", 1)

    # Ensure lotes is list of dicts with uid,name
    if "lotes" not in ss or not isinstance(ss.lotes, list) or (ss.lotes and isinstance(ss.lotes[0], str)):
        old = ss.get("lotes", [""])
        new = []
        for i, name in enumerate(old):
            new.append({"uid": new_uid("l"), "name": name or ""})
        if not new:
            new = [{"uid": new_uid("l"), "name": ""}]
        ss["lotes"] = new

    ss.setdefault("lote_color_map", {0: allocate_lote_color(0)})

    # Normalize diluciones_* as list of dicts with uid
    def normalize_list_of_dils(key, short):
        if key not in ss or not isinstance(ss[key], list):
            ss[key] = []
            return
        ns = []
        for d in ss[key]:
            if isinstance(d, dict) and "uid" in d:
                ns.append(d)
            elif isinstance(d, dict):
                if key == "diluciones_muestra":
                    per = d.get("per_lote_ids", []) if isinstance(d.get("per_lote_ids", []), list) else []
                    ns.append({"uid": new_uid(short), "v_pip": d.get("v_pip",""), "v_final": d.get("v_final",""), "per_lote_ids": per[:]})
                else:
                    ns.append({"uid": new_uid(short), "v_pip": d.get("v_pip",""), "v_final": d.get("v_final",""), "id_text": d.get("id_text","")})
        ss[key] = ns

    normalize_list_of_dils("diluciones_std", "ds")
    normalize_list_of_dils("diluciones_muestra", "dm")
    normalize_list_of_dils("diluciones_placebo", "dp")

    ss.setdefault("reactivos", ss.get("reactivos", []))
    ss.setdefault("id_color_map", ss.get("id_color_map", {}))
    ss.setdefault("viales_multiplicadores", ss.get("viales_multiplicadores", {}))
    ss.setdefault("last_pdf", None)
    ss.setdefault("last_total", 0)

init_session_state()
ss = st.session_state

# ---------- construir ids y asignar colores (función utilizable en tests) ----------
def construir_ids_viales_from_state(state):
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

    manual_ids = []
    for d in state.get("diluciones_std", []):
        idv = (d.get("id_text") or "").strip()
        if idv and idv not in manual_ids:
            manual_ids.append(idv)
    for idv in manual_ids:
        if state.get("dup_patron"):
            items.append({"id": f"{idv}/A", "type": "std", "lot_index": None})
            items.append({"id": f"{idv}/B", "type": "std", "lot_index": None})
        else:
            items.append({"id": idv, "type": "std", "lot_index": None})

    lotes = state.get("lotes", [])
    for i, lote in enumerate(lotes):
        lote_name = (lote.get("name","") or "").strip() or f"Lote{i+1}"
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
        val = (r or "").strip()
        if val:
            items.append({"id": val, "type": "reactivo", "lot_index": None})

    # remove duplicates preserving order
    seen = set()
    final = []
    for it in items:
        if it["id"] not in seen:
            final.append(it)
            seen.add(it["id"])
    return final

def assign_colors_for_ids_for_state(items, state):
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

# ---------- Core: construir la lista de etiquetas (stateless helper para tests) ----------
def build_etiquetas_from_state(state_in):
    """
    Recibe un dict con la estructura esperada (similar a st.session_state) y devuelve
    la lista de tuplas (tipo, id_text, color_hex) que luego se van a dibujar en el PDF.
    Esto permite compararlo con la versión desktop en tests.
    """
    # Work on a copy to avoid mutating incoming dict
    state = json.loads(json.dumps(state_in))
    etiquetas = []

    # Standards header
    if state.get("dup_patron"):
        etiquetas.append(("STD_A", f"STD A {_format_with_unit(state.get('peso_patron'), 'g')}/{_format_with_unit(state.get('vol_patron'), 'ml')}", STD_A_COLOR))
        etiquetas.append(("STD_B", f"STD B {_format_with_unit(state.get('peso_patron'), 'g')}/{_format_with_unit(state.get('vol_patron'), 'ml')}", STD_B_COLOR))
    else:
        etiquetas.append(("STD_A", f"STD {_format_with_unit(state.get('peso_patron'), 'g')}/{_format_with_unit(state.get('vol_patron'), 'ml')}", STD_A_COLOR))

    # std chains (non-manual)
    std_chains = []
    for d in state.get("diluciones_std", []):
        v1 = (d.get("v_pip") or "").strip()
        v2 = (d.get("v_final") or "").strip()
        id_override = (d.get("id_text") or "").strip()
        if id_override:
            continue
        if not v1 or not v2:
            continue
        prev = std_chains[-1] if std_chains else ""
        chain = (prev + "→" if prev else "") + f"{v1}:{v2}"
        std_chains.append(chain)

    manual_ids = [ (d.get("id_text") or "").strip() for d in state.get("diluciones_std", []) if (d.get("id_text") or "").strip() ]
    if manual_ids:
        for idv in manual_ids:
            if state.get("dup_patron"):
                etiquetas.append(("STD_A", f"{idv}/A", STD_A_COLOR))
                etiquetas.append(("STD_B", f"{idv}/B", STD_B_COLOR))
            else:
                etiquetas.append(("STD_A", f"{idv}", STD_A_COLOR))

    if any(not (d.get("id_text") or "").strip() for d in state.get("diluciones_std", [])):
        for chain in std_chains:
            if state.get("dup_patron"):
                etiquetas.append(("STD_A", f"STD {chain}/A", STD_A_COLOR))
                etiquetas.append(("STD_B", f"STD {chain}/B", STD_B_COLOR))
            else:
                etiquetas.append(("STD_A", f"STD {chain}", STD_A_COLOR))

    # ensure lote colors
    lote_count = len(state.get("lotes", []))
    lote_map = state.get("lote_color_map", {})
    for i in range(lote_count):
        if i not in lote_map or (lote_map.get(i) or "").lower() in FORBIDDEN_COLORS:
            lote_map[i] = allocate_lote_color(i)
    state["lote_color_map"] = lote_map

    # Base sample labels per lote
    for li, lote in enumerate(state.get("lotes", [])):
        name = (lote.get("name","") or "").strip() or f"Lote{li+1}"
        peso_label = _format_with_unit(state.get("muestra_peso"), "g")
        vol_label = _format_with_unit(state.get("muestra_vol"), "ml")
        suffix_parts = []
        if peso_label:
            suffix_parts.append(peso_label)
        if vol_label:
            suffix_parts.append(vol_label)
        suffix = ("/".join(suffix_parts)) if suffix_parts else ""
        color = state["lote_color_map"].get(li) or allocate_lote_color(li)
        state["lote_color_map"][li] = color
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

    # Sample dilutions accumulative
    if state.get("diluciones_muestra"):
        num_dils = len(state.get("diluciones_muestra"))
        for li, lote in enumerate(state.get("lotes", [])):
            name = (lote.get("name","") or "").strip() or f"Lote{li+1}"
            color = state["lote_color_map"].get(li) or allocate_lote_color(li)
            accumulated = []
            for m in range(1, num_dils + 1):
                d = state["diluciones_muestra"][m-1]
                per_ids = d.get("per_lote_ids") or []
                custom = (per_ids[li] or "") if li < len(per_ids) else ""
                custom = (custom or "").strip()
                v1 = (d.get("v_pip") or "").strip()
                v2 = (d.get("v_final") or "").strip()
                if custom:
                    accumulated.append(custom)
                elif v1 and v2:
                    accumulated.append(f"{v1}:{v2}")
                else:
                    accumulated.append(None)
                if any(x is None for x in accumulated):
                    continue
                chain = "-->".join(accumulated)
                if state.get("uniformidad"):
                    n = safe_int_from_str(state.get("num_uniform_samples"), 1)
                    n = max(1, min(n, 100))
                    for k in range(1, n+1):
                        etiquetas.append(("MUESTRA", f"{name}/{k} {chain}", color))
                else:
                    if state.get("dup_muestra"):
                        etiquetas.append(("MUESTRA", f"{name}/A {chain}", color))
                        etiquetas.append(("MUESTRA", f"{name}/B {chain}", color))
                    else:
                        etiquetas.append(("MUESTRA", f"{name} {chain}", color))

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
        if state.get("diluciones_placebo"):
            for d in state.get("diluciones_placebo"):
                v1 = (d.get("v_pip") or "").strip()
                v2 = (d.get("v_final") or "").strip()
                id_override = (d.get("id_text") or "").strip()
                if id_override:
                    etiquetas.append(("PLACEBO", f"Placebo {id_override}", PLACEBO_COLOR))
                elif v1 or v2:
                    etiquetas.append(("PLACEBO", f"Placebo {v1}:{v2}", PLACEBO_COLOR))

    # Reactivos
    for r in state.get("reactivos", []):
        if (r or "").strip():
            etiquetas.append(("REACTIVO", r.strip(), REACTIVO_COLOR))

    # Viales multiplicadores: only include if checkbox set.
    if state.get("incluir_viales"):
        items = construir_ids_viales_from_state(state)
        assign_colors_for_ids_for_state(items, state)
        # synchronize multipliers: default reactivo->0 else->1, keep only current ids
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
        state["viales_multiplicadores"] = new_mult
        for vid, mult in state["viales_multiplicadores"].items():
            try:
                m = int(mult)
            except Exception:
                m = 0
            for _ in range(max(0, m)):
                etiquetas.append(("VIAL", vid, state["id_color_map"].get(vid, "#cccccc")))

    return etiquetas

# ---------- remaining PDF helpers ----------
def calcular_tamano_fuente_optimizado(avail_w, avail_h, id_text, datos_text, square_size):
    margin_w = avail_w * 0.05
    margin_h = avail_h * 0.05
    text_width_available = avail_w - (2 * margin_w) - (square_size * 0.6)
    text_height_available = avail_h - (2 * margin_h)
    max_id_size = min(text_height_available * 0.32, 18)
    max_data_size = max_id_size * 0.78
    char_width_factor = 0.58
    line_height_factor = 1.15
    while max_id_size > 7:
        id_width = len(id_text) * max_id_size * char_width_factor
        id_height = max_id_size * line_height_factor
        max_data_line_length = max((len(d) for d in datos_text), default=0)
        data_width = max_data_line_length * max_data_size * char_width_factor
        data_total_height = len(datos_text) * max_data_size * line_height_factor
        total_text_height = id_height + data_total_height + (max_id_size * 0.25)
        if id_width <= text_width_available and data_width <= text_width_available and total_text_height <= text_height_available:
            break
        max_id_size -= 0.5
        max_data_size = max_id_size * 0.78
    return max(max_id_size, 7), max(max_data_size, 6)

def dibujar_texto_centrado(c, text, x, y, width, font_name, font_size):
    text_width = c.stringWidth(text, font_name, font_size)
    if text_width > width:
        overflow = text_width - width
        x_adjusted = x - (overflow * 0.3)
    else:
        x_adjusted = x + (width - text_width) / 2
    c.drawString(x_adjusted, y, text)
    return x_adjusted

# ---------- Generar PDF (usa build_etiquetas_from_state) ----------
def generar_pdf_bytes_and_next_start():
    ss_local = st.session_state
    # Convert session state into plain dict for build_etiquetas_from_state
    state = {
        "show_color_square": ss_local.show_color_square,
        "dup_patron": ss_local.dup_patron,
        "dup_muestra": ss_local.dup_muestra,
        "uniformidad": ss_local.uniformidad,
        "incluir_placebo": ss_local.incluir_placebo,
        "incluir_viales": ss_local.incluir_viales,
        "num_uniform_samples": ss_local.num_uniform_samples,
        "num_lotes": ss_local.num_lotes,
        "num_reactivos": ss_local.num_reactivos,
        "texto_blanco": ss_local.texto_blanco,
        "texto_wash": ss_local.texto_wash,
        "peso_patron": ss_local.peso_patron,
        "vol_patron": ss_local.vol_patron,
        "muestra_peso": ss_local.muestra_peso,
        "muestra_vol": ss_local.muestra_vol,
        "placebo_peso": ss_local.placebo_peso,
        "placebo_vol": ss_local.placebo_vol,
        "nombre_prod": ss_local.nombre_prod,
        "lote": ss_local.lote,
        "determinacion": ss_local.determinacion,
        "analista": ss_local.analista,
        "fecha": ss_local.fecha,
        "start_label": ss_local.start_label,
        "lotes": ss_local.lotes,
        "reactivos": ss_local.reactivos,
        "diluciones_std": ss_local.diluciones_std,
        "diluciones_muestra": ss_local.diluciones_muestra,
        "diluciones_placebo": ss_local.diluciones_placebo,
        "id_color_map": ss_local.id_color_map,
        "lote_color_map": ss_local.lote_color_map,
        "viales_multiplicadores": ss_local.viales_multiplicadores,
    }

    etiquetas = build_etiquetas_from_state(state)

    # draw pdf
    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    margin_x_int = ETIQ_WIDTH * 0.05
    margin_y_int = ETIQ_HEIGHT * 0.05
    square_size = 0.45 * CM_TO_PT

    etiqueta_idx = safe_int_from_str(ss_local.start_label, 1) - 1
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

        c.setStrokeColor(colors.grey)
        c.setLineWidth(0.6)
        c.rect(base_x, base_y, ETIQ_WIDTH, ETIQ_HEIGHT)

        inner_x = base_x + margin_x_int
        inner_y = base_y + margin_y_int
        inner_w = ETIQ_WIDTH - 2 * margin_x_int
        inner_h = ETIQ_HEIGHT - 2 * margin_y_int

        if ss_local.show_color_square:
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
            f"Producto: {ss_local.nombre_prod}",
            f"Determinación: {ss_local.determinacion}",
            f"Lote: {ss_local.lote}",
            f"Analista: {ss_local.analista}    Fecha: {ss_local.fecha}"
        ]

        size_id, size_data = calcular_tamano_fuente_optimizado(inner_w, inner_h, id_text, datos, square_size)
        margin_text_w = inner_w * 0.05
        margin_text_h = inner_h * 0.05
        text_area_x = inner_x + margin_text_w
        text_area_y = inner_y + margin_text_h
        text_area_w = inner_w - (2 * margin_text_w) - (square_size * 0.6)
        text_area_h = inner_h - (2 * margin_text_h)

        c.setFont("Helvetica-Bold", size_id)
        c.setFillColor(colors.black)
        text_id_y = text_area_y + text_area_h - size_id
        dibujar_texto_centrado(c, id_text, text_area_x, text_id_y, text_area_w, "Helvetica-Bold", size_id)

        c.setFont("Helvetica", size_data)
        line_height = size_data * 1.15
        data_start_y = text_id_y - line_height
        for i, dato in enumerate(datos):
            y_pos = data_start_y - i * line_height
            if y_pos < text_area_y:
                break
            c.setFillColor(colors.black)
            dibujar_texto_centrado(c, dato, text_area_x, y_pos, text_area_w, "Helvetica", size_data)

        etiqueta_idx += 1
        total_generated += 1

    c.save()
    buffer.seek(0)
    pdf_bytes = buffer.read()

    # compute next_start
    if total_generated == 0:
        next_start = safe_int_from_str(ss_local.start_label, 1)
    else:
        last_pos_on_page = ((safe_int_from_str(ss_local.start_label, 1) - 1) + total_generated - 1) % TOTAL_ETIQUETAS_PAGINA + 1
        next_pos = last_pos_on_page + 1
        if next_pos > TOTAL_ETIQUETAS_PAGINA:
            next_pos = 1
        next_start = next_pos

    return pdf_bytes, total_generated, int(next_start)

# ---------- UI: scale down UI ~30% ----------
# We inject CSS to scale down the whole app to ~70% (reducción 30%)
SCALE = 0.70
st.markdown(f"""
    <style>
      /* Reduce UI scale to fit more on one page */
      :root > .stApp {{
        transform: scale({SCALE});
        transform-origin: 0 0;
        width: calc(100% / {SCALE});
      }}
      /* Slightly smaller headings */
      .css-1d391kg h1, .css-1d391kg h2 {{
        font-size: 0.9em;
      }}
    </style>
""", unsafe_allow_html=True)

# ---------- Streamlit UI ----------
st.set_page_config(layout="wide", page_title="Generador de etiquetas APLI 10199")
st.title("Generador de etiquetas APLI 10199")

left_col, right_col = st.columns([2, 1])

with left_col:
    st.subheader("Patrón")
    c0, c1, c2, c3 = st.columns([0.8, 1.1, 1.1, 0.6])
    with c0:
        st.checkbox("Duplicado (A/B)", value=ss.dup_patron, key="dup_patron")
    with c1:
        st.markdown("**Peso (patrón):**")
        st.text_input("", value=ss.peso_patron, key="peso_patron", max_chars=12)
    with c2:
        st.markdown("**Vol final (patrón):**")
        st.text_input("", value=ss.vol_patron, key="vol_patron", max_chars=12)
    with c3:
        st.markdown("")

    st.markdown("**Diluciones estándar**")
    std_snapshot = list(ss.diluciones_std)
    new_std = []
    for i, d in enumerate(std_snapshot):
        uid = d.get("uid") or new_uid("ds")
        c1s, c2s, c3s, c4s = st.columns([0.9, 0.9, 2, 0.3])
        with c1s:
            st.markdown(f"D{i+1} V<sub>pip</sub>", unsafe_allow_html=True)
            v1 = st.text_input("", value=d.get("v_pip",""), key=f"std_vpip_{uid}")
        with c2s:
            st.markdown(f"D{i+1} V<sub>final</sub>", unsafe_allow_html=True)
            v2 = st.text_input("", value=d.get("v_final",""), key=f"std_vfinal_{uid}")
        with c3s:
            st.markdown("ID (opcional)")
            idt = st.text_input("", value=d.get("id_text",""), key=f"std_id_{uid}")
        with c4s:
            if st.button("✕", key=f"del_std_{uid}"):
                continue
        new_std.append({"uid": uid, "v_pip": v1, "v_final": v2, "id_text": idt})
    ss.diluciones_std = new_std

    if st.button("+ Agregar dilución estándar"):
        ss.diluciones_std.append({"uid": new_uid("ds"), "v_pip": "", "v_final": "", "id_text": ""})

    st.markdown("---")
    st.subheader("Muestras")
    st.checkbox("Duplicado por muestra (A/B)", value=ss.dup_muestra, key="dup_muestra")
    st.checkbox("Uniformidad de contenido", value=ss.uniformidad, key="uniformidad")
    if ss.uniformidad:
        st.text_input("N° muestras (uniformidad)", value=ss.num_uniform_samples, key="num_uniform_samples", max_chars=4)

    cw1, cw2 = st.columns([1,1])
    with cw1:
        st.markdown("**Peso (patrón/muestra):**")
        st.text_input("", value=ss.muestra_peso, key="muestra_peso", max_chars=12)
    with cw2:
        st.markdown("**Vol final (patrón/muestra):**")
        st.text_input("", value=ss.muestra_vol, key="muestra_vol", max_chars=12)

    st.markdown("**Lotes**")
    st.text_input("N° de lotes", value=ss.num_lotes, key="num_lotes", max_chars=3)
    if st.button("Aplicar lotes"):
        try:
            n = int(ss.num_lotes)
            n = max(0, min(40, n))
        except Exception:
            n = 1
        current = len(ss.lotes)
        if n > current:
            for i in range(current, n):
                ss.lotes.append({"uid": new_uid("l"), "name": ""})
                ss.lote_color_map[i] = allocate_lote_color(i)
        elif n < current:
            ss.lotes = ss.lotes[:n]
            for k in list(ss.lote_color_map.keys()):
                if k >= n:
                    ss.lote_color_map.pop(k, None)

    # Lote name inputs (stable keys using uid). Update lote (general) automatically.
    for i, lote in enumerate(ss.lotes):
        uid = lote.get("uid") or new_uid("l")
        colc, cold = st.columns([0.08, 1])
        with colc:
            color = ss.lote_color_map.get(i, allocate_lote_color(i))
            st.markdown(f"<div style='width:18px;height:12px;background:{color};border:1px solid #000'></div>", unsafe_allow_html=True)
        with cold:
            name = st.text_input(f"Lote {i+1}", value=lote.get("name",""), key=f"lote_name_{uid}")
            ss.lotes[i]["name"] = name

    # Update general lote (join non-empty names) in real time
    combined = ", ".join([lv.get("name","").strip() for lv in ss.lotes if lv.get("name","") and lv.get("name","").strip()])
    ss.lote = combined

    st.markdown("**Diluciones de muestra (acumulativas)**")
    dm_snapshot = list(ss.diluciones_muestra)
    new_dm = []
    for idx, d in enumerate(dm_snapshot):
        uid = d.get("uid") or new_uid("dm")
        st.markdown(f"**D{idx+1}:**")
        c1m, c2m = st.columns([1,1])
        with c1m:
            st.markdown("V<sub>pip</sub>", unsafe_allow_html=True)
            v1 = st.text_input("", value=d.get("v_pip",""), key=f"dm_vpip_{uid}")
        with c2m:
            st.markdown("V<sub>final</sub>", unsafe_allow_html=True)
            v2 = st.text_input("", value=d.get("v_final",""), key=f"dm_vfinal_{uid}")

        st.text("IDs por lote (vacío = usar ID por defecto):")
        per = list(d.get("per_lote_ids", []))
        # ensure length matches lotes
        if len(per) < len(ss.lotes):
            per += [""] * (len(ss.lotes) - len(per))
        for li in range(len(ss.lotes)):
            lote_name = (ss.lotes[li].get("name","") or "").strip() or f"Lote{li+1}"
            # label reflects lote name in real time
            key = f"dm_{uid}_per_{li}"
            val = st.text_input(f"{lote_name} ID", value=per[li], key=key)
            per[li] = val

        if st.button("✕ Eliminar dilución muestra", key=f"del_dm_{uid}"):
            continue
        new_dm.append({"uid": uid, "v_pip": v1, "v_final": v2, "per_lote_ids": per})
    ss.diluciones_muestra = new_dm

    if st.button("+ Agregar dilución de muestra"):
        per = ["" for _ in range(len(ss.lotes))]
        ss.diluciones_muestra.append({"uid": new_uid("dm"), "v_pip":"", "v_final":"", "per_lote_ids": per})

with right_col:
    st.subheader("Opciones viales y generales (compacto)")
    st.checkbox("Mostrar cuadro de color", value=ss.show_color_square, key="show_color_square")
    st.checkbox("Incluir viales", value=ss.incluir_viales, key="incluir_viales")
    st.text_input("Blanco:", value=ss.texto_blanco, key="texto_blanco")
    st.text_input("Wash:", value=ss.texto_wash, key="texto_wash")

    st.markdown("**Lista de viales HPLC (multiplicadores)**")
    items = construir_ids_viales_from_state({
        "texto_blanco": ss.texto_blanco,
        "texto_wash": ss.texto_wash,
        "dup_patron": ss.dup_patron,
        "dup_muestra": ss.dup_muestra,
        "uniformidad": ss.uniformidad,
        "lotes": ss.lotes,
        "reactivos": ss.reactivos,
        "diluciones_std": ss.diluciones_std
    })
    assign_colors_for_ids_for_state(items, {"id_color_map": ss.id_color_map, "lote_color_map": ss.lote_color_map, "lotes": ss.lotes})
    # ensure viales_multiplicadores defaults and prune obsolete keys
    for it in items:
        vid = it["id"]
        if vid not in ss.viales_multiplicadores:
            ss.viales_multiplicadores[vid] = 0 if it["type"] == "reactivo" else 1
    for k in list(ss.viales_multiplicadores.keys()):
        if k not in [it["id"] for it in items]:
            ss.viales_multiplicadores.pop(k, None)

    for it in items:
        vid = it["id"]
        color = ss.id_color_map.get(vid, "#cccccc")
        c1, c2 = st.columns([0.12, 1])
        with c1:
            if ss.show_color_square:
                st.markdown(f"<div style='width:14px;height:12px;background:{color};border:1px solid #000'></div>", unsafe_allow_html=True)
        with c2:
            subc1, subc2 = st.columns([3,1])
            with subc1:
                st.text(vid)
            with subc2:
                default = int(ss.viales_multiplicadores.get(vid, 0 if it["type"] == "reactivo" else 1))
                key = sanitize_key(f"mult_{vid}")
                val = st.number_input("", min_value=0, value=default, step=1, key=key)
                ss.viales_multiplicadores[vid] = int(val)

    st.markdown("---")
    st.subheader("Placebo y Reactivos")
    st.checkbox("Incluir placebo", value=ss.incluir_placebo, key="incluir_placebo")
    if ss.incluir_placebo:
        st.text_input("Placebo peso:", value=ss.placebo_peso, key="placebo_peso")
        st.text_input("Placebo vol:", value=ss.placebo_vol, key="placebo_vol")
        pp_snapshot = list(ss.diluciones_placebo)
        new_pp = []
        for i, d in enumerate(pp_snapshot):
            uid = d.get("uid") or new_uid("dp")
            v1 = st.text_input(f"P{i+1} v_pip", value=d.get("v_pip",""), key=f"pp_vpip_{uid}")
            v2 = st.text_input(f"P{i+1} v_final", value=d.get("v_final",""), key=f"pp_vfinal_{uid}")
            idt = st.text_input(f"P{i+1} ID (opcional)", value=d.get("id_text",""), key=f"pp_id_{uid}")
            if st.button("✕ Eliminar dilución placebo", key=f"del_pp_{uid}"):
                continue
            new_pp.append({"uid": uid, "v_pip": v1, "v_final": v2, "id_text": idt})
        ss.diluciones_placebo = new_pp
        if st.button("+ Agregar dilución placebo"):
            ss.diluciones_placebo.append({"uid": new_uid("dp"), "v_pip":"", "v_final":"", "id_text":""})

    st.markdown("**Reactivos (configurar nº y nombres)**")
    st.text_input("N° de reactivos", value=ss.num_reactivos, key="num_reactivos")
    try:
        nr = max(0, min(30, int(ss.num_reactivos)))
    except Exception:
        nr = 0
    if len(ss.reactivos) < nr:
        for _ in range(nr - len(ss.reactivos)):
            ss.reactivos.append("")
    elif len(ss.reactivos) > nr:
        ss.reactivos = ss.reactivos[:nr]
    for i in range(nr):
        ss.reactivos[i] = st.text_input(f"Reactivo {i+1}", value=ss.reactivos[i], key=f"reactivo_{i}")

    st.markdown("---")
    st.subheader("Datos generales")
    st.text_input("Nombre producto:", value=ss.nombre_prod, key="nombre_prod")
    st.text_input("Lote (general):", value=ss.lote, key="lote_general")
    st.text_input("Determinación:", value=ss.determinacion, key="determinacion")
    st.text_input("Analista:", value=ss.analista, key="analista")
    st.text_input("Fecha:", value=ss.fecha, key="fecha")

    st.number_input("Etiqueta inicial (1-80):", min_value=1, max_value=TOTAL_ETIQUETAS_PAGINA, value=int(ss.start_label), key="start_label")

    # Generate callback
    def on_generate():
        pdf_bytes, total, next_start = generar_pdf_bytes_and_next_start()
        ss["last_pdf"] = pdf_bytes
        ss["last_total"] = total
        ss["start_label"] = next_start

    st.button("GENERAR PDF", on_click=on_generate)

    # If PDF available, show "Abrir en nueva pestaña" button and download
    if ss.last_pdf:
        b64 = base64.b64encode(ss.last_pdf).decode("ascii")
        filename = f"{datetime.today().strftime('%Y%m%d')}_{limpiar_nombre_archivo(ss.nombre_prod)}_{limpiar_nombre_archivo(ss.lote)}.pdf"

        # Provide a button to open the PDF in new tab (avoid automatic popup). Use callback to inject JS.
        def open_in_tab():
            # This components.html will execute and open a new tab with the file blob.
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

        st.button("Abrir en nueva pestaña", on_click=open_in_tab)
        st.success(f"PDF generado: {ss.last_total} etiquetas")
        st.download_button("Descargar PDF", data=ss.last_pdf, file_name=filename, mime="application/pdf")

st.markdown("---")
st.caption("La aplicación recuerda los valores durante la sesión (no guarda en disco).")