"""Seed catalogue: the shops, products, customers and orders of the Kavrelle mall.

Customers and orders mirror agentrel's TechMart store (agentrel.envs.store) so the
support assistant and the reliability lab see the same world as the website.
Prices are in cents.
"""

from __future__ import annotations

from typing import TypedDict

SIZES = ["XS", "S", "M", "L", "XL", "XXL"]
SHOE_SIZES = ["40", "41", "42", "43", "44", "45"]


class ProductSeed(TypedDict):
    slug: str
    name: str
    color: str
    price_cents: int
    variant_label: str
    variants: list[str]


class ShopSeed(TypedDict):
    slug: str
    name: str
    category: str
    floor: int
    position: int
    description: str
    products: list[ProductSeed]


def _p(
    slug: str, name: str, color: str, price: int, label: str, variants: list[str]
) -> ProductSeed:
    return {"slug": slug, "name": name, "color": color, "price_cents": price,
            "variant_label": label, "variants": variants}  # fmt: skip


SHOPS: list[ShopSeed] = [
    {"slug": "northline", "name": "Northline", "category": "Fashion", "floor": 1, "position": 1,
     "description": "Jackets, hoodies and everyday basics for men and women.",
     "products": [
         _p("tech-fleece-hoodie", "Tech Fleece Full-Zip Hoodie", "University Red", 9897, "Size", SIZES),
         _p("windrunner-jacket", "Windrunner Shell Jacket", "Anthracite", 10900, "Size", SIZES),
         _p("packable-down-jacket", "Packable Down Jacket", "Safety Orange", 14900, "Size", SIZES),
         _p("canvas-chore-coat", "Canvas Chore Coat", "Rust Brown", 12000, "Size", SIZES),
     ]},
    {"slug": "stride-lab", "name": "Stride Lab", "category": "Sneakers", "floor": 1, "position": 2,
     "description": "Runners, court and trail shoes.",
     "products": [
         _p("air-runner-2", "Air Runner 2", "White / Royal", 12900, "Size (EU)", SHOE_SIZES),
         _p("court-classic-low", "Court Classic Low", "Black / Gum", 9500, "Size (EU)", SHOE_SIZES),
         _p("trail-pro-gtx", "Trail Pro GTX", "Olive / Volt", 16000, "Size (EU)", SHOE_SIZES),
     ]},
    {"slug": "glow-room", "name": "Glow Room", "category": "Beauty", "floor": 1, "position": 3,
     "description": "Skincare, fragrance and makeup.",
     "products": [
         _p("hydra-serum", "Hydra Daily Serum", "30 ml", 3200, "Size", ["30 ml", "50 ml"]),
         _p("cedar-eau-de-parfum", "Cedar Eau de Parfum", "50 ml", 7800, "Size", ["50 ml", "100 ml"]),
     ]},
    {"slug": "volt-and-co", "name": "Volt & Co.", "category": "Electronics", "floor": 1, "position": 4,
     "description": "Phones, laptops, TVs and audio.",
     "products": [
         _p("phone-case", "Phone Case", "Black", 1500, "Model", ["Standard", "Pro"]),
         _p("fast-charger", "Fast Charger", "White", 2500, "Plug", ["Type C", "Type G"]),
         _p("wireless-headphones", "Wireless Headphones", "Matte Black", 6000, "Color", ["Black", "White"]),
         _p("mechanical-keyboard", "Mechanical Keyboard", "Grey", 4500, "Layout", ["US", "UK"]),
         _p("wireless-mouse", "Wireless Mouse", "Black", 2000, "Color", ["Black", "White"]),
         _p("mouse-pad", "Mouse Pad", "Grey", 1000, "Size", ["M", "L"]),
         _p("monitor-27", '27" Monitor', "Black", 15000, "Stand", ["Fixed", "Adjustable"]),
     ]},
    {"slug": "casa-linen", "name": "Casa Linen", "category": "Home", "floor": 1, "position": 5,
     "description": "Furniture, lamps, ceramics and plants.",
     "products": [
         _p("arc-floor-lamp", "Arc Floor Lamp", "Linen / Brass", 18900, "Finish", ["Brass", "Black", "Chrome"]),
         _p("stoneware-vase", "Stoneware Vase", "Terracotta", 4500, "Size", ["Small", "Medium", "Large"]),
         _p("fiddle-leaf-plant", "Fiddle Leaf Plant", "Cream Pot", 5900, "Pot size", ["20 cm", "26 cm"]),
     ]},
    {"slug": "kite-and-page", "name": "Kite & Page", "category": "Books and toys", "floor": 1,
     "position": 6, "description": "Books, games and toys for every age.",
     "products": [
         _p("strategy-board-game", "Strategy Board Game", "Classic", 3900, "Edition", ["Classic", "Deluxe"]),
         _p("illustrated-atlas", "Illustrated World Atlas", "Hardcover", 2900, "Format", ["Hardcover"]),
     ]},
    {"slug": "food-court", "name": "Food Court", "category": "Food", "floor": 2, "position": 1,
     "description": "Twelve kitchens under one skylight.", "products": []},
    {"slug": "sports-hub", "name": "Sports Hub", "category": "Sports", "floor": 2, "position": 2,
     "description": "Gear for running, the gym and the pitch.",
     "products": [_p("match-football", "Match Football", "White / Orange", 3500, "Size", ["4", "5"])]},
    {"slug": "kids-world", "name": "Kids World", "category": "Kids", "floor": 2, "position": 3,
     "description": "Clothes and toys for little ones.",
     "products": [_p("plush-bear", "Plush Bear", "Honey", 1900, "Size", ["Small", "Large"])]},
    {"slug": "jewel-box", "name": "Jewel Box", "category": "Jewelry", "floor": 2, "position": 4,
     "description": "Rings, chains and watches.",
     "products": [_p("silver-chain", "Sterling Silver Chain", "Silver", 8900, "Length", ["45 cm", "50 cm"])]},
    {"slug": "cinema-6", "name": "Cinema 6", "category": "Entertainment", "floor": 2, "position": 5,
     "description": "Six screens and fresh popcorn.", "products": []},
    {"slug": "pharmacy", "name": "Pharmacy", "category": "Health", "floor": 2, "position": 6,
     "description": "Everyday health and personal care.",
     "products": [_p("first-aid-kit", "Home First Aid Kit", "Red", 2400, "Size", ["Standard"])]},
]  # fmt: skip

CUSTOMERS = [
    {"name": "Ali Khan", "email": "ali@example.com", "zip_code": "54000"},
    {"name": "Sara Ahmed", "email": "sara@example.com", "zip_code": "44000"},
]

# (code, customer email, status, [(product slug, variant, qty)])
ORDERS: list[tuple[str, str, str, list[tuple[str, str, int]]]] = [
    ("A100", "ali@example.com", "shipped", [("phone-case", "Standard", 1), ("fast-charger", "Type C", 1)]),
    ("A101", "ali@example.com", "pending", [("wireless-headphones", "Black", 1)]),
    ("A102", "ali@example.com", "delivered", [("mechanical-keyboard", "US", 1)]),
    ("A103", "sara@example.com", "delivered", [("wireless-mouse", "Black", 1), ("mouse-pad", "M", 1)]),
    ("A104", "sara@example.com", "pending", [("monitor-27", "Fixed", 1)]),
]  # fmt: skip

DEFAULT_STOCK = 25
