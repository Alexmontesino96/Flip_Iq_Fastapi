"""Cliente Amazon via Keepa API.

Keepa provee datos reales de Amazon: precios Buy Box, historial,
sales rank, y ofertas de sellers. Se mapean al formato CompsResult
para integrarse con el pipeline de análisis existente.
"""

import logging
import re
from datetime import datetime, timedelta, timezone

import httpx

from app.config import settings
from app.services.marketplace.base import CompsResult, MarketplaceClient, MarketplaceListing
from app.services.marketplace.identity import MAJORITY_MIN_SHARE, choose_candidate
from app.services.marketplace.multipack import is_multipack_title

logger = logging.getLogger(__name__)

KEEPA_BASE = "https://api.keepa.com"
KEEPA_EPOCH = datetime(2011, 1, 1, tzinfo=timezone.utc)
KEEPA_TIMEOUT = 20  # segundos

# Indices del array csv de Keepa
CSV_AMAZON = 0
CSV_NEW = 1
CSV_USED = 2
CSV_SALES_RANK = 3
CSV_BUY_BOX = 18


def keepa_time_to_datetime(keepa_minutes: int) -> datetime:
    """Convierte Keepa time (minutos desde 2011-01-01 UTC) a datetime."""
    return KEEPA_EPOCH + timedelta(minutes=keepa_minutes)


def estimate_sales_per_day(sales_rank: int | None) -> float:
    """Estima ventas diarias a partir del Best Sellers Rank (BSR).

    Heurística simplificada basada en rangos típicos de Amazon US.
    """
    if sales_rank is None or sales_rank <= 0:
        return 0.0
    if sales_rank < 5_000:
        return 10.0
    if sales_rank < 50_000:
        return 3.5
    if sales_rank < 200_000:
        return 0.75
    return 0.15


MAX_REASONABLE_PRICE = 5000.0  # Cap de sanidad — filtrar precios > $5,000


def _is_multipack(title: str) -> bool:
    """True si el título indica un multi-pack INEQUÍVOCO ("Pack of N", "N-Pack"…).

    Delega en multipack.is_multipack_title (fuente única de verdad). "N Count"/
    "N ct" ya NO cuenta como multipack: es ambiguo (puede ser el descriptor de
    la unidad base, p.ej. "Vitamin C, 100 Count" = 1 frasco) y tratarlo como pack
    descartaba de los comps unidades sueltas válidas de categorías muy comunes
    (vitaminas, baterías, K-cups, cosméticos).
    """
    return is_multipack_title(title)


def _filter_multipacks(products: list[dict]) -> list[dict]:
    """Filtra multi-packs, conservando solo unidades individuales.

    Si todos son multi-pack, no filtra (datos incompletos > sin datos).
    """
    singles = [p for p in products if not _is_multipack(p.get("title") or "")]
    if singles:
        removed = len(products) - len(singles)
        if removed > 0:
            logger.info(
                "Keepa: filtrado %d/%d productos multi-pack",
                removed, len(products),
            )
        return singles
    # Todos son multi-pack — retornar sin filtrar
    return products


def _extract_brand_model(product: dict) -> tuple[str | None, str | None]:
    """Extrae brand y model del producto Keepa."""
    brand = product.get("brand") or None
    model = product.get("model") or product.get("partNumber") or None
    return brand, model


def _extract_image_url(product: dict) -> str | None:
    """URL de la primera imagen del producto Keepa (imagesCSV → hash)."""
    images_csv = product.get("imagesCSV")
    if images_csv:
        first_hash = images_csv.split(",")[0].strip()
        if first_hash:
            return f"https://images-na.ssl-images-amazon.com/images/I/{first_hash}"
    return None


def _extract_buy_box_price(product: dict) -> float | None:
    """Precio actual del Buy Box (stats.current[18], centavos); fallback a New."""
    stats = product.get("stats")
    if not stats:
        return None
    current = stats.get("current")
    if not current or len(current) <= CSV_BUY_BOX:
        return None
    bb = current[CSV_BUY_BOX]
    if bb is not None and bb > 0:
        return round(bb / 100.0, 2)
    new = current[CSV_NEW] if len(current) > CSV_NEW else None
    if new is not None and new > 0:
        return round(new / 100.0, 2)
    return None


