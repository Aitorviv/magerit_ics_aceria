"""
Análisis de Riesgos Industrial
Metodología MAGERIT adaptada a entornos OT/ICS
"""

import streamlit as st
import pandas as pd
import sqlite3
import plotly.graph_objects as go
import plotly.express as px
import base64, os, math, io
from datetime import datetime
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import cm
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table,
    TableStyle, HRFlowable, PageBreak, KeepTogether)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

st.set_page_config(
    page_title="MAGERIT OT/ICS",
    page_icon="",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ══════════════════════════════════════════════════════════════
# BASE DE DATOS
# ══════════════════════════════════════════════════════════════
DB_PATH = "magerit_ot_v2.db"

def get_conn():
    return sqlite3.connect(DB_PATH, check_same_thread=False)

def init_db():
    conn = get_conn()
    c = conn.cursor()

    c.execute("""CREATE TABLE IF NOT EXISTS activos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nombre TEXT NOT NULL,
        tipo TEXT,
        subtipo TEXT,
        descripcion TEXT,
        seccion_aceria TEXT,
        zona_purdue INTEGER DEFAULT 2,
        es_sis INTEGER DEFAULT 0,
        -- Disponibilidad (degradación por tiempo de indisponibilidad)
        disp_15m INTEGER DEFAULT 0,
        disp_3h  INTEGER DEFAULT 0,
        disp_1d  INTEGER DEFAULT 0,
        disp_1s  INTEGER DEFAULT 0,
        disp_2s  INTEGER DEFAULT 0,
        -- Confidencialidad
        conf_ri INTEGER DEFAULT 0,
        conf_re INTEGER DEFAULT 0,
        -- Integridad
        int_dt  INTEGER DEFAULT 0,
        int_err INTEGER DEFAULT 0,
        int_man INTEGER DEFAULT 0,
        int_aut INTEGER DEFAULT 0,
        -- Dimensiones OT adicionales
        ot_safety_sil     INTEGER DEFAULT 0,
        ot_continuidad    INTEGER DEFAULT 0,
        ot_integ_fisica   INTEGER DEFAULT 0,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )""")

    c.execute("""CREATE TABLE IF NOT EXISTS dependencias (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        activo_hijo_id INTEGER,
        activo_padre_id INTEGER,
        FOREIGN KEY(activo_hijo_id) REFERENCES activos(id),
        FOREIGN KEY(activo_padre_id) REFERENCES activos(id)
    )""")

    c.execute("""CREATE TABLE IF NOT EXISTS evaluacion_riesgos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        activo_id INTEGER,
        amenaza_codigo TEXT,
        amenaza_nombre TEXT,
        amenaza_categoria TEXT,
        valor_amenaza INTEGER DEFAULT 1,
        -- Riesgos por dimensión: R = V_activo_dim × Prob_amenaza_dim × multiplicador_OT
        riesgo_D  REAL DEFAULT 0,
        riesgo_C  REAL DEFAULT 0,
        riesgo_I  REAL DEFAULT 0,
        riesgo_safety      REAL DEFAULT 0,
        riesgo_continuidad REAL DEFAULT 0,
        riesgo_integ_fisica REAL DEFAULT 0,
        riesgo_potencial   REAL DEFAULT 0,
        nivel_riesgo TEXT DEFAULT 'Trivial',
        multiplicador_ot   REAL DEFAULT 1.0,
        notas TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(activo_id) REFERENCES activos(id)
    )""")

    c.execute("""CREATE TABLE IF NOT EXISTS salvaguardas (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        evaluacion_id INTEGER,
        nombre TEXT,
        capa TEXT,
        estado_cmm INTEGER DEFAULT 0,
        eficacia_objetivo REAL DEFAULT 0.25,
        ctrl_iso27002 TEXT,
        ctrl_iec62443 TEXT,
        afecta_D INTEGER DEFAULT 0,
        afecta_C INTEGER DEFAULT 0,
        afecta_I INTEGER DEFAULT 0,
        afecta_safety INTEGER DEFAULT 0,
        notas TEXT,
        FOREIGN KEY(evaluacion_id) REFERENCES evaluacion_riesgos(id)
    )""")

    c.execute("""CREATE TABLE IF NOT EXISTS evidencias (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        activo_id INTEGER NOT NULL,
        nombre_fichero TEXT NOT NULL,
        tipo_fichero TEXT,
        descripcion TEXT,
        datos BLOB NOT NULL,
        tamanio INTEGER,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(activo_id) REFERENCES activos(id)
    )""")

    conn.commit()
    conn.close()

# ══════════════════════════════════════════════════════════════
# CATÁLOGOS OT
# ══════════════════════════════════════════════════════════════

TIPOS_ACTIVO_OT = {
    "PLC": {
        "descripcion": "PLC o autómata Siemens STEP7 o TIA Portal",
        "subtipos": ["Siemens S7-300", "Siemens S7-400", "Siemens S7-1500", "Siemens S7-1200", "TIA Portal CPU", "Autómata programable"],
        "es_sis_tipico": False,
    },
    "HERAEUS": {
        "descripcion": "Equipo embebido para medición de temperatura",
        "subtipos": ["Heraeus Electro-Nite sublanza", "Heraeus medición continua", "Pirómetro embebido"],
        "es_sis_tipico": False,
    },
    "TP": {
        "descripcion": "Panel táctil HMI embebido",
        "subtipos": ["Siemens TP700", "Siemens TP900", "Siemens TP1200", "Siemens TP1500", "Panel KTP"],
        "es_sis_tipico": False,
    },
    "PC HMI": {
        "descripcion": "PC SCADA o HMI mandos y manejo instalación",
        "subtipos": ["PC SCADA operador", "PC HMI sala control", "PC mandos instalación", "Puesto operador"],
        "es_sis_tipico": False,
    },
    "PC REG": {
        "descripcion": "PC regulación electrodos embebidos",
        "subtipos": ["PC regulación electrodos EAF", "PC control arco eléctrico", "PC regulación proceso"],
        "es_sis_tipico": False,
    },
    "RTU": {
        "descripcion": "Unidad terminal remota para recibir señales equipos subestación",
        "subtipos": ["RTU subestación", "RTU campo señales", "RTU medida energía", "IED subestación"],
        "es_sis_tipico": False,
    },
    "PC S7": {
        "descripcion": "PC programación PLC o autómatas, TP, variadores, etc.",
        "subtipos": ["EWS TIA Portal", "EWS STEP7", "PC programación variadores", "Laptop programación OT"],
        "es_sis_tipico": False,
    },
    "PC PRO": {
        "descripcion": "PC producción para correos, SDA, etc.",
        "subtipos": ["PC producción oficina", "PC sistema SDA", "PC gestión producción"],
        "es_sis_tipico": False,
    },
    "PC NAS": {
        "descripcion": "Servidores almacenamiento NAS",
        "subtipos": ["NAS producción", "NAS backup OT", "NAS almacenamiento datos proceso"],
        "es_sis_tipico": False,
    },
    "COMM": {
        "descripcion": "Equipos y electrónica de comunicaciones",
        "subtipos": ["Switch OT gestionado", "Switch Hirschmann", "Router OT", "Firewall IT/OT", "Gateway OT", "Convertidor protocolo"],
        "es_sis_tipico": False,
    },
    "BERTHOLD": {
        "descripcion": "Equipos electrónicos para medir el nivel de acero en molde en las coladas",
        "subtipos": ["Berthold LB 350", "Sensor nivel radiométrico colada", "Medidor nivel acero molde"],
        "es_sis_tipico": True,
    },
    "PLETINA": {
        "descripcion": "Pletina o terminal modelos Axel con Citrix, VNC o RDP",
        "subtipos": ["Terminal Axel thin client", "Terminal Citrix", "Terminal VNC/RDP", "Thin client producción"],
        "es_sis_tipico": False,
    },
    "ANALIZADOR": {
        "descripcion": "Analizador de red Siemens PAC Sentron para energía",
        "subtipos": ["Siemens PAC3200", "Siemens PAC4200", "Siemens PAC Sentron", "Analizador de red energía"],
        "es_sis_tipico": False,
    },
    "OTROS EQUIPOS": {
        "descripcion": "Otros equipos muy específicos o no prioritarios para producir",
        "subtipos": ["Equipo específico proceso", "Dispositivo IoT OT", "Equipo auxiliar producción"],
        "es_sis_tipico": False,
    },
    "UPS": {
        "descripcion": "UPS para proteger de cortes de alimentación",
        "subtipos": ["UPS industrial rack", "UPS sala control", "UPS CPD OT", "SAI industrial"],
        "es_sis_tipico": False,
    },
    "ZIV": {
        "descripcion": "ZIV protecciones subestación",
        "subtipos": ["ZIV relé protección", "ZIV IED protección diferencial", "ZIV protección distancia", "IED protección subestación"],
        "es_sis_tipico": True,
    },
    "CCTV PC": {
        "descripcion": "PC con software para mantenimiento de switch, cámara y NVR de los sistemas CCTV de producción",
        "subtipos": ["PC gestión CCTV", "PC mantenimiento NVR", "PC administración cámaras"],
        "es_sis_tipico": False,
    },
    "CCTV NVR": {
        "descripcion": "Network Video Record: visualizar cámaras en monitores",
        "subtipos": ["NVR Hikvision", "NVR Axis", "Servidor grabación vídeo", "NVR producción"],
        "es_sis_tipico": False,
    },
    "CCTV CAM": {
        "descripcion": "Cámaras de producción",
        "subtipos": ["Cámara IP fija producción", "Cámara PTZ área proceso", "Cámara térmica EAF", "Cámara colada"],
        "es_sis_tipico": False,
    },
    "CCTV SWI": {
        "descripcion": "Switch HP Aruba para cámaras y NVR",
        "subtipos": ["Switch HP Aruba CCTV", "Switch red cámaras", "Switch PoE cámaras"],
        "es_sis_tipico": False,
    },
    "PC PORTATIL": {
        "descripcion": "PC portátil de mantenimiento para programar PLC o autómatas en modo local",
        "subtipos": ["Laptop Siemens PG", "Laptop mantenimiento OT", "Laptop programación campo", "PC portátil TIA Portal"],
        "es_sis_tipico": False,
    },
}

# Lista plana de tipos para selectbox
TIPOS_LISTA = list(TIPOS_ACTIVO_OT.keys())

SECCIONES_ACERIA = [
    "SUBESTACION",
    "MCENTRAL",
    "HORNO",
    "AFINO",
    "COLADA CONTINUA",
    "COLADA BLOOM",
    "AUXILIARES",
    "TALLER ELECTRICO",
    "DEP. INFORMATICA",
]

ZONAS_PURDUE = {
    0: "Nivel 0 — Campo (sensores/actuadores)",
    1: "Nivel 1 — Control (PLCs/RTUs)",
    2: "Nivel 2 — Supervisión (SCADA/HMI)",
    3: "Nivel 3 — Gestión Operaciones (MES)",
    4: "DMZ Industrial",
}

# Multiplicador OT por zona Purdue + SIS
def multiplicador_ot(zona_purdue: int, es_sis: bool) -> float:
    base = {0: 1.4, 1: 1.25, 2: 1.1, 3: 1.0, 4: 1.0}.get(zona_purdue, 1.0)
    return round(base * (1.5 if es_sis else 1.0), 2)

# CMM → factor de reducción real
CMM_FACTOR = {0: 0.0, 1: 0.25, 2: 0.5, 3: 0.9, 4: 1.0}
CMM_LABELS = {0: "0-Sin implantar", 1: "1-Inicial", 2: "2-Definido", 3: "3-Implantado", 4: "4-Evaluado"}

CAPAS_SALVAGUARDA = ["Prevención", "Detección", "Represión", "Corrección", "Recuperación", "Evaluación"]
# ══════════════════════════════════════════════════════════════
# SECURITY LEVELS IEC 62443-4-2
# ══════════════════════════════════════════════════════════════
CR_SL_MINIMO = {
    # CR 1 — IAC: Identificación y Control de Acceso
    # Fuente: Secciones "Niveles de seguridad" UNE-EN IEC 62443-4-2
    "CR 1.1": 1,  # SL1-4: obligatorio en todos
    "CR 1.2": 2,  # SL1: No seleccionado
    "CR 1.3": 1,  # SL1-4: obligatorio en todos
    "CR 1.4": 1,  # SL1-4: obligatorio en todos
    "CR 1.5": 1,  # SL1-4: obligatorio en todos (RE(1) desde SL3)
    "CR 1.6": 2,  # SL1: No seleccionado
    "CR 1.7": 1,  # SL1-4: obligatorio (RE(1) SL3+, RE(2) SL4)
    "CR 1.8": 2,  # SL1: No seleccionado
    "CR 1.9": 2,  # SL1: No seleccionado
    "CR 1.10": 1, # SL1-4: obligatorio en todos
    "CR 1.11": 1, # SL1-4: obligatorio en todos
    "CR 1.12": 1, # SL1-4: obligatorio en todos
    "CR 1.13": 2, # SL1: No seleccionado
    "CR 1.14": 2, # SL1: No seleccionado
    # CR 2 — UC: Uso y Control
    "CR 2.1": 1,  # SL1-4: obligatorio (RE adicionales en SL2/3/4)
    "CR 2.2": 1,  # SL1-4: obligatorio en todos
    "CR 2.3": 1,  # SL1-4: obligatorio en todos
    "CR 2.4": 1,  # SL1-4: obligatorio en todos
    "CR 2.5": 1,  # SL1-4: obligatorio en todos
    "CR 2.6": 2,  # SL1: No seleccionado
    "CR 2.7": 3,  # SL1-2: No seleccionado
    "CR 2.8": 1,  # SL1-4: obligatorio en todos
    "CR 2.9": 1,  # SL1-4: obligatorio en todos
    "CR 2.10": 1, # SL1-4: obligatorio en todos
    "CR 2.11": 1, # SL1-4: obligatorio (RE(1) SL3+, RE(2) SL4)
    "CR 2.12": 1, # SL1-4: obligatorio (RE(1) desde SL4)
    "CR 2.13": 2, # SL1: No seleccionado
    # CR 3 — SI: Integridad del Sistema
    "CR 3.1": 1,  # SL1-4: obligatorio en todos
    "CR 3.2": 2,  # SL1: No seleccionado
    "CR 3.3": 1,  # SL1-4: obligatorio (RE(1) desde SL4)
    "CR 3.4": 1,  # SL1-4: obligatorio en todos
    "CR 3.5": 1,  # SL1-4: obligatorio en todos
    "CR 3.6": 1,  # SL1-4: obligatorio en todos
    "CR 3.7": 2,  # SL1: No seleccionado
    "CR 3.8": 2,  # SL1: No seleccionado
    "CR 3.9": 2,  # SL1: No seleccionado (RE(1) desde SL4)
    "CR 3.10": 1, # SL1-4: obligatorio en todos
    "CR 3.11": 3, # SL1-2: No seleccionado
    "CR 3.12": 3, # SL1-2: No seleccionado
    "CR 3.13": 3, # SL1-2: No seleccionado
    "CR 3.14": 3, # SL1-2: No seleccionado
    # CR 4 — DC: Confidencialidad de Datos
    "CR 4.1": 1,  # SL1-4: obligatorio en todos
    "CR 4.2": 2,  # SL1: No seleccionado
    "CR 4.3": 1,  # SL1-4: obligatorio en todos
    # CR 5 — DM: Gestión del Flujo de Datos
    "CR 5.1": 1,  # SL1-4: obligatorio en todos
    "CR 5.2": 1,  # SL1-4: obligatorio en todos
    "CR 5.3": 2,  # SL1: No seleccionado
    "CR 5.4": 2,  # SL1: No seleccionado
    # CR 6 — ER: Respuesta a Eventos
    "CR 6.1": 1,  # SL1-4: obligatorio en todos
    "CR 6.2": 2,  # SL1: No seleccionado
    # CR 7 — RM: Gestión de Recursos
    "CR 7.1": 1,  # SL1-4: obligatorio en todos
    "CR 7.2": 1,  # SL1-4: obligatorio en todos
    "CR 7.3": 1,  # SL1-4: obligatorio en todos
    "CR 7.4": 1,  # SL1-4: obligatorio en todos
    "CR 7.5": 1,  # SL1-4: obligatorio en todos
    "CR 7.6": 1,  # SL1-4: obligatorio en todos
    "CR 7.7": 1,  # SL1-4: obligatorio en todos
    "CR 7.8": 2,  # SL1: No seleccionado
}

# CRs donde SL4 añade Mejoras de Requisitos (RE) adicionales vs SL3
# IEC 62443-4-2 secciones individuales
CR_SL4_RE_EXTRA = {
    "CR 1.7":  "RE(2): Restricciones de vigencia de contraseña para todos los usuarios",
    "CR 2.1":  "RE(4): Control de permisos a nivel de máquina de estados de dispositivo",
    "CR 2.11": "RE(2): Sincronización de tiempo con fuente externa de alta precisión",
    "CR 2.12": "RE(1): No rechazo con mecanismos criptográficos fuertes",
    "CR 3.3":  "RE(1): Verificación automatizada de integridad en tiempo de ejecución",
    "CR 3.9":  "RE(1): Protección criptográfica del registro de auditoría",
}

# SL objetivo por ubicación Sidenor
# Basado en criticidad del proceso y nivel de amenaza
SL_OBJETIVO_UBICACION = {
    "HORNO":           3,  # EAF — proceso crítico, riesgo físico alto
    "COLADA CONTINUA": 3,  # Colada — breakthrough, riesgo vital
    "COLADA BLOOM":    3,  # Similar colada continua
    "AFINO":           3,  # Metalurgia secundaria, gases, temperatura
    "SUBESTACION":     3,  # Energía crítica — IED/ZIV, protecciones
    "MCENTRAL":        2,  # Sala control — supervisión, menor riesgo físico
    "AUXILIARES":      2,  # Servicios apoyo
    "TALLER ELECTRICO":2,  # Mantenimiento eléctrico
    "DEP. INFORMATICA":1,  # IT/OT gestionado, menor riesgo proceso
}

SL_LABELS = {
    1: "SL 1 — Básico (error accidental / amenaza casual)",
    2: "SL 2 — Medio (atacante con recursos simples)",
    3: "SL 3 — Alto (atacante sofisticado con conocimiento OT)",
    4: "SL 4 — Crítico (actor estado-nación, recursos ilimitados)",
}
SL_COLORS = {1: "#22c55e", 2: "#eab308", 3: "#f59e0b", 4: "#ef4444"}
SL_ICONS  = {1: "🟢", 2: "🟡", 3: "🟠", 4: "🔴"}


# Umbrales MAGERIT
def nivel_riesgo(r: float) -> str:
    if r >= 15: return "Crítico"
    if r >= 4:  return "Alto"
    if r >= 3:  return "Medio"
    return "Trivial"

def color_nivel(nivel: str) -> str:
    return {"Crítico": "#ef4444", "Alto": "#f59e0b", "Medio": "#eab308", "Trivial": "#22c55e"}.get(nivel, "#64748b")

# ══════════════════════════════════════════════════════════════
# CATÁLOGO AMENAZAS OT/ICS
# ══════════════════════════════════════════════════════════════
AMENAZAS_OT = [
    # ── Amenazas físicas / entorno industrial ──
    {"codigo": "F.01", "nombre": "Fuego en sala control / CPD OT", "categoria": "Desastres Naturales", "fuente": "ISO27005",
     "disp": [5,5,5,5,5], "conf": [0,0], "integ": [5,0,0,0], "safety": 3, "cont": 4, "fisic": 2},
    {"codigo": "F.02", "nombre": "Incendio en área de producción (Horno/Colada/Afino)", "categoria": "Desastres Naturales", "fuente": "ISO27005",
     "disp": [5,5,5,5,5], "conf": [0,0], "integ": [3,0,0,0], "safety": 5, "cont": 5, "fisic": 5},
    {"codigo": "F.03", "nombre": "Corte de energía eléctrica (subestación)", "categoria": "Desastres Naturales", "fuente": "ISO27005",
     "disp": [5,5,3,0,0], "conf": [0,0], "integ": [0,0,0,0], "safety": 4, "cont": 5, "fisic": 2},
    {"codigo": "F.04", "nombre": "Fallo de climatización CPD OT", "categoria": "Fallos Técnicos", "fuente": "ISO27005",
     "disp": [0,5,4,0,0], "conf": [0,0], "integ": [0,0,0,0], "safety": 1, "cont": 3, "fisic": 0},
    {"codigo": "F.05", "nombre": "Inundación instalaciones industriales", "categoria": "Desastres Naturales", "fuente": "ISO27005",
     "disp": [3,2,1,0,0], "conf": [0,0], "integ": [1,0,0,0], "safety": 3, "cont": 4, "fisic": 3},
    # ── Errores humanos OT ──
    {"codigo": "E.01", "nombre": "Error configuración PLC/RTU", "categoria": "Errores Humanos", "fuente": "ISO27005",
     "disp": [3,3,0,0,0], "conf": [0,0], "integ": [3,4,3,3], "safety": 3, "cont": 4, "fisic": 2},
    {"codigo": "E.02", "nombre": "Error mantenimiento firmware OT", "categoria": "Errores Humanos", "fuente": "ISO27005",
     "disp": [5,3,3,0,0], "conf": [0,0], "integ": [0,3,0,0], "safety": 2, "cont": 3, "fisic": 1},
    {"codigo": "E.03", "nombre": "Error operador sala control", "categoria": "Errores Humanos", "fuente": "ISO27005",
     "disp": [3,2,2,0,0], "conf": [0,0], "integ": [3,3,0,0], "safety": 3, "cont": 4, "fisic": 2},
    {"codigo": "E.04", "nombre": "Error configuración red OT (VLAN/firewall)", "categoria": "Errores Humanos", "fuente": "ISO27005",
     "disp": [3,3,0,0,0], "conf": [3,3], "integ": [0,0,0,0], "safety": 1, "cont": 2, "fisic": 0},
    {"codigo": "E.05", "nombre": "Uso incorrecto acceso remoto OT", "categoria": "Errores Humanos", "fuente": "ISO27005",
     "disp": [0,0,0,0,0], "conf": [5,0], "integ": [3,0,3,3], "safety": 2, "cont": 2, "fisic": 1},
    # ── Fallos técnicos OT ──
    {"codigo": "T.01", "nombre": "Fallo hardware PLC (CPU/módulo I/O)", "categoria": "Fallos Técnicos", "fuente": "ISO27005",
     "disp": [5,5,3,0,0], "conf": [0,0], "integ": [0,3,0,0], "safety": 4, "cont": 5, "fisic": 2},
    {"codigo": "T.02", "nombre": "Fallo comunicaciones bus de campo (Profibus/EtherNet/IP)", "categoria": "Fallos Técnicos", "fuente": "ISO27005",
     "disp": [5,3,1,0,0], "conf": [0,0], "integ": [0,1,0,0], "safety": 3, "cont": 4, "fisic": 1},
    {"codigo": "T.03", "nombre": "Fallo switch industrial / red OT", "categoria": "Fallos Técnicos", "fuente": "ISO27005",
     "disp": [3,3,0,0,0], "conf": [0,0], "integ": [0,0,0,0], "safety": 2, "cont": 3, "fisic": 0},
    {"codigo": "T.04", "nombre": "Fallo sensor crítico (temperatura EAF > 3000°C)", "categoria": "Fallos Técnicos", "fuente": "ISO27005",
     "disp": [0,0,0,0,0], "conf": [0,0], "integ": [5,3,0,0], "safety": 5, "cont": 4, "fisic": 4},
    {"codigo": "T.05", "nombre": "Corrupción firmware PLC/RTU", "categoria": "Fallos Técnicos", "fuente": "ISO27005",
     "disp": [5,5,5,0,0], "conf": [0,0], "integ": [5,5,5,5], "safety": 4, "cont": 5, "fisic": 3},
    # ── Ciberataques OT/ICS ──
    {"codigo": "C.01", "nombre": "Malware ICS tipo Stuxnet (ataque firmware PLC)", "categoria": "Ciberataques ICS", "fuente": "ICS-CERT",
     "disp": [5,5,5,3,0], "conf": [0,4], "integ": [5,5,5,5], "safety": 5, "cont": 5, "fisic": 5},
    {"codigo": "C.02", "nombre": "Ransomware OT (cifrado SCADA/Historian)", "categoria": "Ciberataques ICS", "fuente": "ICS-CERT",
     "disp": [5,5,5,5,5], "conf": [0,5], "integ": [5,0,0,0], "safety": 3, "cont": 5, "fisic": 2},
    {"codigo": "C.03", "nombre": "Movimiento lateral IT→OT (pivoting a red industrial)", "categoria": "Ciberataques ICS", "fuente": "MITRE ATT&CK ICS",
     "disp": [3,3,3,0,0], "conf": [5,5], "integ": [3,3,5,5], "safety": 4, "cont": 4, "fisic": 3},
    {"codigo": "C.04", "nombre": "Ataque DoS a red OT / bus de campo", "categoria": "Ciberataques ICS", "fuente": "ICS-CERT",
     "disp": [5,5,3,1,0], "conf": [0,0], "integ": [0,0,0,0], "safety": 4, "cont": 5, "fisic": 3},
    {"codigo": "C.05", "nombre": "Suplantación protocolo industrial (Modbus/DNP3 spoofing)", "categoria": "Ciberataques ICS", "fuente": "MITRE ATT&CK ICS",
     "disp": [0,0,0,0,0], "conf": [3,4], "integ": [5,3,5,5], "safety": 5, "cont": 4, "fisic": 5},
    {"codigo": "C.06", "nombre": "Compromiso EWS (Engineering Workstation)", "categoria": "Ciberataques ICS", "fuente": "MITRE ATT&CK ICS",
     "disp": [3,3,0,0,0], "conf": [4,5], "integ": [5,5,5,5], "safety": 4, "cont": 4, "fisic": 3},
    {"codigo": "C.07", "nombre": "Acceso físico no autorizado a sala control / CPD OT", "categoria": "Ciberataques ICS", "fuente": "ISO27005",
     "disp": [3,0,0,0,0], "conf": [0,5], "integ": [0,0,5,5], "safety": 3, "cont": 3, "fisic": 4},
    {"codigo": "C.08", "nombre": "USB malicioso en activo OT (Stuxnet vector)", "categoria": "Ciberataques ICS", "fuente": "ICS-CERT",
     "disp": [5,5,3,0,0], "conf": [0,4], "integ": [5,5,5,5], "safety": 5, "cont": 5, "fisic": 3},
    {"codigo": "C.09", "nombre": "Interceptación comunicaciones OT (MitM Profinet)", "categoria": "Ciberataques ICS", "fuente": "MITRE ATT&CK ICS",
     "disp": [0,0,0,0,0], "conf": [5,5], "integ": [0,0,5,5], "safety": 3, "cont": 3, "fisic": 2},
    {"codigo": "C.10", "nombre": "Ataque supply chain firmware (componente malicioso)", "categoria": "Ciberataques ICS", "fuente": "ICS-CERT",
     "disp": [3,3,3,3,0], "conf": [3,5], "integ": [5,5,5,5], "safety": 5, "cont": 4, "fisic": 4},
    {"codigo": "C.11", "nombre": "Compromiso acceso remoto OT (VPN/TeamViewer industrial)", "categoria": "Ciberataques ICS", "fuente": "MITRE ATT&CK ICS",
     "disp": [0,0,0,0,0], "conf": [5,5], "integ": [3,3,5,5], "safety": 4, "cont": 4, "fisic": 3},
    {"codigo": "C.12", "nombre": "Denegación servicio SIS (bypass sistema seguridad)", "categoria": "Ciberataques ICS", "fuente": "ICS-CERT",
     "disp": [5,5,5,5,5], "conf": [0,0], "integ": [5,5,5,5], "safety": 5, "cont": 5, "fisic": 5},
    # ── Amenazas específicas acería ──
    {"codigo": "A.01", "nombre": "Manipulación setpoints Horno EAF (temperatura/electrodos)", "categoria": "Amenazas Específicas Acería", "fuente": "ICS-CERT",
     "disp": [0,0,0,0,0], "conf": [0,0], "integ": [5,5,5,5], "safety": 5, "cont": 5, "fisic": 5},
    {"codigo": "A.02", "nombre": "Alteración velocidad colada continua (0.2-2.0 m/min)", "categoria": "Amenazas Específicas Acería", "fuente": "ICS-CERT",
     "disp": [0,0,0,0,0], "conf": [0,0], "integ": [5,5,5,5], "safety": 5, "cont": 5, "fisic": 5},
    {"codigo": "A.03", "nombre": "Fallo sistema enfriamiento molde colada (1200-1400°C)", "categoria": "Amenazas Específicas Acería", "fuente": "ISO27005",
     "disp": [5,5,0,0,0], "conf": [0,0], "integ": [5,3,0,0], "safety": 5, "cont": 5, "fisic": 5},
    {"codigo": "A.04", "nombre": "Compromiso CEMS (medición emisiones atmosféricas)", "categoria": "Amenazas Específicas Acería", "fuente": "INCIDENCIA",
     "disp": [0,0,0,0,0], "conf": [3,5], "integ": [5,3,5,5], "safety": 2, "cont": 2, "fisic": 1},
    {"codigo": "A.05", "nombre": "Fallo sistema detección desgarre molde (breakthrough)", "categoria": "Amenazas Específicas Acería", "fuente": "ISO27005",
     "disp": [0,0,0,0,0], "conf": [0,0], "integ": [5,5,0,0], "safety": 5, "cont": 5, "fisic": 5},
    # ── Amenazas normativas / continuidad ──
    {"codigo": "N.01", "nombre": "Incumplimiento NIS2 (falta de notificación incidente)", "categoria": "Normativas / Legal", "fuente": "NIS2",
     "disp": [0,0,0,0,0], "conf": [0,3], "integ": [0,0,0,3], "safety": 0, "cont": 1, "fisic": 0},
    {"codigo": "N.02", "nombre": "Falta de personal técnico OT (pandemia/huelga)", "categoria": "Normativas / Legal", "fuente": "INCIDENCIA",
     "disp": [3,4,3,2,1], "conf": [0,0], "integ": [0,0,0,0], "safety": 2, "cont": 4, "fisic": 1},
]

# ══════════════════════════════════════════════════════════════
# CATÁLOGO IEC 62443-4-2 — Controles
# ══════════════════════════════════════════════════════════════
IEC62443_CONTROLES = {
    "CR 1 — IAC: Identificación y Control de Acceso": [
        ("CR 1.1",  "Identificación y autenticación de usuarios humanos",              "Preventiva"),
        ("CR 1.2",  "Identificación y autenticación de procesos de software y dispositivos", "Preventiva"),
        ("CR 1.3",  "Gestión de cuentas",                                              "Preventiva"),
        ("CR 1.4",  "Gestión de identificadores",                                      "Preventiva"),
        ("CR 1.5",  "Gestión de autenticadores",                                       "Preventiva"),
        ("CR 1.6",  "Gestión de acceso inalámbrico",                                   "Preventiva"),
        ("CR 1.7",  "Fortaleza de la autenticación basada en contraseña",              "Preventiva"),
        ("CR 1.8",  "Certificados de infraestructura de clave pública",                "Preventiva"),
        ("CR 1.9",  "Fortaleza de la autenticación basada en clave pública",           "Preventiva"),
        ("CR 1.10", "Retroalimentación del autenticador",                              "Preventiva"),
        ("CR 1.11", "Intentos fallidos de inicio de sesión",                           "Preventiva"),
        ("CR 1.12", "Notificación de uso del sistema",                                 "Preventiva"),
        ("CR 1.13", "Acceso a través de redes que no son de confianza",                "Preventiva"),
        ("CR 1.14", "Fortaleza de la autenticación basada en clave simétrica",         "Preventiva"),
    ],
    "CR 2 — UC: Uso y Control": [
        ("CR 2.1",  "Aplicación de la autorización",                                   "Preventiva"),
        ("CR 2.2",  "Control de uso inalámbrico",                                      "Preventiva"),
        ("CR 2.3",  "Control de uso para dispositivos portátiles y móviles",           "Preventiva"),
        ("CR 2.4",  "Código móvil",                                                    "Preventiva"),
        ("CR 2.5",  "Bloqueo de sesión",                                               "Preventiva"),
        ("CR 2.6",  "Terminación de sesión remota",                                    "Preventiva"),
        ("CR 2.7",  "Control de sesiones simultáneas",                                 "Preventiva"),
        ("CR 2.8",  "Eventos auditables",                                              "Detectiva"),
        ("CR 2.9",  "Capacidad de almacenamiento de los datos de auditoría",           "Detectiva"),
        ("CR 2.10", "Respuesta a los fallos de procesamiento de auditorías",           "Detectiva"),
        ("CR 2.11", "Marcas de tiempo",                                                "Detectiva"),
        ("CR 2.12", "No rechazo",                                                      "Detectiva"),
        ("CR 2.13", "Uso de interfaces físicas de diagnóstico y ensayo",               "Preventiva"),
    ],
    "CR 3 — SI: Integridad del Sistema": [
        ("CR 3.1",  "Integridad de la comunicación",                                   "Preventiva"),
        ("CR 3.2",  "Protección contra código malicioso",                              "Preventiva"),
        ("CR 3.3",  "Verificación de la funcionalidad de la seguridad",                "Preventiva"),
        ("CR 3.4",  "Integridad del software y de la información",                     "Preventiva"),
        ("CR 3.5",  "Validación de entrada",                                           "Preventiva"),
        ("CR 3.6",  "Salida determinista",                                             "Preventiva"),
        ("CR 3.7",  "Tratamiento de errores",                                          "Preventiva"),
        ("CR 3.8",  "Integridad de la sesión",                                         "Preventiva"),
        ("CR 3.9",  "Protección de la información de auditoría",                       "Preventiva"),
        ("CR 3.10", "Soporte para actualizaciones",                                    "Preventiva"),
        ("CR 3.11", "Resistencia a la manipulación física y mecanismos de detección",  "Preventiva"),
        ("CR 3.12", "Provisión de raíces de confianza de los proveedores de servicios","Preventiva"),
        ("CR 3.13", "Provisión de raíces de confianza del propietario del activo",     "Preventiva"),
        ("CR 3.14", "Integridad del proceso de arranque",                              "Preventiva"),
    ],
    "CR 4 — DC: Confidencialidad de Datos": [
        ("CR 4.1",  "Confidencialidad de la información",                              "Preventiva"),
        ("CR 4.2",  "Persistencia de la información",                                  "Preventiva"),
        ("CR 4.3",  "Uso de criptografía",                                             "Preventiva"),
    ],
    "CR 5 — DM: Gestión del Flujo de Datos": [
        ("CR 5.1",  "Segmentación de la red",                                          "Preventiva"),
        ("CR 5.2",  "Protección de los límites de zona",                               "Preventiva"),
        ("CR 5.3",  "Restricciones de comunicación entre personas de carácter general","Preventiva"),
        ("CR 5.4",  "Partición de aplicaciones",                                       "Preventiva"),
    ],
    "CR 6 — ER: Respuesta a Eventos": [
        ("CR 6.1",  "Accesibilidad de los registros de auditoría",                     "Detectiva"),
        ("CR 6.2",  "Supervisión continua",                                            "Detectiva"),
    ],
    "CR 7 — RM: Gestión de Recursos": [
        ("CR 7.1",  "Protección contra denegación de servicio",                        "Preventiva"),
        ("CR 7.2",  "Gestión de recursos",                                             "Preventiva"),
        ("CR 7.3",  "Copia de seguridad del sistema de control",                       "De recuperación"),
        ("CR 7.4",  "Recuperación y reconstitución del sistema de control",            "De recuperación"),
        ("CR 7.5",  "Alimentación de emergencia",                                      "De recuperación"),
        ("CR 7.6",  "Ajustes de configuración de red y seguridad",                     "Preventiva"),
        ("CR 7.7",  "Funcionalidad mínima",                                            "Preventiva"),
        ("CR 7.8",  "Inventario de componentes del sistema de control",                "Preventiva"),
    ],
}

# ══════════════════════════════════════════════════════════════
# LÓGICA MAGERIT Y OT
# ══════════════════════════════════════════════════════════════

def valor_activo_con_dependencias(activo_id: int, conn) -> dict:
    """
    Calcula el valor real del activo propagando dependencias Purdue.
    V[dim] = MAX(V_propio[dim], MAX(V_padre[dim] × dep_flag))
    """
    row = conn.execute("SELECT * FROM activos WHERE id=?", (activo_id,)).fetchone()
    cols = [d[0] for d in conn.execute("SELECT * FROM activos WHERE id=?", (activo_id,)).description]
    activo = dict(zip(cols, row))

    dims = ["disp_15m","disp_3h","disp_1d","disp_1s","disp_2s",
            "conf_ri","conf_re","int_dt","int_err","int_man","int_aut",
            "ot_safety_sil","ot_continuidad","ot_integ_fisica"]

    # Valor propio
    val = {d: activo.get(d, 0) or 0 for d in dims}

    # Propagar desde padres
    padres = conn.execute(
        "SELECT activo_padre_id FROM dependencias WHERE activo_hijo_id=?", (activo_id,)
    ).fetchall()
    for (padre_id,) in padres:
        padre_row = conn.execute("SELECT * FROM activos WHERE id=?", (padre_id,)).fetchone()
        if padre_row:
            padre_cols = [d[0] for d in conn.execute("SELECT * FROM activos WHERE id=?", (padre_id,)).description]
            padre = dict(zip(padre_cols, padre_row))
            for d in dims:
                val[d] = max(val[d], padre.get(d, 0) or 0)

    # Calcular valores agregados por dimensión MAGERIT
    val["D"] = max(val["disp_15m"], val["disp_3h"], val["disp_1d"], val["disp_1s"], val["disp_2s"])
    val["C"] = max(val["conf_ri"], val["conf_re"])
    val["I"] = max(val["int_dt"], val["int_err"], val["int_man"], val["int_aut"])
    val["SAFETY"] = val["ot_safety_sil"]
    val["CONT"] = val["ot_continuidad"]
    val["FISIC"] = val["ot_integ_fisica"]
    return val

def calcular_riesgo(activo_id: int, amenaza: dict, valor_amenaza: int, conn) -> dict:
    """
    Fórmula MAGERIT-OT:
    R_dim = V_activo[dim] × Prob_amenaza[dim] × multiplicador_OT
    R_potencial = MAX(R_D, R_C, R_I, R_safety, R_cont, R_fisic)
    """
    row = conn.execute("SELECT zona_purdue, es_sis FROM activos WHERE id=?", (activo_id,)).fetchone()
    zona_purdue, es_sis = row
    mult = multiplicador_ot(zona_purdue, bool(es_sis))
    val = valor_activo_con_dependencias(activo_id, conn)

    # Prob por dimensión: max de las subdimensiones de la amenaza
    prob_D = max(amenaza["disp"]) if amenaza["disp"] else 0
    prob_C = max(amenaza["conf"]) if amenaza["conf"] else 0
    prob_I = max(amenaza["integ"]) if amenaza["integ"] else 0
    prob_safety = amenaza.get("safety", 0)
    prob_cont   = amenaza.get("cont", 0)
    prob_fisic  = amenaza.get("fisic", 0)

    # Riesgo por dimensión = valor × probabilidad_amenaza × multiplicador_OT
    # Limitado a escala MAGERIT (máx 25 = 5×5)
    def r(v, p): return round(min(25, v * p * mult), 2)

    rD     = r(val["D"],      prob_D)     if prob_D     else 0.0
    rC     = r(val["C"],      prob_C)     if prob_C     else 0.0
    rI     = r(val["I"],      prob_I)     if prob_I     else 0.0
    rS     = r(val["SAFETY"], prob_safety) if prob_safety else 0.0
    rCont  = r(val["CONT"],   prob_cont)   if prob_cont   else 0.0
    rFisic = r(val["FISIC"],  prob_fisic)  if prob_fisic  else 0.0

    riesgo_pot = max(rD, rC, rI, rS, rCont, rFisic)
    return {
        "riesgo_D": rD, "riesgo_C": rC, "riesgo_I": rI,
        "riesgo_safety": rS, "riesgo_continuidad": rCont, "riesgo_integ_fisica": rFisic,
        "riesgo_potencial": riesgo_pot,
        "nivel_riesgo": nivel_riesgo(riesgo_pot),
        "multiplicador_ot": mult,
    }

def calcular_residual(evaluacion_id: int, conn) -> dict:
    """
    MAGERIT: Residual Objetivo y Real usando salvaguardas y CMM
    R_obj  = R_pot × ∏(1 - eficacia_i)
    R_real = R_pot × ∏(1 - eficacia_i × CMM_factor_i)
    """
    ev = conn.execute("SELECT riesgo_potencial FROM evaluacion_riesgos WHERE id=?", (evaluacion_id,)).fetchone()
    if not ev: return {}
    r_pot = ev[0]

    salvs = conn.execute(
        "SELECT eficacia_objetivo, estado_cmm FROM salvaguardas WHERE evaluacion_id=?",
        (evaluacion_id,)
    ).fetchall()

    r_obj  = r_pot
    r_real = r_pot
    for (eficacia, cmm) in salvs:
        eficacia = eficacia or 0
        cmm_f = CMM_FACTOR.get(cmm, 0)
        r_obj  = r_obj  * (1 - eficacia)
        r_real = r_real * (1 - eficacia * cmm_f)

    return {
        "potencial": round(r_pot, 2),
        "residual_objetivo": round(r_obj, 2),
        "residual_real": round(r_real, 2),
    }

# ══════════════════════════════════════════════════════════════
# MOTOR ML — SCORING DE IEC 62443 BASADO EN MAGERIT Y OT
# ══════════════════════════════════════════════════════════════

def scoring_ml(activo: dict, amenazas_df: pd.DataFrame, salvs_df: pd.DataFrame) -> pd.DataFrame:
    """
    Scoring de controles IEC 62443 basado en:
    - Perfil de riesgo MAGERIT-OT del activo
    - Dimensiones con mayor riesgo (D, C, I, Safety, Continuidad)
    - Zona Purdue y SIS
    - Amenazas registradas
    - Penalización si el control ya está implantado (CMM≥3)
    """
    zona    = activo.get("zona_purdue", 2)
    es_sis  = bool(activo.get("es_sis", 0))
    seccion = activo.get("seccion_aceria", "")
    sl_obj  = SL_OBJETIVO_UBICACION.get(seccion, 2)  # SL objetivo según ubicación
    tipo = activo.get("tipo", "")
    subtipo = activo.get("subtipo", "")
    mult = multiplicador_ot(zona, es_sis)

    r_max = float(amenazas_df["riesgo_potencial"].max()) if len(amenazas_df) > 0 else 0
    r_D   = float(amenazas_df["riesgo_D"].max())   if len(amenazas_df) > 0 else 0
    r_C   = float(amenazas_df["riesgo_C"].max())   if len(amenazas_df) > 0 else 0
    r_I   = float(amenazas_df["riesgo_I"].max())   if len(amenazas_df) > 0 else 0
    r_S   = float(amenazas_df["riesgo_safety"].max()) if len(amenazas_df) > 0 else 0
    cats  = amenazas_df["amenaza_categoria"].tolist() if len(amenazas_df) > 0 else []
    tiene_ics  = "Ciberataques ICS" in cats
    tiene_aceria = "Amenazas Específicas Acería" in cats
    tiene_criticos = "Crítico" in (amenazas_df["nivel_riesgo"].tolist() if len(amenazas_df) > 0 else [])

    # Controles ya implantados (CMM≥3 = penalización)
    implantados = set()
    if len(salvs_df) > 0 and "ctrl_iec62443" in salvs_df.columns:
        for _, s in salvs_df.iterrows():
            if s.get("estado_cmm", 0) >= 3 and s.get("ctrl_iec62443"):
                implantados.add(s["ctrl_iec62443"].strip())

    # Reglas de scoring por control
    reglas = {
        # ── CR 1: Identificación y Control de Acceso ──────────────────
        "CR 1.1": [
            (20, "Todo activo OT requiere autenticación de usuarios", True),
            (20, "PLC/RTU: autenticación evita acceso no autorizado a lógica", tipo in ("PLC","PC REG","RTU")),
            (15, "Amenazas ICS incluyen suplantación de operador", tiene_ics),
            (15, "Riesgo C elevado: autenticación urgente", r_C >= 4),
        ],
        "CR 1.2": [
            (15, "Autenticación de procesos software — relevante en SCADA/HMI", tipo in ("PC HMI","TP")),
            (20, "PLC requiere autenticación de dispositivos en bus de campo", tipo in ("PLC","RTU")),
            (10, "Amenazas de suplantación protocolo activas", tiene_ics),
        ],
        "CR 1.3": [
            (20, "Gestión de cuentas: eliminar cuentas por defecto PLC/SCADA", True),
            (25, "PLC/SIS con cuentas por defecto — vector de ataque conocido", tipo in ("PLC","PC REG","RTU") or es_sis),
            (15, "PC S7/EWS: cuentas de programación deben gestionarse", tipo in ("PC S7","PC PORTATIL")),
        ],
        "CR 1.4": [
            (15, "Gestión de identificadores únicos por activo OT", True),
            (15, "Amenazas ICS activas requieren trazabilidad de identidades", tiene_ics),
        ],
        "CR 1.5": [
            (15, "Gestión de contraseñas y credenciales robusta", True),
            (20, "PLC/SIS: rotación de credenciales en mantenimiento", tipo in ("PLC","PC REG","RTU") or es_sis),
            (15, "Riesgo C alto implica credenciales expuestas", r_C >= 4),
        ],
        "CR 1.6": [
            (10, "Control de acceso inalámbrico si el activo tiene WiFi/BT", True),
            (20, "COMM/switches: gestión de acceso inalámbrico crítica", tipo in ("COMM","CCTV SWI")),
        ],
        "CR 1.7": [
            (20, "Contraseñas robustas en todos los activos OT", True),
            (25, "PC HMI/PC S7: contraseñas fuertes evitan acceso no autorizado", tipo in ("PC HMI","PC S7","PC PRO")),
            (15, "Riesgo C elevado exige política contraseñas rigurosa", r_C >= 4),
        ],
        "CR 1.11": [
            (15, "Bloqueo tras intentos fallidos evita fuerza bruta", True),
            (20, "PLC/HMI expuestos a red deben bloquear intentos fallidos", tipo in ("PLC","PC HMI","TP")),
        ],
        "CR 1.13": [
            (25, "Acceso remoto OT a través de redes no confiables — crítico", True),
            (30, "PC S7/Portátil: principal vector de acceso remoto a PLCs", tipo in ("PC S7","PC PORTATIL")),
            (20, "Amenazas ICS incluyen compromiso acceso remoto", tiene_ics),
            (10, "Zona Purdue baja: acceso remoto especialmente peligroso", zona <= 1),
        ],
        # ── CR 2: Uso y Control ────────────────────────────────────────
        "CR 2.1": [
            (20, "Autorización basada en roles en todos los activos OT", True),
            (25, "PLC/SIS: principio mínimo privilegio obligatorio IEC 62443", tipo in ("PLC","PC REG","RTU") or es_sis),
            (15, "PC HMI: perfiles de acceso por función (operador/supervisor)", tipo in ("PC HMI","TP")),
        ],
        "CR 2.3": [
            (25, "Control de USB y dispositivos portátiles — vector Stuxnet", True),
            (30, "PLC/SIS/EWS: USB es vector principal de infección OT", tipo in ("PLC","PC REG","RTU") or es_sis or tipo in ("PC S7","PC PORTATIL")),
            (20, "Amenazas ICS incluyen USB malicioso activamente", tiene_ics),
        ],
        "CR 2.5": [
            (15, "Bloqueo automático de sesión inactiva en HMI/SCADA", tipo in ("PC HMI","TP","PC S7")),
            (10, "Evita acceso no autorizado en sala de control", True),
        ],
        "CR 2.6": [
            (15, "Terminación de sesión remota al desconectar VPN OT", True),
            (20, "PC S7/Portátil: cierre seguro sesiones de programación remota", tipo in ("PC S7","PC PORTATIL")),
        ],
        "CR 2.8": [
            (20, "Registro de eventos auditables en todos los activos OT", True),
            (25, "PC HMI/SCADA: log de todos los comandos enviados a PLCs", tipo in ("PC HMI","TP")),
            (15, "Requerido por NIS2 para detección de incidentes OT", True),
        ],
        "CR 2.9": [
            (15, "Almacenamiento suficiente para logs de auditoría OT", True),
            (20, "NAS/Servers OT: capacidad de retención de logs >= 1 año", tipo in ("PC NAS",)),
        ],
        "CR 2.11": [
            (15, "Sincronización de tiempo NTP en todos los activos OT", True),
            (20, "Correlación de eventos requiere marcas de tiempo precisas", tiene_ics),
        ],
        "CR 2.13": [
            (20, "Controlar interfaces físicas diagnóstico (puertos serie, USB debug)", True),
            (25, "PLC/RTU: puertos de diagnóstico son vector de acceso físico", tipo in ("PLC","RTU","PC REG")),
            (20, "ZIV: interfaces de configuración física deben bloquearse", es_sis),
        ],
        # ── CR 3: Integridad del Sistema ───────────────────────────────
        "CR 3.1": [
            (20, "Integridad de comunicaciones OT — protocolos industriales sin cifrado", True),
            (25, "PLC/RTU: Profibus/Profinet sin integridad nativa — crítico", tipo in ("PLC","RTU")),
            (20, "Amenazas de MitM y spoofing en red OT activas", tiene_ics),
        ],
        "CR 3.2": [
            (20, "Protección antimalware compatible OT (application whitelisting)", True),
            (25, "PC HMI/PC S7: antimalware obligatorio en EWS y SCADA", tipo in ("PC HMI","PC S7","PC PRO","PC NAS")),
            (20, "Amenazas malware ICS activas (Stuxnet, ransomware OT)", tiene_ics),
        ],
        "CR 3.4": [
            (20, "Verificar integridad de firmware y software OT", True),
            (25, "PLC/RTU/SIS: integridad firmware crítica — Stuxnet vector", tipo in ("PLC","RTU","PC REG") or es_sis),
            (15, "Amenazas de manipulación firmware activas", tiene_ics),
        ],
        "CR 3.10": [
            (20, "Proceso formal de actualización y parcheo de firmware OT", True),
            (25, "PLC/SIS: evaluación de parches en entorno test antes de producción", tipo in ("PLC","PC REG","RTU") or es_sis),
            (15, "Activo con riesgo crítico requiere parcheo urgente", r_max >= 15),
        ],
        "CR 3.11": [
            (20, "Resistencia a manipulación física — acería con acceso amplio", True),
            (25, "ZIV/SIS/PLC: mecanismos antitampering obligatorios", es_sis or tipo in ("ZIV","BERTHOLD")),
            (15, "Zona de producción con acceso físico no controlado", zona <= 1),
        ],
        "CR 3.14": [
            (20, "Verificar integridad del arranque — evita firmware malicioso", tipo in ("PLC","PC REG","RTU") or es_sis),
            (15, "Amenazas de supply chain firmware activas", tiene_ics),
        ],
        # ── CR 4: Confidencialidad de Datos ───────────────────────────
        "CR 4.1": [
            (20, "Confidencialidad de datos de proceso y configuración OT", True),
            (25, "PC S7/EWS: proyectos PLC contienen lógica de proceso confidencial", tipo in ("PC S7","PC PORTATIL")),
            (15, "Riesgo C elevado exige cifrado de datos en reposo", r_C >= 4),
        ],
        "CR 4.2": [
            (15, "Eliminar datos de configuración al dar de baja activo OT", True),
            (20, "PC S7/NAS: borrado seguro al reemplazar hardware", tipo in ("PC S7","PC NAS","PC PORTATIL")),
        ],
        "CR 4.3": [
            (20, "Uso de criptografía para comunicaciones OT sensibles", True),
            (25, "Acceso remoto OT debe usar VPN con cifrado robusto", tipo in ("PC S7","PC PORTATIL","COMM")),
            (15, "Riesgo C alto requiere cifrado extremo a extremo", r_C >= 4),
        ],
        # ── CR 5: Gestión del Flujo de Datos ──────────────────────────
        "CR 5.1": [
            (25, "Segmentación de red OT — fundamental arquitectura Purdue", True),
            (30, "Activo en zona baja requiere aislamiento adicional", zona <= 1),
            (20, "Amenazas ICS incluyen lateral movement IT→OT", tiene_ics),
            (15, "COMM/switches: implementar VLANs por zona Purdue", tipo in ("COMM","CCTV SWI")),
        ],
        "CR 5.2": [
            (25, "Protección límites de zona — firewall IT/OT obligatorio", True),
            (20, "Activo expuesto a amenazas de red OT", tiene_ics),
            (15, "Riesgo D elevado por ataques de red", r_D >= 8),
        ],
        "CR 5.4": [
            (15, "Partición de aplicaciones OT por función y criticidad", True),
            (20, "PC HMI/SCADA: separar funciones operador/administrador", tipo in ("PC HMI","TP","PC S7")),
        ],
        # ── CR 6: Respuesta a Eventos ──────────────────────────────────
        "CR 6.1": [
            (20, "Acceso a registros de auditoría para respuesta a incidentes", True),
            (25, "NAS/Servers: centralizar logs OT para análisis forense", tipo in ("PC NAS",)),
            (20, "Requerido por NIS2 para notificación de incidentes", True),
        ],
        "CR 6.2": [
            (25, "Supervisión continua de activos OT — detección anomalías", True),
            (30, "Activos en zona baja requieren monitorización en tiempo real", zona <= 1),
            (20, "Amenazas ICS activas requieren detección temprana", tiene_ics),
            (15, "Riesgo crítico exige supervisión continua inmediata", r_max >= 15),
        ],
        # ── CR 7: Gestión de Recursos ──────────────────────────────────
        "CR 7.1": [
            (20, "Protección contra DoS — protocolo industrial vulnerable", True),
            (25, "PLC/RTU/COMM: DoS en bus de campo paraliza producción", tipo in ("PLC","RTU","COMM")),
            (20, "Amenazas DoS OT activas sobre activo", tiene_ics),
            (15, "Riesgo D alto por ataques de disponibilidad", r_D >= 8),
        ],
        "CR 7.3": [
            (25, "Backup configuración — sin él proceso se detiene tras incidente", True),
            (30, "PLC/SIS: backup proyecto obligatorio para recuperación", tipo in ("PLC","PC REG","RTU") or es_sis),
            (25, "PC S7/EWS: backup proyectos TIA Portal/STEP7", tipo in ("PC S7","PC PORTATIL")),
            (20, "NAS: política backup datos de proceso", tipo in ("PC NAS",)),
        ],
        "CR 7.4": [
            (25, "Plan recuperación sistema de control tras ciberincidente", True),
            (30, "Activo en HORNO/COLADA: DR incluye parada controlada proceso",
             activo.get("seccion_aceria","") in ("HORNO","COLADA CONTINUA","COLADA BLOOM","AFINO")),
            (20, "Riesgo crítico exige plan DR documentado y probado", r_max >= 15),
            (15, "Requerido por NIS2 para continuidad operacional", True),
        ],
        "CR 7.5": [
            (20, "Alimentación de emergencia — UPS protege activos críticos", True),
            (30, "UPS: dispositivo específico para protección eléctrica", tipo in ("UPS",)),
            (25, "PLC/SIS en zona baja requieren UPS dedicada", (tipo in ("PLC","RTU") or es_sis) and zona <= 1),
        ],
        "CR 7.6": [
            (20, "Gestión centralizada de configuración de red OT", True),
            (25, "COMM/switches: configuración debe ser versionada y auditada", tipo in ("COMM","CCTV SWI","RTU")),
            (15, "ZIV: configuración de protecciones requiere control estricto", tipo in ("ZIV",)),
        ],
        "CR 7.7": [
            (20, "Funcionalidad mínima — deshabilitar servicios no usados", True),
            (25, "PLC/RTU: deshabilitar puertos y servicios innecesarios", tipo in ("PLC","RTU","PC REG")),
            (20, "PC HMI/S7: eliminar software no necesario para función OT", tipo in ("PC HMI","PC S7","TP")),
            (15, "Amenazas ICS explotan servicios activos innecesarios", tiene_ics),
        ],
        "CR 7.8": [
            (15, "Inventario actualizado de componentes del sistema de control", True),
            (20, "Inventario OT requerido por NIS2 para gestión de riesgos", True),
            (15, "Base para gestión de vulnerabilidades y CVEs", tiene_criticos),
        ],
    }

    resultados = []
    for area, controles in IEC62443_CONTROLES.items():
        for (codigo, nombre, tipo_ctrl) in controles:
            score = 0
            desglose = []
            sl_min_ctrl = CR_SL_MINIMO.get(codigo, 1)
            sl_aplica = sl_min_ctrl <= sl_obj
            for codigo_r, rules in reglas.items():
                if codigo != codigo_r:
                    continue
                for peso, descripcion, condicion in rules:
                    if condicion:
                        score += peso
                        desglose.append(f"+{peso}: {descripcion}")

            # Bonus multiplicador OT (mayor riesgo OT → mayor urgencia)
            bonus = round((mult - 1.0) * 20)
            score += bonus
            if bonus > 0:
                desglose.append(f"+{bonus}: Multiplicador OT zona Purdue {zona}")

            # Penalización si ya implantado
            if codigo.strip() in implantados:
                score = int(score * 0.2)
                desglose.append("×0.2: Control ya implantado (CMM≥3)")

            score = min(100, max(0, score))
            prioridad = "ALTA" if score >= 55 else "MEDIA" if score >= 30 else "BAJA"

            # Suma si el objetivo de SL requiere este control
            if sl_aplica and sl_min_ctrl >= 2:
                score = min(100, score + 10)
                desglose.append(f"+10: SL objetivo ({sl_obj}) requiere este control (SL min {sl_min_ctrl})")
            # Penalización leve si el SL objetivo no requiere este control aún
            if not sl_aplica:
                score = max(0, score - 15)
                desglose.append(f"-15: SL objetivo ({sl_obj}) no requiere este control hasta SL {sl_min_ctrl}")

            resultados.append({
                "codigo": codigo, "nombre": nombre, "area": area,
                "tipo": tipo_ctrl, "score": score,
                "prioridad": prioridad,
                "sl_min": sl_min_ctrl,
                "sl_aplica": "✅" if sl_aplica else "⬜",
                "desglose": "\n".join(desglose),
            })

    return pd.DataFrame(resultados).sort_values("score", ascending=False)


# ══════════════════════════════════════════════════════════════
# CSS — rojo-negro-foto
# ══════════════════════════════════════════════════════════════

def get_bg_b64():
    p = os.path.join(os.path.dirname(__file__), "background.jpg")
    if os.path.exists(p):
        return base64.b64encode(open(p,"rb").read()).decode()
    return ""

BG_B64 = get_bg_b64()

def inject_css():
    bg_style = f"""
    .stApp::before {{
        content: '';
        position: fixed; top: 0; left: 0; width: 100%; height: 100%;
        background: url("data:image/jpeg;base64,{BG_B64}") center/cover no-repeat fixed;
        filter: brightness(0.18) saturate(0.4);
        z-index: -1;
    }}""" if BG_B64 else ""

    st.markdown(f"""<style>
    @import url('https://fonts.googleapis.com/css2?family=Syne:wght@400;700;800&family=JetBrains+Mono:wght@400;600&display=swap');

    :root {{
        --red: #e01010; --red-dark: #9b0000; --red-glow: #ff2222;
        --bg: #080000; --surface: rgba(16,2,2,0.88);
        --border: rgba(200,0,0,0.18); --text: #f0eded; --muted: #9a8a8a;
    }}
    .stApp {{ background: var(--bg); font-family: 'JetBrains Mono', monospace; }}
    {bg_style}

    section[data-testid="stSidebar"] {{
        background: rgba(8,0,0,0.97) !important;
        border-right: 1px solid var(--border) !important;
    }}
    section[data-testid="stSidebar"] * {{ color: var(--text) !important; }}

    .block-container {{ padding: 1.5rem 2rem 3rem; max-width: 1400px; }}

    /* Cards */
    .card {{
        background: var(--surface);
        border: 1px solid var(--border);
        border-radius: 10px;
        padding: 1.2rem 1.4rem;
        margin-bottom: 0.8rem;
        backdrop-filter: blur(8px);
    }}
    .card-red {{ border-left: 3px solid var(--red); }}

    /* Section titles */
    .sec-title {{
        font-family: 'Syne', sans-serif;
        font-size: 0.7rem; font-weight: 700;
        color: var(--red); text-transform: uppercase;
        letter-spacing: 3px; margin: 1.2rem 0 0.6rem;
    }}

    /* Risk badges */
    .badge-critico {{ background:#7f1d1d; color:#fca5a5; border:1px solid #ef4444; border-radius:4px; padding:2px 8px; font-size:0.65rem; font-family:'JetBrains Mono'; }}
    .badge-alto    {{ background:#78350f; color:#fcd34d; border:1px solid #f59e0b; border-radius:4px; padding:2px 8px; font-size:0.65rem; font-family:'JetBrains Mono'; }}
    .badge-medio   {{ background:#422006; color:#fde68a; border:1px solid #eab308; border-radius:4px; padding:2px 8px; font-size:0.65rem; font-family:'JetBrains Mono'; }}
    .badge-trivial {{ background:#052e16; color:#86efac; border:1px solid #22c55e; border-radius:4px; padding:2px 8px; font-size:0.65rem; font-family:'JetBrains Mono'; }}

    /* OT badge */
    .badge-sis {{ background:#1e1b4b; color:#a5b4fc; border:1px solid #6366f1; border-radius:4px; padding:2px 8px; font-size:0.65rem; }}
    .badge-ot  {{ background:#0f172a; color:#7dd3fc; border:1px solid #0284c7; border-radius:4px; padding:2px 8px; font-size:0.65rem; }}

    /* Info box */
    .info-box {{
        background: rgba(224,16,16,0.06); border: 1px solid rgba(224,16,16,0.25);
        border-radius: 8px; padding: 0.8rem 1rem; margin: 0.5rem 0 1rem;
        font-size: 0.78rem; color: #d4b4b4; line-height: 1.6;
    }}

    /* Formula box */
    .formula-box {{
        background: rgba(0,0,0,0.6); border: 1px solid #330000;
        border-left: 3px solid var(--red); border-radius: 6px;
        padding: 0.7rem 1rem; font-family: 'JetBrains Mono'; font-size: 0.75rem;
        color: #ff8888; margin: 0.5rem 0;
    }}

    /* Streamlit overrides */
    h1,h2,h3 {{ font-family:'Syne',sans-serif !important; color: var(--text) !important; }}
    .stSelectbox label, .stTextInput label, .stNumberInput label,
    .stSlider label, .stRadio label, .stCheckbox label,
    .stTextArea label {{ color: var(--muted) !important; font-size:0.72rem !important; }}
    .stButton>button {{
        background: var(--red-dark) !important; color: white !important;
        border: 1px solid var(--red) !important; border-radius: 6px !important;
        font-family: 'Syne', sans-serif !important; font-weight: 700 !important;
        transition: all 0.2s;
    }}
    .stButton>button:hover {{ background: var(--red) !important; box-shadow: 0 0 12px rgba(224,16,16,0.4) !important; }}
    .stTabs [data-baseweb="tab"] {{ color: var(--muted) !important; font-family:'JetBrains Mono' !important; }}
    .stTabs [aria-selected="true"] {{ color: var(--red) !important; border-bottom: 2px solid var(--red) !important; }}
    [data-testid="stMetricValue"] {{ color: var(--text) !important; font-family:'Syne',sans-serif !important; }}
    [data-testid="stMetricLabel"] {{ color: var(--muted) !important; font-size:0.7rem !important; }}
    .stDataFrame {{ background: var(--surface) !important; }}
    </style>""", unsafe_allow_html=True)

# ══════════════════════════════════════════════════════════════
# barra lateral app
# ══════════════════════════════════════════════════════════════

def render_sidebar():
    with st.sidebar:
        st.markdown("""
        <div style='text-align:center; padding:1rem 0;'>
            <div style='font-family:Syne; font-size:1.4rem; font-weight:800; color:#e01010;
                        letter-spacing:2px; text-shadow:0 0 20px rgba(224,16,16,0.5);'>
                MAGERIT ICS
            </div>
            <div style='font-family:JetBrains Mono; font-size:0.6rem; color:#9a8a8a;
                        letter-spacing:3px; margin-top:2px;'>
                PLANTA ACERÍA
            </div>
            <div style='width:60%; height:1px; background:linear-gradient(90deg,transparent,#e01010,transparent);
                        margin:0.7rem auto;'></div>
            <div style='font-family:JetBrains Mono; font-size:0.58rem; color:#6a5a5a; letter-spacing:1px;'>
                IEC 62443 · NIS2 · MAGERIT
            </div>
        </div>
        """, unsafe_allow_html=True)

        nav = st.radio("", [
            "Dashboard",
            "Activos OT",
            "Amenazas OT",
            "Tratamiento",
            "Motor ML",
            "Catálogos",
        ], label_visibility="collapsed")

        st.markdown("---")
        conn = get_conn()
        n_activos = conn.execute("SELECT COUNT(*) FROM activos").fetchone()[0]
        n_evals   = conn.execute("SELECT COUNT(*) FROM evaluacion_riesgos").fetchone()[0]
        n_criticos = conn.execute("SELECT COUNT(*) FROM evaluacion_riesgos WHERE nivel_riesgo='Crítico'").fetchone()[0]
        n_salvs   = conn.execute("SELECT COUNT(*) FROM salvaguardas").fetchone()[0]
        conn.close()

        st.markdown(f"""
        <div style='font-family:JetBrains Mono; font-size:0.68rem; color:#9a8a8a; line-height:2;'>
        <span style='color:#e01010;'>▸</span> Activos OT: <span style='color:#f0eded; font-weight:700;'>{n_activos}</span><br>
        <span style='color:#e01010;'>▸</span> Evaluaciones: <span style='color:#f0eded; font-weight:700;'>{n_evals}</span><br>
        <span style='color:#ef4444;'>▸</span> Riesgos Críticos: <span style='color:#ef4444; font-weight:700;'>{n_criticos}</span><br>
        <span style='color:#e01010;'>▸</span> Salvaguardas: <span style='color:#f0eded; font-weight:700;'>{n_salvs}</span>
        </div>
        """, unsafe_allow_html=True)

        st.markdown("---")

    return nav

# ══════════════════════════════════════════════════════════════
# MÓDULO 1 — DASHBOARD
# ══════════════════════════════════════════════════════════════



# ══════════════════════════════════════════════════════════════
# GENERADOR DE INFORME PDF — MAGERIT-OT
# ══════════════════════════════════════════════════════════════

def generar_informe_pdf(conn) -> bytes:
    """
    Genera un informe ejecutivo PDF de la auditoría MAGERIT-OT.
    Retorna bytes del PDF listos para descarga.
    """
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        leftMargin=1.8*cm, rightMargin=1.8*cm,
        topMargin=2*cm, bottomMargin=2*cm,
        title="Informe Auditoría MAGERIT-OT — Sidenor Basauri",
        author="MAGERIT-OT v2.0"
    )

    # ── Estilos ───────────────────────────────────────────────
    C_ROJO    = colors.HexColor("#C0392B")
    C_ROJO_L  = colors.HexColor("#FADBD8")
    C_GRIS    = colors.HexColor("#2C3E50")
    C_GRIS_L  = colors.HexColor("#F2F3F4")
    C_GRIS_M  = colors.HexColor("#5D6D7E")
    C_NEGRO   = colors.HexColor("#1a0000")
    C_VERDE   = colors.HexColor("#1E8449")
    C_NARANJA = colors.HexColor("#D68910")
    C_BLANCO  = colors.white

    def color_nivel_pdf(nivel):
        return {"Crítico": colors.HexColor("#C0392B"),
                "Alto":    colors.HexColor("#D68910"),
                "Medio":   colors.HexColor("#B7950B"),
                "Trivial": colors.HexColor("#1E8449")}.get(nivel, C_GRIS_M)

    st_title = ParagraphStyle("titulo", fontName="Helvetica-Bold",
        fontSize=22, textColor=C_ROJO, spaceAfter=6, leading=26)
    st_sub = ParagraphStyle("subtitulo", fontName="Helvetica",
        fontSize=11, textColor=C_GRIS_M, spaceAfter=4)
    st_h1 = ParagraphStyle("h1", fontName="Helvetica-Bold",
        fontSize=13, textColor=C_ROJO, spaceBefore=16, spaceAfter=6,
        borderPad=4, leading=16)
    st_h2 = ParagraphStyle("h2", fontName="Helvetica-Bold",
        fontSize=10, textColor=C_GRIS, spaceBefore=10, spaceAfter=4)
    st_body = ParagraphStyle("body", fontName="Helvetica",
        fontSize=9, textColor=C_GRIS, spaceAfter=4, leading=13)
    st_small = ParagraphStyle("small", fontName="Helvetica",
        fontSize=7.5, textColor=C_GRIS_M, spaceAfter=2, leading=10)
    st_center = ParagraphStyle("center", fontName="Helvetica",
        fontSize=9, textColor=C_GRIS, alignment=TA_CENTER)
    st_bold = ParagraphStyle("bold", fontName="Helvetica-Bold",
        fontSize=9, textColor=C_GRIS, spaceAfter=2)

    # ── Datos ─────────────────────────────────────────────────
    df_act  = pd.read_sql("SELECT * FROM activos", conn)
    df_ev   = pd.read_sql("""
        SELECT e.*, a.nombre as activo_nombre, a.tipo, a.seccion_aceria,
               a.zona_purdue, a.es_sis, a.id as activo_id
        FROM evaluacion_riesgos e JOIN activos a ON e.activo_id=a.id
        ORDER BY e.riesgo_potencial DESC
    """, conn)
    df_salvs = pd.read_sql("""
        SELECT s.*, e.amenaza_nombre, e.nivel_riesgo, a.nombre as activo_nombre
        FROM salvaguardas s
        JOIN evaluacion_riesgos e ON s.evaluacion_id=e.id
        JOIN activos a ON e.activo_id=a.id
    """, conn)
    comp = calcular_completitud(df_act, df_ev, conn)
    fecha = datetime.now().strftime("%d/%m/%Y %H:%M")

    story = []

    # ── PORTADA ───────────────────────────────────────────────
    story.append(Spacer(1, 1.5*cm))
    story.append(Paragraph("INFORME DE AUDITORÍA", ParagraphStyle("port1",
        fontName="Helvetica", fontSize=10, textColor=C_GRIS_M,
        alignment=TA_CENTER, spaceAfter=4)))
    story.append(Paragraph("MAGERIT-OT v2.0", ParagraphStyle("port2",
        fontName="Helvetica-Bold", fontSize=28, textColor=C_ROJO,
        alignment=TA_CENTER, spaceAfter=6)))
    story.append(Paragraph("Análisis de Riesgos de Ciberseguridad Industrial",
        ParagraphStyle("port3", fontName="Helvetica", fontSize=13,
        textColor=C_GRIS, alignment=TA_CENTER, spaceAfter=4)))
    story.append(Paragraph("Sidenor Basauri — Acería Eléctrica",
        ParagraphStyle("port4", fontName="Helvetica-Bold", fontSize=11,
        textColor=C_GRIS_M, alignment=TA_CENTER, spaceAfter=2)))
    story.append(Spacer(1, 0.4*cm))
    story.append(HRFlowable(width="100%", thickness=2, color=C_ROJO))
    story.append(Spacer(1, 0.4*cm))
    story.append(Paragraph(f"Fecha de generación: {fecha}",
        ParagraphStyle("port5", fontName="Helvetica", fontSize=9,
        textColor=C_GRIS_M, alignment=TA_CENTER)))
    story.append(Paragraph("Documento de uso interno — Confidencial",
        ParagraphStyle("port6", fontName="Helvetica-Oblique", fontSize=8,
        textColor=C_GRIS_M, alignment=TA_CENTER)))
    story.append(Spacer(1, 1*cm))

    # Tabla resumen portada
    n_crit = len(df_ev[df_ev["nivel_riesgo"]=="Crítico"]) if len(df_ev)>0 else 0
    n_alto = len(df_ev[df_ev["nivel_riesgo"]=="Alto"]) if len(df_ev)>0 else 0
    r_max  = float(df_ev["riesgo_potencial"].max()) if len(df_ev)>0 else 0

    tabla_port = Table([
        [Paragraph("Activos OT", st_center), Paragraph("Evaluaciones", st_center),
         Paragraph("Riesgos Críticos", st_center), Paragraph("Riesgos Altos", st_center),
         Paragraph("Completitud", st_center)],
        [Paragraph(str(len(df_act)), ParagraphStyle("kpi", fontName="Helvetica-Bold",
            fontSize=20, textColor=C_GRIS, alignment=TA_CENTER)),
         Paragraph(str(len(df_ev)), ParagraphStyle("kpi", fontName="Helvetica-Bold",
            fontSize=20, textColor=C_GRIS, alignment=TA_CENTER)),
         Paragraph(str(n_crit), ParagraphStyle("kpi", fontName="Helvetica-Bold",
            fontSize=20, textColor=C_ROJO, alignment=TA_CENTER)),
         Paragraph(str(n_alto), ParagraphStyle("kpi", fontName="Helvetica-Bold",
            fontSize=20, textColor=C_NARANJA, alignment=TA_CENTER)),
         Paragraph(f"{comp['global']}%", ParagraphStyle("kpi", fontName="Helvetica-Bold",
            fontSize=20, textColor=C_VERDE if comp["global"]>=75 else C_NARANJA if comp["global"]>=50 else C_ROJO,
            alignment=TA_CENTER))],
    ], colWidths=[3.3*cm]*5)
    tabla_port.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), C_GRIS_L),
        ("BACKGROUND", (0,1), (-1,1), colors.white),
        ("BOX", (0,0), (-1,-1), 0.5, C_GRIS_M),
        ("INNERGRID", (0,0), (-1,-1), 0.3, colors.HexColor("#D5D8DC")),
        ("ROWBACKGROUNDS", (0,1), (-1,1), [colors.white]),
        ("TOPPADDING", (0,0), (-1,-1), 8),
        ("BOTTOMPADDING", (0,0), (-1,-1), 8),
    ]))
    story.append(tabla_port)
    story.append(PageBreak())

    # ── 1. COMPLETITUD DE AUDITORÍA ───────────────────────────
    story.append(Paragraph("1. Completitud de la Auditoría MAGERIT-OT", st_h1))
    story.append(HRFlowable(width="100%", thickness=1, color=C_ROJO))
    story.append(Spacer(1, 0.2*cm))
    story.append(Paragraph(
        f"La auditoría presenta un índice de completitud global del <b>{comp['global']}%</b>, "
        f"calculado como la media ponderada de cuatro métricas del proceso MAGERIT-OT. "
        f"Este índice refleja el grado de avance en la identificación, evaluación y tratamiento "
        f"de riesgos de ciberseguridad en las instalaciones OT de Sidenor Basauri.", st_body))
    story.append(Spacer(1, 0.2*cm))

    tabla_comp = Table([
        ["Métrica", "Resultado", "Detalle", "Peso"],
        ["1. Valoración de Activos",
         f"{comp['m1']}%",
         f"{comp['n_valorados']} de {comp['n_act']} activos valorados",
         "30%"],
        ["2. Cobertura de Amenazas",
         f"{comp['m2']}%",
         f"{comp['n_con_eval']} de {comp['n_act']} activos evaluados",
         "35%"],
        ["3. Tratamiento de Riesgos",
         f"{comp['m3']}%",
         f"{comp['n_con_salvaguarda']} de {comp['n_criticos_altos']} criticos/altos tratados",
         "25%"],
        ["4. Madurez CMM",
         f"{comp['m4']}%",
         f"{comp['n_con_cmm']} de {comp['n_evals']} evaluaciones con CMM>=2",
         "10%"],
        ["INDICE GLOBAL", f"{comp['global']}%", "", ""],
    ], colWidths=[6*cm, 2*cm, 7*cm, 1.5*cm])
    tabla_comp.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), C_GRIS),
        ("TEXTCOLOR", (0,0), (-1,0), C_BLANCO),
        ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"),
        ("FONTSIZE", (0,0), (-1,-1), 8.5),
        ("BACKGROUND", (0,-1), (-1,-1), C_ROJO_L),
        ("FONTNAME", (0,-1), (-1,-1), "Helvetica-Bold"),
        ("TEXTCOLOR", (0,-1), (-1,-1), C_ROJO),
        ("ROWBACKGROUNDS", (0,1), (-1,-2), [colors.white, C_GRIS_L]),
        ("BOX", (0,0), (-1,-1), 0.5, C_GRIS_M),
        ("INNERGRID", (0,0), (-1,-1), 0.3, colors.HexColor("#D5D8DC")),
        ("TOPPADDING", (0,0), (-1,-1), 5),
        ("BOTTOMPADDING", (0,0), (-1,-1), 5),
        ("LEFTPADDING", (0,0), (-1,-1), 6),
    ]))
    story.append(tabla_comp)

    # ── 2. INVENTARIO DE ACTIVOS ──────────────────────────────
    story.append(Spacer(1, 0.5*cm))
    story.append(Paragraph("2. Inventario de Activos OT", st_h1))
    story.append(HRFlowable(width="100%", thickness=1, color=C_ROJO))
    story.append(Spacer(1, 0.2*cm))

    if len(df_act) > 0:
        story.append(Paragraph(
            f"Se han inventariado <b>{len(df_act)} activos OT</b> distribuidos en las "
            f"{df_act['seccion_aceria'].nunique()} ubicaciones de la planta.", st_body))
        story.append(Spacer(1, 0.2*cm))

        # Activos por ubicación
        por_sec = df_act.groupby("seccion_aceria").size().reset_index(name="n").sort_values("n", ascending=False)
        filas_sec = [["Ubicación", "N. Activos", "Tipos principales"]]
        for _, rs in por_sec.iterrows():
            tipos = df_act[df_act["seccion_aceria"]==rs["seccion_aceria"]]["tipo"].value_counts().head(3)
            tipos_str = ", ".join([f"{t}({n})" for t,n in tipos.items()])
            filas_sec.append([str(rs["seccion_aceria"]), str(rs["n"]), tipos_str])
        t_sec = Table(filas_sec, colWidths=[5*cm, 2.5*cm, 9*cm])
        t_sec.setStyle(TableStyle([
            ("BACKGROUND", (0,0), (-1,0), C_GRIS),
            ("TEXTCOLOR", (0,0), (-1,0), C_BLANCO),
            ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"),
            ("FONTSIZE", (0,0), (-1,-1), 8),
            ("ROWBACKGROUNDS", (0,1), (-1,-1), [colors.white, C_GRIS_L]),
            ("BOX", (0,0), (-1,-1), 0.5, C_GRIS_M),
            ("INNERGRID", (0,0), (-1,-1), 0.3, colors.HexColor("#D5D8DC")),
            ("TOPPADDING", (0,0), (-1,-1), 4),
            ("BOTTOMPADDING", (0,0), (-1,-1), 4),
            ("LEFTPADDING", (0,0), (-1,-1), 5),
        ]))
        story.append(t_sec)

    # ── 3. EVALUACIÓN DE RIESGOS ──────────────────────────────
    story.append(Spacer(1, 0.5*cm))
    story.append(Paragraph("3. Evaluación de Riesgos — Resultados", st_h1))
    story.append(HRFlowable(width="100%", thickness=1, color=C_ROJO))
    story.append(Spacer(1, 0.2*cm))

    if len(df_ev) > 0:
        dist = df_ev["nivel_riesgo"].value_counts()
        dist_str = " | ".join([f"{niv}: {n}" for niv, n in dist.items()])
        story.append(Paragraph(
            f"Se han registrado <b>{len(df_ev)} evaluaciones de riesgo</b>. "
            f"Distribución por nivel: {dist_str}. "
            f"Riesgo potencial máximo detectado: <b>{r_max:.1f}</b> (escala MAGERIT 0-25).", st_body))
        story.append(Spacer(1, 0.2*cm))

        # Top riesgos
        story.append(Paragraph("Riesgos de mayor criticidad (Top 15)", st_h2))
        top15 = df_ev.head(15)
        filas_ev = [["Activo", "Amenaza", "Secc.", "R.Pot.", "Nivel", "Mult.OT"]]
        for _, r in top15.iterrows():
            filas_ev.append([
                str(r["activo_nombre"])[:22],
                str(r["amenaza_nombre"])[:35],
                str(r["seccion_aceria"])[:12],
                f"{r['riesgo_potencial']:.1f}",
                str(r["nivel_riesgo"]),
                f"x{r['multiplicador_ot']:.2f}",
            ])
        t_ev = Table(filas_ev, colWidths=[3.5*cm, 5.5*cm, 2*cm, 1.5*cm, 1.8*cm, 1.5*cm])
        row_colors = []
        for i, (_, r) in enumerate(top15.iterrows(), 1):
            c_niv = color_nivel_pdf(r["nivel_riesgo"])
            row_colors.append(("TEXTCOLOR", (4,i), (4,i), c_niv))
            row_colors.append(("FONTNAME", (4,i), (4,i), "Helvetica-Bold"))
        t_ev.setStyle(TableStyle([
            ("BACKGROUND", (0,0), (-1,0), C_GRIS),
            ("TEXTCOLOR", (0,0), (-1,0), C_BLANCO),
            ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"),
            ("FONTSIZE", (0,0), (-1,-1), 7.5),
            ("ROWBACKGROUNDS", (0,1), (-1,-1), [colors.white, C_GRIS_L]),
            ("BOX", (0,0), (-1,-1), 0.5, C_GRIS_M),
            ("INNERGRID", (0,0), (-1,-1), 0.3, colors.HexColor("#D5D8DC")),
            ("TOPPADDING", (0,0), (-1,-1), 4),
            ("BOTTOMPADDING", (0,0), (-1,-1), 4),
            ("LEFTPADDING", (0,0), (-1,-1), 4),
        ] + row_colors))
        story.append(t_ev)

        # Riesgo por sección
        story.append(Spacer(1, 0.3*cm))
        story.append(Paragraph("Riesgo máximo por ubicación", st_h2))
        sec_risk = df_ev.groupby("seccion_aceria")["riesgo_potencial"].max().sort_values(ascending=False).reset_index()
        filas_sr = [["Ubicación", "Riesgo Máximo", "Nivel"]]
        for _, rs in sec_risk.iterrows():
            niv = "Crítico" if rs["riesgo_potencial"]>=15 else "Alto" if rs["riesgo_potencial"]>=4 else "Medio" if rs["riesgo_potencial"]>=3 else "Trivial"
            filas_sr.append([str(rs["seccion_aceria"]), f"{rs['riesgo_potencial']:.1f}", niv])
        t_sr = Table(filas_sr, colWidths=[7*cm, 4*cm, 5.5*cm])
        niv_colors_sr = []
        for i, (_, rs) in enumerate(sec_risk.iterrows(), 1):
            niv = "Crítico" if rs["riesgo_potencial"]>=15 else "Alto" if rs["riesgo_potencial"]>=4 else "Medio" if rs["riesgo_potencial"]>=3 else "Trivial"
            c_niv = color_nivel_pdf(niv)
            niv_colors_sr.append(("TEXTCOLOR", (2,i), (2,i), c_niv))
            niv_colors_sr.append(("FONTNAME", (2,i), (2,i), "Helvetica-Bold"))
        t_sr.setStyle(TableStyle([
            ("BACKGROUND", (0,0), (-1,0), C_GRIS),
            ("TEXTCOLOR", (0,0), (-1,0), C_BLANCO),
            ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"),
            ("FONTSIZE", (0,0), (-1,-1), 8.5),
            ("ROWBACKGROUNDS", (0,1), (-1,-1), [colors.white, C_GRIS_L]),
            ("BOX", (0,0), (-1,-1), 0.5, C_GRIS_M),
            ("INNERGRID", (0,0), (-1,-1), 0.3, colors.HexColor("#D5D8DC")),
            ("TOPPADDING", (0,0), (-1,-1), 5),
            ("BOTTOMPADDING", (0,0), (-1,-1), 5),
            ("LEFTPADDING", (0,0), (-1,-1), 6),
        ] + niv_colors_sr))
        story.append(t_sr)
    else:
        story.append(Paragraph("No se han registrado evaluaciones de riesgo.", st_body))

    # ── 4. SALVAGUARDAS ───────────────────────────────────────
    story.append(Spacer(1, 0.5*cm))
    story.append(Paragraph("4. Salvaguardas y Riesgo Residual", st_h1))
    story.append(HRFlowable(width="100%", thickness=1, color=C_ROJO))
    story.append(Spacer(1, 0.2*cm))

    if len(df_salvs) > 0:
        story.append(Paragraph(
            f"Se han definido <b>{len(df_salvs)} salvaguardas</b> sobre los riesgos evaluados. "
            f"El nivel de madurez CMM refleja el grado real de implantación de cada medida de proteccion.", st_body))
        story.append(Spacer(1, 0.2*cm))

        cmm_labels = {0:"Sin implantar", 1:"Inicial", 2:"Definido", 3:"Implantado", 4:"Evaluado"}
        filas_salv = [["Activo", "Amenaza", "Salvaguarda", "Capa", "CMM", "Eficacia"]]
        for _, s in df_salvs.head(20).iterrows():
            filas_salv.append([
                str(s["activo_nombre"])[:18],
                str(s["amenaza_nombre"])[:25],
                str(s["nombre"])[:25],
                str(s["capa"])[:12],
                cmm_labels.get(int(s["estado_cmm"] or 0), "—"),
                f"{float(s['eficacia_objetivo'] or 0)*100:.0f}%",
            ])
        t_salv = Table(filas_salv, colWidths=[2.8*cm, 3.8*cm, 3.8*cm, 2*cm, 2.2*cm, 1.5*cm])
        t_salv.setStyle(TableStyle([
            ("BACKGROUND", (0,0), (-1,0), C_GRIS),
            ("TEXTCOLOR", (0,0), (-1,0), C_BLANCO),
            ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"),
            ("FONTSIZE", (0,0), (-1,-1), 7.5),
            ("ROWBACKGROUNDS", (0,1), (-1,-1), [colors.white, C_GRIS_L]),
            ("BOX", (0,0), (-1,-1), 0.5, C_GRIS_M),
            ("INNERGRID", (0,0), (-1,-1), 0.3, colors.HexColor("#D5D8DC")),
            ("TOPPADDING", (0,0), (-1,-1), 4),
            ("BOTTOMPADDING", (0,0), (-1,-1), 4),
            ("LEFTPADDING", (0,0), (-1,-1), 4),
        ]))
        story.append(t_salv)
        if len(df_salvs) > 20:
            story.append(Paragraph(
                f"* Se muestran las primeras 20 salvaguardas de {len(df_salvs)} registradas.", st_small))
    else:
        story.append(Paragraph("No se han registrado salvaguardas.", st_body))

    # ── 5. CONCLUSIONES ───────────────────────────────────────
    story.append(Spacer(1, 0.5*cm))
    story.append(Paragraph("5. Conclusiones y Recomendaciones", st_h1))
    story.append(HRFlowable(width="100%", thickness=1, color=C_ROJO))
    story.append(Spacer(1, 0.2*cm))

    if n_crit > 0:
        story.append(Paragraph(
            f"Se han identificado <b>{n_crit} riesgo(s) de nivel CRITICO</b> que requieren "
            f"tratamiento inmediato. Estos riesgos superan el umbral de 15 puntos en la escala "
            f"MAGERIT-OT y corresponden a activos con multiplicador OT elevado (zona Purdue 0-1 "
            f"o activos SIS). Se recomienda priorizar la implantacion de controles IEC 62443 "
            f"de nivel SL3 para estos activos.", st_body))
    if n_alto > 0:
        story.append(Paragraph(
            f"Adicionalmente, <b>{n_alto} riesgo(s) de nivel ALTO</b> deben ser tratados "
            f"en el proximo ciclo de auditoría. Se recomienda aplicar los controles IEC 62443 "
            f"de nivel SL2 identificados por el Motor ML para cada activo.", st_body))
    if comp["m2"] < 50:
        story.append(Paragraph(
            f"La cobertura de amenazas es del <b>{comp['m2']}%</b>, lo que indica que "
            f"existen activos OT sin evaluacion de riesgo. Se recomienda completar la "
            f"evaluacion de todos los activos antes de la proxima revision NIS2.", st_body))
    story.append(Spacer(1, 0.3*cm))
    story.append(Paragraph(
        "Este informe ha sido generado automaticamente por la herramienta MAGERIT-OT v2.0, "
        "basada en la metodologia MAGERIT v3.0 adaptada al entorno OT/ICS y alineada con "
        "la norma IEC 62443-4-2:2019 y la Directiva NIS2 (UE 2022/2555).", st_small))

    doc.build(story)
    buffer.seek(0)
    return buffer.getvalue()

