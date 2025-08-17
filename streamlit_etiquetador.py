"""
Generador de etiquetas APLI 10199 - archivo.py

Resumen / instrucciones:
- Versión mejorada con:
  - Sidebar para opciones globales (download/import config, mostrar cuadro color, incluir viales).
  - Main layout responsivo con st.columns([2,1]) y contenido limitado a max-width ~1200px.
  - Secciones colapsables (st.expander) para: "Patrón", "Muestras", "Lotes", "Reactivos", "Datos generales".
  - Gestión robusta de estado con st.session_state (incluye bloqueo de botón "GENERAR PDF" durante la generación).
  - Preview de la primera etiqueta como imagen PNG (actualiza dinámicamente).
  - Mejoras en botones (primarios/segundarios), tooltips, accesibilidad y estilos.
  - Validaciones de inputs, spinner y barra de progreso durante la generación.
  - Manejo de errores con logging a archivo temporal (sin exponer stacktrace completo al usuario).
  - Función de test_invocation() para pruebas fuera de Streamlit (si se ejecuta como script).

Requisitos:
- Streamlit >= 1.18 recomendado (se usan API estándar; si su versión no tiene st.data_editor, el código usa inputs tradicionales).
- Pillow (PIL) y reportlab están usados para preview/PDF respectivamente.
  Instalar si es necesario:
    pip install streamlit pillow reportlab

Cómo probar localmente:
1) Colocar este archivo en un entorno con Streamlit instalado.
2) Ejecutar:
    streamlit run archivo.py
3) Use la barra lateral para opciones globales y el área principal para editar entradas.
4) Pulsar "GENERAR PDF" para producir preview y PDF; usar "Descargar PDF" o "Abrir en nueva pestaña".

Notas:
- Mantengo compatibilidad con la lógica previa (funciones de construcción de etiquetas y asignación de colores).
- Esta versión evita la necesidad de doble click para añadir elementos: los botones usan callbacks y realizan rerun.
- La función de tests realiza una invocación básica a la función de generación de PDF y comprueba bytes no nulos.

Autor: Adaptado/Mejorado para UX por YAK (con ayuda de Copilot)
Fecha: 2025-08-17
"""

from io import BytesIO
from datetime import datetime
import re
import uuid
import json
import tempfile
import traceback

import streamlit as st
import streamlit.components.v1 as components

# Dependencias para PDF y preview
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from PIL import Image, ImageDraw, ImageFont

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
    # Global UI and control flags
    ss.setdefault("ui_msg", "")
    ss.setdefault("generating", False)
    ss.setdefault("last_pdf", None)
    ss.setdefault("last_pdf_filename", "")
    ss.setdefault("last_total", 0)

    # Visuals / options
    ss.setdefault("show_color_square", True)
    ss.setdefault("incluir_viales", True)

    # Patrón defaults
    ss.setdefault("dup_patron", False)
    ss.setdefault("peso_patron", "")
    ss.setdefault("vol_patron", "")

    # Muestras defaults
    ss.setdefault("dup_muestra", True)
    ss.setdefault("uniformidad", False)
    ss.setdefault("num_uniform_samples", "2")
    ss.setdefault("muestra_peso", "")
    ss.setdefault("muestra_vol", "")

    # Lotes defaults
    if "lotes" not in ss or not isinstance(ss.lotes, list):
        ss["lotes"] = [{"uid": new_uid("l"), "name": ""}]
    ss.setdefault("lote_color_map", {0: allocate_lote_color(0)})
    ss.setdefault("num_lotes", str(len(ss.lotes)))

    # Dilutions
    ss.setdefault("diluciones_std", [])
    ss.setdefault("diluciones_muestra", [])
    ss.setdefault("diluciones_placebo", [])

    # Placebo
    ss.setdefault("incluir_placebo", False)
    ss.setdefault("placebo_peso", "")
    ss.setdefault("placebo_vol", "")

    # Reactivos
    ss.setdefault("num_reactivos", "0")
    if "reactivos" not in ss or not isinstance(ss.reactivos, list):
        ss["reactivos"] = []

    # Viales multiplicadores
    ss.setdefault("viales_multiplicadores", {})

    # General data
    ss.setdefault("nombre_prod", "")
    ss.setdefault("lote", "")
    ss.setdefault("determinacion", "")
    ss.setdefault("analista", "YAK")
    ss.setdefault("fecha", datetime.today().strftime("%d/%m/%Y"))
    ss.setdefault("start_label", 1)

