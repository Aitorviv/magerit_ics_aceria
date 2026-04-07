# 🛡️ MAGERIT OT — Herramienta de Análisis de Riesgos Industrial

Aplicación web para realizar análisis de riesgos **Magerit v3.0** en entornos OT/ICS (Operational Technology).

## Características

| Módulo | Descripción |
|--------|-------------|
| 📊 Dashboard | Heatmap de riesgos, KPIs, distribución por nivel y zona |
| 🖥️ Activos OT | Gestión de PLCs, HMIs, SCADA, RTUs con valoración por dimensiones |
| ⚠️ Amenazas | Catálogo Magerit completo + cálculo automático de riesgo |
| 🔒 Salvaguardas | Controles de seguridad con eficacia y estado de implementación |
| 📚 Catálogo | 30+ amenazas Magerit adaptadas a OT, buscables |

## Requisitos

- Python 3.10+
- pip

## Instalación y ejecución

```bash
# 1. Instalar dependencias
pip install -r requirements.txt

# 2. Lanzar la aplicación
streamlit run app.py
```

La app estará disponible en: **http://localhost:8501**

## Fórmula de cálculo de riesgo

```
Riesgo = Impacto_max × (Degradación / 100) × Probabilidad
```

Donde:
- **Impacto_max**: máximo entre disponibilidad, integridad y confidencialidad (1-5)
- **Degradación**: porcentaje de daño sobre el activo (0-100%)
- **Probabilidad**: 1 (Muy Rara) → 5 (Casi Cierta)

| Riesgo | Nivel |
|--------|-------|
| < 4 | 🟢 Bajo |
| 4–8 | 🟡 Medio |
| 9–15 | 🟠 Alto |
| ≥ 16 | 🔴 Crítico |

## Base de datos

SQLite local (`magerit_ot.db`) — se crea automáticamente al iniciar.

## Normativa

- Magerit v3.0 (CCN)
- IEC 62443
- NIS2 (UE 2022/2555)
- ENS / CCN-STIC