def calcular_completitud(df_act, df_ev, conn):
    # Todos los valores escalares con int() seguro via scalar()
    def sc(x):
        """Convierte cualquier valor pandas/numpy a int Python puro."""
        try:
            return int(x)
        except Exception:
            try:
                return int(x.iloc[0]) if hasattr(x, 'iloc') else int(x.item())
            except Exception:
                return 0

    n_act = sc(len(df_act))
    if n_act == 0:
        return {"global":0,"m1":0,"m2":0,"m3":0,"m4":0,
                "n_act":0,"n_valorados":0,"n_con_eval":0,
                "n_criticos_altos":0,"n_con_salvaguarda":0,"n_evals":0,"n_con_cmm":0}

    dim_cols = ["disp_15m","disp_3h","disp_1d","disp_1s","disp_2s",
                "conf_ri","conf_re","int_dt","int_err","int_man","int_aut",
                "ot_safety_sil","ot_continuidad","ot_integ_fisica"]
    valorados = 0
    for _, r in df_act.iterrows():
        vals = [sc(r[d]) for d in dim_cols if d in df_act.columns]
        if sum(vals) > 0:
            valorados += 1
    m1 = int(round(valorados / n_act * 100))

    # M2: activos con evaluación — usar SQL directo para evitar problemas con Series
    try:
        n_con_eval = conn.execute(
            "SELECT COUNT(DISTINCT activo_id) FROM evaluacion_riesgos"
        ).fetchone()[0]
        n_con_eval = sc(n_con_eval)
    except Exception:
        n_con_eval = 0
    m2 = int(round(n_con_eval / n_act * 100))

    # M3: riesgos críticos/altos con salvaguarda
    try:
        n_ca = sc(conn.execute(
            "SELECT COUNT(*) FROM evaluacion_riesgos WHERE nivel_riesgo IN ('Crítico','Alto')"
        ).fetchone()[0])
        n_con_salv = sc(conn.execute("""
            SELECT COUNT(DISTINCT e.id) FROM evaluacion_riesgos e
            JOIN salvaguardas s ON s.evaluacion_id = e.id
            WHERE e.nivel_riesgo IN ('Crítico','Alto')
        """).fetchone()[0])
        m3 = int(round(n_con_salv / n_ca * 100)) if n_ca > 0 else 100
    except Exception:
        n_ca = 0; n_con_salv = 0; m3 = 0

    # M4: evaluaciones con CMM >= 2
    try:
        n_evals = sc(conn.execute("SELECT COUNT(*) FROM evaluacion_riesgos").fetchone()[0])
        n_con_cmm = sc(conn.execute("""
            SELECT COUNT(DISTINCT evaluacion_id) FROM salvaguardas WHERE estado_cmm >= 2
        """).fetchone()[0])
        m4 = int(round(n_con_cmm / n_evals * 100)) if n_evals > 0 else 0
    except Exception:
        n_evals = 0; n_con_cmm = 0; m4 = 0

    g = int(round(m1*0.30 + m2*0.35 + m3*0.25 + m4*0.10))
    return {"global":g,"m1":m1,"m2":m2,"m3":m3,"m4":m4,
            "n_act":n_act,"n_valorados":valorados,"n_con_eval":n_con_eval,
            "n_criticos_altos":n_ca,"n_con_salvaguarda":n_con_salv,
            "n_evals":n_evals,"n_con_cmm":n_con_cmm}