def _build_candidates(products: list[dict]) -> list[dict]:
    """Proyecta los products de Keepa a candidatos para el badge Multi-ASIN y el
    consenso de marca (identity.choose_candidate). Solo los que traen ASIN."""
    out: list[dict] = []
    for p in products:
        asin = p.get("asin")
        if not asin:
            continue
        brand, _ = _extract_brand_model(p)
        title = p.get("title")
        out.append({
            "asin": asin,
            "title": title,
            "brand": brand,
            "package_quantity": p.get("packageQuantity") or p.get("numberOfItems"),
            "is_multipack": is_multipack_title(title or ""),
            "image_url": _extract_image_url(p),
        })
    return out


def _pick_main_product(products: list[dict]) -> dict | None:
    """Producto 'principal' = el que el usuario evalúa (define el guard de pack).

    Heurística: el de mejor (menor) sales rank real; fallback al primero. Usa
    current[CSV_SALES_RANK], NO salesRankReference (que es un id de categoría,
    no un rank — ver docs/AMAZON_ENGINE_FINDINGS.md #9).
    """
    if not products:
        return None

    def _rank(p: dict) -> int | None:
        stats = p.get("stats") or {}
        current = stats.get("current") or []
        if len(current) > CSV_SALES_RANK:
            r = current[CSV_SALES_RANK]
            if r and r > 0:
                return r
        return None

    with_rank = [(p, r) for p in products if (r := _rank(p)) is not None]
    if with_rank:
        return min(with_rank, key=lambda pr: pr[1])[0]
    return products[0]


def _extract_package_quantity(product: dict) -> int | None:
    """Unidades del empaque según señales estructuradas de Keepa.

    packageQuantity / numberOfItems. Devuelve None si no hay señal (NUNCA 0):
    'desconocido' debe distinguirse de '1' para el guard de multipack.
    """
    for key in ("packageQuantity", "numberOfItems"):
        val = product.get(key)
        if val is not None:
            try:
                n = int(val)
            except (TypeError, ValueError):
                continue
            if n >= 1:
                return n
    return None


def _map_keepa_offers(product: dict) -> list[MarketplaceListing]:
    """Convierte ofertas de sellers de Keepa a MarketplaceListing.

    offerCSV usa TRIPLES: [keepa_time, price_cents, shipping_cents, ...]
    """
    listings: list[MarketplaceListing] = []
    offers = product.get("offers") or []
    title = product.get("title", "")
    asin = product.get("asin", "")
    brand, model = _extract_brand_model(product)

    for offer in offers:
        csv = offer.get("offerCSV", [])
        if not csv or len(csv) < 3:
            continue

        # offerCSV es TRIPLES: [keepa_time, price, shipping, keepa_time, price, shipping, ...]
        # Tomamos el último triple con precio válido (iteramos hacia atrás)
        price_val = None
        ship_val = 0.0
        for i in range(len(csv) - 3, -1, -3):
            p = csv[i + 1]  # precio en centavos
            if p is not None and p > 0:
                price_val = p / 100.0
                s = csv[i + 2]  # shipping en centavos
                ship_val = s / 100.0 if s and s > 0 else 0.0
                break

        if price_val is None or price_val <= 0 or price_val > MAX_REASONABLE_PRICE:
            continue

        condition_raw = offer.get("condition", 1)
        # Keepa: 1=New, 2=Used-Like New, 3=Used-Very Good, 4=Used-Good, 5=Used-Acceptable
        condition = "new" if condition_raw == 1 else "used"

        seller = offer.get("sellerName") or offer.get("sellerId", "")
        is_fba = offer.get("isFBA", False)

        # Para FBA el shipping ya está incluido
        shipping = 0.0 if is_fba else ship_val

        listings.append(MarketplaceListing(
            title=title,
            price=price_val,
            condition=condition,
            url=f"https://www.amazon.com/dp/{asin}",
            sold=True,
            marketplace="amazon",
            item_id=asin,
            shipping_price=shipping,
            total_price=round(price_val + shipping, 2),
            ended_at=datetime.now(timezone.utc),
            seller_username=seller,
            brand=brand,
            model=model,
        ))

    return listings


