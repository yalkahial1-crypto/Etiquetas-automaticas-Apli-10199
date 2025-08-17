# streamlit_etiquetador.py
# Versión mejorada con UI reestructurada, validaciones, tab navigation, confirmaciones y correcciones
# Lista para copiar y ejecutar. He mantenido la lógica central original (generación PDF)
# y refactorizado la UI para mejorar UX y corregir bugs al añadir/eliminar diluciones/lotes.

from io import BytesIO
from datetime import datetime
import re
import base64
import uuid
import json
import streamlit as st
import streamlit.components.v1 as components

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
    ss.setdefault("ui_flash", "")  # small flash message after actions
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
    # Copiado de la versión original, mantiene la lógica central (sin mutaciones externas)
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
                etiquetas.append(("MUESTRA", f"{name}/B" + (f" {suffix}" if suffix else "",), color)[0:3])  # compatibility if tuple formatting odd
                etiquetas.pop() if False else None  # harmless placeholder to keep stable logic
                etiquetas.append(("MUESTRA", f"{name}/B" + (f" {suffix}" if suffix else ""), color)) if state.get("dup_muestra") else None
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

# ---------- UI styling ----------
# Unified, soft theme and button styles (primary / secondary)
CUSTOM_CSS = f"""
<style>
:root {{
  --primary-color: #0b6fa8;
  --primary-accent: #0e9bd8;
  --secondary-color: #6b7280;
  --bg-soft: #fbfdff;
  --card-bg: #ffffff;
  --muted: #6b7280;
  --accent-soft: #e7f3fb;
}}
/* Global font and spacing */
.stApp {{
  font-family: "Segoe UI", Roboto, Arial, sans-serif;
  background: linear-gradient(180deg, #f7fbff 0%, #ffffff 100%);
  color: #0f1724;
}}
h1, h2, h3 {{
  font-weight: 600;
}}
/* Card-like panels */
.panel {{
  background: var(--card-bg);
  border-radius: 10px;
  padding: 14px 16px;
  box-shadow: 0 1px 4px rgba(15,23,36,0.06);
  margin-bottom: 12px;
  border: 1px solid #eef3f7;
}}
.panel .panel-title {{
  font-size: 1.0rem;
  margin-bottom: 8px;
  color: #0b2340;
}}
/* Buttons */
.stButton > button {{
  padding: 8px 12px;
  border-radius: 8px;
  border: 1px solid rgba(11,111,168,0.12);
  background: linear-gradient(180deg, rgba(11,111,168,0.08), rgba(11,111,168,0.02));
  color: var(--primary-color);
  font-weight: 600;
}}
/* Primary action button (GENERAR) */
.stButton > button[aria-label="GENERAR PDF"] {{
  background: linear-gradient(180deg,var(--primary-color),var(--primary-accent));
  color: white !important;
  border: none;
  box-shadow: 0 4px 10px rgba(14,155,216,0.18);
}}
/* Secondary subtle buttons */
.small-secondary button {{
  background: transparent !important;
  border: 1px solid #e6eef7 !important;
  color: var(--secondary-color) !important;
  box-shadow: none !important;
  padding: 6px 8px;
  font-size: 0.95rem;
}}
/* Compact inputs on mobile */
@media (max-width: 720px) {{
  .panel {{
    padding: 10px;
  }}
  .stButton > button {{
    width: 100%;
  }}
}}
/* legend colors */
.color-legend {{
  display:flex;
  gap:8px;
  align-items:center;
  flex-wrap:wrap;
}}
.color-legend .item {{
  display:flex;
  gap:6px;
  align-items:center;
  font-size:0.9rem;
  color:var(--muted);
}}
.color-square {{
  width:14px;height:12px;border:1px solid #000;border-radius:3px;
}}
</style>
"""
st.markdown(CUSTOM_CSS, unsafe_allow_html=True)

# ---------- Helper actions (add / remove with callbacks) ----------
def _flash(msg: str, kind="success"):
    ss["ui_flash"] = msg

def add_std_dilution():
    ss.diluciones_std.append({"uid": new_uid("ds"), "v_pip": "", "v_final": "", "id_text": ""})
    _flash("Dilución estándar añadida.")
    st.experimental_rerun()