def render_completitud(comp):
    g = int(comp["global"])
    g_col = "#22c55e" if g>=75 else "#f59e0b" if g>=50 else "#ef4444" if g>=25 else "#7f1d1d"
    estado = ("Auditoría avanzada" if g>=75 else "Auditoría en progreso"
              if g>=50 else "Auditoría iniciada" if g>=25 else "Auditoría pendiente")

    def barra(pct, color):
        return ('<div style="background:#1a0000;border-radius:4px;height:6px;overflow:hidden;margin-top:4px;">'
                f'<div style="width:{pct}%;height:100%;background:{color};border-radius:4px;"></div></div>')

    def mini(label, pct, detalle):
        c = "#22c55e" if pct>=75 else "#f59e0b" if pct>=40 else "#ef4444"
        return ('<div style="flex:1;min-width:140px;background:rgba(10,0,0,0.6);border:1px solid #220000;'
                'border-radius:8px;padding:0.7rem 0.9rem;">'
                f'<div style="font-size:0.6rem;color:#9a8a8a;letter-spacing:1px;">{label}</div>'
                f'<div style="font-size:1.3rem;font-weight:800;color:{c};font-family:Syne,sans-serif;">{pct}%</div>'
                f'<div style="font-size:0.6rem;color:#6a5a5a;">{detalle}</div>'
                + barra(pct, c) + '</div>')

    html = (
        '<div style="background:rgba(10,0,0,0.85);border:1px solid rgba(200,0,0,0.2);'
        'border-radius:12px;padding:1.2rem 1.4rem;margin-bottom:1rem;">'
        '<div style="display:flex;justify-content:space-between;align-items:center;'
        'flex-wrap:wrap;gap:0.8rem;margin-bottom:1rem;">'
        '<div>'
        '<div style="font-family:Syne,sans-serif;font-size:0.65rem;color:#e01010;'
        'letter-spacing:3px;text-transform:uppercase;font-weight:700;">Completitud de Auditoría MAGERIT-OT</div>'
        f'<div style="font-size:0.7rem;color:#9a8a8a;margin-top:2px;">{estado} — NIS2 Art. 21</div>'
        '</div>'
        f'<div style="text-align:center;"><div style="font-size:2.8rem;font-weight:900;color:{g_col};'
        f'font-family:Syne,sans-serif;line-height:1;">{g}%</div>'
        '<div style="font-size:0.6rem;color:#9a8a8a;">ÍNDICE GLOBAL</div></div>'
        '</div>'
        f'<div style="background:#1a0000;border-radius:6px;height:10px;overflow:hidden;margin-bottom:1rem;">'
        f'<div style="width:{g}%;height:100%;border-radius:6px;'
        f'background:linear-gradient(90deg,{g_col}88,{g_col});"></div></div>'
        '<div style="display:flex;gap:0.6rem;flex-wrap:wrap;">'
        + mini("1. VALORACIÓN ACTIVOS", comp["m1"],
               f"{comp['n_valorados']}/{comp['n_act']} activos valorados")
        + mini("2. COBERTURA AMENAZAS", comp["m2"],
               f"{comp['n_con_eval']}/{comp['n_act']} activos evaluados")
        + mini("3. TRATAMIENTO RIESGOS", comp["m3"],
               f"{comp['n_con_salvaguarda']}/{comp['n_criticos_altos']} críticos/altos tratados")
        + mini("4. MADUREZ CMM", comp["m4"],
               f"{comp['n_con_cmm']}/{comp['n_evals']} evaluaciones CMM≥2")
        + '</div>'
        '<div style="margin-top:0.8rem;font-size:0.6rem;color:#4a3a3a;font-style:italic;">'
        'Ponderación: Valoración ×30% · Cobertura amenazas ×35% · Tratamiento riesgos ×25% · Madurez CMM ×10%'
        '</div></div>'
    )
    st.markdown(html, unsafe_allow_html=True)

