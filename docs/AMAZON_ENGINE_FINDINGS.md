# Motor de búsqueda de datos de Amazon — Hallazgos a resolver

> Auditoría del motor de Amazon de **BatchFlip** (`/Users/alexmontesino/Amazon_batch_analyse`, paquete `batchflip_core`), verificada de forma adversarial (58 agentes, cada hallazgo releído contra el código real). Sirve doble propósito: **(1)** lista de fixes para BatchFlip y **(2)** lista de bugs a **NO replicar** al portar este flujo a FlipIQ.
>
> Resultado: **31 hallazgos confirmados** (1 alto · 4 medios · 26 bajos) y **19 descartados** como falsos positivos o código muerto.
>
> Convención de severidad: la severidad mostrada es la **corregida tras verificación** (varios "alto" iniciales se rebajaron al comprobar mitigaciones reales). Ningún hallazgo fabrica un "falso rentable" peligroso en la configuración por defecto.

---

## Leyenda de impacto en FlipIQ

Marca si el bug **ya existe en FlipIQ** (`app/services/marketplace/amazon.py`), porque BatchFlip nació como fork de ese archivo:

- 🔴 **HEREDADO** — el mismo bug está hoy en FlipIQ; arreglarlo aquí y allá.
- 🟠 **N/A (paradigma)** — no aplica a FlipIQ hoy porque usa el modelo "comps" (`CompsResult`), pero aplicará al portar SP-API/`ProductData`.
- ⚪ **Solo BatchFlip** — específico de infraestructura que FlipIQ no tiene.

---

## Tabla de prioridad

