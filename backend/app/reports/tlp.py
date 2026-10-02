ORDER = ("white", "green", "amber", "red")
LABEL = {"white": "CLEAR", "green": "GREEN", "amber": "AMBER", "red": "RED"}
COLORS = {"white": ("#FFFFFF", "#000000"), "green": ("#33FF00", "#000000"),
          "amber": ("#FFC000", "#000000"), "red": ("#FF2B2B", "#FFFFFF")}


def floor(tlps) -> str:
    vals = [t for t in tlps if t in ORDER]
    return max(vals, key=ORDER.index) if vals else "white"


def at_least(t: str, minimum: str) -> bool:
    return t in ORDER and minimum in ORDER and ORDER.index(t) >= ORDER.index(minimum)
