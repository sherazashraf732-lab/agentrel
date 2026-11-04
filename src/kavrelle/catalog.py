"""Seed catalogue: the shops, products, customers and orders of the Kavrelle mall.

Customers and orders mirror agentrel's TechMart store (agentrel.envs.store) so the
support assistant and the reliability lab see the same world as the website.
Prices are in cents. Product photos are free-licence images from Pexels
(https://www.pexels.com/license/); each product records its source page.
"""

from __future__ import annotations

from typing import TypedDict

SIZES = ["XS", "S", "M", "L", "XL", "XXL"]
SHOE_SIZES = ["40", "41", "42", "43", "44", "45"]
WOMEN_SHOE_SIZES = ["36", "37", "38", "39", "40", "41"]
ONE_SIZE = ["One size"]
IMAGE_ROOT = "/static/img/products"


class ProductSeed(TypedDict):
    slug: str
    name: str
    color: str
    price_cents: int
    variant_label: str
    variants: list[str]
    description: str
    image_url: str | None
    image_credit: str | None


class ShopSeed(TypedDict):
    slug: str
    name: str
    category: str
    floor: int
    position: int
    description: str
    products: list[ProductSeed]


def _p(
    slug: str,
    name: str,
    color: str,
    price: int,
    label: str,
    variants: list[str],
    pexels_id: int,
    description: str = "",
) -> ProductSeed:
    return {
        "slug": slug, "name": name, "color": color, "price_cents": price,
        "variant_label": label, "variants": variants, "description": description,
        "image_url": f"{IMAGE_ROOT}/{slug}.jpg",
        "image_credit": f"Photo: Pexels, https://www.pexels.com/photo/{pexels_id}/",
    }  # fmt: skip


