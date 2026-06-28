"""Detección de mismatch de tamaño coste-vs-listing (red secundaria del guard).

Portado de batchflip_core/services/size_match.py. El coste del usuario es por SU
unidad (p.ej. una bolsa de 50 g), pero el ASIN matcheado puede ser de OTRA
granularidad (150 g, o un pack de 6 = 270 g). El coste queda subestimado contra
un precio del tamaño mayor → ROI fantasma. Ocurre incluso cuando los 3 gates del
guard de multipack no disparan (snack ligero, packageQuantity=1, título sin
"Pack of N").

Señal: comparar el gramaje/volumen del TÍTULO de Amazon contra el del título/
keyword del producto que el usuario evalúa.

Disciplina (anti-falso-positivo):
  - Se compara SOLO dentro de la misma dimensión física (masa↔masa, volumen↔
    volumen). 'fl oz'/'ml'/'l' son VOLUMEN; 'g'/'oz'/'lb'/'kg' son MASA.
  - Se ABSTIENE (False) si falta el tamaño en cualquiera de los dos lados
    (libros, electrónica, ropa: sin gramaje → nunca se degrada).
  - Umbral 1.5x: deja ~45% de margen para relleno/redondeo antes de acusar.
NO toca el coste ni el profit (AC-3): el caller solo degrada la recomendación.
"""

from __future__ import annotations

import re

# Factores de conversión a la base de cada dimensión.
_MASS = {  # → gramos
    "kg": 1000.0, "g": 1.0, "gr": 1.0, "grs": 1.0, "gramos": 1.0,
    "lb": 453.592, "lbs": 453.592, "oz": 28.3495,
}
_VOL = {  # → mililitros
    "l": 1000.0, "cl": 10.0, "ml": 1.0,
}
_FL_OZ_ML = 29.5735

# 'fl oz' / 'fl. oz' (volumen) — se captura ANTES que 'oz' (masa).
_FL_OZ_RE = re.compile(r"(\d+(?:\.\d+)?)\s*fl\.?\s*oz\b", re.IGNORECASE)
_VOL_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(ml|cl|l)\b", re.IGNORECASE)
_MASS_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(kg|grs|gramos|gr|g|lbs|lb|oz)\b", re.IGNORECASE)

DEFAULT_FACTOR = 1.5


def extract_sizes(text: str | None) -> dict[str, float]:
    """{'mass': gramos, 'vol': ml} con el MAYOR valor hallado por dimensión.

    Devuelve {} si el texto no contiene ninguna cantidad con unidad reconocida.
    El máximo por dimensión captura el tamaño del paquete frente a menciones
    menores (p.ej. "5.3 oz (150 g)" → mass≈150).
    """
    if not text:
        return {}
    t = str(text)
    out: dict[str, float] = {}

    for val in _FL_OZ_RE.findall(t):
        out["vol"] = max(out.get("vol", 0.0), float(val) * _FL_OZ_ML)
    # Quitar las coincidencias fl-oz para que 'oz' de masa no las recapture.
    t_wo_floz = _FL_OZ_RE.sub(" ", t)

    for val, unit in _VOL_RE.findall(t_wo_floz):
        out["vol"] = max(out.get("vol", 0.0), float(val) * _VOL[unit.lower()])
    for val, unit in _MASS_RE.findall(t_wo_floz):
        out["mass"] = max(out.get("mass", 0.0), float(val) * _MASS[unit.lower()])

    return {k: v for k, v in out.items() if v > 0}


def detect_size_mismatch(
    amazon_title: str | None,
    supplier_title: str | None,
    factor: float = DEFAULT_FACTOR,
) -> bool:
    """True si los tamaños difieren por >= factor dentro de una dimensión común.

    Prioriza MASA sobre volumen cuando ambos lados la tienen. Se abstiene (False)
    si no hay dimensión común (falta el dato, o uno es masa y el otro volumen).
    """
    a = extract_sizes(amazon_title)
    s = extract_sizes(supplier_title)
    for dim in ("mass", "vol"):
        if dim in a and dim in s:
            hi, lo = max(a[dim], s[dim]), min(a[dim], s[dim])
            return lo > 0 and (hi / lo) >= factor
    return False