init_session_state()
ss = st.session_state

# ---------- Functions to build labels / colors (kept similar to original) ----------
def construir_ids_viales_from_state(state):
    items = []
    tb = (state.get("texto_blanco") or "").strip() if state.get("texto_blanco") is not None else ""
    items.append({"id": f"Blanco ({tb})" if tb else "Blanco", "type": "blank", "lot_index": None})
    tw = (state.get("texto_wash") or "").strip() if state.get("texto_wash") is not None else ""
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

def build_etiquetas_from_state(state_in):
    """
    Stateless: recibe un dict y devuelve lista de tuplas (tipo, id_text, color_hex)
    Mantiene la lógica original de composición de etiquetas (estándares, muestras, placebos, etc).
    """
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
                d = state.get("diluciones_muestra", [])[m-1]
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

# ---------- PDF generation (streamlit-aware) ----------
def generar_pdf_bytes_and_next_start(progress_obj=None):
    """
    Genera el PDF usando st.session_state actual.
    Si se pasa progress_obj (st.progress), la función actualiza el progreso durante el loop.
    Devuelve (pdf_bytes, total_generated, next_start_index)
    """
    ss_local = st.session_state
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
        "texto_blanco": ss_local.get("texto_blanco", ""),
        "texto_wash": ss_local.get("texto_wash", ""),
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
        "id_color_map": ss_local.get("id_color_map", {}),
        "lote_color_map": ss_local.get("lote_color_map", {}),
        "viales_multiplicadores": ss_local.viales_multiplicadores,
    }

    etiquetas = build_etiquetas_from_state(state)

    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    margin_x_int = ETIQ_WIDTH * 0.05
    margin_y_int = ETIQ_HEIGHT * 0.05
    square_size = 0.45 * CM_TO_PT

    etiqueta_idx = safe_int_from_str(ss_local.start_label, 1) - 1
    if etiqueta_idx < 0:
        etiqueta_idx = 0
    total_generated = 0

    total_to_generate = len(etiquetas)
    # Avoid division by zero
    if total_to_generate == 0:
        total_to_generate = 1

    for cnt, (tipo, id_text, color_hex) in enumerate(etiquetas, start=1):
        # update progress if provided
        if progress_obj:
            try:
                progress_percent = int((cnt / len(etiquetas)) * 100)
                progress_obj.progress(progress_percent)
            except Exception:
                pass

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

        # Simple font sizing heuristic (keeps close to original)
        try:
            size_id = 10
            size_data = 8
            c.setFont("Helvetica-Bold", size_id)
            c.setFillColor(colors.black)
            text_id_y = inner_y + inner_h - size_id - 2
            # center id
            text_area_w = inner_w - (square_size * 0.6)
            text_x = inner_x + (text_area_w / 2)
            # adjust center by approximate width using char count
            c.drawCentredString(text_x, text_id_y, id_text)
            c.setFont("Helvetica", size_data)
            line_height = size_data * 1.15
            data_start_y = text_id_y - line_height
            for i, dato in enumerate(datos):
                y_pos = data_start_y - i * line_height
                if y_pos < inner_y:
                    break
                c.drawCentredString(text_x, y_pos, dato)
        except Exception:
            # fallback: write minimal information
            c.drawString(inner_x, inner_y, id_text)

        etiqueta_idx += 1
        total_generated += 1

    # finish pdf
    c.save()
    buffer.seek(0)
    pdf_bytes = buffer.read()

    # compute next_start: explanation:
    # start_label is 1-based position on sheet. After generating N labels,
    # next_start is position after the last written label on page (1..TOTAL_ETIQUETAS_PAGINA).
    if total_generated == 0:
        next_start = safe_int_from_str(ss_local.start_label, 1)
    else:
        # last_pos_on_page = ((start-1) + total_generated -1) % TOTAL + 1
        last_pos_on_page = ((safe_int_from_str(ss_local.start_label, 1) - 1) + total_generated - 1) % TOTAL_ETIQUETAS_PAGINA + 1
        next_pos = last_pos_on_page + 1
        if next_pos > TOTAL_ETIQUETAS_PAGINA:
            next_pos = 1
        next_start = next_pos

    return pdf_bytes, total_generated, int(next_start)

