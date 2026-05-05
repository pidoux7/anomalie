"""
Effets spéciaux jouables sur une phase :
    - mosaique     : grille modulée par une vidéo carte
    - mur_moniteurs : chaque cellule joue une vidéo différente
    - lsd          : distorsion ondulatoire + aberration + saturation
    - masque       : utilise le système de masques en effet (creer_phase_masque_seul)

`jouer_effet` est le dispatcher utilisé par `phases.creer_phase`.
"""

import math
import sys

from . import config
from .commun import run, get_duration, parser_timestamp
from .grille import (
    fabriquer_segment,
    fabriquer_segments_paralleles,
    assembler_grille,
    assembler_grille_par_lignes,
    concat_segments_simple,
    construire_sous_segment,
)
from .masques import (
    charger_masque_en_positions,
    positions_pour_apparition,
    tirer_anomalies_pour_positions,
    choisir_filtre,
)


def phase_utilise_effets(taille):
    """Détermine si la phase courante doit jouer des effets spéciaux."""
    if not config.EFFETS_ACTIFS or not config.EFFETS_LISTE:
        return False
    n_cellules = taille * taille
    portee = config.EFFETS.get("portee", "min_cases")
    if portee == "min_cases":
        return n_cellules >= config.EFFETS.get("seuil_cases", 1024)
    elif portee == "specifique":
        return n_cellules in config.EFFETS.get("phases_specifiques", [])
    return False


# ============================================================
# Mosaïque
# ============================================================
def creer_effet_mosaique(video_source, taille, debut_source, duree,
                          output_path, cell_w, cell_h, pad_x, pad_y):
    """
    Mosaïque vidéo : la grille forme l'image d'une autre vidéo (la "carte"),
    où chaque cellule joue la vidéo source mais sa luminosité est modulée par
    le pixel correspondant de la carte.
    """
    cfg_m = config.EFFETS.get("mosaique", {})
    video_carte = cfg_m["video_carte"]
    debut_carte = parser_timestamp(cfg_m.get("debut", 0)) or 0
    en_nb = cfg_m.get("carte_en_noir_blanc", False)
    intensite = float(cfg_m.get("intensite", 1.0))
    mode_fusion = cfg_m.get("mode_fusion", "multiply")

    grid_w = cell_w * taille
    grid_h = cell_h * taille

    print(f"    [mosaique] préparation grille de base...")
    mini_normal = config.WORK_DIR / f"mosaic_mini_{taille}_{int(debut_source)}.mp4"
    fabriquer_segment(video_source, mini_normal, debut_source, duree,
                       cell_w, cell_h)

    grille_base = config.WORK_DIR / f"mosaic_grille_{taille}_{int(debut_source)}.mp4"
    paths = [mini_normal] * (taille * taille)
    if (taille * taille) > 256:
        assembler_grille_par_lignes(taille, paths, duree, grille_base, 0, 0)
    else:
        assembler_grille(taille, paths, duree, grille_base, 0, 0)

    print(f"    [mosaique] préparation de la carte...")
    carte_path = config.WORK_DIR / f"mosaic_carte_{taille}_{int(debut_source)}.mp4"

    vf_carte = [
        f"scale={taille}:{taille}:flags=area",
        f"scale={grid_w}:{grid_h}:flags=neighbor",
    ]
    if en_nb:
        vf_carte.append("hue=s=0")
    vf_carte.append("setsar=1")

    run([
        "ffmpeg", "-y",
        "-stream_loop", "-1",
        "-ss", str(debut_carte),
        "-i", str(video_carte),
        "-t", str(duree),
        "-vf", ",".join(vf_carte),
        "-r", "30",
        "-fps_mode", "cfr",
        *config.args_encodage("22"),
        "-pix_fmt", "yuv420p", "-an",
        str(carte_path)
    ])

    print(f"    [mosaique] fusion ({mode_fusion}, intensité={intensite})...")
    if mode_fusion == "preserve_couleur":
        # Mode "preserve_couleur" : on désature la carte (luma seulement)
        # et on blend en overlay → la grille garde ses couleurs d'origine,
        # seule la luminosité varie selon la carte. Évite la teinte verte
        # quand la carte a une dominante de couleur.
        fc = (
            f"[1:v]hue=s=0,eq=contrast={0.5+0.5*intensite}[carte_lum];"
            f"[0:v][carte_lum]blend=all_mode=overlay:all_opacity={intensite}[v]"
        )
    elif intensite < 1.0:
        fc = (
            f"[1:v]eq=brightness={(1-intensite)*0.3}:contrast={intensite}[carte_mod];"
            f"[0:v][carte_mod]blend=all_mode={mode_fusion}[v]"
        )
    else:
        fc = f"[0:v][1:v]blend=all_mode={mode_fusion}[v]"

    fusionne = config.WORK_DIR / f"mosaic_fusion_{taille}_{int(debut_source)}.mp4"
    run([
        "ffmpeg", "-y",
        "-i", str(grille_base),
        "-i", str(carte_path),
        "-filter_complex", fc,
        "-map", "[v]",
        "-t", str(duree),
        *config.args_encodage(),
        "-pix_fmt", "yuv420p",
        str(fusionne)
    ])

    if pad_x > 0 or pad_y > 0:
        run([
            "ffmpeg", "-y", "-i", str(fusionne),
            "-vf", f"pad={config.FINAL_W}:{config.FINAL_H}:{pad_x}:{pad_y}:black,setsar=1",
            *config.args_encodage(),
            "-pix_fmt", "yuv420p",
            str(output_path)
        ])
    else:
        fusionne.rename(output_path)


