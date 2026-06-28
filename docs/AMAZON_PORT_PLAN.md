# Plan de portabilidad — Motor de Amazon de BatchFlip → FlipIQ

> Cómo traer el flujo de análisis de Amazon de **BatchFlip** (`batchflip_core`) a **FlipIQ**, calibrado para el caso B2C single-product de FlipIQ (un reseller evalúa **un** producto), NO para la ingesta batch industrial de BatchFlip.
>
> Basado en una evaluación componente-por-componente leyendo el código real de ambos lados. Acompaña a [`AMAZON_ENGINE_FINDINGS.md`](./AMAZON_ENGINE_FINDINGS.md) (bugs a no replicar).

---

## TL;DR — la decisión de fondo

**No migres de paradigma. Enriquece el que ya tienes.**

FlipIQ modela Amazon como **"comps"** (`CompsResult` = nube de listings estilo eBay) que pasan por el mismo pipeline de 13 motores que eBay. BatchFlip modela Amazon como **`ProductData`** (1 ficha exacta por ASIN) → `dual_profit`.

Migrar a `ProductData` "tal cual" **rompería** tus motores: meter 1 punto exacto en motores que asumen distribución (`risk`, `competition`, `trend`, `seller_premium`, `confidence`) colapsa `cv=0, iqr=0, p25=p75=median` → hunde el risk score y deja sin sentido la mitad del pipeline. Eso es un refactor mayor (rama single-SKU), **no un port**.

**Estrategia ganadora:** las piezas de mayor valor de BatchFlip son **defensivas y de calidad de datos** (anti-falso-match, anti-precio-fantasma, pacing de tokens), y **casi todas encajan dentro de tu `CompsResult` actual sin tocar los motores**. Lo caro (SP-API completo, `ProductData`, `id_rescue`, `column_mapper`, dual FBA/MFN) es específico del batch o se difiere.

---

## Insight clave: ya tienes los datos, los estás tirando

Tu `amazon.py` pide a Keepa `stats=30 + offers=20 + buybox=1 + history=1 + days=90` — **paga por** Buy Box real, ventas mensuales, drops de rank, package quantity… y luego **solo extrae** `best_rank`, `referralFeePercentage`, `pickAndPackFee` e `imagesCSV`. Todo lo demás llega en el payload y se descarta.

| Dato que Keepa YA devuelve | ¿Lo usa FlipIQ? | Valor |
|---|---|---|
| `stats.buyBoxPrice` (Buy Box real) | ❌ (pero sí lo extrae `price_tracker.py:56-84`) | **Alto** — precio ejecutable real vs mediana sintética |
| `monthlySold` (ventas reales) | ❌ | **Alto** — velocity real vs estimación BSR |
| `salesRankDrops30/90` | ❌ | Alto — testigo de velocidad real |
| `packageQuantity` / `numberOfItems` | ❌ | **Alto** — multipack estructurado vs regex |
| `offerCountNew` (ofertas nuevas) | ❌ | Medio — liquidez real |
| `reviewCount` / `rating` | ❌ | Medio — confianza/señal |

> **Fase 1 entera no necesita SP-API.** Cosechar estos campos del payload que ya pagas es el mayor salto de calidad con el menor esfuerzo.

---

## Matriz de portabilidad

| Componente BatchFlip | Veredicto | Valor | Esfuerzo | Fase | Encaja en CompsResult |
|---|---|---|---|---|---|
| **Token Governor + cliente httpx compartido** | portar-con-adaptación | Alto | Medio | 1 | ✅ sí (transparente) |
| **Consenso de marca** (`identity.choose_candidate`) | portar-como-filtro | Alto | **Bajo** | 0 | ✅ sí (filtra products) |
| **Guard multipack** (`_detect_multipack_mismatch`) | portar | Alto | Medio | 1 | ✅ sí (motor nuevo) |
| **`size_match`** (mismatch de tamaño) | portar tal cual | Alto | Bajo | 1 | ✅ sí (función pura) |
| **`history_confidence`** (anti-precio-fantasma) | portar | Alto | Medio | 1 | ✅ sí (advisory) |
| **Cosechar señales Keepa** (buy_box, monthly_sold…) | nuevo | Alto | Bajo | 0/1 | ✅ sí (campos nuevos) |
| **SP-API** (Buy Box real-time + fees exactos) | portar-con-adaptación | Alto | Medio | 2 | ✅ como enriquecedor |
| **ProductData + rama single-SKU** | reimplementar | Alto | **Alto** | 3 (diferir) | ❌ refactor |
| **`can_sell` / restrictions** | diferir | Medio | Alto | 3 (diferir) | requiere OAuth por-usuario |
| **dual FBA/MFN** (`compute_dual_profit`) | diferir | Medio | Alto | 3 (diferir) | over-engineering hoy |
| **`id_rescue`** (reconstruir UPC de Excel) | **omitir** | — | — | — | batch-only |
| **`column_mapper`** (detección de columnas) | **omitir** | — | — | — | batch-only |