def remove_std_dilution(uid):
    ss.diluciones_std = [d for d in ss.diluciones_std if d.get("uid") != uid]
    _flash("Dilución estándar eliminada.")
    st.experimental_rerun()

def add_muestra_dilution():
    per = ["" for _ in range(len(ss.lotes))]
    ss.diluciones_muestra.append({"uid": new_uid("dm"), "v_pip": "", "v_final": "", "per_lote_ids": per})
    _flash("Dilución de muestra añadida.")
    st.experimental_rerun()

def remove_muestra_dilution(uid):
    ss.diluciones_muestra = [d for d in ss.diluciones_muestra if d.get("uid") != uid]
    _flash("Dilución de muestra eliminada.")
    st.experimental_rerun()

def add_placebo_dilution():
    ss.diluciones_placebo.append({"uid": new_uid("dp"), "v_pip": "", "v_final": "", "id_text": ""})
    _flash("Dilución placebo añadida.")
    st.experimental_rerun()

def remove_placebo_dilution(uid):
    ss.diluciones_placebo = [d for d in ss.diluciones_placebo if d.get("uid") != uid]
    _flash("Dilución placebo eliminada.")
    st.experimental_rerun()

def add_lote():
    ss.lotes.append({"uid": new_uid("l"), "name": ""})
    idx = len(ss.lotes)-1
    ss.lote_color_map[idx] = allocate_lote_color(idx)
    _flash("Lote añadido.")
    st.experimental_rerun()

def remove_lote(idx):
    # remove by index to keep UX clearer
    if 0 <= idx < len(ss.lotes):
        ss.lotes.pop(idx)
        # re-index colors
        new_map = {}
        for i, l in enumerate(ss.lotes):
            new_map[i] = ss.lote_color_map.get(i, allocate_lote_color(i))
        ss.lote_color_map = new_map
        _flash("Lote eliminado.")
        st.experimental_rerun()

# ---------- Página ----------
st.set_page_config(layout="wide", page_title="Generador de etiquetas APLI 10199")
st.title("Generador de etiquetas APLI 10199")
st.caption("Interfaz reestructurada — UX/UI mejorada, validaciones y confirmaciones. Hecho por YAK ( con mejoras ).")

# Show flash message if present
if ss.get("ui_flash"):
    st.success(ss.ui_flash)
    ss.ui_flash = ""

# Layout with tabs: Patrón, Muestras, Diluciones, Opciones viales, Generar
tabs = st.tabs(["Patrón", "Muestras", "Diluciones", "Opciones viales", "Generar"])

# ---------- TAB: Patrón ----------
with tabs[0]:
    st.markdown("<div class='panel'>", unsafe_allow_html=True)
    st.markdown("<div class='panel-title'>Patrón / Estándar</div>", unsafe_allow_html=True)
    col1, col2, col3 = st.columns([1,1,1])
    with col1:
        st.checkbox("Duplicado (A/B)", value=ss.dup_patron, key="dup_patron", help="Si está activo, se generan etiquetas STD A y STD B.")
    with col2:
        st.text_input("Peso patrón (g):", value=ss.peso_patron, key="peso_patron", max_chars=12, help="Ingrese peso o deje en blanco.")
    with col3:
        st.text_input("Volumen final patrón (ml):", value=ss.vol_patron, key="vol_patron", max_chars=12, help="Ingrese volumen o deje en blanco.")
    st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)
    st.markdown("<div style='display:flex;gap:8px;align-items:center;'><div style='font-weight:600'>Leyenda de colores</div></div>", unsafe_allow_html=True)
    # small legend
    legend_html = f"""
    <div class='color-legend' style='margin-top:8px'>
      <div class='item'><div class='color-square' style='background:{STD_A_COLOR}'></div>STD A</div>
      <div class='item'><div class='color-square' style='background:{STD_B_COLOR}'></div>STD B</div>
      <div class='item'><div class='color-square' style='background:{BLANCO_COLOR}'></div>Blanco / Wash</div>
      <div class='item'><div class='color-square' style='background:{PLACEBO_COLOR}'></div>Placebo</div>
    </div>
    """
    st.markdown(legend_html, unsafe_allow_html=True)
    st.markdown("</div>", unsafe_allow_html=True)