# ============================================================
# Mur de moniteurs
# ============================================================
def creer_effet_mur_moniteurs(video_source, taille, debut_source, duree,
                                output_path, cell_w, cell_h, pad_x, pad_y):
    """
    Mur de moniteurs : chaque cellule joue une vidéo différente tirée au sort
    parmi une liste fournie (et éventuellement la vidéo source elle-même).
    """
    import random
    cfg_w = config.EFFETS.get("mur_moniteurs", {})
    videos = list(cfg_w.get("videos", []))
    if cfg_w.get("inclure_source", True):
        videos.append(str(video_source))
    if not videos:
        print("ERREUR : mur_moniteurs sans vidéos disponibles")
        sys.exit(1)
    decalage_aleatoire = cfg_w.get("decalage_aleatoire", True)

    n_cellules = taille * taille
    print(f"    [mur_moniteurs] {len(videos)} vidéos disponibles, "
          f"{n_cellules} cellules à remplir")

    durees_videos = {}
    for v in videos:
        try:
            durees_videos[v] = get_duration(v)
        except Exception:
            durees_videos[v] = duree

    paths = []
    taches = []
    for i in range(n_cellules):
        v = random.choice(videos)
        if decalage_aleatoire:
            d_max = max(0, durees_videos[v] - duree)
            debut = random.uniform(0, d_max)
        else:
            debut = 0

        mini = config.WORK_DIR / f"wall_mini_{taille}_{i:04d}_{int(debut_source)}.mp4"
        taches.append((v, mini, debut, duree, cell_w, cell_h, None))
        paths.append(mini)

    print(f"    [mur_moniteurs] préparation de {n_cellules} cellules...")
    fabriquer_segments_paralleles(taches)

    if n_cellules > 256:
        assembler_grille_par_lignes(taille, paths, duree, output_path, pad_x, pad_y)
    else:
        assembler_grille(taille, paths, duree, output_path, pad_x, pad_y)