def _map_buybox_history(product: dict, days: int = 30) -> list[MarketplaceListing]:
    """Convierte historial Buy Box de Keepa a MarketplaceListing.

    csv[18] (BUY_BOX_SHIPPING) usa TRIPLES: [keepa_time, price_cents, shipping_cents, ...]
    """
    listings: list[MarketplaceListing] = []
    csv_data = product.get("csv") or []
    title = product.get("title", "")
    asin = product.get("asin", "")
    brand, model = _extract_brand_model(product)

    if len(csv_data) <= CSV_BUY_BOX:
        return listings

    buybox_history = csv_data[CSV_BUY_BOX]
    if not buybox_history:
        return listings

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    # buybox_history (csv[18]) es TRIPLES: [keepa_time, price, shipping, ...]
    for i in range(0, len(buybox_history) - 2, 3):
        keepa_time = buybox_history[i]
        price_cents = buybox_history[i + 1]
        shipping_cents = buybox_history[i + 2]

        if keepa_time is None or price_cents is None or price_cents <= 0:
            continue

        dt = keepa_time_to_datetime(keepa_time)
        if dt < cutoff:
            continue

        price = price_cents / 100.0
        if price > MAX_REASONABLE_PRICE:
            continue

        shipping = shipping_cents / 100.0 if shipping_cents and shipping_cents > 0 else 0.0

        listings.append(MarketplaceListing(
            title=title,
            price=price,
            condition="new",
            url=f"https://www.amazon.com/dp/{asin}",
            sold=True,
            marketplace="amazon",
            item_id=asin,
            shipping_price=shipping,
            total_price=round(price + shipping, 2),
            ended_at=dt,
            seller_username="Amazon Buy Box",
            brand=brand,
            model=model,
        ))

    return listings