---

## Roadmap por fases

### 🟢 FASE 0 — Quick wins (sin dependencias nuevas, ~2-3 días)

Arregla los bugs heredados + el quick-win de mayor ROI. Cero infra nueva.

**0.1 — Corregir los bugs que FlipIQ ya tiene** (ver `AMAZON_ENGINE_FINDINGS.md` §"Bugs que FlipIQ ya tiene hoy"):
- `_PACK_RE` con "N count" (`amazon.py:57-66`): deja de filtrar unidades sueltas válidas. Reemplazar por la disciplina del extractor (regex solo para patrones **inequívocos**; "N count" suelto NO es multipack). **Esto se resuelve solo al portar el extractor en Fase 1**, pero el fix mínimo es quitar `\b(\d+)\s*[-\s]?count\b` del regex de filtrado.
- `salesRankReference` como rank (`amazon.py:415, 502`): usar `current[CSV_SALES_RANK]` directo + sanity cap. En FlipIQ esto alimenta velocity → impacto real.

**0.2 — Consenso de marca anti-contaminación** (`identity.py` → `app/services/marketplace/identity.py`, ~40 líneas):
- Re-empaquetar `choose_candidate` como **filtro de products**, no como selector de 1 ASIN: `filter_products_by_brand_consensus(products) -> (kept, needs_review, dominant_brand, reason)`.
- Conservar los products de la marca dominante (≥60%), descartar los contaminantes (≤30%). Sin señal o 1 marca → devuelve todo (cero regresión).
- **Gatear SOLO al path de barcode** en `get_sold_comps` (tras `_keepa_product_by_code`, antes de `_filter_multipacks`). **NUNCA al path de keyword** (ahí marcas distintas son legítimas) ni a `get_sold_comps_by_asin`.
- Surfacear `needs_review` como warning del `CompsResult` + `logger.warning`.
- ⚠️ No replicar el bug del `default_brand=''` (un candidato sin marca tratado como outlier). El modelo-filtro lo evita naturalmente.
- Evita el caso real "Summer's Eve UPC también en ASIN de Arrid" → falso buy con ROI 2477% sobre el producto equivocado.

**0.3 — Cosechar `buy_box_price` y señales estructuradas de Keepa** (`amazon.py` `_build_comps_from_products`):
- Inyectar en `CompsResult` (campos nuevos): `buy_box_price` (de `stats.buyBoxPrice`, reusar el parseo de `price_tracker.py`), `monthly_sold`, `sales_rank_drops_30`, `package_quantity`/`number_of_items`, `offer_count_new`, `review_count`, `rating`.
- Es 1 bloque de extracción; los datos ya llegan en el payload.

---

### 🟡 FASE 1 — Robustez y guards (alto valor, ~1-2 semanas)

Lo que más mueve la aguja en calidad de decisión, todo dentro de `CompsResult`.