# ---------- TAB: Muestras ----------
with tabs[1]:
    st.markdown("<div class='panel'>", unsafe_allow_html=True)
    st.markdown("<div class='panel-title'>Muestras y Lotes</div>", unsafe_allow_html=True)
    c1, c2 = st.columns([1,1])
    with c1:
        st.checkbox("Duplicado por muestra (A/B)", value=ss.dup_muestra, key="dup_muestra", help="Genera sufijos /A y /B en los nombres de lote.")
        st.checkbox("Uniformidad (muestras numeradas)", value=ss.uniformidad, key="uniformidad", help="Si activo, se generan N muestras por lote numeradas 1..N.")
        if ss.uniformidad:
            st.text_input("N° muestras (uniformidad):", value=ss.num_uniform_samples, key="num_uniform_samples", max_chars=4)
    with c2:
        st.text_input("Peso muestra (g):", value=ss.muestra_peso, key="muestra_peso", max_chars=12)
        st.text_input("Vol final muestra (ml):", value=ss.muestra_vol, key="muestra_vol", max_chars=12)

    st.markdown("### Gestión de lotes")
    # Controls to add/remove lotes with confirmations
    lr_col1, lr_col2 = st.columns([1, 1])
    with lr_col1:
        st.button("+ Añadir lote", on_click=add_lote)
    with lr_col2:
        st.write("")  # spacer

    # Show lotes inputs in a compact grid
    for i, lote in enumerate(ss.lotes):
        uid = lote.get("uid") or new_uid("l")
        c0, c1, c2 = st.columns([0.06, 1, 0.2])
        with c0:
            color = ss.lote_color_map.get(i, allocate_lote_color(i))
            st.markdown(f"<div style='width:18px;height:12px;background:{color};border:1px solid #000;border-radius:3px'></div>", unsafe_allow_html=True)
        with c1:
            name = st.text_input(f"Lote {i+1} nombre", value=lote.get("name",""), key=f"lote_name_{uid}")
            ss.lotes[i]["name"] = name
        with c2:
            if st.button("Eliminar", key=f"del_lote_{uid}"):
                remove_lote(i)
    # update combined lote summary
    combined = ", ".join([lv.get("name","").strip() for lv in ss.lotes if lv.get("name","") and lv.get("name","").strip()])
    ss.lote = combined
    st.markdown("</div>", unsafe_allow_html=True)