class AmazonClient(MarketplaceClient):
    """Cliente Amazon usando Keepa REST API."""

    def __init__(self) -> None:
        self._api_key = settings.keepa_api_key

    async def _keepa_get(self, endpoint: str, params: dict) -> dict | None:
        """GET genérico a Keepa API."""
        if not self._api_key:
            return None

        params["key"] = self._api_key
        try:
            async with httpx.AsyncClient(timeout=KEEPA_TIMEOUT) as client:
                resp = await client.get(
                    f"{KEEPA_BASE}/{endpoint}",
                    params=params,
                    headers={"Accept": "application/json"},
                )
                resp.raise_for_status()
                return resp.json()
        except httpx.HTTPStatusError as e:
            logger.warning("Keepa API error %s: %s", e.response.status_code, e)
            return None
        except Exception as e:
            logger.warning("Keepa request failed: %s", e)
            return None

    async def _keepa_search(self, keyword: str, limit: int = 10) -> list[str]:
        """Busca ASINs por keyword.

        El endpoint /search devuelve products[] con datos parciales (sin offers).
        Extraemos los ASINs para luego pedir datos completos via /product.
        """
        data = await self._keepa_get("search", {
            "domain": 1,
            "type": "product",
            "term": keyword,
        })
        if not data:
            return []

        # La respuesta tiene products[] con asin, no asinList
        products = data.get("products", [])
        asins = [p["asin"] for p in products if p.get("asin")]
        return asins[:limit]

    async def _keepa_product(
        self,
        asins: list[str],
        stats: int = 30,
        offers: int = 20,
    ) -> list[dict]:
        """Obtiene datos de productos por ASIN(s)."""
        if not asins:
            return []

        data = await self._keepa_get("product", {
            "domain": 1,
            "asin": ",".join(asins),
            "stats": stats,
            "buybox": 1,
            "history": 1,
            "days": 90,
            "offers": offers,
        })
        if not data:
            return []

        return data.get("products", [])

    async def _keepa_product_by_code(self, code: str) -> list[dict]:
        """Obtiene datos de producto por UPC/EAN."""
        data = await self._keepa_get("product", {
            "domain": 1,
            "code": code,
            "stats": 30,
            "buybox": 1,
            "history": 1,
            "days": 90,
            "offers": 20,
        })
        if not data:
            return []

        return data.get("products", [])

    async def search_by_barcode(self, barcode: str) -> list[MarketplaceListing]:
        products = await self._keepa_product_by_code(barcode)
        listings: list[MarketplaceListing] = []
        for product in products:
            listings.extend(_map_keepa_offers(product))
        return listings

    async def search_by_keyword(self, keyword: str, limit: int = 20) -> list[MarketplaceListing]:
        asins = await self._keepa_search(keyword, limit=limit)
        if not asins:
            return []

        products = await self._keepa_product(asins)
        listings: list[MarketplaceListing] = []
        for product in products:
            listings.extend(_map_keepa_offers(product))
        return listings[:limit]

    async def get_sold_comps(
        self,
        barcode: str | None = None,
        keyword: str | None = None,
        days: int = 30,
        limit: int = 50,
        product_type: str | None = None,
    ) -> CompsResult:
        """Obtiene comps de Amazon via Keepa.

        Combina ofertas actuales de sellers + historial Buy Box
        para crear un dataset comparable al de eBay sold.
        """
        if not self._api_key:
            logger.info("No KEEPA_API_KEY, retornando CompsResult vacío")
            return CompsResult(marketplace="amazon")

        products: list[dict] = []
        candidate_asins: list | None = None
        identity_needs_review = False
        identity_reason: str | None = None

        # Intentar por barcode primero
        if barcode:
            products = await self._keepa_product_by_code(barcode)
            # Multi-ASIN: un UPC/EAN puede resolver a varios ASINs (variantes o
            # contaminación de catálogo). Consenso de marca (anti-contaminación)
            # SOLO en el path por code; en keyword las marcas distintas son
            # legítimas (productos distintos que matchean el término).
            if products:
                cands = _build_candidates(products)
                if len(cands) > 1:
                    choice = choose_candidate(barcode, cands)
                    candidate_asins = cands
                    identity_needs_review = choice.needs_review
                    identity_reason = choice.reason
                    # Descartar los contaminantes de otra marca antes de armar los
                    # comps SIEMPRE que haya una marca dominante CLARA (≥60%) que
                    # coincide con la elegida (cubre la corrección Y default_ok con
                    # mayoría — la mediana no debe mezclar el producto equivocado).
                    # Conservador: si el filtro dejara 0, no filtra; si no hay mayoría
                    # clara (caso ambiguo), no se filtra (lo decide el usuario).
                    if (
                        choice.chosen_brand
                        and choice.chosen_brand == choice.dominant_brand
                        and choice.dominant_share >= MAJORITY_MIN_SHARE
                    ):
                        kept = [
                            p for p in products
                            if (p.get("brand") or "").strip().lower() == choice.chosen_brand
                        ]
                        products = kept or products

        # Si no hay resultados por barcode, buscar por keyword
        if not products and keyword:
            asins = await self._keepa_search(keyword, limit=10)
            if asins:
                products = await self._keepa_product(asins)

        if not products:
            logger.info("Keepa: sin resultados para barcode=%s keyword=%s", barcode, keyword)
            return CompsResult(marketplace="amazon")

        # Filtrar multi-packs: mismo UPC puede mapear a packs de 2, 3, 6
        products = _filter_multipacks(products)

        # Filtro por product_type: excluir productos cuyo título no contiene el tipo
        if product_type:
            pt_lower = product_type.lower().strip()
            # Generar variantes singular/plural
            pt_variants = {pt_lower}
            if pt_lower.endswith("s"):
                pt_variants.add(pt_lower[:-1])
            else:
                pt_variants.add(pt_lower + "s")
            if pt_lower.endswith("ies"):
                pt_variants.add(pt_lower[:-3] + "y")
            elif pt_lower.endswith("y") and not pt_lower.endswith("ey"):
                pt_variants.add(pt_lower[:-1] + "ies")

            filtered_products = [
                p for p in products
                if any(
                    re.search(r"\b" + re.escape(v) + r"\b", (p.get("title") or "").lower())
                    for v in pt_variants
                )
            ]
            if filtered_products:
                removed = len(products) - len(filtered_products)
                if removed > 0:
                    logger.info(
                        "Keepa: filtrado %d/%d productos por product_type='%s'",
                        removed, len(products), product_type,
                    )
                products = filtered_products
            # Si filtrar deja 0 productos, no filtrar (datos incompletos > sin datos)

        # Mapear productos a listings + extraer fees reales de Keepa
        all_listings: list[MarketplaceListing] = []
        best_rank: int | None = None
        image_url: str | None = None
        # Collect per-product FBA fees from Keepa
        referral_pcts: list[float] = []
        fulfillment_fees: list[float] = []

        for product in products:
            # Ofertas actuales de sellers
            all_listings.extend(_map_keepa_offers(product))
            # Historial Buy Box
            all_listings.extend(_map_buybox_history(product, days=days))

            # Mejor sales rank para estimar velocidad
            rank = None
            stats = product.get("stats")
            if stats:
                rank = stats.get("salesRankReference") or stats.get("current", [None] * 4)[CSV_SALES_RANK]
            if rank and (best_rank is None or rank < best_rank):
                best_rank = rank

            # Extract image URL from Keepa product data
            if not image_url:
                images_csv = product.get("imagesCSV")
                if images_csv:
                    first_hash = images_csv.split(",")[0].strip()
                    if first_hash:
                        image_url = f"https://images-na.ssl-images-amazon.com/images/I/{first_hash}"

            # Extract real Amazon FBA fees from Keepa product data
            # referralFeePercentage: integer 0-100 (e.g. 15 = 15%)
            ref_pct = product.get("referralFeePercentage")
            if ref_pct is not None and ref_pct > 0:
                referral_pcts.append(ref_pct / 100.0)

            # fbaFees.pickAndPackFee: integer in cents (e.g. 322 = $3.22)
            fba_fees = product.get("fbaFees")
            if fba_fees and isinstance(fba_fees, dict):
                pick_pack = fba_fees.get("pickAndPackFee")
                if pick_pack is not None and pick_pack > 0:
                    fulfillment_fees.append(pick_pack / 100.0)

        if not all_listings:
            logger.info("Keepa: productos encontrados pero sin listings mapeables")
            return CompsResult(marketplace="amazon")

        # Limitar a 'limit' listings
        all_listings = all_listings[:limit]

        result = CompsResult.from_listings(all_listings, marketplace="amazon", days=days)
        result.image_url = image_url

        # Apply real Keepa fees if available
        if referral_pcts:
            result.fba_referral_pct = round(
                sum(referral_pcts) / len(referral_pcts), 4,
            )
        if fulfillment_fees:
            result.fba_fulfillment_fee = round(
                sum(fulfillment_fees) / len(fulfillment_fees), 2,
            )
        if referral_pcts or fulfillment_fees:
            logger.info(
                "Keepa fees: referral=%.1f%%, fulfillment=$%.2f (from %d products)",
                (result.fba_referral_pct or 0) * 100,
                result.fba_fulfillment_fee or 0,
                len(products),
            )

        # Sobreescribir sales_per_day con estimación de BSR (más precisa que contar listings)
        if best_rank:
            result.sales_per_day = estimate_sales_per_day(best_rank)

        # Señales del producto evaluado para el guard de multipack (PR-M2).
        main = _pick_main_product(products)
        if main:
            result.evaluated_title = main.get("title")
            result.evaluated_package_quantity = _extract_package_quantity(main)

        # Multi-ASIN: candidatos del UPC + flag de revisión de identidad.
        result.candidate_asins = candidate_asins
        result.identity_needs_review = identity_needs_review
        result.identity_reason = identity_reason

        logger.info(
            "Keepa: %d listings para '%s' (rank=%s, spd=%.1f)",
            len(all_listings),
            keyword or barcode,
            best_rank,
            result.sales_per_day,
        )

        return result

    def _build_comps_from_products(
        self,
        products: list[dict],
        days: int,
        limit: int,
        label: str,
    ) -> CompsResult:
        """Mapea productos Keepa a CompsResult (lógica compartida)."""
        all_listings: list[MarketplaceListing] = []
        best_rank: int | None = None
        image_url: str | None = None
        referral_pcts: list[float] = []
        fulfillment_fees: list[float] = []

        for product in products:
            all_listings.extend(_map_keepa_offers(product))
            all_listings.extend(_map_buybox_history(product, days=days))

            rank = None
            stats = product.get("stats")
            if stats:
                rank = stats.get("salesRankReference") or stats.get("current", [None] * 4)[CSV_SALES_RANK]
            if rank and (best_rank is None or rank < best_rank):
                best_rank = rank

            if not image_url:
                images_csv = product.get("imagesCSV")
                if images_csv:
                    first_hash = images_csv.split(",")[0].strip()
                    if first_hash:
                        image_url = f"https://images-na.ssl-images-amazon.com/images/I/{first_hash}"

            ref_pct = product.get("referralFeePercentage")
            if ref_pct is not None and ref_pct > 0:
                referral_pcts.append(ref_pct / 100.0)

            fba_fees = product.get("fbaFees")
            if fba_fees and isinstance(fba_fees, dict):
                pick_pack = fba_fees.get("pickAndPackFee")
                if pick_pack is not None and pick_pack > 0:
                    fulfillment_fees.append(pick_pack / 100.0)

        if not all_listings:
            logger.info("Keepa: productos encontrados pero sin listings mapeables")
            return CompsResult(marketplace="amazon")

        all_listings = all_listings[:limit]

        result = CompsResult.from_listings(all_listings, marketplace="amazon", days=days)
        result.image_url = image_url

        if referral_pcts:
            result.fba_referral_pct = round(
                sum(referral_pcts) / len(referral_pcts), 4,
            )
        if fulfillment_fees:
            result.fba_fulfillment_fee = round(
                sum(fulfillment_fees) / len(fulfillment_fees), 2,
            )
        if referral_pcts or fulfillment_fees:
            logger.info(
                "Keepa fees: referral=%.1f%%, fulfillment=$%.2f (from %d products)",
                (result.fba_referral_pct or 0) * 100,
                result.fba_fulfillment_fee or 0,
                len(products),
            )

        if best_rank:
            result.sales_per_day = estimate_sales_per_day(best_rank)

        # Señales del producto evaluado para el guard de multipack (PR-M2).
        main = _pick_main_product(products)
        if main:
            result.evaluated_title = main.get("title")
            result.evaluated_package_quantity = _extract_package_quantity(main)

        logger.info(
            "Keepa: %d listings para '%s' (rank=%s, spd=%.1f)",
            len(all_listings),
            label,
            best_rank,
            result.sales_per_day,
        )

        return result

    async def get_sold_comps_by_asin(
        self,
        asin: str,
        days: int = 30,
        limit: int = 50,
    ) -> CompsResult:
        """Obtiene comps de Amazon por ASIN directo (sin búsqueda)."""
        if not self._api_key:
            logger.info("No KEEPA_API_KEY, retornando CompsResult vacío")
            return CompsResult(marketplace="amazon")

        products = await self._keepa_product([asin])
        if not products:
            logger.info("Keepa: sin resultados para ASIN=%s", asin)
            return CompsResult(marketplace="amazon")

        return self._build_comps_from_products(products, days, limit, label=asin)

    async def get_variant_prices(self, asins: list[str]) -> list[dict]:
        """Precio de mercado de cada ASIN candidato (drawer Multi-ASIN).

        Un solo request Keepa para todos (cap 20). Devuelve por variante
        {asin, title, brand, image_url, median_price, buy_box_price}; el frontend
        calcula el margen contra el cost_price del producto que el usuario evalúa.
        """
        if not self._api_key or not asins:
            return []

        products = await self._keepa_product(asins[:20])
        out: list[dict] = []
        for p in products:
            listings = _map_keepa_offers(p) + _map_buybox_history(p, days=90)
            prices = sorted(
                l.total_price or l.price for l in listings if (l.total_price or l.price)
            )
            median = None
            if prices:
                n = len(prices)
                median = round(
                    prices[n // 2] if n % 2 == 1
                    else (prices[n // 2 - 1] + prices[n // 2]) / 2, 2,
                )
            brand, _ = _extract_brand_model(p)
            out.append({
                "asin": p.get("asin", ""),
                "title": p.get("title"),
                "brand": brand,
                "image_url": _extract_image_url(p),
                "median_price": median,
                "buy_box_price": _extract_buy_box_price(p),
            })
        return out