def render_dashboard():
    st.markdown('<div class="sec-title">Dashboard — Visión General de Riesgos OT</div>', unsafe_allow_html=True)

    conn = get_conn()
    df_ev = pd.read_sql("""
        SELECT e.*, a.nombre as activo_nombre, a.tipo, a.seccion_aceria,
               a.zona_purdue, a.es_sis, a.id as activo_id
        FROM evaluacion_riesgos e
        JOIN activos a ON e.activo_id = a.id
    """, conn)
    df_act = pd.read_sql("SELECT * FROM activos", conn)

    # ── Indicador de completitud ──────────────────────────────────
    comp = calcular_completitud(df_act, df_ev, conn)
    render_completitud(comp)
    conn.close()

    # ── Botón exportar PDF ───────────────────────────────────
    col_exp1, col_exp2 = st.columns([3,1])
    with col_exp2:
        if st.button("📄 Exportar Informe PDF", use_container_width=True):
            try:
                conn_pdf = get_conn()
                pdf_bytes = generar_informe_pdf(conn_pdf)
                conn_pdf.close()
                fecha_fn = datetime.now().strftime("%Y%m%d_%H%M")
                st.download_button(
                    label="⬇️ Descargar PDF",
                    data=pdf_bytes,
                    file_name=f"informe_magerit_ot_{fecha_fn}.pdf",
                    mime="application/pdf",
                    use_container_width=True,
                )
            except Exception as e:
                st.error(f"Error generando PDF: {e}")

    # KPIs
    col1,col2,col3,col4,col5 = st.columns(5)
    n_act = len(df_act)
    n_ev  = len(df_ev)
    n_crit = len(df_ev[df_ev["nivel_riesgo"]=="Crítico"]) if len(df_ev)>0 else 0
    n_alto = len(df_ev[df_ev["nivel_riesgo"]=="Alto"]) if len(df_ev)>0 else 0
    r_max  = df_ev["riesgo_potencial"].max() if len(df_ev)>0 else 0

    with col1: st.metric("Activos OT", n_act)
    with col2: st.metric("Evaluaciones", n_ev)
    with col3: st.metric("🔴 Críticos", n_crit, delta=None)
    with col4: st.metric("🟠 Altos", n_alto)
    with col5: st.metric("Riesgo Máx.", f"{r_max:.1f}")

    if len(df_ev) == 0:
        st.info("No hay evaluaciones de riesgo. Ve a **Amenazas OT** para registrar riesgos.")
        return

    col_a, col_b = st.columns([2, 1])

    with col_a:
        st.markdown('<div class="sec-title">Mapa de Calor — Riesgo por Sección de Acería</div>', unsafe_allow_html=True)
        if "seccion_aceria" in df_ev.columns:
            sec = df_ev.groupby("seccion_aceria")["riesgo_potencial"].agg(
                Media="mean", Maximo="max", N="count"
            ).reset_index().sort_values("Media", ascending=False)

            fig = go.Figure(go.Bar(
                x=sec["Media"], y=sec["seccion_aceria"], orientation="h",
                marker=dict(color=sec["Media"],
                    colorscale=[[0,"#1a0000"],[0.4,"#7f0000"],[0.7,"#cc0000"],[1,"#ff2222"]],
                    showscale=True,
                    colorbar=dict(title="Riesgo", thickness=10,
                                  tickfont=dict(family="JetBrains Mono",size=9,color="#9a8a8a"))),
                text=[f"{v:.1f}" for v in sec["Media"]],
                textposition="inside", textfont=dict(family="JetBrains Mono",size=10,color="white"),
                hovertemplate="<b>%{y}</b><br>Riesgo medio: %{x:.2f}<extra></extra>"
            ))
            fig.update_layout(
                paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                font=dict(family="JetBrains Mono",color="#9a8a8a",size=10),
                height=max(280, len(sec)*38), margin=dict(l=5,r=5,t=5,b=5),
                xaxis=dict(gridcolor="#220000", title="Riesgo MAGERIT-OT"),
                yaxis=dict(autorange="reversed"),
            )
            st.plotly_chart(fig, use_container_width=True)

        # Comparativa Potencial vs Residual por sección
        st.markdown('<div class="sec-title">Comparativa Riesgo Potencial vs Residual Real por Sección</div>', unsafe_allow_html=True)
        conn = get_conn()
        df_salvs = pd.read_sql("SELECT * FROM salvaguardas", conn)
        conn.close()

        potencial_sec = df_ev.groupby("seccion_aceria")["riesgo_potencial"].max().reset_index()
        potencial_sec.columns = ["seccion", "potencial"]

        # Calcular residual real por evaluación
        residuales = []
        for _, row in df_ev.iterrows():
            salvs_ev = df_salvs[df_salvs["evaluacion_id"]==row["id"]] if len(df_salvs)>0 else pd.DataFrame()
            r_real = row["riesgo_potencial"]
            for _, s in salvs_ev.iterrows():
                cmm_f = CMM_FACTOR.get(int(s["estado_cmm"] or 0), 0)
                r_real = r_real * (1 - (s["eficacia_objetivo"] or 0) * cmm_f)
            residuales.append({"seccion": row["seccion_aceria"], "residual_real": r_real})

        if residuales:
            df_res = pd.DataFrame(residuales).groupby("seccion")["residual_real"].max().reset_index()
            merged = potencial_sec.merge(df_res, on="seccion", how="left").fillna(0)

            fig2 = go.Figure()
            fig2.add_trace(go.Bar(x=merged["seccion"], y=merged["potencial"],
                name="Potencial", marker_color="#ef4444",
                hovertemplate="%{x}<br>Potencial: %{y:.1f}<extra></extra>"))
            fig2.add_trace(go.Bar(x=merged["seccion"], y=merged["residual_real"],
                name="Residual Real", marker_color="#22c55e", opacity=0.8,
                hovertemplate="%{x}<br>Residual Real: %{y:.1f}<extra></extra>"))
            fig2.update_layout(
                paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                font=dict(family="JetBrains Mono",color="#9a8a8a",size=10),
                barmode="group", height=280, margin=dict(l=5,r=5,t=5,b=80),
                legend=dict(orientation="h",y=1.1,font=dict(size=10)),
                xaxis=dict(gridcolor="#220000", tickangle=-30),
                yaxis=dict(gridcolor="#220000", title="Riesgo"),
            )
            st.plotly_chart(fig2, use_container_width=True)

    with col_b:
        # Distribución por nivel
        st.markdown('<div class="sec-title">Distribución por Nivel</div>', unsafe_allow_html=True)
        dist = df_ev["nivel_riesgo"].value_counts()
        cols_pie = {"Crítico":"#ef4444","Alto":"#f59e0b","Medio":"#eab308","Trivial":"#22c55e"}
        fig_pie = go.Figure(go.Pie(
            labels=dist.index, values=dist.values, hole=0.55,
            marker=dict(colors=[cols_pie.get(l,"#64748b") for l in dist.index],
                        line=dict(color="#080000",width=3)),
            textfont=dict(family="JetBrains Mono",size=10,color="white"),
            hovertemplate="%{label}: %{value}<extra></extra>"
        ))
        fig_pie.update_layout(
            paper_bgcolor="rgba(0,0,0,0)", height=220,
            margin=dict(l=5,r=5,t=5,b=5),
            legend=dict(font=dict(family="JetBrains Mono",size=9,color="#9a8a8a"))
        )
        st.plotly_chart(fig_pie, use_container_width=True)

        # Distribución dimensiones OT
        st.markdown('<div class="sec-title">Riesgo por Dimensión MAGERIT-OT</div>', unsafe_allow_html=True)
        dims_vals = {
            "Disponibilidad": df_ev["riesgo_D"].max(),
            "Confidencialidad": df_ev["riesgo_C"].max(),
            "Integridad": df_ev["riesgo_I"].max(),
            "Safety (SIL)": df_ev["riesgo_safety"].max(),
            "Continuidad Proceso": df_ev["riesgo_continuidad"].max(),
            "Integridad Física": df_ev["riesgo_integ_fisica"].max(),
        }
        fig_dim = go.Figure(go.Bar(
            x=list(dims_vals.values()), y=list(dims_vals.keys()),
            orientation="h",
            marker_color=["#e01010","#ff6666","#cc0000","#6366f1","#0ea5e9","#22c55e"],
            text=[f"{v:.1f}" for v in dims_vals.values()],
            textposition="outside", textfont=dict(family="JetBrains Mono",size=9,color="#f0eded"),
        ))
        fig_dim.update_layout(
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            font=dict(family="JetBrains Mono",color="#9a8a8a",size=9),
            height=240, margin=dict(l=5,r=30,t=5,b=5),
            xaxis=dict(gridcolor="#220000", range=[0,28]),
            yaxis=dict(autorange="reversed"),
        )
        st.plotly_chart(fig_dim, use_container_width=True)

    # ── Histórico de evolución del riesgo ───────────────────────
    st.markdown('<div class="sec-title">Histórico — Evolución del Riesgo por Activo</div>', unsafe_allow_html=True)

    if len(df_ev) > 0 and "created_at" in df_ev.columns:
        conn2 = get_conn()
        df_hist = pd.read_sql("""
            SELECT e.created_at, e.riesgo_potencial, e.nivel_riesgo,
                   e.amenaza_nombre, a.nombre as activo_nombre, a.seccion_aceria
            FROM evaluacion_riesgos e
            JOIN activos a ON e.activo_id = a.id
            ORDER BY e.created_at ASC
        """, conn2)
        conn2.close()

        # Selector de activo para el histórico
        activos_hist = ["Todos"] + sorted(df_hist["activo_nombre"].unique().tolist())
        col_h1, col_h2 = st.columns([2, 1])
        with col_h1:
            activo_hist_sel = st.selectbox("Filtrar por activo", activos_hist,
                key="hist_activo_sel")
        with col_h2:
            vista_hist = st.selectbox("Vista", ["Evolución temporal", "Por sección"],
                key="hist_vista")

        if activo_hist_sel != "Todos":
            df_hist = df_hist[df_hist["activo_nombre"] == activo_hist_sel]

        if len(df_hist) > 0:
            # Parsear fechas
            df_hist["fecha"] = pd.to_datetime(df_hist["created_at"], errors="coerce")
            df_hist = df_hist.dropna(subset=["fecha"])

            if vista_hist == "Evolución temporal":
                # Gráfica de línea — evolución del riesgo potencial a lo largo del tiempo
                fig_hist = go.Figure()

                if activo_hist_sel == "Todos":
                    # Agrupar por fecha (día) y mostrar riesgo máximo
                    df_grouped = df_hist.copy()
                    df_grouped["dia"] = df_grouped["fecha"].dt.date
                    df_grouped = df_grouped.groupby("dia")["riesgo_potencial"].max().reset_index()
                    fig_hist.add_trace(go.Scatter(
                        x=df_grouped["dia"], y=df_grouped["riesgo_potencial"],
                        mode="lines+markers",
                        line=dict(color="#e01010", width=2),
                        marker=dict(size=7, color="#e01010"),
                        name="Riesgo máximo diario",
                        hovertemplate="Fecha: %{x}<br>Riesgo máx: %{y:.1f}<extra></extra>"
                    ))
                else:
                    # Mostrar cada evaluación del activo seleccionado
                    for amenaza in df_hist["amenaza_nombre"].unique():
                        df_amenaza = df_hist[df_hist["amenaza_nombre"] == amenaza].sort_values("fecha")
                        if len(df_amenaza) > 0:
                            fig_hist.add_trace(go.Scatter(
                                x=df_amenaza["fecha"], y=df_amenaza["riesgo_potencial"],
                                mode="lines+markers", name=amenaza[:40],
                                marker=dict(size=7),
                                hovertemplate="<b>" + amenaza[:40] + "</b><br>%{x}<br>Riesgo: %{y:.1f}<extra></extra>"
                            ))

                # Líneas de umbral
                fig_hist.add_hline(y=15, line_dash="dash", line_color="#ef4444",
                    annotation_text="Crítico ≥15", annotation_position="right",
                    annotation_font=dict(size=9, color="#ef4444"))
                fig_hist.add_hline(y=4, line_dash="dash", line_color="#f59e0b",
                    annotation_text="Alto ≥4", annotation_position="right",
                    annotation_font=dict(size=9, color="#f59e0b"))

                fig_hist.update_layout(
                    paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                    font=dict(family="JetBrains Mono", color="#9a8a8a", size=10),
                    height=280, margin=dict(l=5, r=80, t=10, b=40),
                    xaxis=dict(gridcolor="#220000", title="Fecha"),
                    yaxis=dict(gridcolor="#220000", title="Riesgo MAGERIT-OT", range=[0, 26]),
                    legend=dict(orientation="h", y=-0.25, font=dict(size=9)),
                )
                st.plotly_chart(fig_hist, use_container_width=True)

                # Resumen estadístico
                r_ini = float(df_hist.sort_values("fecha")["riesgo_potencial"].iloc[0])
                r_act = float(df_hist.sort_values("fecha")["riesgo_potencial"].iloc[-1])
                tendencia = r_act - r_ini
                tend_col = "#22c55e" if tendencia < 0 else "#ef4444" if tendencia > 0 else "#9a8a8a"
                tend_icon = "↓" if tendencia < 0 else "↑" if tendencia > 0 else "→"
                st.markdown(
                    '<div style="display:flex;gap:1rem;flex-wrap:wrap;margin-top:0.3rem;">'
                    '<div style="background:rgba(10,0,0,0.6);border:1px solid #220000;border-radius:6px;'
                    'padding:0.5rem 0.9rem;flex:1;min-width:120px;text-align:center;">'
                    f'<div style="font-size:1.1rem;font-weight:800;color:#e01010;font-family:Syne,sans-serif;">{r_ini:.1f}</div>'
                    '<div style="font-size:0.6rem;color:#9a8a8a;">RIESGO INICIAL</div></div>'
                    '<div style="background:rgba(10,0,0,0.6);border:1px solid #220000;border-radius:6px;'
                    'padding:0.5rem 0.9rem;flex:1;min-width:120px;text-align:center;">'
                    f'<div style="font-size:1.1rem;font-weight:800;color:#f0eded;font-family:Syne,sans-serif;">{r_act:.1f}</div>'
                    '<div style="font-size:0.6rem;color:#9a8a8a;">RIESGO ACTUAL</div></div>'
                    '<div style="background:rgba(10,0,0,0.6);border:1px solid #220000;border-radius:6px;'
                    'padding:0.5rem 0.9rem;flex:1;min-width:120px;text-align:center;">'
                    f'<div style="font-size:1.1rem;font-weight:800;color:{tend_col};font-family:Syne,sans-serif;">{tend_icon} {abs(tendencia):.1f}</div>'
                    '<div style="font-size:0.6rem;color:#9a8a8a;">VARIACIÓN</div></div>'
                    '<div style="background:rgba(10,0,0,0.6);border:1px solid #220000;border-radius:6px;'
                    'padding:0.5rem 0.9rem;flex:1;min-width:120px;text-align:center;">'
                    f'<div style="font-size:1.1rem;font-weight:800;color:#9a8a8a;font-family:Syne,sans-serif;">{len(df_hist)}</div>'
                    '<div style="font-size:0.6rem;color:#9a8a8a;">EVALUACIONES</div></div>'
                    '</div>',
                    unsafe_allow_html=True
                )

            else:  # Por sección
                conn3 = get_conn()
                df_sec_hist = pd.read_sql("""
                    SELECT a.seccion_aceria,
                           MAX(e.created_at) as ultima_eval,
                           MAX(e.riesgo_potencial) as riesgo_max,
                           COUNT(e.id) as n_evaluaciones
                    FROM evaluacion_riesgos e
                    JOIN activos a ON e.activo_id = a.id
                    GROUP BY a.seccion_aceria
                    ORDER BY riesgo_max DESC
                """, conn3)
                conn3.close()

                for _, rs in df_sec_hist.iterrows():
                    r = float(rs["riesgo_max"])
                    niv = "Crítico" if r>=15 else "Alto" if r>=4 else "Medio" if r>=3 else "Trivial"
                    c_s = color_nivel(niv)
                    fecha_str = str(rs["ultima_eval"])[:10] if rs["ultima_eval"] else "—"
                    st.markdown(
                        '<div style="display:flex;justify-content:space-between;align-items:center;'
                        'background:rgba(10,0,0,0.6);border:1px solid #220000;border-left:3px solid ' + c_s + ';'
                        'border-radius:6px;padding:0.4rem 0.9rem;margin-bottom:4px;flex-wrap:wrap;gap:0.5rem;">'
                        '<div>'
                        '<span style="font-size:0.8rem;font-weight:700;color:#f0eded;">' + str(rs["seccion_aceria"]) + '</span>'
                        '<span style="font-size:0.62rem;color:#9a8a8a;margin-left:8px;">Última eval: ' + fecha_str + '</span>'
                        '</div>'
                        '<div style="display:flex;gap:0.8rem;align-items:center;">'
                        '<span style="font-size:0.62rem;color:#9a8a8a;">' + str(int(rs["n_evaluaciones"])) + ' evaluaciones</span>'
                        '<span style="font-family:Syne,sans-serif;font-size:1rem;font-weight:800;color:' + c_s + ';">' + f"{r:.1f}" + '</span>'
                        '</div></div>',
                        unsafe_allow_html=True
                    )
        else:
            st.info("No hay suficientes datos históricos para mostrar la evolución.")
    else:
        st.info("Registra evaluaciones de amenazas para ver el histórico de riesgo.")

    # Top amenazas
    st.markdown('<div class="sec-title">Top 10 — Mayor Riesgo Potencial (MAGERIT-OT)</div>', unsafe_allow_html=True)
    top = df_ev.sort_values("riesgo_potencial", ascending=False).head(10)
    for _, r in top.iterrows():
        nivel = r["nivel_riesgo"]
        c = color_nivel(nivel)
        badge_bg = (
            "background:#7f1d1d;color:#fca5a5;border:1px solid #ef4444" if nivel == "Crítico" else
            "background:#78350f;color:#fcd34d;border:1px solid #f59e0b" if nivel == "Alto" else
            "background:#422006;color:#fde68a;border:1px solid #eab308" if nivel == "Medio" else
            "background:#052e16;color:#86efac;border:1px solid #22c55e"
        )
        mult_html = ('<span style="font-family:JetBrains Mono;font-size:0.62rem;color:#f59e0b;">x'
                     + f"{r['multiplicador_ot']:.2f}" + '</span>') if r['multiplicador_ot'] > 1 else ""
        sis_html  = ('<span style="font-size:0.62rem;color:#6366f1;margin-left:6px;">SIS</span>'
                     if r.get("es_sis") else "")
        html = (
            '<div style="background:rgba(12,0,0,0.8);border:1px solid ' + c + '33;'
            'border-left:3px solid ' + c + ';border-radius:6px;'
            'padding:0.5rem 0.9rem;margin-bottom:4px;'
            'display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:0.5rem;">'
            '<div>'
            '<span style="font-family:JetBrains Mono;font-size:0.7rem;color:' + c + ';font-weight:700;">'
            + str(r['amenaza_codigo']) + '</span>'
            '<span style="font-size:0.78rem;color:#f0eded;margin-left:8px;">' + str(r['amenaza_nombre']) + '</span>'
            '<span style="font-size:0.68rem;color:#9a8a8a;margin-left:8px;">→ ' + str(r['activo_nombre']) + '</span>'
            + sis_html +
            '</div>'
            '<div style="display:flex;gap:0.8rem;align-items:center;">'
            + mult_html +
            '<span style="font-family:Syne,sans-serif;font-size:1.1rem;font-weight:800;color:' + c + ';">'
            + f"{r['riesgo_potencial']:.1f}" + '</span>'
            '<span style="' + badge_bg + ';border-radius:4px;padding:3px 10px;font-size:0.65rem;">'
            + str(nivel) + '</span>'
            '</div></div>'
        )
        st.markdown(html, unsafe_allow_html=True)

