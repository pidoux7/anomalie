"""
Système de masques : conversion d'une image en positions de cellules
"anomales" sur une grille, puis tirage des filtres/vidéos pour chaque
position.
"""

import random

from PIL import Image

from . import config


# ============================================================
# Image → positions
# ============================================================
def charger_masque_en_positions(image_path, taille, seuil, inverser):
    img = Image.open(image_path).convert("L")
    img = img.resize((taille, taille), Image.LANCZOS)
    pixels = list(img.getdata())
    positions = []
    for i, p in enumerate(pixels):
        is_dark = p < seuil
        is_anom = is_dark if not inverser else not is_dark
        if is_anom:
            positions.append(i)
    return positions


def phase_utilise_masque(taille):
    if not config.MASQUES_ACTIFS:
        return False
    n_cellules = taille * taille
    portee = config.MSK.get("portee", "derniere")
    if portee == "derniere":
        return taille == config.PHASES[-1]
    elif portee == "minimum":
        return n_cellules >= config.MSK.get("phase_minimum", 256)
    elif portee == "specifique":
        return n_cellules in config.MSK.get("phases_specifiques", [])
    return False


def positions_pour_apparition(positions_finales, fraction):
    n = max(1, int(round(len(positions_finales) * fraction)))
    rng = random.Random(42)
    ordre = list(positions_finales)
    rng.shuffle(ordre)
    return set(ordre[:n])


# ============================================================
# Tirage d'anomalies
# ============================================================
def nb_anomalies_pour_grille(n_cellules):
    if n_cellules == 1:
        return 0
    if config.MODE_QUANTITE == "fixe":
        return min(config.NB_ANOMALIES_FIXE, n_cellules)
    return max(1, int(round(n_cellules * config.RATIO_PROPORTIONNEL)))


def choisir_filtre():
    return random.choice(config.ANOMALIES)


def choisir_anomalie():
    if config.MODE_ANOMALIE == "filtres":
        return ("filtre", choisir_filtre())
    elif config.MODE_ANOMALIE == "video":
        return ("video", None)
    else:
        return ("filtre", choisir_filtre()) if random.random() < 0.5 else ("video", None)


def tirer_anomalies_pour_positions(positions, mode_filtre_masque=None):
    anomalies_par_pos = {}
    if mode_filtre_masque == "tirage_global":
        type_a, info_a = choisir_anomalie()
        for pos in positions:
            anomalies_par_pos[pos] = (type_a, info_a)
    elif mode_filtre_masque == "tirage_par_cellule":
        for pos in positions:
            anomalies_par_pos[pos] = choisir_anomalie()
    elif mode_filtre_masque == "video_externe":
        for pos in positions:
            anomalies_par_pos[pos] = ("video", None)
    else:
        for pos in positions:
            anomalies_par_pos[pos] = choisir_anomalie()
    return anomalies_par_pos