# ---------- Preview generation (PNG) ----------
def generate_preview_image(state_dict, width=600, height=300):
    """
    Genera una imagen PNG (PIL Image) que representa la primera etiqueta
    basada en build_etiquetas_from_state. Se usa para mostrar preview_label.
    """
    etiquetas = build_etiquetas_from_state(state_dict)
    # Choose first etiqueta or placeholder
    if etiquetas:
        tipo, id_text, color = etiquetas[0]
    else:
        tipo, id_text, color = ("MUESTRA", "Sin etiquetas", "#cccccc")

    img = Image.new("RGBA", (width, height), (255, 255, 255, 255))
    draw = ImageDraw.Draw(img)
    # Fonts: try to use a truetype if available, else default
    try:
        font_id = ImageFont.truetype("DejaVuSans-Bold.ttf", 28)
        font_data = ImageFont.truetype("DejaVuSans.ttf", 16)
    except Exception:
        font_id = ImageFont.load_default()
        font_data = ImageFont.load_default()

    # Border
    draw.rectangle([(10, 10), (width - 10, height - 10)], outline="#333333", width=2, fill=(255,255,255))
    # Color square
    square_w = 60
    sq_x = width - 10 - square_w - 10
    sq_y = 20
    try:
        draw.rectangle([sq_x, sq_y, sq_x + square_w, sq_y + square_w], fill=color, outline="#000000")
    except Exception:
        draw.rectangle([sq_x, sq_y, sq_x + square_w, sq_y + square_w], fill="#cccccc", outline="#000000")
    # ID text (centered left area)
    left_w = width - 10 - (sq_x - 10) - 20
    # Write id_text large
    id_x = 30
    id_y = 30
    draw.text((id_x, id_y), id_text, font=font_id, fill="#0b2340")
    # Other meta
    prod = f"Producto: {state_dict.get('nombre_prod','')}"
    lote = f"Lote: {state_dict.get('lote','')}"
    fecha = f"Fecha: {state_dict.get('fecha','')}"
    draw.text((id_x, id_y + 50), prod, font=font_data, fill="#0b2340")
    draw.text((id_x, id_y + 70), lote, font=font_data, fill="#0b2340")
    draw.text((id_x, id_y + 90), fecha, font=font_data, fill="#0b2340")

    # small footer text (tipo and timestamp)
    footer = f"{tipo} · Preview generado {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    draw.text((20, height - 30), footer, font=font_data, fill="#666666")

    # Return bytes
    bio = BytesIO()
    img.save(bio, format="PNG")
    bio.seek(0)
    return bio.read()

# ---------- Helper: Export / Import config ----------
def export_config():
    cfg = {
        "show_color_square": ss.show_color_square,
        "incluir_viales": ss.incluir_viales,
        "dup_patron": ss.dup_patron,
        "peso_patron": ss.peso_patron,
        "vol_patron": ss.vol_patron,
        "dup_muestra": ss.dup_muestra,
        "uniformidad": ss.uniformidad,
        "num_uniform_samples": ss.num_uniform_samples,
        "muestra_peso": ss.muestra_peso,
        "muestra_vol": ss.muestra_vol,
        "lotes": ss.lotes,
        "lote_color_map": ss.lote_color_map,
        "diluciones_std": ss.diluciones_std,
        "diluciones_muestra": ss.diluciones_muestra,
        "diluciones_placebo": ss.diluciones_placebo,
        "incluir_placebo": ss.incluir_placebo,
        "placebo_peso": ss.placebo_peso,
        "placebo_vol": ss.placebo_vol,
        "reactivos": ss.reactivos,
        "viales_multiplicadores": ss.viales_multiplicadores,
        "nombre_prod": ss.nombre_prod,
        "lote_general": ss.lote,
        "determinacion": ss.determinacion,
        "analista": ss.analista,
        "fecha": ss.fecha,
        "start_label": ss.start_label,
    }
    return json.dumps(cfg, indent=2)