# ══════════════════════════════════════════════════════════════
# MÓDULO 2 — ACTIVOS OT
# ══════════════════════════════════════════════════════════════

def render_activos():
    st.markdown('<div class="sec-title">Inventario de Activos OT — MAGERIT-OT</div>', unsafe_allow_html=True)

    st.markdown("""
    <div class="info-box">
    Los <strong>activos OT</strong> se valoran en 3 dimensiones MAGERIT (Disponibilidad, Confidencialidad, Integridad)
    + 3 dimensiones OT adicionales (<strong>Safety SIL, Continuidad de Proceso, Integridad Física</strong>).
    El valor se <strong>propaga por dependencias</strong> siguiendo el modelo Purdue: un activo hereda el valor máximo de sus padres.
    </div>
    """, unsafe_allow_html=True)

    tab1, tab2, tab3, tab4 = st.tabs(["➕ Nuevo Activo", "📋 Inventario", "🔗 Dependencias", "📎 Evidencias"])

    with tab1:
        with st.form("form_activo"):
            st.markdown('<div class="sec-title">Identificación</div>', unsafe_allow_html=True)
            c1, c2 = st.columns(2)
            with c1:
                nombre = st.text_input("Nombre del activo *")
                tipo = st.selectbox("Tipo", TIPOS_LISTA)
            with c2:
                seccion = st.selectbox("Ubicación", SECCIONES_ACERIA)
                zona_purdue = st.selectbox("Zona Purdue", list(ZONAS_PURDUE.keys()),
                    format_func=lambda x: ZONAS_PURDUE[x])
            tipo_info = TIPOS_ACTIVO_OT[tipo]
            subtipo = st.selectbox("Modelo / Subtipo", tipo_info["subtipos"] + ["Otro"])
            st.markdown(f'<div style="font-family:JetBrains Mono; font-size:0.68rem; color:#9a8a8a; margin:-0.3rem 0 0.5rem; padding:4px 8px; background:rgba(0,0,0,0.3); border-radius:4px;">📋 {tipo_info["descripcion"]}</div>', unsafe_allow_html=True)
            descripcion = st.text_area("Descripción / Identificador", height=60,
                placeholder="Ej: PLC-HORNO-01 · N/S: XYZ123 · IP: 192.168.10.5")
            es_sis = st.checkbox(
                "Activo SIS (Sistema Instrumentado de Seguridad) — Multiplicador ×1.5",
                value=tipo_info["es_sis_tipico"],
                help="Se marca automáticamente para tipos con función de seguridad (ZIV, BERTHOLD)"
            )

            # ── DISPONIBILIDAD ──────────────────────────────────────────
            st.markdown('<div class="sec-title">Disponibilidad (D) — ¿Qué impacto tiene que este activo no esté disponible?</div>', unsafe_allow_html=True)
            st.markdown(
                '<div class="info-box">'
                '<strong>Pregunta guía:</strong> Si este activo fallara ahora mismo, ¿en cuánto tiempo se vería afectada la producción?<br><br>'
                '<span style="font-size:0.7rem;">'
                '<b style="color:#e01010;">0</b> — Sin impacto: puede estar parado indefinidamente &nbsp;|&nbsp; '
                '<b style="color:#e01010;">1</b> — Leve: proceso continúa con alternativa manual &nbsp;|&nbsp; '
                '<b style="color:#e01010;">2</b> — Significativo: reduce eficiencia, no se detiene<br>'
                '<b style="color:#e01010;">3</b> — Grave: parada parcial de sección, pérdida medible &nbsp;|&nbsp; '
                '<b style="color:#e01010;">4</b> — Crítico: parada total sección (EAF/Colada) &gt;1h &nbsp;|&nbsp; '
                '<b style="color:#e01010;">5</b> — Catastrófico: parada de planta o daño irreversible'
                '</span></div>',
                unsafe_allow_html=True)
            st.markdown('<div style="font-size:0.68rem;color:#9a8a8a;margin-bottom:0.3rem;">Valora el impacto según el tiempo máximo tolerable sin este activo:</div>', unsafe_allow_html=True)
            dc1,dc2,dc3,dc4,dc5 = st.columns(5)
            with dc1: d15m = st.number_input("15 min", 0, 5, 0,
                help="¿La producción se ve afectada en <15 min si falla? EAF activo, Colada en curso → 4 o 5")
            with dc2: d3h  = st.number_input("3 horas", 0, 5, 0,
                help="¿Qué impacto si está parado 3 horas? Turno completo perdido → 3 o 4")
            with dc3: d1d  = st.number_input("1 día", 0, 5, 0,
                help="¿Qué impacto si está parado 1 día? Producción diaria perdida → 3")
            with dc4: d1s  = st.number_input("1 semana", 0, 5, 0,
                help="¿Qué impacto si está parado 1 semana? Penalización contratos → 2 o 3")
            with dc5: d2s  = st.number_input("2 semanas", 0, 5, 0,
                help="¿Qué impacto si está parado 2+ semanas? Equipos auxiliares → 1")

            # ── CONFIDENCIALIDAD ─────────────────────────────────────────
            st.markdown('<div class="sec-title">Confidencialidad (C) — ¿Qué impacto tiene que la información de este activo sea conocida por terceros?</div>', unsafe_allow_html=True)
            st.markdown(
                '<div class="info-box">'
                '<strong>Pregunta guía:</strong> ¿Qué consecuencias tendría que alguien accediera a los datos o configuración de este activo?<br><br>'
                '<span style="font-size:0.7rem;">'
                '<b style="color:#e01010;">RI Interno</b>: acceso por empleado no autorizado — ej. operador ve configuración PLC de otra sección<br>'
                '<b style="color:#e01010;">RE Externo</b>: acceso por atacante externo — ej. competencia obtiene recetas de acero o lógica de control<br><br>'
                '0=Sin impacto · 1=Datos públicos · 2=Uso interno · 3=Confidencial · 4=Reservado · 5=Secreto industrial/seguridad crítica'
                '</span></div>',
                unsafe_allow_html=True)
            cc1,cc2 = st.columns(2)
            with cc1: c_ri = st.number_input("Riesgo Interno (RI)", 0, 5, 0,
                help="Impacto si un empleado de Sidenor sin autorización accede a este activo. PC S7 con proyectos PLC → 4")
            with cc2: c_re = st.number_input("Riesgo Externo (RE)", 0, 5, 0,
                help="Impacto si un atacante externo accede. PC S7, PC HMI, RTU subestación → 4 o 5")

            # ── INTEGRIDAD ───────────────────────────────────────────────
            st.markdown('<div class="sec-title">Integridad (I) — ¿Qué impacto tiene que los datos o configuración sean incorrectos o manipulados?</div>', unsafe_allow_html=True)
            st.markdown(
                '<div class="info-box">'
                '<strong>Pregunta guía:</strong> ¿Qué ocurre si este activo opera con datos erróneos o una configuración manipulada?<br><br>'
                '<span style="font-size:0.7rem;">'
                '<b style="color:#e01010;">DT Datos</b>: datos almacenados incorrectos — ej. Historian con temperaturas EAF erróneas<br>'
                '<b style="color:#e01010;">ERR Errores</b>: errores en proceso — ej. PLC ejecuta lógica con señal de sensor corrupto<br>'
                '<b style="color:#e01010;">MAN Manipulación</b>: modificación maliciosa — ej. setpoint EAF alterado → temperatura incorrecta → daño<br>'
                '<b style="color:#e01010;">AUT Autenticidad</b>: origen no verificable — ej. comando enviado suplantando operador legítimo (SIS → 5)'
                '</span></div>',
                unsafe_allow_html=True)
            ic1,ic2,ic3,ic4 = st.columns(4)
            with ic1: i_dt  = st.number_input("Datos (DT)", 0, 5, 0,
                help="Impacto de tener datos almacenados incorrectos. Historian, NAS → 3. PLC con lógica corrupta → 5")
            with ic2: i_err = st.number_input("Errores (ERR)", 0, 5, 0,
                help="Impacto si el activo procesa datos con errores. PLC con señal de sensor corrupto → 4 o 5")
            with ic3: i_man = st.number_input("Manipulación (MAN)", 0, 5, 0,
                help="Impacto si alguien modifica configuración/setpoints maliciosamente. PLC Horno, PC REG → 5")
            with ic4: i_aut = st.number_input("Autenticidad (AUT)", 0, 5, 0,
                help="Impacto si no se puede verificar el origen de comandos. SIS, ZIV, PLC → 5")

            # ── DIMENSIONES OT ───────────────────────────────────────────
            st.markdown('<div class="sec-title">Dimensiones OT — Exclusivas del entorno industrial (aportación MAGERIT-OT)</div>', unsafe_allow_html=True)
            st.markdown(
                '<div class="info-box">'
                'Estas tres dimensiones <b>no existen en MAGERIT IT tradicional</b>. Son la aportación central de este trabajo '
                'para adaptar la metodología al entorno OT/ICS de una acería.<br><br>'
                '<span style="font-size:0.7rem;">'
                '<b style="color:#6366f1;">Safety SIL</b>: ¿Tiene función de seguridad funcional certificada? '
                '0=Sin SIL · 1=Contribuye indirectamente · 2=SIL1 · 3=SIL2 (parada emergencia) · 4=SIL3/4<br>'
                '<b style="color:#0ea5e9;">Continuidad</b>: ¿Su fallo detiene o degrada el proceso productivo? '
                '0=Sin impacto · 3=Parada parcial sección · 5=Parada total planta<br>'
                '<b style="color:#22c55e;">Integ. Física</b>: ¿Un valor erróneo puede causar daño físico a personas o instalaciones? '
                '0=Sin riesgo · 3=Daño material · 5=Riesgo vital<br><br>'
                '<i>Ejemplos: BERTHOLD (nivel acero colada) → Safety 4, Int.Física 5 &nbsp;|&nbsp; '
                'PLC EAF → Safety 3, Continuidad 5 &nbsp;|&nbsp; PC PRO → todo a 0</i>'
                '</span></div>',
                unsafe_allow_html=True)
            oc1,oc2,oc3 = st.columns(3)
            with oc1:
                ot_sil = st.number_input("Safety SIL (0-4)", 0, 4, 0,
                    help="0=Sin función seguridad (PC, CCTV) · 1=Contribuye indirectamente · 2=SIL1 · 3=SIL2 parada emergencia · 4=SIL3/4 (ZIV protecciones, BERTHOLD colada)")
            with oc2:
                ot_cont = st.number_input("Continuidad Proceso (0-5)", 0, 5, 0,
                    help="0=No afecta producción · 3=Parada parcial sección · 5=Parada total planta. UPS, COMM principal, PLC EAF → 5")
            with oc3:
                ot_fisic = st.number_input("Integridad Física (0-5)", 0, 5, 0,
                    help="0=Sin riesgo físico · 3=Posible daño material · 5=Riesgo vital. BERTHOLD colada, sensores EAF, ZIV → 4 o 5")

            submitted = st.form_submit_button("Registrar Activo OT", use_container_width=True)
            if submitted and nombre:
                conn = get_conn()
                conn.execute("""INSERT INTO activos
                    (nombre,tipo,subtipo,descripcion,seccion_aceria,zona_purdue,es_sis,
                     disp_15m,disp_3h,disp_1d,disp_1s,disp_2s,
                     conf_ri,conf_re,int_dt,int_err,int_man,int_aut,
                     ot_safety_sil,ot_continuidad,ot_integ_fisica)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (nombre,tipo,subtipo,descripcion,seccion,zona_purdue,int(es_sis),
                     d15m,d3h,d1d,d1s,d2s,c_ri,c_re,i_dt,i_err,i_man,i_aut,
                     ot_sil,ot_cont,ot_fisic))
                conn.commit(); conn.close()
                st.success(f"✅Activo **{nombre}** registrado.")
                st.rerun()

    with tab2:
        conn = get_conn()
        df = pd.read_sql("SELECT * FROM activos ORDER BY id DESC", conn)
        # Cargar conteo evidencias por activo
        df_ev_count = pd.read_sql(
            "SELECT activo_id, COUNT(*) as n_ev FROM evidencias GROUP BY activo_id", conn)
        ev_count_map = {int(r["activo_id"]): int(r["n_ev"]) for _, r in df_ev_count.iterrows()}
        conn.close()
        if len(df) == 0:
            st.info("No hay activos. Añade uno en la pestaña 'Nuevo Activo'.")
        else:
            for _, r in df.iterrows():
                v_D   = max(int(r.disp_15m or 0), int(r.disp_3h or 0), int(r.disp_1d or 0), int(r.disp_1s or 0), int(r.disp_2s or 0))
                v_C   = max(int(r.conf_ri or 0), int(r.conf_re or 0))
                v_I   = max(int(r.int_dt or 0), int(r.int_err or 0), int(r.int_man or 0), int(r.int_aut or 0))
                v_sil = int(r.ot_safety_sil or 0)
                v_cnt = int(r.ot_continuidad or 0)
                v_fis = int(r.ot_integ_fisica or 0)
                mult  = multiplicador_ot(int(r.zona_purdue or 2), bool(r.es_sis))
                zona_lbl  = ZONAS_PURDUE.get(int(r.zona_purdue or 2), "")
                sis_html  = '<span style="background:#1e1b4b;color:#a5b4fc;border:1px solid #6366f1;border-radius:4px;padding:2px 7px;font-size:0.62rem;margin-left:6px;">SIS</span>' if r.es_sis else ""
                tipo_desc = TIPOS_ACTIVO_OT.get(str(r.tipo or ""), {}).get("descripcion", "")
                n_evidencias = ev_count_map.get(int(r["id"]), 0)
                ev_badge = (f'<span style="font-family:JetBrains Mono;font-size:0.6rem;color:#22c55e;'
                           f'border:1px solid #22c55e55;border-radius:3px;padding:1px 5px;margin-left:6px;">'
                           f'📎 {n_evidencias} evidencia(s)</span>') if n_evidencias > 0 else (
                           f'<span style="font-family:JetBrains Mono;font-size:0.6rem;color:#6a5a5a;'
                           f'border:1px solid #33000055;border-radius:3px;padding:1px 5px;margin-left:6px;">'
                           f'📎 Sin evidencias</span>')

                html = (
                    '<div style="background:rgba(16,2,2,0.88);border:1px solid rgba(200,0,0,0.18);'
                    'border-left:3px solid #e01010;border-radius:10px;padding:1rem 1.4rem;margin-bottom:0.6rem;">'
                    '<div style="display:flex;justify-content:space-between;flex-wrap:wrap;gap:0.5rem;">'
                    '<div style="flex:1;min-width:180px;">'
                    '<span style="font-family:Syne,sans-serif;font-size:0.95rem;font-weight:700;color:#f0eded;">'
                    + str(r['nombre']) + '</span>' + sis_html +
                    '<div style="font-size:0.7rem;color:#9a8a8a;margin-top:3px;">'
                    + str(r['tipo'] or '') + ' · ' + str(r['subtipo'] or '') + ' · ' + str(r['seccion_aceria'] or '') +
                    '</div>'
                    '<div style="font-size:0.62rem;color:#6a5a5a;">' + zona_lbl + '</div>'
                    '<div style="font-size:0.6rem;color:#4a3a3a;font-style:italic;">' + tipo_desc + '</div>'
                    + ev_badge +
                    '</div>'
                    '<div style="display:flex;gap:1.2rem;align-items:center;flex-wrap:wrap;">'
                    '<div style="text-align:center;"><div style="font-size:1.2rem;font-weight:800;color:#e01010;font-family:Syne,sans-serif;">' + str(v_D) + '</div><div style="font-size:0.55rem;color:#9a8a8a;">DISP</div></div>'
                    '<div style="text-align:center;"><div style="font-size:1.2rem;font-weight:800;color:#e01010;font-family:Syne,sans-serif;">' + str(v_C) + '</div><div style="font-size:0.55rem;color:#9a8a8a;">CONF</div></div>'
                    '<div style="text-align:center;"><div style="font-size:1.2rem;font-weight:800;color:#e01010;font-family:Syne,sans-serif;">' + str(v_I) + '</div><div style="font-size:0.55rem;color:#9a8a8a;">INTEG</div></div>'
                    '<div style="text-align:center;"><div style="font-size:1.2rem;font-weight:800;color:#6366f1;font-family:Syne,sans-serif;">' + str(v_sil) + '</div><div style="font-size:0.55rem;color:#9a8a8a;">SIL</div></div>'
                    '<div style="text-align:center;"><div style="font-size:1.2rem;font-weight:800;color:#0ea5e9;font-family:Syne,sans-serif;">' + str(v_cnt) + '</div><div style="font-size:0.55rem;color:#9a8a8a;">CONT</div></div>'
                    '<div style="text-align:center;"><div style="font-size:1.2rem;font-weight:800;color:#22c55e;font-family:Syne,sans-serif;">' + str(v_fis) + '</div><div style="font-size:0.55rem;color:#9a8a8a;">INT.FÍS</div></div>'
                    '<div style="text-align:center;"><div style="font-size:1.2rem;font-weight:800;color:#f59e0b;font-family:Syne,sans-serif;">x' + f'{mult:.2f}' + '</div><div style="font-size:0.55rem;color:#9a8a8a;">MULT OT</div></div>'
                    '</div></div></div>'
                )
                st.markdown(html, unsafe_allow_html=True)

    with tab3:
        st.markdown('<div class="sec-title">Dependencias entre Activos (Modelo Purdue)</div>', unsafe_allow_html=True)
        st.markdown("""
        <div class="info-box">
        <strong>Propagación por dependencias</strong>: el valor de un activo hijo hereda el MAX del valor de sus padres.
        Ejemplo: un PLC (N1) que depende de una LAN OT (N2) heredará su valor de disponibilidad si es mayor.
        Fórmula: <code>V_hijo[dim] = MAX(V_propio[dim], MAX(V_padre[dim]))</code>
        </div>
        """, unsafe_allow_html=True)

        conn = get_conn()
        df_act = pd.read_sql("SELECT id, nombre, tipo FROM activos", conn)

        if len(df_act) >= 2:
            col_d1, col_d2 = st.columns(2)
            with col_d1:
                hijo_opts = {r["nombre"]: r["id"] for _, r in df_act.iterrows()}
                hijo_sel = st.selectbox("Activo hijo (depende de)", list(hijo_opts.keys()))
                hijo_id = hijo_opts[hijo_sel]
            with col_d2:
                padre_opts = {r["nombre"]: r["id"] for _, r in df_act.iterrows() if r["nombre"] != hijo_sel}
                padre_sel = st.selectbox("Activo padre (del que depende)", list(padre_opts.keys()))
                padre_id = padre_opts[padre_sel]

            if st.button("➕ Añadir Dependencia"):
                conn.execute("INSERT INTO dependencias (activo_hijo_id, activo_padre_id) VALUES (?,?)",
                             (hijo_id, padre_id))
                conn.commit()
                st.success(f"Dependencia: **{hijo_sel}** → **{padre_sel}**")
                st.rerun()

        # Mostrar dependencias existentes
        df_deps = pd.read_sql("""
            SELECT d.id, h.nombre as hijo, p.nombre as padre
            FROM dependencias d
            JOIN activos h ON d.activo_hijo_id = h.id
            JOIN activos p ON d.activo_padre_id = p.id
        """, conn)
        conn.close()

        if len(df_deps) > 0:
            st.markdown('<div class="sec-title">Dependencias Registradas</div>', unsafe_allow_html=True)
            for _, dep in df_deps.iterrows():
                st.markdown(f"""
                <div style="display:flex; align-items:center; gap:0.8rem; padding:0.4rem 0.8rem;
                     background:rgba(12,0,0,0.6); border:1px solid #220000; border-radius:6px; margin-bottom:4px;">
                    <span style="color:#f0eded; font-size:0.78rem;">{dep['hijo']}</span>
                    <span style="color:#e01010;">→ depende de →</span>
                    <span style="color:#f0eded; font-size:0.78rem;">{dep['padre']}</span>
                </div>""", unsafe_allow_html=True)

    with tab4:
        st.markdown('<div class="sec-title">Evidencias de Auditoría por Activo</div>', unsafe_allow_html=True)
        st.markdown("""
        <div class="info-box">
        📎 Adjunta documentación que acredite la implantación de controles: procedimientos, certificados,
        capturas de configuración, actas de revisión, etc. Estas evidencias son necesarias para demostrar
        el cumplimiento ante una inspección NIS2.
        </div>
        """, unsafe_allow_html=True)

        conn = get_conn()
        df_act_ev = pd.read_sql("SELECT id, nombre, tipo, seccion_aceria FROM activos ORDER BY nombre", conn)

        if len(df_act_ev) == 0:
            st.info("No hay activos registrados. Añade activos primero.")
            conn.close()
        else:
            col_ev1, col_ev2 = st.columns([1, 2])

            with col_ev1:
                activo_ev_opts = {r["nombre"]: r["id"] for _, r in df_act_ev.iterrows()}
                activo_ev_sel  = st.selectbox("Seleccionar activo", list(activo_ev_opts.keys()),
                    key="ev_activo_sel")
                activo_ev_id = activo_ev_opts[activo_ev_sel]

                # Conteo evidencias del activo
                n_ev_act = conn.execute(
                    "SELECT COUNT(*) FROM evidencias WHERE activo_id=?", (activo_ev_id,)
                ).fetchone()[0]
                st.markdown(
                    f'<div style="font-family:JetBrains Mono;font-size:0.7rem;color:#9a8a8a;'
                    f'margin:0.4rem 0;">📎 {n_ev_act} evidencia(s) registrada(s)</div>',
                    unsafe_allow_html=True)

                st.markdown('<div class="sec-title">Subir nueva evidencia</div>', unsafe_allow_html=True)
                fichero = st.file_uploader(
                    "Selecciona fichero",
                    type=["pdf","docx","doc","png","jpg","jpeg","xlsx","txt"],
                    key="ev_uploader"
                )
                descripcion_ev = st.text_input("Descripción de la evidencia",
                    placeholder="Ej: Procedimiento backup PLCs v2.1",
                    key="ev_desc")

                if fichero is not None:
                    tipo_map = {
                        "pdf":"PDF","docx":"Word","doc":"Word","png":"Imagen",
                        "jpg":"Imagen","jpeg":"Imagen","xlsx":"Excel","txt":"Texto"
                    }
                    ext = fichero.name.split(".")[-1].lower()
                    tipo_fich = tipo_map.get(ext, "Otro")
                    datos = fichero.read()
                    tamanio = len(datos)

                    if st.button("💾 Guardar Evidencia", use_container_width=True, key="ev_save"):
                        conn.execute("""INSERT INTO evidencias
                            (activo_id, nombre_fichero, tipo_fichero, descripcion, datos, tamanio)
                            VALUES (?,?,?,?,?,?)""",
                            (activo_ev_id, fichero.name, tipo_fich,
                             descripcion_ev, datos, tamanio))
                        conn.commit()
                        st.success(f"✅ Evidencia '{fichero.name}' guardada.")
                        st.rerun()

            with col_ev2:
                st.markdown('<div class="sec-title">Evidencias registradas</div>', unsafe_allow_html=True)
                df_evids = pd.read_sql(
                    """SELECT id, nombre_fichero, tipo_fichero, descripcion,
                              tamanio, datos, created_at
                       FROM evidencias WHERE activo_id=? ORDER BY created_at DESC""",
                    conn, params=(activo_ev_id,))

                if len(df_evids) == 0:
                    st.info("No hay evidencias para este activo.")
                else:
                    for _, ev in df_evids.iterrows():
                        tipo_col = {"PDF":"#ef4444","Word":"#3b82f6","Imagen":"#22c55e",
                                    "Excel":"#16a34a","Texto":"#9a8a8a"}.get(
                                    str(ev["tipo_fichero"]),"#9a8a8a")
                        tam_kb = round(int(ev["tamanio"] or 0) / 1024, 1)
                        fecha_ev = str(ev["created_at"])[:10]

                        col_info, col_dl, col_del = st.columns([4, 1, 1])
                        with col_info:
                            st.markdown(
                                '<div style="background:rgba(10,0,0,0.6);border:1px solid #220000;'
                                'border-left:3px solid ' + tipo_col + ';border-radius:6px;'
                                'padding:0.5rem 0.8rem;margin-bottom:4px;">'
                                '<div style="display:flex;align-items:center;gap:0.6rem;">'
                                '<span style="font-family:JetBrains Mono;font-size:0.65rem;color:' + tipo_col + ';'
                                'border:1px solid ' + tipo_col + '55;border-radius:3px;padding:1px 5px;">'
                                + str(ev["tipo_fichero"]) + '</span>'
                                '<span style="font-size:0.78rem;color:#f0eded;font-weight:600;">'
                                + str(ev["nombre_fichero"]) + '</span>'
                                '</div>'
                                '<div style="font-size:0.65rem;color:#9a8a8a;margin-top:2px;">'
                                + (str(ev["descripcion"]) if ev["descripcion"] else "Sin descripción")
                                + f' · {tam_kb} KB · {fecha_ev}</div>'
                                '</div>',
                                unsafe_allow_html=True
                            )
                        with col_dl:
                            # Determinar mime type
                            ext_dl = str(ev["nombre_fichero"]).split(".")[-1].lower()
                            mime_map = {"pdf":"application/pdf","docx":"application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                                        "doc":"application/msword","png":"image/png","jpg":"image/jpeg",
                                        "jpeg":"image/jpeg","xlsx":"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                        "txt":"text/plain"}
                            mime_type = mime_map.get(ext_dl, "application/octet-stream")
                            st.download_button(
                                "⬇️",
                                data=bytes(ev["datos"]),
                                file_name=str(ev["nombre_fichero"]),
                                mime=mime_type,
                                key=f"dl_ev_{ev['id']}",
                                use_container_width=True
                            )
                        with col_del:
                            if st.button("🗑️", key=f"del_ev_{ev['id']}", use_container_width=True):
                                conn.execute("DELETE FROM evidencias WHERE id=?", (ev["id"],))
                                conn.commit()
                                st.rerun()

            conn.close()

# ══════════════════════════════════════════════════════════════
# MÓDULO 3 — AMENAZAS OT
# ══════════════════════════════════════════════════════════════

def render_amenazas():
    st.markdown('<div class="sec-title">Evaluación de Amenazas OT/ICS — MAGERIT-OT</div>', unsafe_allow_html=True)

    st.markdown("""
    <div class="info-box">
    <strong>Catálogo de amenazas OT/ICS</strong> basado en ISO27005, ICS-CERT y MITRE ATT&CK for ICS.
    Incluye amenazas específicas de acería y ciberataques ICS (Stuxnet, ransomware OT, lateral movement).
    <br><strong>Fórmula MAGERIT-OT</strong>: R = V_activo[dim] × Prob_amenaza[dim] × Multiplicador_OT
    <br>El multiplicador penaliza activos SIS (×1.5) y zonas bajas del Purdue (nivel 0: ×1.4, nivel 1: ×1.25).
    </div>
    """, unsafe_allow_html=True)

    conn = get_conn()
    df_act = pd.read_sql("SELECT id, nombre, tipo, zona_purdue, es_sis, seccion_aceria FROM activos", conn)
    conn.close()

    if len(df_act) == 0:
        st.warning("No hay activos. Ve a **Activos OT** primero.")
        return

    tab1, tab2 = st.tabs(["Registrar Evaluación", "Evaluaciones Registradas"])

    with tab1:
        col_ev1, col_ev2 = st.columns([1,2])

        with col_ev1:
            activo_opts = {r["nombre"]: r["id"] for _, r in df_act.iterrows()}
            activo_sel = st.selectbox("Activo a evaluar", list(activo_opts.keys()))
            activo_id = activo_opts[activo_sel]
            activo_row = df_act[df_act["id"]==activo_id].iloc[0]

            mult = multiplicador_ot(int(activo_row["zona_purdue"]), bool(activo_row["es_sis"]))
            zona_lbl = ZONAS_PURDUE.get(int(activo_row["zona_purdue"]), "")
            sis_str = "🔴 SIS" if activo_row["es_sis"] else ""

            sis_tag = ('<span style="background:#1e1b4b;color:#a5b4fc;border:1px solid #6366f1;'
                       'border-radius:4px;padding:2px 7px;font-size:0.62rem;margin-left:8px;">SIS</span>'
                       if sis_str else "")
            st.markdown(
                '<div style="background:rgba(16,2,2,0.88);border:1px solid rgba(200,0,0,0.18);'
                'border-left:3px solid #e01010;border-radius:10px;padding:1rem 1.4rem;margin-top:0.5rem;">'
                '<div style="font-size:0.95rem;font-weight:700;color:#f0eded;">' + str(activo_sel) + '</div>'
                '<div style="font-size:0.7rem;color:#9a8a8a;">' + str(activo_row['tipo']) + ' · ' + zona_lbl + '</div>'
                '<div style="margin-top:0.5rem;display:flex;gap:1rem;align-items:center;">'
                '<div style="font-size:1.4rem;font-weight:800;color:#e01010;font-family:Syne,sans-serif;">x' + f'{mult:.2f}' + '</div>'
                '<div style="font-size:0.65rem;color:#9a8a8a;">Multiplicador<br>MAGERIT-OT</div>'
                + sis_tag +
                '</div></div>',
                unsafe_allow_html=True)

            # Filtro categoría amenaza
            cats = sorted(set(a["categoria"] for a in AMENAZAS_OT))
            cat_sel = st.selectbox("Filtrar por categoría", ["Todas"] + cats)
            amenazas_filtradas = [a for a in AMENAZAS_OT if cat_sel == "Todas" or a["categoria"] == cat_sel]
            amenaza_opts = {f"[{a['codigo']}] {a['nombre']}": a for a in amenazas_filtradas}
            amenaza_sel_key = st.selectbox("Amenaza", list(amenaza_opts.keys()))
            amenaza = amenaza_opts[amenaza_sel_key]

            valor_amenaza = st.slider("Probabilidad amenaza (1=Improbable → 5=Casi seguro)", 1, 5, 2)
            prob_labels = {1:"Improbable", 2:"Bastante improbable", 3:"Probable", 4:"Muy probable", 5:"Casi seguro"}
            st.markdown(f'<div style="font-size:0.68rem; color:#9a8a8a;">→ {prob_labels[valor_amenaza]}</div>', unsafe_allow_html=True)

            notas = st.text_area("Notas", height=60)

        with col_ev2:
            st.markdown('<div class="sec-title">Vista previa del Riesgo MAGERIT-OT</div>', unsafe_allow_html=True)
            conn = get_conn()
            resultado = calcular_riesgo(activo_id, amenaza, valor_amenaza, conn)
            conn.close()

            r_pot = resultado["riesgo_potencial"]
            nivel = resultado["nivel_riesgo"]
            c_nivel = color_nivel(nivel)

            # Mostrar fórmula
            val_activo = valor_activo_con_dependencias(activo_id, get_conn())
            st.markdown(f"""
            <div class="formula-box">
            R = V_activo × Prob_amenaza × Mult_OT<br>
            R_D  = {val_activo['D']} × {max(amenaza['disp'])} × {mult:.2f} = <strong>{resultado['riesgo_D']:.2f}</strong><br>
            R_C  = {val_activo['C']} × {max(amenaza['conf']) if amenaza['conf'] else 0} × {mult:.2f} = <strong>{resultado['riesgo_C']:.2f}</strong><br>
            R_I  = {val_activo['I']} × {max(amenaza['integ']) if amenaza['integ'] else 0} × {mult:.2f} = <strong>{resultado['riesgo_I']:.2f}</strong><br>
            R_Safety = {val_activo['SAFETY']} × {amenaza['safety']} × {mult:.2f} = <strong>{resultado['riesgo_safety']:.2f}</strong><br>
            R_Cont   = {val_activo['CONT']} × {amenaza['cont']} × {mult:.2f} = <strong>{resultado['riesgo_continuidad']:.2f}</strong><br>
            R_Fisic  = {val_activo['FISIC']} × {amenaza['fisic']} × {mult:.2f} = <strong>{resultado['riesgo_integ_fisica']:.2f}</strong><br>
            <span style="color:#ff8888; font-weight:700;">R_potencial = MAX = {r_pot:.2f}</span>
            </div>
            """, unsafe_allow_html=True)

            # Radar de dimensiones
            dims_r = ["Disponib.", "Confidenc.", "Integridad", "Safety", "Continuidad", "Integ.Física"]
            vals_r = [resultado["riesgo_D"], resultado["riesgo_C"], resultado["riesgo_I"],
                      resultado["riesgo_safety"], resultado["riesgo_continuidad"], resultado["riesgo_integ_fisica"]]

            fig_radar = go.Figure(go.Scatterpolar(
                r=vals_r + [vals_r[0]], theta=dims_r + [dims_r[0]],
                fill="toself", fillcolor=f"rgba(224,16,16,0.15)",
                line=dict(color="#e01010", width=2),
                hovertemplate="%{theta}: %{r:.2f}<extra></extra>"
            ))
            fig_radar.update_layout(
                polar=dict(
                    bgcolor="rgba(0,0,0,0)",
                    radialaxis=dict(visible=True, range=[0,25], gridcolor="#330000",
                                   tickfont=dict(family="JetBrains Mono",size=8,color="#9a8a8a")),
                    angularaxis=dict(gridcolor="#330000",
                                    tickfont=dict(family="JetBrains Mono",size=9,color="#9a8a8a"))
                ),
                paper_bgcolor="rgba(0,0,0,0)",
                height=260, margin=dict(l=20,r=20,t=20,b=20)
            )
            st.plotly_chart(fig_radar, use_container_width=True)

            # Resultado final
            badge_bg_r = (
                "background:#7f1d1d;color:#fca5a5;border:1px solid #ef4444" if nivel == "Crítico" else
                "background:#78350f;color:#fcd34d;border:1px solid #f59e0b" if nivel == "Alto" else
                "background:#422006;color:#fde68a;border:1px solid #eab308" if nivel == "Medio" else
                "background:#052e16;color:#86efac;border:1px solid #22c55e"
            )
            st.markdown(
                '<div style="background:rgba(12,0,0,0.9);border:2px solid ' + c_nivel + ';'
                'border-radius:10px;padding:1rem;text-align:center;margin-top:0.5rem;">'
                '<div style="font-size:0.65rem;color:#9a8a8a;letter-spacing:3px;">RIESGO POTENCIAL</div>'
                '<div style="font-size:2.5rem;font-weight:800;color:' + c_nivel + ';font-family:Syne,sans-serif;">'
                + f"{r_pot:.2f}" + '</div>'
                '<span style="' + badge_bg_r + ';border-radius:4px;padding:4px 14px;font-size:0.75rem;">'
                + str(nivel) + '</span>'
                '<div style="font-size:0.65rem;color:#9a8a8a;margin-top:0.5rem;">Umbral ≥15: Crítico · ≥4: Alto · ≥3: Medio</div>'
                '</div>',
                unsafe_allow_html=True)

            if st.button("Registrar Evaluación de Riesgo", use_container_width=True):
                conn = get_conn()
                conn.execute("""INSERT INTO evaluacion_riesgos
                    (activo_id, amenaza_codigo, amenaza_nombre, amenaza_categoria, valor_amenaza,
                     riesgo_D, riesgo_C, riesgo_I, riesgo_safety, riesgo_continuidad, riesgo_integ_fisica,
                     riesgo_potencial, nivel_riesgo, multiplicador_ot, notas)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (activo_id, amenaza["codigo"], amenaza["nombre"], amenaza["categoria"], valor_amenaza,
                     resultado["riesgo_D"], resultado["riesgo_C"], resultado["riesgo_I"],
                     resultado["riesgo_safety"], resultado["riesgo_continuidad"], resultado["riesgo_integ_fisica"],
                     r_pot, nivel, mult, notas))
                conn.commit(); conn.close()
                st.success("✅ Evaluación registrada.")
                st.rerun()

    with tab2:
        conn = get_conn()
        df_ev = pd.read_sql("""
            SELECT e.id, a.nombre as activo, e.amenaza_codigo, e.amenaza_nombre,
                   e.amenaza_categoria, e.valor_amenaza, e.riesgo_potencial,
                   e.nivel_riesgo, e.multiplicador_ot,
                   e.riesgo_D, e.riesgo_C, e.riesgo_I,
                   e.riesgo_safety, e.riesgo_continuidad, e.riesgo_integ_fisica
            FROM evaluacion_riesgos e JOIN activos a ON e.activo_id=a.id
            ORDER BY e.riesgo_potencial DESC
        """, conn)
        conn.close()
        if len(df_ev) == 0:
            st.info("No hay evaluaciones registradas.")
        else:
            st.dataframe(df_ev, use_container_width=True, hide_index=True)
            csv = df_ev.to_csv(index=False).encode("utf-8")
            st.download_button("⬇️ Exportar CSV", csv, "evaluaciones_magerit_ot.csv", "text/csv")