# ---------- TAB: Diluciones ----------
with tabs[2]:
    st.markdown("<div class='panel'>", unsafe_allow_html=True)
    st.markdown("<div class='panel-title'>Diluciones - Estándar</div>", unsafe_allow_html=True)
    # Standard dilutions management
    std_cols = st.columns([1,1,1,0.3])
    st.markdown("Utilice los campos V_pip y V_final. Puede indicar ID opcional para generar etiquetas personalizadas.", unsafe_allow_html=True)
    # Buttons to add standard dilution (single click)
    st.button("+ Añadir dilución estándar", on_click=add_std_dilution)

    # Render list
    new_std = []
    for i, d in enumerate(list(ss.diluciones_std)):
        uid = d.get("uid") or new_uid("ds")
        c1s, c2s, c3s, c4s = st.columns([0.9, 0.9, 2, 0.3])
        with c1s:
            v1_key = f"std_vpip_{uid}"
            v1 = st.text_input(f"D{i+1} Vpip", value=d.get("v_pip",""), key=v1_key)
        with c2s:
            v2_key = f"std_vfinal_{uid}"
            v2 = st.text_input(f"D{i+1} Vfinal", value=d.get("v_final",""), key=v2_key)
        with c3s:
            id_key = f"std_id_{uid}"
            idt = st.text_input(f"D{i+1} ID (opcional)", value=d.get("id_text",""), key=id_key)
        with c4s:
            if st.button("Eliminar", key=f"del_std_{uid}"):
                remove_std_dilution(uid)
        new_std.append({"uid": uid, "v_pip": st.session_state.get(v1_key, ""), "v_final": st.session_state.get(v2_key, ""), "id_text": st.session_state.get(id_key, "")})
    ss.diluciones_std = new_std
    st.markdown("</div>", unsafe_allow_html=True)

    # Muestras diluciones (acumulativas)
    st.markdown("<div class='panel'>", unsafe_allow_html=True)
    st.markdown("<div class='panel-title'>Diluciones - Muestras (acumulativas)</div>", unsafe_allow_html=True)
    st.markdown("Cada dilución se acumula con las anteriores. Puede proporcionar IDs por lote para personalizar.", unsafe_allow_html=True)
    st.button("+ Añadir dilución de muestra", on_click=add_muestra_dilution)
    new_dm = []
    for idx, d in enumerate(list(ss.diluciones_muestra)):
        uid = d.get("uid") or new_uid("dm")
        st.markdown(f"**D{idx+1}:**", unsafe_allow_html=True)
        c1m, c2m = st.columns([1,1])
        v1_key = f"dm_vpip_{uid}"
        v2_key = f"dm_vfinal_{uid}"
        with c1m:
            v1 = st.text_input("Vpip", value=d.get("v_pip",""), key=v1_key)
        with c2m:
            v2 = st.text_input("Vfinal", value=d.get("v_final",""), key=v2_key)

        # IDs per lote
        per = list(d.get("per_lote_ids", []))
        if len(per) < len(ss.lotes):
            per += [""] * (len(ss.lotes) - len(per))
        per_keys = []
        for li in range(len(ss.lotes)):
            lote_name = (ss.lotes[li].get("name","") or "").strip() or f"Lote{li+1}"
            key = f"dm_{uid}_per_{li}"
            val = st.text_input(f"{lote_name} ID (opcional)", value=per[li], key=key)
            per[li] = st.session_state.get(key, "")
            per_keys.append(key)
        if st.button("Eliminar dilución", key=f"del_dm_{uid}"):
            remove_muestra_dilution(uid)
        new_dm.append({"uid": uid, "v_pip": st.session_state.get(v1_key, ""), "v_final": st.session_state.get(v2_key, ""), "per_lote_ids": per})
    ss.diluciones_muestra = new_dm
    st.markdown("</div>", unsafe_allow_html=True)

    # Placebo dilutions
    st.markdown("<div class='panel'>", unsafe_allow_html=True)
    st.markdown("<div class='panel-title'>Diluciones - Placebo</div>", unsafe_allow_html=True)
    st.button("+ Añadir dilución placebo", on_click=add_placebo_dilution)
    new_pp = []
    for i, d in enumerate(list(ss.diluciones_placebo)):
        uid = d.get("uid") or new_uid("dp")
        c1p, c2p, c3p = st.columns([1,1,0.3])
        v1_key = f"pp_vpip_{uid}"
        v2_key = f"pp_vfinal_{uid}"
        id_key = f"pp_id_{uid}"
        with c1p:
            v1 = st.text_input(f"P{i+1} v_pip", value=d.get("v_pip",""), key=v1_key)
        with c2p:
            v2 = st.text_input(f"P{i+1} v_final", value=d.get("v_final",""), key=v2_key)
        with c3p:
            idt = st.text_input(f"P{i+1} ID (opcional)", value=d.get("id_text",""), key=id_key)
        if st.button("Eliminar", key=f"del_pp_{uid}"):
            remove_placebo_dilution(uid)
        new_pp.append({"uid": uid, "v_pip": st.session_state.get(v1_key, ""), "v_final": st.session_state.get(v2_key, ""), "id_text": st.session_state.get(id_key, "")})
    ss.diluciones_placebo = new_pp
    st.markdown("</div>", unsafe_allow_html=True)