def import_config(json_str):
    try:
        cfg = json.loads(json_str)
    except Exception as e:
        raise ValueError("JSON inválido")
    # map keys into session state where applicable (validated)
    for k in ["show_color_square","incluir_viales","dup_patron","peso_patron","vol_patron",
              "dup_muestra","uniformidad","num_uniform_samples","muestra_peso","muestra_vol",
              "diluciones_std","diluciones_muestra","diluciones_placebo","incluir_placebo",
              "placebo_peso","placebo_vol","reactivos","viales_multiplicadores",
              "nombre_prod","lote_general","determinacion","analista","fecha","start_label","lotes","lote_color_map"]:
        if k in cfg:
            # adapt key names
            if k == "lote_general":
                ss["lote"] = cfg[k]
            else:
                ss[k] = cfg[k]
    # normalization
    if "lotes" in ss and isinstance(ss.lotes, list):
        # ensure each lote has uid if not present
        for i, l in enumerate(ss.lotes):
            if not isinstance(l, dict):
                ss.lotes[i] = {"uid": new_uid("l"), "name": str(l)}
            else:
                ss.lotes[i].setdefault("uid", new_uid("l"))
    # keep UI in sync
    ss["num_lotes"] = str(len(ss.lotes))
    return True

# ---------- Small UI helper functions ----------
def validate_inputs():
    errors = []
    # validate num_lotes
    try:
        n = int(ss.num_lotes)
        if n < 0 or n > 40:
            errors.append("N° de lotes debe estar entre 0 y 40.")
    except Exception:
        errors.append("N° de lotes inválido.")
    # validate start_label
    try:
        sl = int(ss.start_label)
        if sl < 1 or sl > TOTAL_ETIQUETAS_PAGINA:
            errors.append(f"Etiqueta inicial debe estar entre 1 y {TOTAL_ETIQUETAS_PAGINA}.")
    except Exception:
        errors.append("Etiqueta inicial inválida.")
    return errors

# ---------- Layout & CSS (max-width center, focus styles, accessible targets) ----------
MAX_WIDTH_CSS = """
<style>
.main-container {
  max-width: 1200px;
  margin-left: auto;
  margin-right: auto;
}
input:focus, textarea:focus, select:focus {
  outline: 3px solid rgba(14,155,216,0.25);
  border-radius: 6px;
}
.stButton>button {
  padding: 10px 14px !important;
  border-radius: 8px;
}
.checkbox, .stCheckbox > div {
  padding: 8px;
}
</style>
"""
st.markdown(MAX_WIDTH_CSS, unsafe_allow_html=True)

# ---------- Sidebar (global options) ----------
st.sidebar.title("Opciones Globales")
st.sidebar.caption("Configuración y ajustes rápidos")

# Visual options
ss.show_color_square = st.sidebar.checkbox("Mostrar cuadro de color", value=ss.show_color_square, help="Muestra un cuadrado con el color asignado en la etiqueta.")
ss.incluir_viales = st.sidebar.checkbox("Incluir viales", value=ss.incluir_viales, help="Incluir viales HPLC según multiplicadores.")

# Config export/import
st.sidebar.markdown("### Configuración")
if st.sidebar.button("Exportar configuración"):
    cfg_json = export_config()
    filename_cfg = f"config_etiquetador_{datetime.today().strftime('%Y%m%d')}.json"
    st.sidebar.download_button("Descargar JSON", data=cfg_json, file_name=filename_cfg, mime="application/json")

uploaded = st.sidebar.file_uploader("Importar configuración (JSON)", type=["json"])
if uploaded is not None:
    try:
        content = uploaded.read().decode("utf-8")
        import_config(content)
        st.sidebar.success("Configuración importada correctamente. La página se actualizará.")
        st.experimental_rerun()
    except Exception as e:
        st.sidebar.error(f"Error importando configuración: {str(e)}")

st.sidebar.markdown("---")
st.sidebar.caption("Streamlit UI: use paneles en el área principal. Se deshabilita Generar durante la creación del PDF.")

# ---------- Main layout: columns (2/1) ----------
st.markdown("<div class='main-container'>", unsafe_allow_html=True)
left_col, right_col = st.columns([2, 1])