**1.1 — Token Governor + cliente httpx compartido** (`keepa.py` → dentro de tu `AmazonClient`):
- Portar `_TokenGovernor`, `compute_wait`, `_TokenSnapshot` (frozen, **per-call**), `_parse_token_snapshot`, `backoff_delay` token-aware, y el patrón `_get_client()`/`_client_lock`/`close()`.
- Reemplazar `_keepa_get` por la versión con governor + retries (4xx no reintenta; 429/5xx con backoff que libera la reserva **antes** de dormir). Sigue devolviendo `dict` → el mapeo a `CompsResult` no cambia.
- Añadir bloque `keepa_*` a `config.py` (`keepa_governor_enabled`, `keepa_bootstrap_refill_rate`, `keepa_max_capacity`, `keepa_acquire_timeout_s`, `keepa_backoff_cap_s`, `keepa_backoff_jitter`, `keepa_max_retries`, `keepa_min_request_spacing_s`, `keepa_code_lookup_cost`).
- **Adaptar `_needed_for` al coste REAL de FlipIQ**: tú pides `offers+buybox+history`, que cuestan **más** de 1 token/ASIN. Copiar el `_needed_for` stats-only de BatchFlip haría sub-reservar → no evitaría los 429.
- **NO** copiar la optimización stats-only de `_keepa_product`: vaciaría tu nube de comps (BatchFlip la repone con SP-API; tú no, aún).
- **Tunear para latencia interactiva**: `MIN_WAIT_SECONDS=5` + bootstrap 20 tokens/min puede añadir segundos a un análisis de 1 producto en cold-start. Sube `bootstrap_refill_rate` o baja `MIN_WAIT` para no penalizar al usuario.
- Cablear `AmazonClient.close()` al `lifespan`/shutdown de FastAPI (hoy no se cierra; el singleton `_get_amazon_client` ya existe — mantenerlo).
- ⚠️ No "simplificar" leyendo `self._tokens_left` en `on_response` (race que rompe la contabilidad); no omitir el `snap=None` tras el retry (doble-release).

**1.2 — Guard de integridad de coste** (`app/services/engines/cost_integrity.py`, nuevo):
- Portar `size_match.py` **tal cual** (función pura, cero deps) + `regex_bundle_factor`/`_has_pack_signal` del `multipack_extractor`.
- Implementar `_detect_multipack_mismatch` adaptado a kwargs: `cost_unit=user_cost`, `sale_price=pricing.market_list`, `keepa_fba_fee=raw_comps.fba_fulfillment_fee`, `bundle_factor` (regex), `size_mismatch` (vs `input_title`).
  - **Gate primario fee-ratio** (`fba_fee >= cost_unit*6`): detecta multipack SIN depender del título. Computable HOY con datos presentes → ataca el modo de fallo #1 (match UPC/keyword → ASIN multipack con coste de unidad simple).
  - **Gate secundario**: `packageQuantity`/`numberOfItems > 1` (de Fase 0.3).
  - **Gate terciario**: `bundle_factor > 1` del título.
- Engancharlo en `_run_pipeline` **tras** `compute_profit`, solo para `marketplace_name=='amazon_fba'`.
- **Invariante AC-3**: el guard **degrada la recomendación** (`buy→watch`/`buy→buy_small`) y emite un warning, **NUNCA toca `profit`/`roi`**. Mapea directo a tu `_validate_buy` + `data_quality_warnings`.
- ⚠️ El gate fee-ratio solo NO basta (caso "Trojan": Pack of 12, `packageQuantity=1`, título sin "Pack of N" → no dispara). Por eso van los 3 gates. Y al portar, **reemplaza** el `_PACK_RE` naive, no lo sumes (es el falso positivo que BatchFlip evita).

**1.3 — History confidence / precio fantasma** (`history_confidence.py` → `app/services/engines/history_confidence.py`):
- Portar el módulo casi 1:1 (función pura, duck-typed). Es el guard contra el **modo de fallo #1 del single-product Amazon**: una única oferta stale → mediana fantasma → ROI absurdo (el caso real "Degree 2108% ROI sobre $82.66").
- Adaptador `SimpleNamespace(CompsResult + ProfitResult)` con los atributos que espera. **Críticos:**
  - ROI en **porcentaje** (`roi*100`; tu `ProfitResult.roi` es fracción 0.4=40%, pero `ROI_SANITY=400.0`).
  - `offer_count_new` = ofertas **nuevas** reales de Keepa, **NO** tu `seller_count` (que cuenta sellers sobre listings sintéticos e incluye usados).
  - Mapear "no consultado" → `None`, **nunca a 0** (un `buy_box_eligible_new=0` falso genera phantom falso).
  - Surfacear `monthlySold`/`salesRankDrops30` **reales** (no `sales_per_day` derivado de BSR, que es colineal) o `real_velocity` nunca dispara → falsos positivos masivos.