| # | Sev | Cat | Hallazgo | Archivo (BatchFlip) | FlipIQ |
|---|-----|-----|----------|---------------------|--------|
| 1 | 🔴 ALTO | lógica | `_is_multipack` trata "N count" como multipack y descarta unidades sueltas válidas | `keepa.py:45-54,297-304,542-545` | 🔴 HEREDADO |
| 2 | 🟡 MEDIO | datos | Flags de Amazon (`amazon_is_seller`/`buy_box_is_amazon`) degradadas a `False` con snapshot parcial SP-API | `hybrid.py:240-241` | 🟠 N/A |
| 3 | 🟡 MEDIO | lógica | `lstrip("0")` mutila un UPC-A con cero líder → rescate al producto equivocado | `file_parser.py:1460-1476` | ⚪ Solo BatchFlip |
| 4 | 🟡 MEDIO | bug | Fast scan subcuenta contadores y puede fallar la sanity tras *recycle* | `fast_scan_processor.py:162-214` | ⚪ Solo BatchFlip |
| 5 | 🟡 MEDIO | edge | Fallo transitorio de chunk marca items como `not_found` (no reintentable) | `batch_processor.py:226-228,471-473` | 🟠 N/A |
| 6 | 🟢 BAJO | datos | `lowest_price_new` de Keepa sobrescrito a `None` en el merge sin guard | `hybrid.py:244-246` | 🟠 N/A |
| 7 | 🟢 BAJO | datos | `seller_count`/`amazon_is_seller` siempre 0/False desde Keepa (offers no se piden) | `keepa.py:369-385,470-471` | 🟠 N/A (FlipIQ sí pide offers) |
| 8 | 🟢 BAJO | datos | `buy_box_price` de Keepa es en realidad *lowest New* (falta `buybox=1`) | `keepa.py:313-328,697-716` | 🟠 N/A (FlipIQ sí pide `buybox=1`) |
| 9 | 🟢 BAJO | lógica | `_extract_sales_rank` lee `salesRankReference` (id de categoría) como si fuera rank | `keepa.py:331-342` | 🔴 HEREDADO |
| 10 | 🟢 BAJO | datos | `CurrencyCode='USD'` hardcodeado en fees SP-API para todos los marketplaces | `spapi.py:699,761` | 🟠 N/A |
| 11 | 🟢 BAJO | rend | Renovación de token LWA sin lock → estampida en cold-start | `spapi_auth.py:41-60` | 🟠 N/A |
| 12 | 🟢 BAJO | lógica | `invalidate()` nunca se llama; un 403 no invalida el token cacheado | `spapi_auth.py:71-74` | 🟠 N/A |
| 13 | 🟢 BAJO | edge | `TypeError` `None < float` en `_parse_offers_payload` (lowest price) | `spapi.py:295-299` | 🟠 N/A |
| 14 | 🟢 BAJO | bug | `KeyError` por acceso `ident['...']` con corchetes en identifiers | `spapi.py:559-562` | 🟠 N/A |
| 15 | 🟢 BAJO | datos | Detección de Amazon como vendedor/Buy Box solo funciona en `us` | `spapi.py:117-119,265-268` | 🟠 N/A (Keepa US-only también) |
| 16 | 🟢 BAJO | datos | `buy_box_shipping`/`lowest_price_used` clobber a `None` (no-op hoy) | `hybrid.py:244-246` | 🟠 N/A |
| 17 | 🟢 BAJO | valid | `can_sell=None` (error/sin seller_id) se trata como vendible (fail-open) | `hybrid.py:114-140` | 🟠 N/A |
| 18 | 🟢 BAJO | edge | Enriquecimiento SP-API no aislado: un `TypeError` tira todo el batch y pierde baseline Keepa | `spapi.py:295-300` + `hybrid.py:70-263` | 🟠 N/A |
| 19 | 🟢 BAJO | edge | `choose_candidate` no degrada contaminación cuando hay solo 2 candidatos (1 marca vs 1) | `identity.py:143` | 🟠 N/A (FlipIQ no tiene `identity`) |
| 20 | 🟢 BAJO | datos | `upsert_product` pisa `package_quantity` conocido (>1) con el default 1 | `product_cache.py:77` | ⚪ Solo BatchFlip |
| 21 | 🟢 BAJO | datos | TTL de 6h del `product_cache` es código muerto (`_is_cache_fresh` nunca se invoca) | `product_cache.py:45` | ⚪ Solo BatchFlip |
| 22 | 🟢 BAJO | valid | `titles_match` acepta variantes con discriminador común y umbral Jaccard 0.20 | `id_rescue.py:106` | ⚪ Solo BatchFlip |
| 23 | 🟢 BAJO | lógica | "1 Pack"/"Pack of 1" junto a "N Count" devuelve factor 1 y suprime el LLM | `multipack_extractor.py:133,197-199` | ⚪ Solo BatchFlip |
| 24 | 🟢 BAJO | edge | `size_match` no reconoce unidades deletreadas (grams/ounces/pounds/liters) | `size_match.py:30-42` | ⚪ Solo BatchFlip |
| 25 | 🟢 BAJO | edge | `oz` siempre se interpreta como masa: producto fluido escrito "oz" pierde el mismatch | `size_match.py:42,84-88` | ⚪ Solo BatchFlip |
| 26 | 🟢 BAJO | edge | Listings sin `ended_at` nunca se descartan por antigüedad | `comp_cleaner.py:351-359` | 🔴 HEREDADO (verificar) |
| 27 | 🟢 BAJO | datos | `comp_cleaner` es código eBay-céntrico sin callers en el path Amazon | `comp_cleaner.py:24-74` | ⚪ (en FlipIQ sí está vivo) |
| 28 | 🟢 BAJO | datos | No hay guard ni conversión de moneda entre coste del proveedor y precio Amazon | `dual_profit.py:181-184`, `file_parser.py:1185` | 🟠 N/A |
| 29 | 🟢 BAJO | lógica | El LLM puede promover una columna a rol barcode/cost sin validar contenido | `column_mapper.py:316-351` | ⚪ Solo BatchFlip |
| 30 | 🟢 BAJO | valid | El gate de "cost" es solo "es numérico"; `_classify_columns` no excluye precios de venta | `column_mapper.py:305-306` | ⚪ Solo BatchFlip |
| 31 | 🟢 BAJO | datos | La caché de mapeo por firma ignora el contenido (mismo header, datos distintos) | `column_mapper.py:215-227` | ⚪ Solo BatchFlip |
| 32 | 🟢 BAJO | edge | `_has_pack_signal` no detecta el conteo pegado ("12ct"/"36CT") | `multipack_extractor.py:51-54` | ⚪ Solo BatchFlip |

---

## 🔴 ALTO

### 1. `_is_multipack` trata "N count" como multipack y descarta unidades sueltas válidas
**`keepa.py:45-54, 297-304, 542-545`** · lógica · **🔴 HEREDADO por FlipIQ**

El `_PACK_RE` de Keepa incluye `\b(\d+)\s*[-\s]?count\b` y `_is_multipack` devuelve `int(qty) > 1`. Por tanto `products_to_data_map` convierte a `None` (→ `not_found`) cualquier **unidad suelta** cuyo título diga "3 Count", "100 Count", etc. — categorías muy comunes: vitaminas/suplementos, condones, baterías, K-cups, cosméticos.