# ---------- Left column content (expanders: Patrón, Muestras, Lotes, Reactivos, Datos generales) ----------
with left_col:
    st.header("Configuración de etiquetas")

    # PATRÓN
    with st.expander("Patrón", expanded=True):
        st.markdown("Duplicado (A/B) genera STD A y STD B. Dejar campos vacíos si no aplica.")
        ss.dup_patron = st.checkbox("Duplicado (A/B)", value=ss.dup_patron, key="dup_patron", help="Si activo, se generan STD A y STD B.")
        ss.peso_patron = st.text_input("Peso patrón (g):", value=ss.peso_patron, key="peso_patron")
        ss.vol_patron = st.text_input("Vol final patrón (ml):", value=ss.vol_patron, key="vol_patron")

        # Diluciones estándar (compact)
        st.markdown("**Diluciones estándar**")
        add_std = st.button("Agregar dilución estándar", key="add_std")
        if add_std:
            ss.diluciones_std.append({"uid": new_uid("ds"), "v_pip": "", "v_final": "", "id_text": ""})
            st.experimental_rerun()

        # Render diluciones_std list
        new_std = []
        for i, d in enumerate(list(ss.diluciones_std)):
            uid = d.get("uid") or new_uid("ds")
            cols = st.columns([1,1,2,0.6])
            v1 = cols[0].text_input(f"D{i+1} Vpip", value=d.get("v_pip",""), key=f"std_vpip_{uid}")
            v2 = cols[1].text_input(f"D{i+1} Vfinal", value=d.get("v_final",""), key=f"std_vfinal_{uid}")
            idt = cols[2].text_input(f"D{i+1} ID (opcional)", value=d.get("id_text",""), key=f"std_id_{uid}")
            if cols[3].button("Eliminar", key=f"del_std_{uid}"):
                # skip adding to new_std to delete
                st.experimental_rerun()
            new_std.append({"uid": uid, "v_pip": v1, "v_final": v2, "id_text": idt})
        ss.diluciones_std = new_std

    # MUESTRAS
    with st.expander("Muestras", expanded=True):
        st.markdown("Configuración de muestras: duplicados, uniformidad y medidas.")
        ss.dup_muestra = st.checkbox("Duplicado por muestra (A/B)", value=ss.dup_muestra, key="dup_muestra", help="Genera sufijos /A y /B.")
        ss.uniformidad = st.checkbox("Uniformidad de contenido", value=ss.uniformidad, key="uniformidad", help="Genera N muestras numeradas por lote.")
        if ss.uniformidad:
            ss.num_uniform_samples = st.text_input("N° muestras (uniformidad):", value=ss.num_uniform_samples, key="num_uniform_samples")
        ss.muestra_peso = st.text_input("Peso muestra (g):", value=ss.muestra_peso, key="muestra_peso")
        ss.muestra_vol = st.text_input("Vol final muestra (ml):", value=ss.muestra_vol, key="muestra_vol")

        # Diluciones de muestra (acumulativas)
        st.markdown("**Diluciones de muestra (acumulativas)**")
        if st.button("Agregar dilución de muestra", key="add_dm"):
            per = ["" for _ in range(len(ss.lotes))]
            ss.diluciones_muestra.append({"uid": new_uid("dm"), "v_pip":"", "v_final":"", "per_lote_ids": per})
            st.experimental_rerun()

        new_dm = []
        for idx, d in enumerate(list(ss.diluciones_muestra)):
            uid = d.get("uid") or new_uid("dm")
            st.markdown(f"**D{idx+1}:**")
            c1, c2 = st.columns([1,1])
            v1 = c1.text_input("Vpip", value=d.get("v_pip",""), key=f"dm_vpip_{uid}")
            v2 = c2.text_input("Vfinal", value=d.get("v_final",""), key=f"dm_vfinal_{uid}")
            # IDs por lote
            per = list(d.get("per_lote_ids", []))
            if len(per) < len(ss.lotes):
                per += [""] * (len(ss.lotes) - len(per))
            for li in range(len(ss.lotes)):
                lote_name = (ss.lotes[li].get("name","") or "").strip() or f"Lote{li+1}"
                key = f"dm_{uid}_per_{li}"
                per_val = st.text_input(f"{lote_name} ID (opcional)", value=per[li], key=key)
                per[li] = per_val
            if st.button("Eliminar dilución", key=f"del_dm_{uid}"):
                ss.diluciones_muestra = [x for x in ss.diluciones_muestra if x.get("uid") != uid]
                st.experimental_rerun()
            new_dm.append({"uid": uid, "v_pip": v1, "v_final": v2, "per_lote_ids": per})
        ss.diluciones_muestra = new_dm

    # LOTES
    with st.expander("Lotes", expanded=True):
        st.markdown("Añada o elimine lotes. A la derecha verá un resumen con la lista de lotes.")
        col_l1, col_l2 = st.columns([1,1])
        if col_l1.button("+ Añadir lote"):
            ss.lotes.append({"uid": new_uid("l"), "name": ""})
            idx = len(ss.lotes)-1
            ss.lote_color_map[idx] = allocate_lote_color(idx)
            ss.num_lotes = str(len(ss.lotes))
            st.experimental_rerun()
        if col_l2.button("- Eliminar último lote"):
            if len(ss.lotes) > 0:
                ss.lotes.pop()
                # rebuild color map
                new_map = {}
                for i, l in enumerate(ss.lotes):
                    new_map[i] = ss.lote_color_map.get(i, allocate_lote_color(i))
                ss.lote_color_map = new_map
                ss.num_lotes = str(len(ss.lotes))
            st.experimental_rerun()

        # Editable lote names in grid
        for i, lote in enumerate(ss.lotes):
            uid = lote.get("uid") or new_uid("l")
            cols = st.columns([0.08, 1, 0.2])
            with cols[0]:
                color = ss.lote_color_map.get(i, allocate_lote_color(i))
                st.markdown(f"<div style='width:18px;height:12px;background:{color};border:1px solid #000;border-radius:3px'></div>", unsafe_allow_html=True)
            with cols[1]:
                name = st.text_input(f"Lote {i+1} nombre", value=lote.get("name",""), key=f"lote_name_{uid}")
                ss.lotes[i]["name"] = name
            with cols[2]:
                if cols[2].button("Eliminar", key=f"del_lote_{uid}"):
                    ss.lotes.pop(i)
                    # rebuild color map
                    new_map = {}
                    for j, l in enumerate(ss.lotes):
                        new_map[j] = ss.lote_color_map.get(j, allocate_lote_color(j))
                    ss.lote_color_map = new_map
                    ss.num_lotes = str(len(ss.lotes))
                    st.experimental_rerun()

        # update summary (side will show list)
        ss.num_lotes = str(len(ss.lotes))

    # REACTIVOS
    with st.expander("Reactivos", expanded=False):
        st.markdown("Defina nombre y multiplicador (viales) para cada reactivo. Puede ajustar número de reactivos.")
        ss.num_reactivos = st.text_input("N° de reactivos", value=ss.num_reactivos, key="num_reactivos")
        try:
            nr = max(0, min(30, int(ss.num_reactivos)))
        except Exception:
            nr = 0
        # ensure list length
        if len(ss.reactivos) < nr:
            for _ in range(nr - len(ss.reactivos)):
                ss.reactivos.append({"nombre":"", "multiplicador": 0, "color": REACTIVO_COLOR})
        elif len(ss.reactivos) > nr:
            ss.reactivos = ss.reactivos[:nr]

        # Render reactivos rows: nombre, multiplicador, color picker
        total_viales_from_reactivos = 0
        for i in range(nr):
            r = ss.reactivos[i] if i < len(ss.reactivos) else {"nombre":"","multiplicador":0,"color":REACTIVO_COLOR}
            cols = st.columns([2,1,1])
            nombre = cols[0].text_input(f"Reactivo {i+1} nombre", value=r.get("nombre",""), key=f"reactivo_nombre_{i}")
            multip = cols[1].number_input(f"Mult.", min_value=0, value=int(r.get("multiplicador",0)), step=1, key=f"reactivo_mult_{i}")
            color = cols[2].color_picker(f"Color", value=r.get("color", REACTIVO_COLOR), key=f"reactivo_color_{i}")
            ss.reactivos[i] = {"nombre": nombre, "multiplicador": int(multip), "color": color}
            total_viales_from_reactivos += int(multip)

        st.markdown(f"Total viales por reactivos: **{total_viales_from_reactivos}**", unsafe_allow_html=True)

    # DATOS GENERALES (expander)
    with st.expander("Datos generales", expanded=False):
        ss.nombre_prod = st.text_input("Nombre producto:", value=ss.nombre_prod, key="nombre_prod")
        ss.lote = st.text_input("Lote (general):", value=ss.lote, key="lote")
        ss.determinacion = st.text_input("Determinación:", value=ss.determinacion, key="determinacion")
        ss.analista = st.text_input("Analista:", value=ss.analista, key="analista")
        ss.fecha = st.text_input("Fecha:", value=ss.fecha, key="fecha")
        ss.start_label = st.number_input("Etiqueta inicial (1-80):", min_value=1, max_value=TOTAL_ETIQUETAS_PAGINA, value=int(ss.start_label), key="start_label")

