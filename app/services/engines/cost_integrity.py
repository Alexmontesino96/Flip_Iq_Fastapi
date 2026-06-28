"""Guard de integridad de coste — detección del mismatch coste-vs-precio.

Portado de batchflip_core/services/dual_profit.py (los gates + corrected_metrics).
El usuario ingresa cost_price = coste de UNA unidad, pero el precio de mercado de
Amazon puede ser de un MULTIPACK (o de otro tamaño) → ROI fantasma (caso Trojan:
coste $1.30 vs pack de 12 a $28.80 → 700% irreal).

Tres gates (cualquiera dispara), en orden de robustez:
  1. fee-ratio: si el FBA fee >= cost_unit * K, el fee delata el pack (Amazon
     cobra fee proporcional al tamaño/peso). NO depende del título. Computable
     hoy con cost_price + raw_comps.fba_fulfillment_fee.
  2. package_quantity: señal estructurada de Keepa (packageQuantity/numberOfItems).
  3. title_bundle: bundle_factor extraído del título (regex/LLM). Cierra el caso
     Trojan (fee solo ~4x el coste, packageQuantity=1, "Pack of N" en el título).

INVARIANTE AC-3: este módulo NO toca profit/roi. El caller solo degrada la
recomendación y avisa; los corrected_* son un dato INFORMATIVO ("si es pack de N,
tu ROI real es X%"), nunca reemplazan el número nominal.
"""

from __future__ import annotations

# Umbral del gate primario: si el FBA fee es >= K veces el coste unitario, el
# ASIN matcheado es casi seguro un multipack. K conservador: deja pasar items
# grandes legítimos cuyo fee es < 6x el coste.
_MULTIPACK_FEE_RATIO_K = 6.0


def multipack_mismatch_reason(
    *,
    cost_unit: float | None,
    keepa_fba_fee: float | None,
    package_quantity: int | None,
    bundle_factor: int | None,
) -> str | None:
    """Razón del mismatch de multipack, o None si ninguno de los gates dispara.

    Devuelve "fee_ratio" | "package_quantity" | "title_bundle" (en ese orden de
    precedencia) para que el caller pueda explicar el porqué al usuario.
    """
    if cost_unit is None or cost_unit <= 0:
        return None
    # Gate 1: ratio FBA fee / coste unitario (None/0.0 no dispara).
    if keepa_fba_fee and keepa_fba_fee >= cost_unit * _MULTIPACK_FEE_RATIO_K:
        return "fee_ratio"
    # Gate 2: señal estructurada de pack de Keepa.
    if package_quantity and package_quantity > 1:
        return "package_quantity"
    # Gate 3: bundle_factor del título (señal que el campo estructurado no da).
    if bundle_factor and bundle_factor > 1:
        return "title_bundle"
    return None


def detect_multipack_mismatch(
    *,
    cost_unit: float | None,
    keepa_fba_fee: float | None,
    package_quantity: int | None,
    bundle_factor: int | None,
) -> bool:
    """True si algún gate detecta el mismatch coste-por-unidad vs precio-por-pack."""
    return multipack_mismatch_reason(
        cost_unit=cost_unit,
        keepa_fba_fee=keepa_fba_fee,
        package_quantity=package_quantity,
        bundle_factor=bundle_factor,
    ) is not None


def corrected_metrics(
    *,
    nominal_profit: float | None,
    cost_unit: float | None,
    bundle_factor: int | None,
) -> tuple[float | None, float | None]:
    """Profit/ROI reales escalando el coste por el bundle del ASIN.

    El precio de venta y los fees del paquete NO cambian; lo único subestimado es
    el coste, que pasa de cost_unit a cost_unit * bundle_factor:

        corrected_profit = nominal_profit - cost_unit*(bundle_factor-1)
        corrected_roi    = corrected_profit / (cost_unit*bundle_factor) * 100

    Devuelve (None, None) si falta algún dato o no hay multipack. NUNCA reemplaza
    el profit/roi nominal (invariante AC-3): es un dato informativo.
    """
    if (
        nominal_profit is None
        or cost_unit is None
        or cost_unit <= 0
        or not bundle_factor
        or bundle_factor <= 1
    ):
        return (None, None)
    corrected_cost = cost_unit * bundle_factor
    corrected_profit = float(nominal_profit) - cost_unit * (bundle_factor - 1)
    corrected_roi = corrected_profit / corrected_cost * 100 if corrected_cost > 0 else None
    return (
        round(corrected_profit, 2),
        round(corrected_roi, 2) if corrected_roi is not None else None,
    )