- Exponer `data_confidence` (low/medium/high) + `phantom_price` (bool) + `reasons` como campo **advisory** en el schema. No toca profit/bucket/recommendation.
- ⚠️ Umbrales (`STALE_RATIO`, `ROI_SANITY=400`, `THIN_SELLERS`…) están marcados "a calibrar con scan real" → **calibrar con datos reales de FlipIQ antes de mostrar el badge**.

---

### 🟠 FASE 2 — SP-API como capa de exactitud (alto valor, ~2-3 semanas)

SP-API cierra los gaps que ni Keepa ni los guards resuelven: **precio de mercado = Buy Box real ejecutable** (vs mediana de comps) y **fees exactos** (vs promedio de Keepa). Adoptarlo como **enriquecedor del `CompsResult`**, no como pipeline `ProductData` paralelo.

**2.1 — Auth app-level** (`spapi_auth.py` → `app/services/marketplace/spapi_auth.py`):
- Portar `SPAPIAuth` (OAuth LWA refresh→access, cache, renovación <60s; su User-Agent ya dice "FlipIQ/1.0") + `_TokenBucket` (rate-limit por endpoint con auto-tune desde `x-amzn-RateLimit-Limit`).
- **Añadir `asyncio.Lock` al `get_access_token`** (en server concurrente varios requests dispararían N refresh — bug #11 de los hallazgos, no lo portes).
- Config: añadir `sp_api_refresh_token` (token del seller **PROPIO de FlipIQ**, app-level — **no por-usuario**) y **reusar** `amazon_lwa_client_id`/`amazon_lwa_client_secret` (**ya existen en `config.py:57-63` sin uso**). Onboarding OAuth **una sola vez** de la cuenta de la empresa.

**2.2 — SPAPIProvider recortado** (`spapi.py` → `app/services/marketplace/spapi.py`):
- Solo los endpoints **single-item**: `get_item_offers` (Buy Box real, lowest new/used, offer_count FBA/FBM), `get_fees_estimate` (referral+FBA+closing+per_item exactos), `get_catalog_item` (dims/peso/UPC/pack estructurado), `check_fba_eligibility`, `resolve_code_with_candidates` (UPC→ASIN oficial).
- **Tirar todas las variantes `*_batch`** (ingesta industrial de miles de ASINs — over-engineering aquí).
- `_get_spapi_client()` singleton (espejo de `_get_amazon_client`). **Crítico**: el cache de token y los `_TokenBucket` son por-instancia; sin singleton se pierden.

**2.3 — Cablear como enriquecedor**:
- Tras construir `amazon_raw` (o en `_run_pipeline` antes de `compute_profit`): `get_item_offers(asin)` + `get_fees_estimate(asin, price)`.
- `FeesResult.referral_fee` → `CompsResult.fba_referral_pct`; `FeesResult.fba_fee` → `CompsResult.fba_fulfillment_fee` (**la ruta de override ya existe**: `analysis_service.py:317-348` → `profit_engine.py:67-68`).
- `OffersResult.buy_box_price` → nuevo `CompsResult.buy_box_price` que en modo single-SKU **sustituye `pricing.market_list`** (la mediana) por el Buy Box real ejecutable.
- Extender el schema (`CompsInfo`/`MarketplaceAnalysis`) con `buy_box_price`/`offer_count`/`fba_eligible`.
- **Cache por ASIN/UPC (TTL corto)**: los rate-limits son 0.5 req/s; N usuarios concurrentes comparten el bucket del singleton → sin cache el usuario N espera ~2·N s y se quema cuota.

**2.4 — Manejo de None (no replicar bugs)**:
- 403/HTTPError → `None` indistinguible de "sin datos": el caller trata `None` como "desconocido" → **cae al estimado de Keepa**, no a 0/ok.
- `CurrencyCode='USD'` hardcoded y `AMAZON_RETAIL_SELLER_IDS` US-only: aceptable en v1 US-only, marcar para i18n.

---

### 🔴 FASE 3 — Diferido (solo si el producto lo pide)

No construir hasta que haya señal de demanda. Es infra pesada con poco retorno marginal hoy.

- **`ProductData` + rama single-SKU en `_run_pipeline`**: el refactor real para que un punto exacto (Buy Box) no degenere los motores de distribución. Requiere reescribir cómo `risk`/`competition`/`trend` derivan señales de una ficha (volatilidad Buy Box, offer_count FBA/FBM, %OOS, drops) en vez de un histograma de comps. Es el único camino para "Amazon como ficha" completo — pero es un proyecto, no un port.
- **`can_sell` / restrictions por-usuario**: requiere OAuth SP-API **por-usuario** (modelo `SellerConnection` sobre Supabase Auth + cifrado Fernet + callback). Con el token global de la empresa darías el gating de **otra cuenta** → falso "puedes vender esto". Omitir hasta que haya gating personalizado.
- **dual FBA/MFN** (`compute_dual_profit`): legítimo para un reseller, pero hoy choca con tu abstracción dual eBay-vs-Amazon y necesita `estimate_mfn_shipping` (peso facturable USPS) + campos de schema. Reabordar junto al refactor `ProductData`. ⚠️ Si se porta, no replicar el bug histórico `fee_fixed_override=0.0` que inflaba el profit MFN.

---

## Qué NO portar (y por qué)

| Componente | Razón |
|---|---|
| **`id_rescue`** | Reconstruir UPCs corruptos de Excel es ingesta-batch puro. En FlipIQ el barcode viene de escaneo limpio / upcitemdb. (Si algún día se quiere un guard anti-falso-match barato, solo `titles_match` es self-contained — pero `comp_relevance.py` ya cubre relevancia.) ⚠️ No portar el `lstrip("0")` (bug #3). |
| **`column_mapper`** / **`file_parser`** | Detección de columnas de un catálogo Excel. FlipIQ no ingesta catálogos. |
| **`batch_processor` / `fast_scan_processor`** | Orquestadores de jobs batch con quotas, recycle, SQS. FlipIQ analiza 1 producto sincrónico. |
| **Variantes `*_batch`** de SP-API/Keepa | Chunking de 20-100 ASINs. En single-product solo se usan las versiones single-item. |
| **`product_cache` (BatchFlip)** | Su TTL es código muerto (bug #21). Si FlipIQ quiere cache por ASIN (recomendado para SP-API), implementarlo limpio, no portar este. |

---

## Resumen de dependencias nuevas por fase

| Fase | Config nuevo | Modelos DB | Auth | Schema |
|---|---|---|---|---|
| 0 | — | — | — | `CompsResult` +campos |
| 1 | bloque `keepa_*` | — | — | campos advisory (phantom/confidence) |
| 2 | `sp_api_refresh_token` (reusa `amazon_lwa_*`) | — (cache por ASIN opcional) | OAuth **1 vez** app-level | `buy_box_price`/`fba_eligible`/`offer_count` |
| 3 | — | `SellerConnection` + `ENCRYPTION_KEY` | OAuth **por-usuario** | `mfn_profit`/`can_sell`/`best_scenario` |

---

## Orden recomendado de ejecución

1. **Fase 0** entera (quick wins + fix de bugs heredados) — máximo ROI inmediato, cero riesgo de infra.
2. **Fase 1.3** (history_confidence/phantom) y **1.2** (guard multipact/size) — atacan los dos modos de fallo más dañinos del single-product Amazon (precio fantasma y multipack-mismatch).
3. **Fase 1.1** (token governor) — cuando el volumen/concurrencia empiece a generar 429 de Keepa (cuesta dinero).
4. **Fase 2** — cuando quieras dar el salto de "mediana sintética" a "Buy Box real + fees exactos". Requiere decidir el onboarding OAuth app-level.
5. **Fase 3** — solo bajo demanda explícita del producto.

> **El 80% del valor de calidad de decisión está en Fases 0-1**, sin SP-API ni refactor. Empieza por ahí.

---

*Generado a partir de la evaluación multi-agente de portabilidad del 2026-06-27, leyendo el código real de `batchflip_core` y de FlipIQ (`app/services/marketplace/amazon.py`, `analysis_service.py`, engines, schemas).*