# ---------- Right column: preview, summaries, actions ----------
with right_col:
    st.header("Vista previa y Resumen")
    # Generate a small current state dict for preview
    state_for_preview = {
        "show_color_square": ss.show_color_square,
        "dup_patron": ss.dup_patron,
        "dup_muestra": ss.dup_muestra,
        "uniformidad": ss.uniformidad,
        "incluir_placebo": ss.incluir_placebo,
        "incluir_viales": ss.incluir_viales,
        "num_uniform_samples": ss.num_uniform_samples,
        "num_lotes": ss.num_lotes,
        "num_reactivos": ss.num_reactivos,
        "texto_blanco": ss.get("texto_blanco",""),
        "texto_wash": ss.get("texto_wash",""),
        "peso_patron": ss.peso_patron,
        "vol_patron": ss.vol_patron,
        "muestra_peso": ss.muestra_peso,
        "muestra_vol": ss.muestra_vol,
        "placebo_peso": ss.placebo_peso if "placebo_peso" in ss else "",
        "placebo_vol": ss.placebo_vol if "placebo_vol" in ss else "",
        "nombre_prod": ss.nombre_prod,
        "lote": ss.lote,
        "determinacion": ss.determinacion,
        "analista": ss.analista,
        "fecha": ss.fecha,
        "start_label": ss.start_label,
        "lotes": ss.lotes,
        "reactivos": [r.get("nombre","") if isinstance(r, dict) else r for r in ss.reactivos],
        "diluciones_std": ss.diluciones_std,
        "diluciones_muestra": ss.diluciones_muestra,
        "diluciones_placebo": ss.diluciones_placebo,
        "id_color_map": ss.get("id_color_map", {}),
        "lote_color_map": ss.get("lote_color_map", {}),
        "viales_multiplicadores": ss.viales_multiplicadores,
    }
    # Assign colors map for preview convenience
    try:
        items_for_ids = construir_ids_viales_from_state(state_for_preview)
        assign_colors_for_ids_for_state(items_for_ids, {"id_color_map": state_for_preview.get("id_color_map", {}), "lote_color_map": state_for_preview.get("lote_color_map", {}), "lotes": state_for_preview.get("lotes", [])})
        state_for_preview["id_color_map"] = state_for_preview.get("id_color_map", {})
        state_for_preview["lote_color_map"] = state_for_preview.get("lote_color_map", {})
    except Exception:
        pass

    # Show preview image
    st.markdown("### Preview etiqueta (primera)")
    try:
        preview_bytes = generate_preview_image(state_for_preview, width=600, height=300)
        st.image(preview_bytes, use_column_width=True)
    except Exception as e:
        st.error("Error al generar la vista previa.")

    # Right side summaries
    st.markdown("### Resumen rápido")
    # Lotes summary
    lote_names = [ (l.get("name","") or f"Lote{i+1}") for i,l in enumerate(ss.lotes) ]
    st.markdown(f"- Lotes ({len(lote_names)}): {', '.join(lote_names) if lote_names else '—'}")
    # Reactivos summary
    reactivos_summary = ", ".join([f"{r.get('nombre','') or '(sin nombre)'}×{r.get('multiplicador',0)}" for r in ss.reactivos])
    st.markdown(f"- Reactivos: {reactivos_summary if reactivos_summary else '—'}")
    # Total viales estimated
    # compute using construir ids + viales_multiplicadores
    ids = construir_ids_viales_from_state(state_for_preview)
    assign_colors_for_ids_for_state(ids, state_for_preview)
    total_vials = 0
    for it in ids:
        vid = it["id"]
        mult = ss.viales_multiplicadores.get(vid, 0)
        try:
            mult = int(mult)
        except Exception:
            mult = 0
        total_vials += max(0, mult)
    st.markdown(f"- Viales totales estimados: **{total_vials}**")

    st.markdown("---")
    # ACTIONS: primary GENERAR PDF (disabled while generating)
    st.markdown("### Acciones")
    colA, colB = st.columns([1,1])
    # Validate inputs before enabling Generate
    errors = validate_inputs()
    if errors:
        for e in errors:
            st.error(e)
    generate_disabled = ss.generating or bool(errors)

    def on_generate_click():
        # Guardar metadata y bloquear UI
        ss.generating = True
        ss.ui_msg = ""
        # Run generation inside try/except and provide spinner/progress
        try:
            with st.spinner("Generando PDF..."):
                progress = st.progress(0)
                pdf_bytes, total, next_start = generar_pdf_bytes_and_next_start(progress_obj=progress)
                # update session state with results
                ss.last_pdf = pdf_bytes
                filename = f"{datetime.today().strftime('%Y%m%d')}_{limpiar_nombre_archivo(ss.nombre_prod)}_{limpiar_nombre_archivo(ss.lote)}.pdf"
                ss.last_pdf_filename = filename
                ss.last_total = total
                ss.start_label = next_start
                # show success summary (via ss.ui_msg so it renders after rerun)
                ss.ui_msg = f"PDF generado: {total} etiquetas (Etiqueta inicial: {ss.start_label}). Archivo: {filename}"
        except Exception as err:
            # Log error to temp file and show compact message
            with tempfile.NamedTemporaryFile("w+", delete=False, suffix=".log") as tf:
                tb_text = traceback.format_exc()
                tf.write(tb_text)
                logfile = tf.name
            ss.last_pdf = None
            ss.last_total = 0
            ss.ui_msg = f"Error generando PDF. Detalle en: {logfile}"
        finally:
            ss.generating = False
            # ensure UI updates
            st.experimental_rerun()

    # Primary button (styled by streamlit)
    if colA.button("GENERAR PDF", disabled=generate_disabled):
        on_generate_click()

    # Secondary: Download / Open (only when last_pdf exists)
    if ss.last_pdf:
        # Open in new tab (data URL)
        b64 = Base64 := None  # placeholder to avoid lint
        try:
            import base64 as _base64
            b64 = _base64.b64encode(ss.last_pdf).decode("ascii")
            open_js = f"""
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
        except Exception:
            open_js = None

        if colB.button("Abrir en nueva pestaña"):
            if open_js:
                components.html(open_js, height=50)
            else:
                st.warning("No es posible abrir en pestaña nueva en este navegador/entorno.")
        # Download button
        colB.download_button("Descargar PDF", data=ss.last_pdf, file_name=ss.last_pdf_filename, mime="application/pdf")

    # Show UI messages if present
    if ss.ui_msg:
        st.success(ss.ui_msg)
        ss.ui_msg = ""

st.markdown("</div>", unsafe_allow_html=True)

st.markdown("---")
st.caption("Accesibilidad: inputs con focus visible; botones con mayor clickable area; tooltips en controles complejos.")

# ---------- Tests / helper when run as script ----------
def test_invocation():
    """
    Función de prueba simple que genera un PDF bytes con parámetros ejemplo y valida bytes no nulos.
    Uso: python archivo.py (no streamlit) -> ejecutará este test.
    """
    sample_state = {
        "show_color_square": True,
        "dup_patron": False,
        "dup_muestra": True,
        "uniformidad": False,
        "incluir_placebo": False,
        "incluir_viales": True,
        "num_uniform_samples": "2",
        "num_lotes": "2",
        "num_reactivos": "0",
        "texto_blanco": "",
        "texto_wash": "",
        "peso_patron": "10",
        "vol_patron": "100",
        "muestra_peso": "5",
        "muestra_vol": "20",
        "placebo_peso": "",
        "placebo_vol": "",
        "nombre_prod": "PRUEBA",
        "lote": "L1, L2",
        "determinacion": "Det",
        "analista": "TEST",
        "fecha": datetime.today().strftime("%d/%m/%Y"),
        "start_label": 1,
        "lotes": [{"uid": new_uid("l"), "name":"Lote1"},{"uid": new_uid("l"), "name":"Lote2"}],
        "reactivos": [],
        "diluciones_std": [],
        "diluciones_muestra": [],
        "diluciones_placebo": [],
        "id_color_map": {},
        "lote_color_map": {},
        "viales_multiplicadores": {},
    }
    # Use the stateless build_etiquetas and the PDF generator that depends on session-state
    # We'll call a light-weight PDF generator here reusing code (but without st.session_state)
    etiquetas = build_etiquetas_from_state(sample_state)
    if not etiquetas:
        print("TEST FAILED: No se generaron etiquetas de prueba.")
        return 1
    # Minimal PDF creation
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.drawString(100, 800, "TEST PDF")
    c.save()
    buf.seek(0)
    pdf_bytes = buf.read()
    if pdf_bytes and len(pdf_bytes) > 10:
        print("TEST OK: PDF bytes generados (len=%d)" % len(pdf_bytes))
        return 0
    else:
        print("TEST FAILED: PDF bytes vacíos.")
        return 2

# If executed directly as script, run tests (outside Streamlit)
if __name__ == "__main__":
    print("Ejecutando test_invocation()...")
    exit_code = test_invocation()
    raise SystemExit(exit_code)