# ============================================================
# LSD
# ============================================================
def creer_effet_lsd(video_source, taille, debut_source, duree,
                     output_path, cell_w, cell_h, pad_x, pad_y):
    """
    Effet LSD : grille classique → distorsion ondulatoire + aberration
    chromatique + saturation + contours.
    """
    cfg_l = config.EFFETS.get("lsd", {})
    amp = float(cfg_l.get("amplitude_onde", 120))
    vit = float(cfg_l.get("vitesse_onde", 0.4))           # ↓ moins rythmé par défaut
    freq = float(cfg_l.get("frequence_onde", 8))
    aber = int(cfg_l.get("aberration", 8))
    sat = float(cfg_l.get("saturation", 2.5))
    teinte = float(cfg_l.get("teinte", 240))
    bruit = float(cfg_l.get("bruit", 30))                  # 0 = aucun, 60+ = très grainé
    lignes_force = float(cfg_l.get("lignes_force", 0.7))
    lignes_mode = cfg_l.get("lignes_mode", "edges")

    grid_w = cell_w * taille
    grid_h = cell_h * taille

    print(f"    [lsd] préparation grille...")
    mini_normal = config.WORK_DIR / f"lsd_mini_{taille}_{int(debut_source)}.mp4"
    fabriquer_segment(video_source, mini_normal, debut_source, duree,
                       cell_w, cell_h)
    grille_base = config.WORK_DIR / f"lsd_grille_{taille}_{int(debut_source)}.mp4"
    paths = [mini_normal] * (taille * taille)
    if (taille * taille) > 256:
        assembler_grille_par_lignes(taille, paths, duree, grille_base, 0, 0)
    else:
        assembler_grille(taille, paths, duree, grille_base, 0, 0)

    print(f"    [lsd] application effets (amp={amp}, sat={sat}, teinte={teinte}, bruit={bruit})...")

    parts = []
    parts.append(f"[0:v]hue=h={teinte}:s={sat}[col]")
    parts.append(f"[col]rgbashift=rh={aber}:bh=-{aber}:rv=-{aber//2}:bv={aber//2}[abr]")

    # Bruit film (uniforme + temporel) appliqué avant la déformation pour
    # créer un grain qui se déforme avec le reste.
    if bruit > 0:
        parts.append(f"[abr]noise=alls={bruit}:allf=t+u[abr2]")
        last_label = "abr2"
    else:
        last_label = "abr"

    # Déformation 2D non-uniforme :
    # - L'amplitude horizontale varie selon X (zones plus déformées que d'autres).
    # - Une déformation verticale plus douce s'ajoute (cosinus en fonction de X+T).
    # Résultat : ondulations moins synchronisées sur toute l'image.
    expr_x = (
        f"X+{amp}*sin(Y/{max(1,grid_h/freq):.3f}+T*{vit*6.28:.3f})"
        f"*(0.5+0.5*sin(X/{max(1,grid_w/3):.0f}+T*{vit*2.5:.3f}))"
    )
    expr_y = (
        f"Y+{amp/3}*cos(X/{max(1,grid_w/(freq*0.7)):.3f}"
        f"+T*{vit*4.5:.3f})"
    )
    parts.append(
        f"[{last_label}]geq="
        f"r='r({expr_x},{expr_y})':"
        f"g='g({expr_x},{expr_y})':"
        f"b='b({expr_x},{expr_y})'"
        f"[wave]"
    )
    if lignes_force > 0:
        edge_mode = "wires" if lignes_mode == "wires" else "canny"
        parts.append(
            f"[wave]split[w1][w2];"
            f"[w2]edgedetect=mode={edge_mode}:low=0.1:high=0.4,"
            f"hue=h={teinte}:s=3,eq=brightness={lignes_force*0.3}[edges];"
            f"[w1][edges]blend=all_mode=screen:all_opacity={lignes_force}[v]"
        )
    else:
        parts.append("[wave]copy[v]")

    fc = ";".join(parts)

    output_lsd = config.WORK_DIR / f"lsd_out_{taille}_{int(debut_source)}.mp4"
    run([
        "ffmpeg", "-y",
        "-i", str(grille_base),
        "-filter_complex", fc,
        "-map", "[v]",
        "-t", str(duree),
        *config.args_encodage(),
        "-pix_fmt", "yuv420p",
        str(output_lsd)
    ])

    if pad_x > 0 or pad_y > 0:
        run([
            "ffmpeg", "-y", "-i", str(output_lsd),
            "-vf", f"pad={config.FINAL_W}:{config.FINAL_H}:{pad_x}:{pad_y}:black,setsar=1",
            *config.args_encodage(),
            "-pix_fmt", "yuv420p",
            str(output_path)
        ])
    else:
        output_lsd.rename(output_path)