Esto **contradice directamente** a `multipack_extractor.regex_bundle_factor` e `identity._title_is_multipack`, que excluyen deliberadamente "N count" como ambiguo (su ejemplo canónico es *"Condoms, 3 Count = factor 1"*). Ironía verificada: `_analyze_item` ya tiene el extractor LLM correcto, pero el item nunca llega porque el regex crudo lo descartó antes. Es un **falso negativo silencioso** (pierde oportunidad, no causa mala compra).

> **En FlipIQ:** el mismo `_PACK_RE` está en `app/services/marketplace/amazon.py:57-66`. El efecto allí es distinto pero igual de dañino: `_filter_multipacks` **elimina de los comps** las unidades sueltas con "N count" → el precio de mercado de Amazon se calcula sobre menos datos (o sobre los multipacks reales si todos quedan filtrados).

**Fix:** una sola fuente de verdad. Que `_is_multipack`/`_build_candidates` (y el de FlipIQ) usen `regex_bundle_factor` (excluye "N count") en vez de `_PACK_RE`. Tests: "Vitamin C, 100 Count" debe pasar como unidad; "Soap (Pack of 12)" debe seguir filtrándose.

---

## 🟡 MEDIOS

### 2. Flags de Amazon degradadas a `False` con snapshot parcial de SP-API
**`hybrid.py:240-241`** · datos · 🟠 N/A (aplica al portar SP-API)

`amazon_is_seller`/`buy_box_is_amazon` se sobrescriben **incondicionalmente** desde SP-API Item Offers (top ~20 ofertas). Si el ganador del Buy Box no está en ese subconjunto → pisa el `True` correcto de Keepa (`stats.buyBoxIsAmazon`). **Garantizado en cualquier marketplace ≠ us** (`AMAZON_RETAIL_SELLER_IDS` solo mapea "us"). Impacto real acotado: **ningún motor de scoring lee estas flags**; solo afectan export y el contexto del LLM. **Fix:** `bool|None` en `OffersResult` y mergear solo con certeza (OR lógico, no overwrite).

### 3. `lstrip("0")` mutila un UPC-A con cero líder
**`file_parser.py:1460-1476`** · lógica · ⚪ Solo BatchFlip

En rescate, un UPC-A legítimo con prefijo GS1 "0" (mayoría de UPCs US), ya zfilleado a 12, pasa por `lstrip("0")` → queda en 11 → se le anexa un check **nuevo** → código de 12 válido **de otro producto**. Si ese fantasma resuelve a un ASIN, el item se rescata al producto equivocado. Mitigado por `reject_rescue_if_title_mismatch` (si hay título) y baja probabilidad de colisión. **Fix:** no generar candidatos si `is_valid_barcode(code)` ya es `True` (identidad legítima no listada, no corrupta).

### 4. Fast scan subcuenta contadores tras *recycle*
**`fast_scan_processor.py:162-214`** · bug · ⚪ Solo BatchFlip

Tras un recycle, reconstruye chunks solo con items `status=='pending'`; los contadores arrancan en 0 y **no recalcula desde todos los items** (a diferencia de deep, `batch_processor.py:746-757`). Resultado: `matched/profitable` subcontados (el usuario ve menos "deals" reales) y, si la cola restante es toda `not_found`, la sanity lanza `RuntimeError` → marca **failed un job mayormente completado**. Los `JobItem` en DB están correctos; solo los contadores resumen. **Fix:** recomputar contadores desde el estado de todos los items al finalizar.

### 5. Fallo transitorio de chunk marca items como `not_found`
**`batch_processor.py:226-228, 471-473`** · edge-case · 🟠 N/A

Si un chunk Keepa devuelve `None` por timeout/rate-limit, esos ASINs quedan sin `ProductData` y se marcan `not_found` — **indistinguible de "el ID no existe en Amazon"**. El `id_rescue` corre *antes* de la fase Keepa y solo reintenta UPC/EAN, así que los ASIN-input afectados **nunca se reintentan**: deals válidos se pierden. El sanity de hit-rate solo detecta caídas masivas, no un chunk aislado. **Fix:** marcar status reintentable (`error`) en vez de `not_found`, o reintentar el chunk antes del análisis.

---