# ══════════════════════════════════════════════════════════════
# MÓDULO 4 — TRATAMIENTO DE RIESGOS
# ══════════════════════════════════════════════════════════════

def render_tratamiento():
    st.markdown('<div class="sec-title">Tratamiento de Riesgos — Salvaguardas y Residual MAGERIT-OT</div>', unsafe_allow_html=True)

    st.markdown("""
    <div class="info-box">
    Implementa salvaguardas y calcula el <strong>Riesgo Residual</strong> siguiendo la fórmula MAGERIT:<br>
    <strong>R_objetivo</strong> = R_potencial × ∏(1 − eficacia_i) &nbsp;|&nbsp;
    <strong>R_real</strong> = R_potencial × ∏(1 − eficacia_i × CMM_i)<br>
    Donde CMM: 0→0% · 1→25% · 2→50% · 3→90% · 4→100%
    </div>
    """, unsafe_allow_html=True)

    conn = get_conn()
    df_ev = pd.read_sql("""
        SELECT e.id, e.amenaza_codigo, e.amenaza_nombre, e.riesgo_potencial,
               e.nivel_riesgo, e.multiplicador_ot, a.nombre as activo
        FROM evaluacion_riesgos e JOIN activos a ON e.activo_id=a.id
        ORDER BY e.riesgo_potencial DESC
    """, conn)
    conn.close()

    if len(df_ev) == 0:
        st.warning("No hay evaluaciones. Ve a **Amenazas OT** primero.")
        return

    tab1, tab2 = st.tabs(["Nueva Salvaguarda", "Análisis Residual"])

    with tab1:
        col_t1, col_t2 = st.columns([1,2])

        with col_t1:
            ev_opts = {f"[{r['amenaza_codigo']}] {r['amenaza_nombre']} → {r['activo']}": r['id']
                       for _, r in df_ev.iterrows()}
            ev_sel = st.selectbox("Riesgo a tratar", list(ev_opts.keys()))
            ev_id = ev_opts[ev_sel]
            ev_row = df_ev[df_ev["id"]==ev_id].iloc[0]

            r_pot = ev_row["riesgo_potencial"]
            niv   = ev_row["nivel_riesgo"]
            c     = color_nivel(niv)
            badge_bg_t = (
                "background:#7f1d1d;color:#fca5a5;border:1px solid #ef4444" if niv == "Crítico" else
                "background:#78350f;color:#fcd34d;border:1px solid #f59e0b" if niv == "Alto" else
                "background:#422006;color:#fde68a;border:1px solid #eab308" if niv == "Medio" else
                "background:#052e16;color:#86efac;border:1px solid #22c55e"
            )
            st.markdown(
                '<div style="background:rgba(16,2,2,0.88);border:1px solid rgba(200,0,0,0.18);'
                'border-left:3px solid #e01010;border-radius:10px;padding:1rem 1.4rem;">'
                '<div style="font-size:0.7rem;color:#9a8a8a;">Riesgo Potencial</div>'
                '<div style="font-size:1.8rem;font-weight:800;color:' + c + ';font-family:Syne,sans-serif;">'
                + f"{r_pot:.2f}" + '</div>'
                '<span style="' + badge_bg_t + ';border-radius:4px;padding:3px 10px;font-size:0.65rem;">'
                + str(niv) + '</span>'
                '<div style="font-size:0.65rem;color:#9a8a8a;margin-top:4px;">Mult OT: x'
                + f"{ev_row['multiplicador_ot']:.2f}" + '</div>'
                '</div>',
                unsafe_allow_html=True)

        with col_t2:
            with st.form("form_salvaguarda"):
                c1, c2 = st.columns(2)
                with c1:
                    nombre_s = st.text_input("Nombre de la salvaguarda *")
                    capa = st.selectbox("Capa MAGERIT", CAPAS_SALVAGUARDA)
                    estado_cmm = st.selectbox("Estado CMM",
                        list(CMM_LABELS.keys()), format_func=lambda x: CMM_LABELS[x])
                with c2:
                    eficacia = st.slider("Eficacia objetivo (reduce % del riesgo)", 0.0, 1.0, 0.25, 0.05,
                        format="%.2f")
                    area_iec = st.selectbox("Área IEC 62443", list(IEC62443_CONTROLES.keys()))
                    ctrl_iec_opts = [f"{c[0]} — {c[1]}" for c in IEC62443_CONTROLES[area_iec]]
                    ctrl_iec_sel = st.selectbox("Control IEC 62443", ctrl_iec_opts)
                    ctrl_iec_codigo = IEC62443_CONTROLES[area_iec][[i for i,c in enumerate(ctrl_iec_opts) if c==ctrl_iec_sel][0]][0]

                ctrl_iso = st.text_input("Control ISO 27002 (ej: 8.9.)")
                notas_s = st.text_area("Notas", height=60)

                # Cálculo en tiempo real
                cmm_f = CMM_FACTOR[estado_cmm]
                r_obj_prev  = r_pot * (1 - eficacia)
                r_real_prev = r_pot * (1 - eficacia * cmm_f)
                st.markdown(f"""
                <div class="formula-box">
                R_objetivo_nuevo  = {r_pot:.2f} × (1 − {eficacia:.2f}) = <strong>{r_obj_prev:.2f}</strong><br>
                R_real_nuevo = {r_pot:.2f} × (1 − {eficacia:.2f} × {cmm_f}) = <strong>{r_real_prev:.2f}</strong>
                </div>
                """, unsafe_allow_html=True)

                if st.form_submit_button("Registrar Salvaguarda", use_container_width=True) and nombre_s:
                    conn = get_conn()
                    conn.execute("""INSERT INTO salvaguardas
                        (evaluacion_id, nombre, capa, estado_cmm, eficacia_objetivo,
                         ctrl_iso27002, ctrl_iec62443, notas)
                        VALUES (?,?,?,?,?,?,?,?)""",
                        (ev_id, nombre_s, capa, estado_cmm, eficacia,
                         ctrl_iso, ctrl_iec_codigo, notas_s))
                    conn.commit(); conn.close()
                    st.success("✅ Salvaguarda registrada.")
                    st.rerun()

    with tab2:
        st.markdown('<div class="sec-title">Análisis Residual por Riesgo</div>', unsafe_allow_html=True)
        conn = get_conn()

        resultados_residual = []
        for _, ev in df_ev.iterrows():
            res = calcular_residual(ev["id"], conn)
            if res:
                resultados_residual.append({
                    "Riesgo": f"{ev['amenaza_codigo']} → {ev['activo']}",
                    "Potencial": res["potencial"],
                    "Residual Objetivo": res["residual_objetivo"],
                    "Residual Real": res["residual_real"],
                    "Nivel": nivel_riesgo(res["residual_real"]),
                    "Reducción %": round((1 - res["residual_real"] / res["potencial"]) * 100, 1) if res["potencial"] > 0 else 0
                })

        conn.close()

        if resultados_residual:
            df_res = pd.DataFrame(resultados_residual)

            # Gráfico waterfall potencial vs residual
            fig_bar = go.Figure()
            fig_bar.add_trace(go.Bar(x=df_res["Riesgo"], y=df_res["Potencial"],
                name="Potencial", marker_color="#ef4444"))
            fig_bar.add_trace(go.Bar(x=df_res["Riesgo"], y=df_res["Residual Objetivo"],
                name="Residual Objetivo", marker_color="#f59e0b"))
            fig_bar.add_trace(go.Bar(x=df_res["Riesgo"], y=df_res["Residual Real"],
                name="Residual Real", marker_color="#22c55e"))
            fig_bar.update_layout(
                paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                font=dict(family="JetBrains Mono",color="#9a8a8a",size=9),
                barmode="group", height=320, margin=dict(l=5,r=5,t=5,b=100),
                legend=dict(orientation="h",y=1.1,font=dict(size=10)),
                xaxis=dict(gridcolor="#220000",tickangle=-35),
                yaxis=dict(gridcolor="#220000",title="Riesgo MAGERIT"),
            )
            st.plotly_chart(fig_bar, use_container_width=True)

            st.dataframe(df_res, use_container_width=True, hide_index=True)