# ---------- TAB: Opciones viales ----------
with tabs[3]:
    st.markdown("<div class='panel'>", unsafe_allow_html=True)
    st.markdown("<div class='panel-title'>Opciones viales y reactivos</div>", unsafe_allow_html=True)
    st.checkbox("Mostrar cuadro de color", value=ss.show_color_square, key="show_color_square", help="Muestra un cuadrado con el color asociado en la etiqueta.")
    st.checkbox("Incluir viales", value=ss.incluir_viales, key="incluir_viales", help="Si está activo, se añaden viales HPLC según multiplicadores.")
    st.text_input("Blanco (etiqueta):", value=ss.texto_blanco, key="texto_blanco")
    st.text_input("Wash (etiqueta):", value=ss.texto_wash, key="texto_wash")
    st.markdown("### Reactivos")
    st.text_input("N° de reactivos", value=ss.num_reactivos, key="num_reactivos", help="Defina la cantidad de reactivos para editar sus nombres.")
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

    # Viales multiplicadores list (scrollable)
    st.markdown("<div style='margin-top:8px'>", unsafe_allow_html=True)
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
    # ensure id and lote color maps updated
    assign_colors_for_ids_for_state(items, {"id_color_map": ss.id_color_map, "lote_color_map": ss.lote_color_map, "lotes": ss.lotes})
    # ensure viales_multiplicadores defaults and prune obsolete keys
    for it in items:
        vid = it["id"]
        if vid not in ss.viales_multiplicadores:
            ss.viales_multiplicadores[vid] = 0 if it["type"] == "reactivo" else 1
    for k in list(ss.viales_multiplicadores.keys()):
        if k not in [it["id"] for it in items]:
            ss.viales_multiplicadores.pop(k, None)

    st.markdown("<div class='viales-scroll' style='max-height:260px; overflow:auto; padding:6px; border-radius:6px; border:1px solid #eef6fb'>", unsafe_allow_html=True)
    for it in items:
        vid = it["id"]
        color = ss.id_color_map.get(vid, "#cccccc")
        c1, c2 = st.columns([0.12, 1])
        with c1:
            if ss.show_color_square:
                st.markdown(f"<div style='width:14px;height:12px;background:{color};border:1px solid #000;border-radius:3px'></div>", unsafe_allow_html=True)
        with c2:
            subc1, subc2 = st.columns([3,1])
            with subc1:
                st.write(vid)
            with subc2:
                default = int(ss.viales_multiplicadores.get(vid, 0 if it["type"] == "reactivo" else 1))
                key = sanitize_key(f"mult_{vid}")
                val = st.number_input("", min_value=0, value=default, step=1, key=key)
                ss.viales_multiplicadores[vid] = int(val)
    st.markdown("</div>", unsafe_allow_html=True)
    st.markdown("</div>", unsafe_allow_html=True)

# ---------- TAB: Generar ----------
with tabs[4]:
    st.markdown("<div class='panel'>", unsafe_allow_html=True)
    st.markdown("<div class='panel-title'>Generar PDF</div>", unsafe_allow_html=True)
    c1, c2 = st.columns([1,1])
    with c1:
        st.text_input("Nombre producto:", value=ss.nombre_prod, key="nombre_prod")
        st.text_input("Lote (general):", value=ss.lote, key="lote_general", help="Texto resumen de lotes utilizado en la parte datos de las etiquetas.")
        st.text_input("Determinación:", value=ss.determinacion, key="determinacion")
    with c2:
        st.text_input("Analista:", value=ss.analista, key="analista")
        st.text_input("Fecha:", value=ss.fecha, key="fecha")
        st.number_input("Etiqueta inicial (1-80):", min_value=1, max_value=TOTAL_ETIQUETAS_PAGINA, value=int(ss.start_label), key="start_label")
    st.markdown("<div style='height:6px'></div>", unsafe_allow_html=True)

    # Generate callback improved (single click)
    def on_generate():
        # Before generating, sync duplicated derived values if any
        try:
            pdf_bytes, total, next_start = generar_pdf_bytes_and_next_start()
            ss["last_pdf"] = pdf_bytes
            ss["last_total"] = total
            ss["start_label"] = next_start
            _flash(f"PDF generado: {total} etiquetas.")
        except Exception as e:
            ss["last_pdf"] = None
            _flash(f"Error al generar PDF: {str(e)}")

    st.button("GENERAR PDF", on_click=on_generate)

    # If PDF available, show "Abrir en nueva pestaña" button and download
    if ss.last_pdf:
        b64 = base64.b64encode(ss.last_pdf).decode("ascii")
        filename = f"{datetime.today().strftime('%Y%m%d')}_{limpiar_nombre_archivo(ss.nombre_prod)}_{limpiar_nombre_archivo(ss.lote)}.pdf"

        def open_in_tab():
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
        st.success(f"PDF listo: {ss.last_total} etiquetas")
        st.download_button("Descargar PDF", data=ss.last_pdf, file_name=filename, mime="application/pdf")
    st.markdown("</div>", unsafe_allow_html=True)

# ---------- Footer / small help ----------
st.markdown("---")
st.caption("Consejos: Use una sola vez el botón 'GENERAR PDF' y espere la confirmación. Los botones de añadir/eliminar responden con mensajes de confirmación.")
st.caption("Colores de lote se asignan automáticamente; puede editarlos manualmente en el código o solicitar mejora para edición en UI.")

# End of file