SHOPS: list[ShopSeed] = [
    {"slug": "northline", "name": "Northline", "category": "Menswear", "floor": 1, "position": 1,
     "description": "Statement shirts, heavyweight hoodies and outerwear with attitude.",
     "products": [
         _p("floral-print-shirt", "Botanica Floral Print Shirt", "Rose / Black", 5900, "Size", SIZES, 13996059,
            "Relaxed camp collar, all-over botanical print, breathable viscose."),
         _p("paisley-shirt", "Paisley Silk-Touch Shirt", "Black / White", 6400, "Size", SIZES, 2897531,
            "Fluid silk-touch fabric with a bold monochrome paisley."),
         _p("hawaiian-shirt", "Palm Shadow Resort Shirt", "Monochrome", 5400, "Size", SIZES, 13096946,
            "Short-sleeve resort shirt with a hand-drawn palm print."),
         _p("artistic-hoodie", "Eyes Closed Graphic Hoodie", "Black", 8900, "Size", SIZES, 31700390,
            "Oversized fit, screen-printed artwork on the back, brushed fleece inside."),
         _p("maroon-hoodie", "Heavyweight Pullover Hoodie", "Maroon", 6900, "Size", SIZES, 18700207,
            "450 gsm cotton fleece with a double-layer hood."),
         _p("white-hoodie", "Cloud Fleece Hoodie", "Off White", 6900, "Size", SIZES, 8217419,
            "Soft cloud fleece in a clean off-white."),
         _p("leather-jacket", "Moto Leather Jacket", "Black", 24900, "Size", SIZES, 2766298,
            "Full-grain leather biker jacket with asymmetric zip."),
         _p("denim-jacket", "Raw Denim Trucker Jacket", "Indigo", 11900, "Size", SIZES, 16428589,
            "Rigid indigo denim that fades with you."),
     ]},
    {"slug": "maison-elle", "name": "Maison Elle", "category": "Womenswear", "floor": 1, "position": 2,
     "description": "Dresses, bags, heels and silk for every occasion.",
     "products": [
         _p("black-evening-dress", "Noir Draped Evening Dress", "Black", 18900, "Size", SIZES, 30706568,
            "Floor-length chiffon with sheer draped sleeves."),
         _p("beige-midi-dress", "Linen Puff-Sleeve Midi Dress", "Sand", 12900, "Size", SIZES, 13816064,
            "Washed linen, V-neck and gathered puff sleeves."),
         _p("white-summer-dress", "Off-Shoulder Summer Dress", "Ivory", 11500, "Size", SIZES, 13381636,
            "Structured off-shoulder bodice with a flowing skirt."),
         _p("purple-satin-dress", "Lilac Ruched Satin Dress", "Lilac", 14500, "Size", SIZES, 19746374,
            "Liquid satin with ruched long sleeves."),
         _p("brown-leather-handbag", "Heritage Leather Tote", "Cognac", 21900, "Size", ONE_SIZE, 2433862,
            "Vegetable-tanned leather tote that ages beautifully."),
         _p("crossbody-bag-duo", "Mini Chain Crossbody", "Mint / Blush", 7900, "Color", ["Mint", "Blush"],
            1038000, "Pebbled leather mini bag on a fine chain."),
         _p("orange-satchel", "Buckle Leather Satchel", "Tan", 18500, "Size", ONE_SIZE, 1152077,
            "Twin-buckle satchel with a padded laptop sleeve."),
         _p("pink-heeled-sandals", "Fuchsia Block-Heel Mules", "Fuchsia", 9900, "Size (EU)",
            WOMEN_SHOE_SIZES, 27113457, "Soft leather mules on a 9 cm block heel."),
         _p("leather-heels", "Croc-Effect Platform Mules", "Navy", 11900, "Size (EU)", WOMEN_SHOE_SIZES,
            26965814, "Croc-embossed leather with a wooden platform."),
         _p("silk-scarf", "Printed Silk Scarf", "Coral Swirl", 4900, "Size", ONE_SIZE, 2120584,
            "Pure silk twill, hand-rolled edges."),
     ]},
    {"slug": "stride-lab", "name": "Stride Lab", "category": "Sneakers", "floor": 1, "position": 3,
     "description": "Runners and low-tops built for every day.",
     "products": [
         _p("street-sneaker", "Velocity Knit Runner", "White / Red", 13900, "Size (EU)", SHOE_SIZES, 2529147,
            "Engineered knit upper on a responsive foam sole."),
         _p("clean-low-top", "Clean Leather Low-Top", "Bone White", 10900, "Size (EU)", SHOE_SIZES, 7193625,
            "Minimal leather sneaker with a stitched cupsole."),
     ]},
    {"slug": "volt-and-co", "name": "Volt & Co.", "category": "Electronics", "floor": 1, "position": 4,
     "description": "Audio, desk setups, wearables and accessories.",
     "products": [
         _p("phone-case", "Phone Case", "Prism Prints", 1500, "Model", ["Standard", "Pro"], 374140,
            "Slim printed case with raised camera edge."),
         _p("fast-charger", "Fast Charger", "White", 2500, "Plug", ["Type C", "Type G"], 914912,
            "20 W fast charger with braided cable."),
         _p("wireless-headphones", "Wireless Headphones", "Matte Black", 6000, "Color", ["Black", "White"],
            815494, "Over-ear, 40 hour battery, active noise cancelling."),
         _p("studio-headphones-white", "Studio Wireless Headphones", "White", 8900, "Color", ["White"],
            3394666, "Hi-res drivers with memory-foam cushions."),
         _p("mechanical-keyboard", "Mechanical Keyboard", "Pastel", 4500, "Layout", ["US", "UK"], 12786602,
            "75% hot-swap board with creamy linear switches."),
         _p("wireless-mouse", "Wireless Mouse", "Black", 2000, "Color", ["Black", "White"], 19304049,
            "Silent clicks, 3 device pairing."),
         _p("mouse-pad", "Mouse Pad", "Graphite", 1000, "Size", ["M", "L"], 10133432,
            "Stitched edges, smooth micro-weave."),
         _p("monitor-27", '27" Monitor', "Black", 15000, "Stand", ["Fixed", "Adjustable"], 20648391,
            "27 inch QHD IPS panel at 165 Hz."),
         _p("smartwatch", "Pulse Smartwatch", "Sky Blue", 19900, "Band size", ["S", "M", "L"], 267394,
            "Heart rate, GPS and 7 day battery."),
     ]},
    {"slug": "glow-room", "name": "Glow Room", "category": "Beauty", "floor": 1, "position": 5,
     "description": "Fragrance, skincare and makeup.",
     "products": [
         _p("amber-eau-de-parfum", "Oceanic Eau de Parfum", "100 ml", 8900, "Size", ["50 ml", "100 ml"],
            11705153, "Sea salt, bergamot and ambergris."),
         _p("hydra-serum", "Botanical Oil Trio", "3 x 15 ml", 4500, "Size", ["Set"], 15061732,
            "Rosehip, jojoba and squalane face oils."),
         _p("glow-serum", "Vitamin Glow Serum", "30 ml", 3200, "Size", ["30 ml", "50 ml"], 31251033,
            "15% vitamin C with ferulic acid."),
         _p("makeup-essentials-set", "Amber Ritual Set", "Gift box", 6900, "Size", ["Set"], 7797009,
            "Cleanser, toner and night oil in amber glass."),
     ]},
    {"slug": "casa-linen", "name": "Casa Linen", "category": "Home", "floor": 1, "position": 6,
     "description": "Lighting, ceramics and quiet objects for the home.",
     "products": [
         _p("tripod-floor-lamp", "Tripod Floor Lamp", "Oak / Taupe", 15900, "Finish", ["Oak", "Black"],
            2876787, "Solid oak legs with a linen drum shade."),
         _p("stoneware-vase", "Stoneware Floor Vase", "Chalk", 6500, "Size", ["Medium", "Large"], 3767442,
            "Hand-thrown stoneware with a matte glaze."),
         _p("ceramic-vase-set", "Ribbed Ceramic Vase Set", "White", 5500, "Size", ["Set of 3"], 8217492,
            "Three ribbed vases in graduated heights."),
         _p("potted-plant", "Studio Desk Lamp", "Sage", 4900, "Finish", ["Sage", "Black"], 5094543,
            "Adjustable desk lamp with a weighted base."),
     ]},
    {"slug": "kite-and-page", "name": "Kite & Page", "category": "Books and games", "floor": 2,
     "position": 1, "description": "Books, chess and games for every age.",
     "products": [
         _p("wooden-chess-set", "Walnut Chess Set", "Walnut", 7900, "Edition", ["Classic", "Tournament"],
            6599591, "Weighted pieces on an inlaid walnut board."),
         _p("book-bundle", "Classics Book Bundle", "10 books", 4900, "Format", ["Paperback"], 8012235,
            "Ten timeless novels, hand-picked by our booksellers."),
     ]},
    {"slug": "sports-hub", "name": "Sports Hub", "category": "Sports", "floor": 2, "position": 2,
     "description": "Gear for the pitch, the court and the gym.",
     "products": [
         _p("match-football", "Match Football", "White / Red", 3500, "Size", ["4", "5"], 6078297,
            "Thermo-bonded panels, FIFA-size match ball."),
     ]},
    {"slug": "kids-world", "name": "Kids World", "category": "Kids", "floor": 2, "position": 3,
     "description": "Toys and soft friends for little ones.",
     "products": [
         _p("plush-bear", "Velvet Red Bear", "Red", 2400, "Size", ["Small", "Large"], 11510543,
            "Super-soft velvet bear in a tiny bowler hat."),
         _p("purple-teddy", "Crochet Lilac Bear", "Lilac", 2900, "Size", ONE_SIZE, 7738663,
            "Hand-crocheted cotton bear in a floral dress."),
     ]},
    {"slug": "jewel-box", "name": "Jewel Box", "category": "Jewelry", "floor": 2, "position": 4,
     "description": "Fine silver and crystal pieces.",
     "products": [
         _p("silver-pendant", "Halo Crystal Pendant", "Sterling Silver", 12900, "Length", ["45 cm", "50 cm"],
            5370650, "Emerald-cut crystal framed by a pave halo."),
         _p("silver-necklace", "Sterling Snake Chain", "Sterling Silver", 8900, "Length", ["40 cm", "45 cm"],
            5475580, "Polished snake chain with a fang charm."),
     ]},
    {"slug": "food-court", "name": "Food Court", "category": "Food", "floor": 2, "position": 5,
     "description": "Twelve kitchens under one skylight.", "products": []},
    {"slug": "pharmacy", "name": "Pharmacy", "category": "Health", "floor": 2, "position": 6,
     "description": "Everyday health and personal care.",
     "products": [
         _p("first-aid-kit", "Family First Aid Kit", "Red / Blue", 2400, "Size", ["Standard"], 5149757,
            "Home and travel kits with 120 essentials."),
     ]},
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
