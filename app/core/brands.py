"""Detección de marcas conocidas en texto."""

import re

KNOWN_BRANDS = [
    "Nike", "Adidas", "Apple", "Samsung", "Sony", "Nintendo", "Microsoft", "Google",
    "LG", "Bose", "JBL", "Canon", "Nikon", "Dyson", "Lego", "Funko",
    "Jordan", "New Balance", "Puma", "Asics", "Reebok", "Converse", "Vans",
    "Under Armour", "North Face", "Patagonia", "Columbia",
    "Dell", "HP", "Lenovo", "Asus", "Acer", "Razer", "Logitech", "Corsair",
    "KitchenAid", "Instant Pot", "Vitamix", "Cuisinart", "Ninja",
    "Beats", "AirPods", "Oakley", "Ray-Ban", "Crocs", "Birkenstock",
    "Pokemon", "Hoka", "On Running", "Brooks", "Saucony", "Salomon",
]

_BRAND_PATTERN = re.compile(
    r"\b(" + "|".join(re.escape(b) for b in KNOWN_BRANDS) + r")\b",
    re.IGNORECASE,
)


def detect_brand(text: str) -> str | None:
    """Detecta marca conocida en texto (keyword o título)."""
    if not text:
        return None
    match = _BRAND_PATTERN.search(text)
    if match:
        matched = match.group(1).lower()
        for brand in KNOWN_BRANDS:
            if brand.lower() == matched:
                return brand
    return None