# ══════════════════════════════════════════════════════════════
# MÓDULO 5 — MOTOR ML
# ══════════════════════════════════════════════════════════════

def render_motor_ml():
    st.markdown('<div class="sec-title">Motor ML — Scoring IEC 62443 basado en MAGERIT-OT</div>', unsafe_allow_html=True)

    conn_ml = get_conn()
    df_act_ml = pd.read_sql("SELECT * FROM activos ORDER BY id", conn_ml)
    conn_ml.close()
    st.markdown("""
    <div class="info-box">
    <strong>Motor de recomendación propio</strong> basado en heurísticas OT ponderadas.<br>
    El score integra: perfil de riesgo MAGERIT-OT, dimensiones críticas (Safety/Continuidad), zona Purdue,
    amenazas registradas y multiplicador OT. Cada puntuación es <strong>trazable y explicable</strong>.
    </div>
    """, unsafe_allow_html=True)

    conn = get_conn()
    df_act = pd.read_sql("SELECT * FROM activos ORDER BY id", conn)
    conn.close()

    if len(df_act) == 0:
        st.warning("No hay activos registrados.")
        return

    col_ml1, col_ml2 = st.columns([1, 2])

    with col_ml1:
        activo_opts = {r["nombre"]: r["id"] for _, r in df_act.iterrows()}
        activo_sel = st.selectbox("Activo a analizar", list(activo_opts.keys()))
        activo_id = activo_opts[activo_sel]
        activo_row = df_act[df_act["id"]==activo_id].iloc[0].to_dict()

        conn = get_conn()
        df_am = pd.read_sql(
            "SELECT * FROM evaluacion_riesgos WHERE activo_id=? ORDER BY riesgo_potencial DESC",
            conn, params=(activo_id,))
        df_sv = pd.read_sql("""
            SELECT s.* FROM salvaguardas s
            JOIN evaluacion_riesgos e ON s.evaluacion_id=e.id
            WHERE e.activo_id=?""", conn, params=(activo_id,))
        conn.close()

        r_max     = float(df_am["riesgo_potencial"].max()) if len(df_am) > 0 else 0
        mult      = multiplicador_ot(int(activo_row["zona_purdue"]), bool(activo_row["es_sis"]))
        nivel_act = nivel_riesgo(r_max)
        c_act     = color_nivel(nivel_act)
        seccion_a = str(activo_row.get("seccion_aceria", ""))
        sl_obj_a  = SL_OBJETIVO_UBICACION.get(seccion_a, 2)
        sl_col_a  = SL_COLORS.get(sl_obj_a, "#9a8a8a")
        sl_lbl_a  = SL_LABELS.get(sl_obj_a, "")

        st.markdown(
            '<div style="background:rgba(16,2,2,0.88);border:1px solid rgba(200,0,0,0.18);'
            'border-left:3px solid #e01010;border-radius:10px;padding:1rem 1.4rem;">'
            '<div style="font-size:0.95rem;font-weight:700;color:#f0eded;">' + str(activo_row["nombre"]) + '</div>'
            '<div style="font-size:0.7rem;color:#9a8a8a;">' + str(activo_row.get("subtipo","")) + ' · ' + seccion_a + '</div>'
            '<div style="margin-top:0.5rem;padding:4px 8px;background:rgba(0,0,0,0.4);border-radius:4px;'
            'border-left:3px solid ' + sl_col_a + ';display:flex;align-items:center;gap:0.6rem;">'
            '<span style="font-family:JetBrains Mono;font-size:0.65rem;color:' + sl_col_a + ';font-weight:700;">SL OBJETIVO ' + str(sl_obj_a) + '</span>'
            '<span style="font-size:0.6rem;color:#9a8a8a;">' + sl_lbl_a + '</span>'
            '</div>'
            '<div style="display:flex;gap:1.2rem;margin-top:0.7rem;">'
            '<div style="text-align:center;">'
            '<div style="font-size:1.5rem;font-weight:800;color:' + c_act + ';font-family:Syne,sans-serif;">' + f"{r_max:.1f}" + '</div>'
            '<div style="font-size:0.58rem;color:#9a8a8a;">RIESGO MÁX</div>'
            '</div>'
            '<div style="text-align:center;">'
            '<div style="font-size:1.5rem;font-weight:800;color:#f59e0b;font-family:Syne,sans-serif;">x' + f"{mult:.2f}" + '</div>'
            '<div style="font-size:0.58rem;color:#9a8a8a;">MULT OT</div>'
            '</div>'
            '<div style="text-align:center;">'
            '<div style="font-size:1.5rem;font-weight:800;color:#f0eded;font-family:Syne,sans-serif;">' + str(len(df_am)) + '</div>'
            '<div style="font-size:0.58rem;color:#9a8a8a;">AMENAZAS</div>'
            '</div>'
            '</div></div>',
            unsafe_allow_html=True)

        n_ctrl = st.slider("Nº controles a recomendar", 3, 15, 7)

    with col_ml2:
        if st.button("Ejecutar Motor ML MAGERIT-OT", use_container_width=True):
            df_scores = scoring_ml(activo_row, df_am, df_sv)
            top_n = df_scores.head(n_ctrl)

            # Riesgo residual si se aplican todos los controles top
            eficacias_ctrl = {
                # CR 1 — IAC: Identificación y Control de Acceso
                "CR 1.1":0.15, "CR 1.2":0.10, "CR 1.3":0.20, "CR 1.5":0.12,
                "CR 1.7":0.12, "CR 1.11":0.08, "CR 1.13":0.20,
                # CR 2 — UC: Uso y Control
                "CR 2.1":0.15, "CR 2.3":0.20, "CR 2.5":0.08, "CR 2.8":0.10,
                "CR 2.13":0.15,
                # CR 3 — SI: Integridad del Sistema
                "CR 3.1":0.15, "CR 3.2":0.18, "CR 3.4":0.15, "CR 3.10":0.12,
                "CR 3.11":0.15, "CR 3.14":0.10,
                # CR 4 — DC: Confidencialidad de Datos
                "CR 4.1":0.12, "CR 4.3":0.10,
                # CR 5 — DM: Gestión del Flujo de Datos
                "CR 5.1":0.25, "CR 5.2":0.20, "CR 5.4":0.10,
                # CR 6 — ER: Respuesta a Eventos
                "CR 6.1":0.08, "CR 6.2":0.15,
                # CR 7 — RM: Gestión de Recursos
                "CR 7.1":0.15, "CR 7.3":0.12, "CR 7.4":0.10,
                "CR 7.5":0.08, "CR 7.6":0.08, "CR 7.7":0.12, "CR 7.8":0.05,
            }
            r_res = r_max
            for _, row in top_n.iterrows():
                ef = eficacias_ctrl.get(row["codigo"].strip(), 0.05)
                r_res = r_res * (1 - ef)
            r_res = round(r_res, 2)
            reduccion = round((1 - r_res/r_max)*100, 1) if r_max > 0 else 0

            st.session_state["ml_scores"] = df_scores
            st.session_state["ml_sl_obj"] = sl_obj_a
            st.session_state["ml_top"] = top_n
            st.session_state["ml_activo_id"] = activo_id
            st.session_state["ml_r_max"] = r_max
            st.session_state["ml_r_res"] = r_res
            st.session_state["ml_reduccion"] = reduccion

        if st.session_state.get("ml_activo_id") == activo_id and "ml_top" in st.session_state:
            top_n    = st.session_state["ml_top"]
            df_scores = st.session_state["ml_scores"]
            r_max_v  = st.session_state["ml_r_max"]
            r_res_v  = st.session_state["ml_r_res"]
            reduccion = st.session_state["ml_reduccion"]

            # ── Separar controles por SL mínimo requerido ──────────────
            sl_obj_tab = st.session_state.get("ml_sl_obj", 2)
            df_sl1 = df_scores[df_scores["sl_min"] == 1].head(n_ctrl)
            df_sl2 = df_scores[df_scores["sl_min"] == 2].head(n_ctrl)
            df_sl3 = df_scores[df_scores["sl_min"] >= 3].head(n_ctrl)

            n1 = len(df_sl1); n2 = len(df_sl2); n3 = len(df_sl3)
            lbl1 = f"🟢 SL1 — Base ({n1})"
            lbl2 = f"🟡 SL2 — Medio ({n2})" + (" ✅" if sl_obj_tab >= 2 else "")
            lbl3 = f"🔴 SL3 — Avanzado ({n3})" + (" ✅" if sl_obj_tab >= 3 else "")
            lbl4 = "Riesgo Residual"
            lbl5 = "Scoring Completo"

            n4   = len(df_scores[df_scores["sl_min"] == 4])
            lbl4_sl = f"🔴 SL4 — Crítico (RE)"
            lbl5_res = "Riesgo Residual"
            lbl6_sc  = "Scoring Completo"
            tab_r1, tab_r2, tab_r3, tab_r4_sl, tab_r5, tab_r6 = st.tabs([lbl1, lbl2, lbl3, lbl4_sl, lbl5_res, lbl6_sc])

            def render_ctrl_cards(df_tab, sl_level):
                if len(df_tab) == 0:
                    st.info(f"No hay controles SL{sl_level} en el scoring actual.")
                    return
                sl_c = SL_COLORS.get(sl_level, "#9a8a8a")
                aplica = sl_level <= sl_obj_tab
                if aplica:
                    st.markdown(
                        '<div style="background:rgba(0,80,0,0.12);border:1px solid #22c55e44;border-radius:6px;'
                        'padding:0.5rem 0.8rem;margin-bottom:0.8rem;font-size:0.72rem;color:#86efac;">'
                        '✅ <strong>Obligatorio</strong> para SL objetivo ' + str(sl_obj_tab) + ' — estos controles deben estar implantados'
                        '</div>', unsafe_allow_html=True)
                else:
                    st.markdown(
                        '<div style="background:rgba(50,50,0,0.12);border:1px solid #eab30844;border-radius:6px;'
                        'padding:0.5rem 0.8rem;margin-bottom:0.8rem;font-size:0.72rem;color:#fde68a;">'
                        '⬜ SL' + str(sl_level) + ' supera el objetivo actual (SL' + str(sl_obj_tab) + ') — implantación opcional o futura'
                        '</div>', unsafe_allow_html=True)
                for rank, (_, row) in enumerate(df_tab.iterrows(), 1):
                    s   = row["score"]
                    p_c = {"ALTA":"#ef4444","MEDIA":"#f59e0b","BAJA":"#22c55e"}.get(row["prioridad"],"#9a8a8a")
                    t_c = {"Preventiva":"#e01010","Detectiva":"#7c3aed","Correctiva":"#f59e0b",
                           "De recuperación":"#3b82f6","Paliativa":"#22c55e"}.get(row["tipo"],"#9a8a8a")
                    card = (
                        '<div style="background:rgba(12,0,0,0.85);border:1px solid ' + p_c + '33;'
                        'border-left:4px solid ' + sl_c + ';border-radius:8px;padding:0.8rem 1rem;margin-bottom:0.5rem;">'
                        '<div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:0.5rem;">'
                        '<div style="display:flex;align-items:center;gap:0.8rem;">'
                        '<span style="font-size:1.1rem;font-weight:900;color:' + sl_c + ';font-family:Syne,sans-serif;min-width:22px;">#' + str(rank) + '</span>'
                        '<div>'
                        '<span style="font-family:JetBrains Mono;font-size:0.7rem;color:' + p_c + ';font-weight:700;">' + str(row["codigo"]) + '</span>'
                        '<span style="font-family:JetBrains Mono;font-size:0.6rem;color:' + sl_c + ';margin-left:8px;'
                        'border:1px solid ' + sl_c + '55;border-radius:3px;padding:1px 5px;">SL' + str(sl_level) + '</span>'
                        '<div style="font-size:0.82rem;color:#f0eded;font-weight:600;">' + str(row["nombre"]) + '</div>'
                        '<div style="font-size:0.62rem;color:#9a8a8a;">' + str(row["area"]) + '</div>'
                        '</div></div>'
                        '<div style="text-align:right;">'
                        '<div style="font-size:1.4rem;font-weight:800;color:' + p_c + ';font-family:Syne,sans-serif;">' + f"{s:.0f}" + '</div>'
                        '<div style="font-size:0.58rem;color:#9a8a8a;">SCORE ML</div>'
                        '<span style="background:rgba(0,0,0,0.4);color:' + p_c + ';border:1px solid ' + p_c + '55;'
                        'border-radius:8px;padding:1px 6px;font-size:0.6rem;">' + str(row["prioridad"]) + '</span>'
                        '</div></div>'
                        '<div style="margin-top:0.4rem;background:#110000;border-radius:3px;height:4px;overflow:hidden;">'
                        '<div style="width:' + f"{s:.0f}" + '%;height:100%;background:linear-gradient(90deg,' + sl_c + '66,' + sl_c + ');border-radius:3px;"></div>'
                        '</div>'
                        '<span style="color:' + t_c + ';font-size:0.6rem;font-family:JetBrains Mono;">' + str(row["tipo"]) + '</span>'
                        '</div>'
                    )
                    st.markdown(card, unsafe_allow_html=True)

            with tab_r1:
                render_ctrl_cards(df_sl1, 1)
            with tab_r2:
                render_ctrl_cards(df_sl2, 2)
            with tab_r3:
                render_ctrl_cards(df_sl3, 3)

            with tab_r4_sl:
                sl4_c = SL_COLORS[4]
                st.markdown(
                    '<div style="background:rgba(80,0,0,0.15);border:1px solid #ef444444;border-radius:6px;'
                    'padding:0.6rem 0.9rem;margin-bottom:0.8rem;font-size:0.72rem;color:#fca5a5;">'
                    '🔴 <strong>SL4 — Nivel Crítico</strong>: Según la IEC 62443-4-2:2019, '
                    'ningún control CR es <em>exclusivo</em> de SL4. Sin embargo, '
                    '<strong>6 controles añaden Mejoras de Requisitos (RE) adicionales</strong> '
                    'que solo son obligatorias a partir de SL4. Estos RE representan el nivel más exigente '
                    'de implementación (mecanismos hardware, criptografía avanzada, automatización).'
                    '</div>',
                    unsafe_allow_html=True)
                for cr_code, re_desc in CR_SL4_RE_EXTRA.items():
                    # Get control name from catalog
                    ctrl_name = ""
                    for area_ctrls in IEC62443_CONTROLES.values():
                        for (cod, nom, _) in area_ctrls:
                            if cod == cr_code:
                                ctrl_name = nom
                                break
                    st.markdown(
                        '<div style="background:rgba(12,0,0,0.85);border:1px solid #ef444433;'
                        'border-left:4px solid ' + sl4_c + ';border-radius:8px;padding:0.8rem 1rem;margin-bottom:0.5rem;">'
                        '<div style="display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:0.5rem;">'
                        '<div>'
                        '<span style="font-family:JetBrains Mono;font-size:0.7rem;color:' + sl4_c + ';font-weight:700;">' + cr_code + '</span>'
                        '<span style="font-family:JetBrains Mono;font-size:0.6rem;color:' + sl4_c + ';margin-left:8px;'
                        'border:1px solid ' + sl4_c + '55;border-radius:3px;padding:1px 5px;">SL4 RE</span>'
                        '<div style="font-size:0.82rem;color:#f0eded;font-weight:600;margin-top:2px;">' + ctrl_name + '</div>'
                        '<div style="font-size:0.72rem;color:#fca5a5;margin-top:4px;font-style:italic;">' + re_desc + '</div>'
                        '</div></div></div>',
                        unsafe_allow_html=True)

            with tab_r5:
                c_r1, c_r2, c_r3 = st.columns(3)
                with c_r1: st.metric("Riesgo Actual", f"{r_max_v:.2f}")
                with c_r2: st.metric("Residual Estimado", f"{r_res_v:.2f}",
                    delta=f"-{r_max_v-r_res_v:.2f}", delta_color="inverse")
                with c_r3: st.metric("Reducción", f"{reduccion}%")

                
                fig_g = go.Figure(go.Indicator(
                    mode="gauge+number+delta",
                    value=r_res_v,
                    delta={"reference":r_max_v,"decreasing":{"color":"#22c55e"}},
                    gauge={
                        "axis":{"range":[0,25],"tickfont":{"family":"JetBrains Mono","size":9}},
                        "bar":{"color":color_nivel(nivel_riesgo(r_res_v))},
                        "steps":[
                            {"range":[0,3],"color":"rgba(34,197,94,0.12)"},
                            {"range":[3,4],"color":"rgba(234,179,8,0.12)"},
                            {"range":[4,15],"color":"rgba(245,158,11,0.12)"},
                            {"range":[15,25],"color":"rgba(239,68,68,0.12)"},
                        ],
                        "threshold":{"line":{"color":"#ff2222","width":3},"value":r_max_v}
                    },
                    title={"text":"Riesgo Residual ML","font":{"family":"Syne","size":12,"color":"#f0eded"}},
                    number={"font":{"family":"Syne","size":28,"color":"#f0eded"}}
                ))
                fig_g.update_layout(paper_bgcolor="rgba(0,0,0,0)",
                    font=dict(color="#9a8a8a"), height=260, margin=dict(l=20,r=20,t=40,b=10))
                st.plotly_chart(fig_g, use_container_width=True)

            with tab_r6:
                df_full = df_scores[["codigo","sl_min","sl_aplica","nombre","area","tipo","score","prioridad"]].copy()
                st.dataframe(df_full, use_container_width=True, hide_index=True, height=400)
                csv_ml = df_full.to_csv(index=False).encode("utf-8")
                st.download_button("⬇️ Exportar Scoring ML (CSV)", csv_ml, "scoring_ml_magerit_ot.csv")

