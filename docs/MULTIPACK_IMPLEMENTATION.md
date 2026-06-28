# Plan de implementación — Manejo de Multipacks en FlipIQ

> Plan detallado del componente **más grande** del port BatchFlip → FlipIQ: detección y corrección del mismatch coste-por-unidad vs precio-por-paquete. Acompaña a [`AMAZON_PORT_PLAN.md`](./AMAZON_PORT_PLAN.md) (visión global) y [`AMAZON_ENGINE_FINDINGS.md`](./AMAZON_ENGINE_FINDINGS.md) (bugs a no replicar).

---

## 1. El problema (con números reales)

El usuario ingresa `cost_price` = **el costo de UNA unidad** (schema lo exige `>0`, `analysis.py:10`). FlipIQ resuelve el barcode/keyword contra Amazon (Keepa) y calcula `market_list` = precio de mercado. Si el producto que Amazon vende es un **paquete de N**, comparamos peras con manzanas → **ROI fantasma**.

**Caso Trojan (real, de `pending decision/06` de BatchFlip):**

```
Usuario escanea: 1 unidad,  cost_price = $1.30
Amazon vende:    "Trojan ... 3 Count (Pack of 12)",  market_list = $28.80
FlipIQ hoy:      compute_profit($28.80, $1.30) → profit ≈ $22, ROI ≈ 700% → BUY ✅ (FANTASMA)
Realidad:        para vender ese pack a $28.80 hay que COMPRAR 12 → coste real $15.60 → ROI ≈ 40%
```

El usuario ve "BUY, ROI 700%", compra 1 unidad, y no puede competir con el listing del pack. Es el **falso-rentable más peligroso** del análisis de Amazon.

### Tres escenarios distintos (importante para el diseño)

| | Escenario | Síntoma | Mecanismo que lo resuelve |
|---|---|---|---|
| **A** | El **producto evaluado** es un pack (barcode → ASIN "Pack of N") | `market_list` es el precio del pack; `cost_price` es de 1 unidad → ROI fantasma | **Guard de 3 gates + corrected ROI** (lo nuevo, núcleo de este plan) |
| **B** | Los **comps están contaminados** con packs (mismo UPC mapeado a packs de 2/3/6) | mediana sesgada al alza | `_filter_multipacks` (arreglar bug "N count") + normalización `lot_size` |
| **C** | Mezcla de A y B | ambos | ambos mecanismos |