## 🟢 BAJOS (agrupados por tema)

### Merge híbrido sin guard de `None`/`False`
- **6.** `lowest_price_new` de Keepa → `None` cuando offers viene sin él (`hybrid.py:244-246`). Las líneas 236/242 sí tienen guard; estas no (oversight claro). **Fix:** mergear solo si SP-API `is not None`.
- **7.** `seller_count`/`amazon_is_seller` siempre 0/False desde Keepa porque offers no se piden (decisión de tokens). Lógica muerta que produce 0/False engañoso en lugar de `None`. (En **FlipIQ NO aplica**: FlipIQ sí pide `offers=20`.)
- **8.** `buy_box_price` de Keepa = *lowest New* real (falta `buybox=1`). Optimización **intencional documentada** (`docs/KEEPA_TOKEN_OPTIMIZATION.md`); SP-API/offers lo corrigen. (**FlipIQ NO aplica**: pide `buybox=1, history=1`.)
- **16.** `buy_box_shipping`/`lowest_price_used` clobber a `None` — **no-op hoy** (Keepa no puebla esos campos).

### Robustez de parsing SP-API (fixes de 1 línea)
- **13/18.** `None < lowest_new` lanza `TypeError` en `_parse_offers_payload` (`spapi.py:297`). Mitigado por `return_exceptions=True` en los procesadores reales; expone solo el endpoint single-ASIN y streamlit. **Fix:** `price is not None and price < lowest_new`.
- **14.** `ident['identifierType']` con corchetes → `KeyError` (`spapi.py:559`). `identifierType`/`identifier` son campos *requeridos* por el schema de Amazon, así que es improbable. **Fix:** `.get()`.

### Auth SP-API
- **11.** Sin `asyncio.Lock` en `get_access_token` → estampida de POST a LWA en cold-start (`spapi_auth.py:41`). Acotado a jobs ASIN-only (~5-8 POSTs, no 20). **Fix:** lock con double-checked locking (espejar el cliente Keepa, que sí lo hace).
- **12.** `invalidate()` nunca se llama; un 403 no invalida el token (`spapi_auth.py:71`). Impacto menor: los escenarios reales (grant revocado) tampoco se recuperan con invalidate; el fix real requiere *retry*, no solo invalidar.

### Gaps no-US (sistema US-céntrico por diseño)
- **10.** `CurrencyCode='USD'` hardcodeado en fees (`spapi.py:699,761`). SP-API no hace FX; el peor caso es degradación a fees de Keepa. **Fix:** mapa `marketplace→currency`.
- **15.** Detección Amazon-seller US-only (`spapi.py:117-119`). `amazon_is_seller` también es US-only en Keepa (sellerId hardcodeado `ATVPDKIKX0DER`). No consumido por scoring.

### Identidad / rescate (acotados al path de rescate, minoritario)
- **19.** `choose_candidate` no degrada el conflicto 1v1 (2 marcas, share 0.5 > 0.30) — **intencional y documentado** (la zona 30-60% se deja como legacy).
- **22.** `titles_match` deja pasar "D3 1000 IU" vs "D3 5000 IU" (comparten "d3", cae a Jaccard 0.20) — limitación **medida e intencional**.
- **20.** `upsert_product` degrada `package_quantity` 12→1 — pero la fila `products.*` es **write-only** respecto a la economía (el item usa el valor fresco).
- **21.** TTL de 6h del cache es **código muerto**; el efecto real es "re-fetch siempre" (datos siempre frescos, no hay datos viejos).

### Multipack / size (feature gated OFF por defecto; size es señal secundaria que no toca cost/profit)
- **23.** "1 Pack" + "N Count" → factor 1 y suprime el LLM (`multipack_extractor.py:133`).
- **24.** `size_match` no reconoce "grams"/"ounces"/"pounds"/"liters" deletreados → mismatch real no detectado. **Fix trivial:** añadir formas largas a las regex.
- **25.** "oz" siempre = masa; un líquido escrito "5 oz" vs "16 fl oz" pierde el mismatch.
- **32.** "12ct"/"36CT" pegado no detectado por `_has_pack_signal` (exige `\b` entre dígito y "ct").