# ══════════════════════════════════════════════════════════════
# MÓDULO 6 — CATÁLOGOS
# ══════════════════════════════════════════════════════════════

def render_catalogos():
    st.markdown('<div class="sec-title">Catálogos MAGERIT-OT</div>', unsafe_allow_html=True)
    tab1, tab2, tab3 = st.tabs(["Amenazas OT/ICS", "IEC 62443 SP", "Fórmulas MAGERIT-OT"])

    with tab1:
        busqueda = st.text_input("Buscar amenaza", "")
        cats = sorted(set(a["categoria"] for a in AMENAZAS_OT))
        cat_f = st.selectbox("Filtrar categoría", ["Todas"] + cats)
        amenazas_f = [a for a in AMENAZAS_OT
            if (busqueda.lower() in a["nombre"].lower() or busqueda == "")
            and (cat_f == "Todas" or a["categoria"] == cat_f)]

        for a in amenazas_f:
            with st.expander(f"[{a['codigo']}] {a['nombre']} — {a['categoria']}"):
                c1, c2, c3 = st.columns(3)
                with c1:
                    st.markdown(f"""
                    **Fuente:** {a['fuente']}  
                    **Disponibilidad** (15m→2s): {a['disp']}  
                    **Confidencialidad** (RI/RE): {a['conf']}
                    """)
                with c2:
                    st.markdown(f"""
                    **Integridad** (DT/ERR/MAN/AUT): {a['integ']}  
                    **Safety SIL:** {a['safety']}  
                    **Continuidad Proceso:** {a['cont']}
                    """)
                with c3:
                    st.markdown(f"**Integridad Física:** {a['fisic']}")

    with tab2:
        for area, controles in IEC62443_CONTROLES.items():
            st.markdown(f'<div class="sec-title">{area}</div>', unsafe_allow_html=True)
            for (codigo, nombre, tipo_ctrl) in controles:
                t_c  = {"Preventiva":"#e01010","Detectiva":"#7c3aed","Correctiva":"#f59e0b",
                        "De recuperación":"#3b82f6"}.get(tipo_ctrl,"#9a8a8a")
                sl_m = CR_SL_MINIMO.get(codigo, 1)
                sl_c = SL_COLORS.get(sl_m, "#9a8a8a")
                cat_html = (
                    '<div style="display:flex;gap:0.8rem;padding:0.4rem 0.8rem;align-items:center;'
                    'background:rgba(10,0,0,0.6);border:1px solid #220000;border-radius:5px;margin-bottom:3px;">'
                    '<span style="font-family:JetBrains Mono;font-size:0.68rem;color:#e01010;min-width:72px;">' + codigo + '</span>'
                    '<span style="font-size:0.78rem;color:#f0eded;flex:1;">' + nombre + '</span>'
                    '<span style="color:' + sl_c + ';font-size:0.58rem;font-family:JetBrains Mono;'
                    'border:1px solid ' + sl_c + '44;border-radius:3px;padding:1px 6px;margin-right:6px;white-space:nowrap;">SL' + str(sl_m) + '+</span>'
                    '<span style="color:' + t_c + ';font-size:0.6rem;font-family:JetBrains Mono;">' + tipo_ctrl + '</span>'
                    '</div>'
                )
                st.markdown(cat_html, unsafe_allow_html=True)

    with tab3:
        st.markdown("""
        <div class="card card-red">
        <div class="sec-title">MAGERIT-OT — Adaptación al Entorno Industrial</div>

        <div class="formula-box">
        ── VALORACIÓN DE ACTIVOS ──────────────────────────────────<br>
        Disponibilidad D: MAX(15min, 3h, 1día, 1semana, 2semanas)<br>
        Confidencialidad C: MAX(Riesgo Interno, Riesgo Externo)<br>
        Integridad I: MAX(Datos, Errores, Manipulación, Autenticidad)<br>
        Safety SIL [OT]: Nivel SIL certificado (0-4)<br>
        Continuidad Proceso [OT]: Impacto en producción industrial (0-5)<br>
        Integridad Física [OT]: Riesgo de daño a personas/instalaciones (0-5)
        </div>

        <div class="formula-box">
        ── PROPAGACIÓN POR DEPENDENCIAS (Modelo Purdue) ──────────<br>
        V_hijo[dim] = MAX(V_propio[dim], MAX(V_padre_i[dim]))
        </div>

        <div class="formula-box">
        ── MULTIPLICADOR OT ─────────────────────<br>
        Mult_OT(zona, es_SIS) = Factor_Purdue × (1.5 si SIS else 1.0)<br>
        Nivel 0 (campo): ×1.4 | Nivel 1 (control): ×1.25<br>
        Nivel 2 (supervisión): ×1.1 | Nivel 3+: ×1.0<br>
        SIS: factor adicional ×1.5 (por consecuencias en safety)<br>
        Ejemplo: PLC SIS en Nivel 1 → ×1.25 × ×1.5 = ×1.875
        </div>

        <div class="formula-box">
        ── FÓRMULA DE RIESGO MAGERIT-OT ──────────────────────────<br>
        R_dim = V_activo[dim] × Prob_amenaza[dim] × Mult_OT<br>
        R_potencial = MAX(R_D, R_C, R_I, R_Safety, R_Cont, R_Fisic)<br>
        Escala: 0–25 | Umbral Crítico: ≥15 | Alto: ≥4 | Medio: ≥3
        </div>

        <div class="formula-box">
        ── RIESGO RESIDUAL ────────────────────────<br>
        R_objetivo = R_pot × ∏(1 − eficacia_i)          [100% implantado]<br>
        R_real     = R_pot × ∏(1 − eficacia_i × CMM_i)  [ponderado madurez]<br>
        CMM: 0→0% | 1→25% | 2→50% | 3→90% | 4→100%
        </div>
        </div>
        """, unsafe_allow_html=True)

# ══════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════

def main():
    init_db()
    inject_css()
    nav = render_sidebar()

    if nav == "Dashboard":
        render_dashboard()
    elif nav == "Activos OT":
        render_activos()
    elif nav == "Amenazas OT":
        render_amenazas()
    elif nav == "Tratamiento":
        render_tratamiento()
    elif nav == "Motor ML":
        render_motor_ml()
    elif nav == "Catálogos":
        render_catalogos()

if __name__ == "__main__":
    main()