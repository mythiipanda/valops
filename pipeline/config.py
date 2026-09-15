"""Event spine + constants. LOCK//IN 2023 (1188) -> Champions Shanghai 2026 (2766)."""

# (vlr_id, year, tier, region)  tier in {masters, champions, regional}
# LOCK//IN counts as masters-level international.
EVENTS = [
    (1188, 2023, "masters", "INTL"),
    (1189, 2023, "regional", "AM"),
    (1190, 2023, "regional", "EMEA"),
    (1191, 2023, "regional", "PAC"),
    (1494, 2023, "masters", "INTL"),
    (1658, 2023, "regional", "AM"),
    (1659, 2023, "regional", "EMEA"),
    (1660, 2023, "regional", "PAC"),
    (1664, 2023, "regional", "CN"),
    (1657, 2023, "champions", "INTL"),
    (1923, 2024, "regional", "AM"),
    (1925, 2024, "regional", "EMEA"),
    (1924, 2024, "regional", "PAC"),
    (1926, 2024, "regional", "CN"),
    (1921, 2024, "masters", "INTL"),
    (2004, 2024, "regional", "AM"),
    (1998, 2024, "regional", "EMEA"),
    (2002, 2024, "regional", "PAC"),
    (2006, 2024, "regional", "CN"),
    (1999, 2024, "masters", "INTL"),
    (2095, 2024, "regional", "AM"),
    (2094, 2024, "regional", "EMEA"),
    (2005, 2024, "regional", "PAC"),
    (2096, 2024, "regional", "CN"),
    (2097, 2024, "champions", "INTL"),
    (2274, 2025, "regional", "AM"),
    (2276, 2025, "regional", "EMEA"),
    (2277, 2025, "regional", "PAC"),
    (2275, 2025, "regional", "CN"),
    (2281, 2025, "masters", "INTL"),
    (2347, 2025, "regional", "AM"),
    (2380, 2025, "regional", "EMEA"),
    (2379, 2025, "regional", "PAC"),
    (2359, 2025, "regional", "CN"),
    (2282, 2025, "masters", "INTL"),
    (2501, 2025, "regional", "AM"),
    (2498, 2025, "regional", "EMEA"),
    (2500, 2025, "regional", "PAC"),
    (2499, 2025, "regional", "CN"),
    (2283, 2025, "champions", "INTL"),
    (2682, 2026, "regional", "AM"),
    (2684, 2026, "regional", "EMEA"),
    (2683, 2026, "regional", "PAC"),
    (2685, 2026, "regional", "CN"),
    (2760, 2026, "masters", "INTL"),
    (2860, 2026, "regional", "AM"),
    (2863, 2026, "regional", "EMEA"),
    (2775, 2026, "regional", "PAC"),
    (2864, 2026, "regional", "CN"),
    (2765, 2026, "masters", "INTL"),
    (2977, 2026, "regional", "AM"),
    (2976, 2026, "regional", "EMEA"),
    (2776, 2026, "regional", "PAC"),
    (2978, 2026, "regional", "CN"),
    (2766, 2026, "champions", "INTL"),
]

TIER_W = {"champions": 1.5, "masters": 1.2, "regional": 0.7}

BASE_ELO = 1500.0
# ponytail: fixed K table, tune only if London backtest Brier > 0.21
K_GROUP, K_MAIN, K_PLAYOFF = 20.0, 24.0, 32.0
CHEM_PENALTY = 50.0  # Elo dock for a fully new lineup, scaled by turnover
OFFSEASON_KEEP = 0.7  # regress 30% to mean each January
PROV_MAPS = 10  # provisional players get 1.5x K

# Shanghai 2026: team ids + actual GSL groups (A-D)
TEAMS = {
    120: "100 Thieves", 6961: "LOUD", 1034: "NRG", 11058: "G2 Esports",
    731: "TYLOO", 13576: "JD Gaming", 1120: "EDward Gaming", 13581: "Xi Lai Gaming",
    8877: "Karmine Corp", 474: "Team Liquid", 1184: "FUT Esports", 2059: "Team Vitality",
    918: "Global Esports", 11060: "Nongshim RedForce", 624: "Paper Rex", 14: "T1",
}
GROUPS = {
    "A": [120, 14, 13576, 1184],
    "B": [918, 2059, 6961, 1120],
    "C": [731, 11058, 474, 624],
    "D": [8877, 13581, 11060, 1034],
}

DB_PATH = "data/valops.db"
RAW_DIR = "data/raw"