> **Hoy FlipIQ solo intenta B**, y mal: `_filter_multipacks` usa el `_PACK_RE` con bug "N count" (descarta unidades sueltas válidas, ver Findings #1). **El escenario A —el más peligroso— no está cubierto.**

### Lo que FlipIQ ya tiene (y no usa bien)

- `MarketplaceListing.is_bundle` / `lot_size` (base.py:34-35) → `comp_cleaner.py:76-78` **ya normaliza** `precio/lot_size` si `is_bundle`. **Pero `amazon.py` nunca los puebla** → normalización latente para Amazon.
- `_filter_multipacks` (amazon.py:82-97) → filtra a nivel producto Keepa, con el regex naive.
- `_validate_buy` (analysis_service.py:873-941) → patrón ya establecido de "warning + degradar recomendación". **Aquí encaja el guard.**

---

## 2. Diseño de la solución

Cuatro piezas. Respetan el **invariante AC-3** de BatchFlip: el guard **degrada la recomendación y avisa, NUNCA toca `profit`/`roi`** (el usuario sigue viendo el número nominal y el motivo).

```
                    ┌─────────────────────────────────────────────┐
                    │  amazon.py: _build_comps_from_products       │
 barcode/keyword →  │  · _filter_multipacks (FIX: regex correcto)  │  Escenario B
                    │  · cosechar del producto evaluado:           │
                    │      package_quantity, fba_fee, title        │
                    │  · bundle_factor = extract(title)            │  ← multipack.py
                    └──────────────────┬──────────────────────────┘
                                       │ CompsResult + señales del producto evaluado
                                       ▼
                    ┌─────────────────────────────────────────────┐
                    │  analysis_service._run_pipeline              │
                    │  profit_market = compute_profit(market_list, │
                    │                    cost_price, ...)          │
                    │                                              │
                    │  mismatch = detect_multipack_mismatch(...)   │  ← cost_integrity.py
                    │  if mismatch:                                │  Escenario A
                    │    corr_profit, corr_roi = corrected_metrics │
                    │    → _validate_buy degrada + warning         │  (AC-3: no toca profit)
                    └─────────────────────────────────────────────┘
```

### 2.1 — Extractor de bundle_factor (`app/services/marketplace/multipack.py`, NUEVO)

Portar de `batchflip_core/services/multipack_extractor.py` con diseño híbrido:

1. **Pre-filtro** `_has_pack_signal(title)` → si no hay ninguna señal de pack, factor 1 (sin LLM).
2. **Regex determinista** `regex_bundle_factor(title)` → patrones **inequívocos** ("Pack of N", "N-Pack", "Case of N", twin/triple). Toma el MAYOR N. **Ignora "N count"/"N ct"** (ambiguo). Devuelve `None` si no hay patrón inequívoco.
3. **LLM (opcional, gated)** `extract_bundle_factor(title)` → solo para "N count" ambiguo ("Paper Towels, 12 Count" = 12 rollos vs "Condoms, 3 Count" = 3 por caja → factor 1).

```python
# Firmas a portar (tal cual, son puras):
def _has_pack_signal(title: str) -> bool
def regex_bundle_factor(title: str) -> int | None      # patrones inequívocos, sin red
async def extract_bundle_factor(title, *, api_key, ...) -> int | None  # +LLM para ambiguos
```

**Adaptación FlipIQ:** la rama LLM usa `AsyncOpenAI→Gemini` en BatchFlip; reusar **`app/core/llm.py`** (Gemini Flash, ya integrado). El circuit-breaker + cache LRU/TTL son para batch; en single-product (1 call/análisis) **simplificar** (o mantener un cache simple por título — el bundle de un ASIN no cambia).

### 2.2 — Guard de integridad de costo (`app/services/engines/cost_integrity.py`, NUEVO)

Portar `_detect_multipack_mismatch` + `corrected_metrics` de `dual_profit.py:32-109`, adaptados a kwargs (sin `JobItem`).

```python
_MULTIPACK_FEE_RATIO_K = 6.0

def detect_multipack_mismatch(
    *,
    cost_unit: float | None,        # cost_price del usuario (1 unidad)
    keepa_fba_fee: float | None,    # result.fba_fulfillment_fee (o del producto evaluado)
    package_quantity: int | None,   # cosechado de Keepa (gate 2)
    bundle_factor: int | None,      # de multipack.py (gate 3)
) -> bool:
    if cost_unit is None or cost_unit <= 0:
        return False
    # GATE 1 (fee-ratio): el FBA fee delata el pack sin mirar el título.
    #   Computable HOY con datos presentes. Ataca el modo de fallo #1.
    if keepa_fba_fee and keepa_fba_fee >= cost_unit * _MULTIPACK_FEE_RATIO_K:
        return True
    # GATE 2 (estructurado): packageQuantity/numberOfItems de Keepa.
    if package_quantity and package_quantity > 1:
        return True
    # GATE 3 (título): bundle_factor por regex/LLM. Cierra el caso Trojan
    #   (fee solo ~4x, packageQuantity=1, multiplicador vive en el título).
    if bundle_factor and bundle_factor > 1:
        return True
    return False

def corrected_metrics(
    *, nominal_profit: float | None, cost_unit: float | None, bundle_factor: int | None
) -> tuple[float | None, float | None]:
    """ROI/profit reales si hay que comprar `bundle_factor` unidades.
       El precio y los fees del pack NO cambian; solo el coste sube a cost*N."""
    if (nominal_profit is None or cost_unit is None or cost_unit <= 0
            or not bundle_factor or bundle_factor <= 1):
        return (None, None)
    corrected_cost   = cost_unit * bundle_factor
    corrected_profit = float(nominal_profit) - cost_unit * (bundle_factor - 1)
    corrected_roi    = corrected_profit / corrected_cost * 100 if corrected_cost > 0 else None
    return (round(corrected_profit, 2),
            round(corrected_roi, 4) if corrected_roi is not None else None)
```

> ⚠️ **Bugs a NO replicar** (de Findings): el `fba = sp_api_fba_fee or keepa_fba_fee` con `or` salta `0.0` → en FlipIQ solo hay Keepa, asegurar null-safety (ya cubierto por `cost_unit<=0 → False`). El gate fee-ratio solo NO basta (caso Trojan no dispara K=6) → por eso van los 3 gates. **Reemplazar** el `_PACK_RE` naive de amazon.py, no sumarlo.

### 2.3 — Cosechar señales del producto evaluado (`amazon.py`)

El reto del modelo de comps: BatchFlip tiene 1 ASIN = `item.title`; FlipIQ tiene muchos comps. **El bundle_factor relevante es el del producto que el usuario va a vender** (el que domina `market_list`).

**Decisión de diseño:** identificar el **producto evaluado** = el producto principal de Keepa (mejor `sales_rank`, o el primero tras el filtro de consenso de marca de Fase 0). De ÉL se extraen `title`, `packageQuantity`/`numberOfItems`, y su `fba_fee`. Se exponen en el `CompsResult` vía campos nuevos:

```python
# base.py — añadir a CompsResult:
evaluated_title: str | None = None             # título del producto principal
evaluated_package_quantity: int | None = None  # packageQuantity/numberOfItems de Keepa
evaluated_bundle_factor: int | None = None      # regex/LLM sobre evaluated_title
# (fba_fulfillment_fee ya existe)
```

En `_build_comps_from_products`, tras elegir el producto principal:
```python
main = _pick_main_product(products)   # mejor rank o primero tras filtro de marca
result.evaluated_title = main.get("title")
result.evaluated_package_quantity = (
    main.get("packageQuantity") or main.get("numberOfItems") or None
)
# bundle_factor se calcula en la capa async (analysis_service), no aquí (puede usar LLM)
```

### 2.4 — Integración en el pipeline (`analysis_service.py`)

1. Calcular `bundle_factor` del producto evaluado (regex; LLM si está habilitado) — en la capa async, antes o dentro de `_run_pipeline`.
2. Tras `profit_market = compute_profit(...)` (línea 331), llamar al guard.
3. Si hay mismatch: calcular `corrected_metrics`, pasar a `_validate_buy` para degradar + warning.

```python
# En _run_pipeline, solo para marketplace_name == "amazon_fba":
mismatch = detect_multipack_mismatch(
    cost_unit=cost_price,
    keepa_fba_fee=raw_comps.fba_fulfillment_fee,
    package_quantity=raw_comps.evaluated_package_quantity,
    bundle_factor=raw_comps.evaluated_bundle_factor,
)
corr_profit, corr_roi = (None, None)
if mismatch:
    corr_profit, corr_roi = corrected_metrics(
        nominal_profit=profit_market.profit,
        cost_unit=cost_price,
        bundle_factor=raw_comps.evaluated_bundle_factor,
    )
```

```python
# En _validate_buy, nuevo bloque (mismo patrón que el de condición):
if multipack_mismatch:
    bf = evaluated_bundle_factor
    if corr_roi is not None:
        warnings.append(
            f"This Amazon listing appears to be a bundle of {bf}. "
            f"Your cost (${cost_price:.2f}) is per single unit, but the market "
            f"price (${profit_market... }) is for the {bf}-pack. "
            f"Real ROI if you must buy {bf}: {corr_roi:.0f}% "
            f"(vs {nominal_roi:.0f}% shown)."
        )
    else:
        warnings.append(
            f"This Amazon listing may be a multipack and your cost looks "
            f"per-unit — the ROI shown may be inflated. Verify pack size."
        )
    if recommendation == "buy":
        recommendation = "buy_small"     # degrada (nunca a watch directo: el guard puede tener FP)
    # Si corr_roi existe y es malo (< target), degradar más:
    if corr_roi is not None and corr_roi < 20 and recommendation in ("buy", "buy_small"):
        recommendation = "watch"
```

> **AC-3:** `profit_market.profit`/`roi` se mantienen intactos en el response. `corrected_*` se exponen como campos **informativos** ("si es pack de N, tu ROI real es X%"). El bucket/decisión se degrada vía `_validate_buy`, no recalculando profit.

### 2.5 — Exponer en el schema (`app/schemas/analysis.py`)

Campos **advisory** en `MarketplaceAnalysis`/respuesta:
```python
is_likely_multipack: bool = False
bundle_factor: int | None = None
corrected_roi_pct: float | None = None
corrected_profit: float | None = None
multipack_reason: str | None = None    # "fee_ratio" | "package_quantity" | "title_bundle"
```

---

## 3. Plan de implementación por PRs

Secuencia incremental; cada PR es deployable y testeable solo.

### PR-M1 — Extractor regex + fix de `_filter_multipacks` (bajo riesgo, ~1 día)
**Archivos:** `app/services/marketplace/multipack.py` (nuevo), `app/services/marketplace/amazon.py`.
- Portar `_PACK_SIGNAL_RE`, `_BUNDLE_RE`, `_TWIN_RE`, `_TRIPLE_RE`, `_MAX_REASONABLE_FACTOR`, `_has_pack_signal`, `regex_bundle_factor` (sin LLM aún).
- **Reemplazar** `_is_multipack`/`_PACK_RE` de amazon.py por `regex_bundle_factor(title) is not None and > 1`. Esto **corrige el bug "N count"** (Findings #1): "Vitamin C, 100 Count" deja de filtrarse como multipack.
- Considerar el fix del conteo pegado (`12ct`) que BatchFlip aún no tiene (Findings #32): añadir `\d+\s*(ct|pk|pack|count)` sin `\b` de apertura al pre-filtro.

**Tests** (`tests/test_multipack_extractor.py`):
| Input | `regex_bundle_factor` esperado |
|---|---|
| `"Soap (Pack of 12)"` | 12 |
| `"Trojan ... 3 Count (Pack of 12)"` | 12 (ignora el 3) |
| `"Vitamin C, 100 Count"` | None (ambiguo → no filtra) |
| `"Paper Towels, 6 Rolls"` | None |
| `"Razors Twin Pack"` | 2 |
| `"Widget"` (sin señal) | None |
| `"AA Batteries 24-Pack"` | 24 |

### PR-M2 — Cosechar señales de Keepa del producto evaluado (~1 día)
**Archivos:** `amazon.py`, `base.py`.
- Añadir `evaluated_title`, `evaluated_package_quantity` a `CompsResult`.
- `_pick_main_product(products)`: mejor `salesRankReference`/rank (¡usar `current[CSV_SALES_RANK]`, no `salesRankReference` — Findings #9!) o el primero.
- Extraer `packageQuantity`/`numberOfItems` del producto principal.
- (Opcional) poblar `is_bundle`/`lot_size` en `_map_keepa_offers` para activar la normalización latente de `comp_cleaner` (Escenario B).

**Tests:** producto Keepa con `packageQuantity=12` → `result.evaluated_package_quantity == 12`. Producto sin el campo → `None` (nunca 0).

### PR-M3 — Guard de 3 gates + integración (el corazón, ~2-3 días)
**Archivos:** `app/services/engines/cost_integrity.py` (nuevo), `analysis_service.py`, `schemas/analysis.py`.
- `detect_multipack_mismatch` + `corrected_metrics` (§2.2).
- Calcular `evaluated_bundle_factor` con `regex_bundle_factor(evaluated_title)`.
- Engancharlo en `_run_pipeline` tras `compute_profit`, **solo para `amazon_fba`**.
- Bloque nuevo en `_validate_buy` (§2.4): warning con corrected ROI + degradar.
- Exponer campos advisory en el schema (§2.5).

**Tests** (`tests/test_cost_integrity.py` + integración):
| Caso | cost | fba_fee | pkg_qty | bundle | mismatch | corrected_roi |
|---|---|---|---|---|---|---|
| Trojan pack | $1.30 | $4.20 | 1 | 12 | ✅ (gate 3) | ~40% |
| Fee delata pack | $1.00 | $8.00 | 1 | None | ✅ (gate 1) | — |
| Pkg estructurado | $2.00 | $3.00 | 6 | None | ✅ (gate 2) | — |
| Unidad simple legítima | $5.00 | $3.50 | 1 | None | ❌ | — |
| Cost 0 / falta | 0 | $4 | 1 | 12 | ❌ (guard) | — |
- Test de integración: análisis Amazon con producto pack → response trae `is_likely_multipack=True`, `corrected_roi_pct` poblado, recomendación degradada, `profit`/`roi` nominales **intactos** (AC-3).

### PR-M4 — LLM para "N count" ambiguo (opcional, gated, ~1-2 días)
**Archivos:** `multipack.py`, `config.py`.
- Portar `extract_bundle_factor` (rama LLM) sobre `app/core/llm.py` (Gemini).
- Flag `multipack_llm_enabled: bool = False` en config (off por defecto, igual que BatchFlip).
- Solo se invoca para títulos con señal de pack pero sin patrón inequívoco ("12 Count").
- Cache simple por título (el bundle de un ASIN no cambia).

> ⚠️ Findings #23: "1 Pack" + "N Count" devuelve factor 1 y suprime el LLM. Al portar, considerar devolver `None` (→ LLM) cuando hay un "N Count" ambiguo junto a un "Pack of 1".

**Tests:** mock del LLM. `"Paper Towels, 12 Count"` → 12; `"Condoms, 3 Count"` → 1. Circuit breaker abierto → `None` (degrada sin crash).

### PR-M5 — UX / response (~1 día)
- Asegurar que `corrected_roi_pct`, `bundle_factor`, `is_likely_multipack`, `multipack_reason` lleguen al cliente y a `engines_data` (auditoría/ML).
- Texto del warning claro para el reseller ("Este listing es un pack de 12; tu costo es por unidad").

---

## 4. Decisiones de diseño y riesgos

| Decisión | Razón |
|---|---|
| Gate 1 (fee-ratio) **activo desde PR-M3** | Es el único que no necesita título ni LLM; computable hoy; ataca el modo de fallo #1 |
| Degradar `buy→buy_small` (no directo a `watch`) | El guard puede tener falsos positivos; degradación suave + warning deja al usuario decidir |
| `corrected_*` como informativo, no reemplazo | Invariante AC-3; el usuario ve el ROI nominal Y el real |
| bundle_factor del **producto principal**, no de todos los comps | El usuario vende UN producto; el mismatch es sobre ese |
| LLM **opcional y off por defecto** | Determinismo + costo; el regex cubre los casos inequívocos |

**Riesgos a vigilar:**
- **`_MAX_REASONABLE_FACTOR=1000` es laxo** (Findings): un número del título (año "2024", gramaje) podría colarse como factor. Bajar el cap (~144) o exigir co-ocurrencia con palabra de pack.
- **Falso positivo del gate fee-ratio**: items grandes/pesados legítimos con fee alto vs costo bajo. K=6 es conservador, pero validar con datos reales de FlipIQ antes de degradar agresivo.
- **Determinar el "producto principal"** en el path de keyword es más ambiguo que en barcode → empezar gateando el guard al path de **barcode** (donde la identidad es fuerte), igual que el consenso de marca.
- **Escenario B residual**: si tras arreglar `_filter_multipacks` quedan SOLO packs, `market_list` sigue siendo del pack → ahí el guard (Escenario A) es justo la red. Por eso A y B son complementarios, no alternativos.

---

## 5. Esfuerzo total estimado

| PR | Alcance | Esfuerzo | Dependencia |
|---|---|---|---|
| M1 | Extractor regex + fix `_filter_multipacks` | 1 día | — |
| M2 | Cosechar señales Keepa | 1 día | — |
| M3 | Guard 3 gates + integración + schema | 2-3 días | M1, M2 |
| M4 | LLM "N count" (opcional) | 1-2 días | M1, M3 |
| M5 | UX/response | 1 día | M3 |
| **Total** | **núcleo (M1-M3, M5)** | **~5-6 días** | LLM aparte |

> **PR-M1 + M3 con solo el gate fee-ratio ya elimina el caso de fallo más peligroso** (ROI fantasma sobre packs detectables por fee). El resto (gate estructurado, LLM) sube la cobertura incrementalmente.

---

*Plan generado el 2026-06-27 a partir de la lectura del código real: `batchflip_core` (multipack_extractor.py, dual_profit.py) y FlipIQ (amazon.py, profit_engine.py, max_buy_price.py, analysis_service.py `_validate_buy`/`_run_pipeline`, pricing_engine.py, comp_cleaner.py, schemas/analysis.py).*