# ============================================================
# Masque (utilisé en effet)
# ============================================================
def creer_phase_masque_seul(video_normale, video_anomalie_externe, taille,
                              debut_phase, duree_phase, output_path,
                              cell_w, cell_h, pad_x, pad_y):
    """
    Version isolée de la logique masque, utilisable comme effet.
    """
    if not config.MASQUES_ACTIFS:
        print("ATTENTION : effet 'masque' demandé mais système masque désactivé")
        construire_sous_segment(video_normale, video_anomalie_externe,
                                 taille, debut_phase, duree_phase, output_path,
                                 cell_w, cell_h, pad_x, pad_y,
                                 set(), {})
        return

    msk = config.MSK
    evolution = msk.get("evolution", "statique")
    seuil = msk.get("seuil", 128)
    inverser = msk.get("inverser", False)
    mode_filtre = msk.get("mode_filtre", "tirage_global")
    images = msk.get("images", [])
    if not images:
        print("ERREUR : aucune image de masque")
        sys.exit(1)

    if evolution == "statique":
        positions = charger_masque_en_positions(images[0], taille, seuil, inverser)
        anomalies_par_pos = tirer_anomalies_pour_positions(positions, mode_filtre)
        construire_sous_segment(video_normale, video_anomalie_externe,
                                 taille, debut_phase, duree_phase, output_path,
                                 cell_w, cell_h, pad_x, pad_y,
                                 set(positions), anomalies_par_pos)
    elif evolution == "apparition":
        duree_appar = msk.get("duree_apparition", 60)
        apparition_filtre = msk.get("apparition_filtre", "fixe")
        n_sub = max(2, min(20, int(duree_appar / 5)))
        duree_sub = duree_phase / n_sub
        positions_finales = charger_masque_en_positions(images[0], taille, seuil, inverser)

        filtre_fixe = None
        if apparition_filtre == "fixe":
            if mode_filtre == "video_externe":
                filtre_fixe = ("video", None)
            else:
                filtre_fixe = ("filtre", choisir_filtre())

        sous_paths = []
        for k in range(n_sub):
            t_start = k * duree_sub
            fraction = min(1.0, (t_start + duree_sub) / duree_appar)
            positions_k = positions_pour_apparition(positions_finales, fraction)
            if filtre_fixe is not None:
                anomalies_par_pos = {pos: filtre_fixe for pos in positions_k}
            else:
                anomalies_par_pos = tirer_anomalies_pour_positions(positions_k, mode_filtre)
            sous_path = config.WORK_DIR / f"effet_app_{taille}_{k:03d}.mp4"
            debut_sub = debut_phase + t_start
            construire_sous_segment(video_normale, video_anomalie_externe,
                                     taille, debut_sub, duree_sub, sous_path,
                                     cell_w, cell_h, pad_x, pad_y,
                                     positions_k, anomalies_par_pos)
            sous_paths.append(sous_path)
        concat_segments_simple(sous_paths, output_path)
    else:  # sequence
        duree_par_image = msk.get("duree_par_image", 30)
        n_sub = max(1, int(math.ceil(duree_phase / duree_par_image)))
        duree_sub = duree_phase / n_sub
        sous_paths = []
        for k in range(n_sub):
            img_path = images[k % len(images)]
            positions = charger_masque_en_positions(img_path, taille, seuil, inverser)
            anomalies_par_pos = tirer_anomalies_pour_positions(positions, mode_filtre)
            sous_path = config.WORK_DIR / f"effet_seq_{taille}_{k:03d}.mp4"
            debut_sub = debut_phase + k * duree_sub
            construire_sous_segment(video_normale, video_anomalie_externe,
                                     taille, debut_sub, duree_sub, sous_path,
                                     cell_w, cell_h, pad_x, pad_y,
                                     set(positions), anomalies_par_pos)
            sous_paths.append(sous_path)
        concat_segments_simple(sous_paths, output_path)


# ============================================================
# Dispatcher
# ============================================================
def jouer_effet(nom_effet, video_source, video_anomalie_externe, taille,
                 debut_source, duree, output_path, cell_w, cell_h, pad_x, pad_y):
    if nom_effet == "mosaique":
        creer_effet_mosaique(video_source, taille, debut_source, duree,
                              output_path, cell_w, cell_h, pad_x, pad_y)
    elif nom_effet == "mur_moniteurs":
        creer_effet_mur_moniteurs(video_source, taille, debut_source, duree,
                                    output_path, cell_w, cell_h, pad_x, pad_y)
    elif nom_effet == "lsd":
        creer_effet_lsd(video_source, taille, debut_source, duree,
                         output_path, cell_w, cell_h, pad_x, pad_y)
    elif nom_effet == "masque":
        creer_phase_masque_seul(video_source, video_anomalie_externe, taille,
                                  debut_source, duree, output_path,
                                  cell_w, cell_h, pad_x, pad_y)
    else:
        print(f"ERREUR : effet inconnu '{nom_effet}'")
        sys.exit(1)
