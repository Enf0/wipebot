"""The game's classes as Wipebot shows them: each name in its class colour."""

# The game's class colours (https://warcraft.wiki.gg/wiki/Class_colors, from the game's ChrClasses table, which no
# source we read publishes; unchanged in years), by Warcraft Logs' class name. A new class gets white.
CLASS_COLORS = {
    "DeathKnight": "C41E3A", "DemonHunter": "A330C9", "Druid": "FF7C0A", "Evoker": "33937F", "Hunter": "AAD372",
    "Mage": "3FC7EB", "Monk": "00FF98", "Paladin": "F48CBA", "Priest": "FFFFFF", "Rogue": "FFF468",
    "Shaman": "0070DD", "Warlock": "8788EE", "Warrior": "C69B6D",
}
WHITE = "ECF0F8"


def class_hex(class_name: str) -> str:
    """'#FF7C0A' for a Druid."""
    return "#" + CLASS_COLORS.get(class_name, WHITE)


def class_rgb(class_name: str) -> tuple[int, int, int]:
    h = CLASS_COLORS.get(class_name, WHITE)
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