### Limpieza de comps (en BatchFlip está **dormido**; en FlipIQ está **vivo**)
- **26.** Listings sin `ended_at` nunca se descartan por antigüedad (`comp_cleaner.py:351-359`). **⚠️ En FlipIQ el `comp_cleaner` SÍ se usa** — verificar si las ofertas Amazon sin fecha contaminan el precio/velocidad.
- **27.** `comp_cleaner` usa vocabulario eBay (`pre-owned`, `shipping_price`, `for_parts`). En BatchFlip no tiene callers en el path Amazon; en FlipIQ sí está activo sobre comps de Amazon → revisar que la semántica eBay no malclasifique condiciones Amazon.

### Moneda y column-mapper (estos no aplican a FlipIQ hoy)
- **28.** Sin guard de moneda coste-proveedor vs precio-Amazon — **decisión intencional diferida** (`pending decision/11`).
- **29/30/31.** `column_mapper`: validación asimétrica del LLM, gate de cost solo numérico, caché content-blind. Mitigados por human-in-the-loop + validación row-level + id_type resuelto por valor. (FlipIQ no tiene ingesta de catálogo.)

### `salesRankReference` como rank — 🔴 HEREDADO por FlipIQ
- **9.** `_extract_sales_rank` lee `stats.salesRankReference` (un **id de categoría**) como si fuera un valor de rank (`keepa.py:331-342`). Hoy latente (el campo no vive en `stats`, cae a `current[3]`). **⚠️ FlipIQ tiene el mismo patrón** en `amazon.py:415` y `:502`: `stats.get("salesRankReference") or stats.get("current", [...])[CSV_SALES_RANK]`. Si Keepa poblara el campo, devolvería un catId gigante → `estimate_sales_per_day` lo trataría como rank>200k → 0.15 ventas/día → velocidad colapsada. **En FlipIQ es más peligroso** porque `sales_per_day` **sí** alimenta el motor de velocidad. **Fix:** usar `current[CSV_SALES_RANK]` directo con sanity check.

---

## Bugs que FlipIQ ya tiene hoy (resumen para acción inmediata)

Independientemente del port, estos viven **ahora** en `app/services/marketplace/amazon.py` de FlipIQ:

| Bug | Línea FlipIQ | Severidad en FlipIQ | Fix |
|-----|-------------|---------------------|-----|
| `_PACK_RE` trata "N count" como multipack (filtra comps válidos) | `57-66, 69-97` | **Alta** (categorías comunes) | Excluir "N count" del regex de filtrado |
| `salesRankReference` leído como rank | `415, 502` | **Media** (alimenta velocity) | Usar `current[CSV_SALES_RANK]` + sanity cap |
| `httpx.AsyncClient` nuevo por request (sin cliente compartido ni governor) | `238` | Media (latencia, sin pacing de tokens 429) | Cliente compartido + (opcional) governor |
| BSR estimador discontinuo y category-agnostic | `36-49` | Baja (último recurso) | Documentar; opcionalmente buckets por categoría |

---

## Apéndice — Falsos positivos descartados (NO actuar)

La verificación adversarial refutó 19 hallazgos; los más relevantes (para no perder tiempo en ellos):

- **"Fees al precio Keepa vs buy_box SP-API → fees erróneos"** → falso: `referral_fee_pct` se recomputa con ambos del mismo snapshot.
- **"Guard de precio-fantasma ausente en fast scan"** → falso: `fast_scan:639-645` sí lo pasa.
- **"Mutación no idempotente del bundle en comp_cleaner"** / **"days-of-data como lookback"** / **"relevancia penaliza sin brand"** → código **muerto** (sin callers en el path Amazon de BatchFlip).
- **"BSR estimador grueso afecta el bucket"** → falso: `compute_analysis_bucket` ignora la velocidad por completo (en **BatchFlip**; en FlipIQ sí afecta velocity — ver bug heredado #9).
- **"APPROVAL_REQUIRED se mapea a can_sell=False"** → manejado por `RestrictionResult` documentado.
- **"sales_rank overwrite sin recalcular velocidad"** → falso: `sales_per_day` se pasa directo, nunca se deriva de `sales_rank` en el path híbrido.
- **"Rollback de sesión ausente en fast"** → falso: usa `db.begin_nested()` (SAVEPOINT).
- **"colmap barcode sin check-digit"** → la validación real es por valor (`detect_id_type`/`is_valid_barcode`), no por la sugerencia del LLM.

---

*Generado a partir de la auditoría multi-agente del 2026-06-26 sobre `batchflip_core`. Detalle completo de evidencia línea-a-línea disponible en el output del workflow `amazon-engine-audit`.*
