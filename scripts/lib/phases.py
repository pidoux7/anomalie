"""
Création d'une phase de montage (grille de taille fixée) et concaténation
finale des segments.
"""

import math
import random
import sys
from pathlib import Path

from . import config
from .commun import run
from .grille import (
    calculer_taille_cellule,
    construire_sous_segment,
    concat_segments_simple,
)
from .masques import (
    charger_masque_en_positions,
    nb_anomalies_pour_grille,
    phase_utilise_masque,
    positions_pour_apparition,
    tirer_anomalies_pour_positions,
    choisir_filtre,
)
from .effets import phase_utilise_effets, jouer_effet


def _creer_phase_scenario(video_normale, video_anomalie_externe, taille,
                          output_path, duree_phase, debut_phase,
                          directives, cell_w, cell_h, pad_x, pad_y):
    """
    Exécute une séquence en mode scenario, à partir des directives explicites.
    Priorité : effet > masque > anomalies (filtre/source/aleatoire).
    """
    n_cellules = taille * taille

    # Effet pleine grille ?
    if "effet" in directives:
        jouer_effet(directives["effet"], video_normale, video_anomalie_externe,
                     taille, debut_phase, duree_phase, output_path,
                     cell_w, cell_h, pad_x, pad_y)
        return

    # Masque ad-hoc (chemin d'image fourni explicitement) ?
    if "masque" in directives:
        seuil = config.MSK.get("seuil", 128) if isinstance(config.MSK, dict) else 128
        inverser = config.MSK.get("inverser", False) if isinstance(config.MSK, dict) else False
        positions = charger_masque_en_positions(directives["masque"], taille,
                                                  seuil, inverser)
        print(f"  Masque '{directives['masque']}' : {len(positions)} cellules anomales")

        if "filtre" in directives:
            anomalies_par_pos = {p: ("filtre", directives["filtre"]) for p in positions}
        elif directives.get("source") == "video":
            anomalies_par_pos = {p: ("video", None) for p in positions}
        else:
            anomalies_par_pos = tirer_anomalies_pour_positions(positions)

        construire_sous_segment(video_normale, video_anomalie_externe,
                                 taille, debut_phase, duree_phase, output_path,
                                 cell_w, cell_h, pad_x, pad_y,
                                 set(positions), anomalies_par_pos)
        return

    # Mode "anomalies à des positions tirées au hasard"
    n_anomalies = directives.get("anomalies", 0)
    n_anomalies = min(n_anomalies, n_cellules)
    if n_anomalies <= 0:
        # Pas d'anomalie : grille normale
        construire_sous_segment(video_normale, video_anomalie_externe,
                                 taille, debut_phase, duree_phase, output_path,
                                 cell_w, cell_h, pad_x, pad_y,
                                 set(), {})
        return

    positions = set(random.sample(range(n_cellules), n_anomalies))

    if "filtre" in directives:
        nom = directives["filtre"][0]
        print(f"  {n_anomalies} anomalie(s), filtre fixe : {nom}")
        anomalies_par_pos = {p: ("filtre", directives["filtre"]) for p in positions}
    elif directives.get("source") == "video":
        print(f"  {n_anomalies} anomalie(s), source : vidéo externe")
        anomalies_par_pos = {p: ("video", None) for p in positions}
    elif directives.get("aleatoire"):
        print(f"  {n_anomalies} anomalie(s), filtres tirés au hasard")
        anomalies_par_pos = tirer_anomalies_pour_positions(positions)
    else:
        # n_anomalies > 0 mais aucun type spécifié → fallback aléatoire
        anomalies_par_pos = tirer_anomalies_pour_positions(positions)

    construire_sous_segment(video_normale, video_anomalie_externe,
                             taille, debut_phase, duree_phase, output_path,
                             cell_w, cell_h, pad_x, pad_y,
                             positions, anomalies_par_pos)


def creer_phase(video_normale, video_anomalie_externe, taille,
                output_path, duree_phase, debut_phase, directives=None):
    n_cellules = taille * taille
    cell_w, cell_h, _, _, pad_x, pad_y = calculer_taille_cellule(taille)

    # Mode scenario : directives explicites prennent le contrôle.
    if directives is not None:
        print(f"\n  Phase {taille}x{taille} ({n_cellules} cellules) [SCENARIO]")
        print(f"  Cellules {cell_w}x{cell_h}, padding ({pad_x},{pad_y})")
        _creer_phase_scenario(video_normale, video_anomalie_externe, taille,
                              output_path, duree_phase, debut_phase,
                              directives, cell_w, cell_h, pad_x, pad_y)
        return

    use_mask = phase_utilise_masque(taille)
    use_effets = phase_utilise_effets(taille)

    tag = ""
    if use_effets:
        tag = " [EFFETS]"
    elif use_mask:
        tag = " [MASQUE]"

    print(f"\n  Phase {taille}x{taille} ({n_cellules} cellules){tag}")
    print(f"  Cellules {cell_w}x{cell_h}, padding ({pad_x},{pad_y})")

    # ============ MODE EFFETS SPÉCIAUX (priorité au masque) ============
    if use_effets:
        durees_cfg = config.EFFETS.get("durees", {})
        sous_paths = []
        cumul = 0
        for idx, nom_effet in enumerate(config.EFFETS_LISTE):
            duree_effet = durees_cfg.get(nom_effet)
            if duree_effet is None:
                duree_effet = config.DUREE_SOURCE
            if cumul + duree_effet > duree_phase:
                duree_effet = duree_phase - cumul
            if duree_effet <= 0:
                break

            print(f"\n  → Effet {idx+1}/{len(config.EFFETS_LISTE)}: '{nom_effet}' "
                  f"({duree_effet:.1f}s)")
            sous_path = config.WORK_DIR / f"effet_{taille}_{idx:02d}_{nom_effet}.mp4"
            debut_sub = debut_phase + cumul
            jouer_effet(nom_effet, video_normale, video_anomalie_externe,
                         taille, debut_sub, duree_effet, sous_path,
                         cell_w, cell_h, pad_x, pad_y)
            sous_paths.append(sous_path)
            cumul += duree_effet

            if cumul >= duree_phase:
                break

        # Si le total des effets est plus court que la phase, on remplit avec
        # une grille classique pour atteindre duree_phase
        if cumul < duree_phase:
            duree_reste = duree_phase - cumul
            print(f"\n  → Remplissage final ({duree_reste:.1f}s grille classique)")
            sous_path = config.WORK_DIR / f"effet_{taille}_remplissage.mp4"
            construire_sous_segment(video_normale, video_anomalie_externe,
                                     taille, debut_phase + cumul, duree_reste,
                                     sous_path, cell_w, cell_h, pad_x, pad_y,
                                     set(), {})
            sous_paths.append(sous_path)

        concat_segments_simple(sous_paths, output_path)
        return

    # ============ MODE MASQUE ============
    if use_mask:
        msk = config.MSK
        evolution = msk.get("evolution", "statique")
        seuil = msk.get("seuil", 128)
        inverser = msk.get("inverser", False)
        mode_filtre = msk.get("mode_filtre", "tirage_global")
        images = msk.get("images", [])
        if not images:
            print("  ERREUR : aucune image de masque définie")
            sys.exit(1)
        for img in images:
            if not Path(img).exists():
                print(f"  ERREUR : masque introuvable : {img}")
                sys.exit(1)

        if evolution == "statique":
            positions = charger_masque_en_positions(images[0], taille, seuil, inverser)
            print(f"  Masque '{images[0]}' : {len(positions)} cellules anomales")
            anomalies_par_pos = tirer_anomalies_pour_positions(positions, mode_filtre)
            construire_sous_segment(video_normale, video_anomalie_externe,
                                     taille, debut_phase, duree_phase, output_path,
                                     cell_w, cell_h, pad_x, pad_y,
                                     set(positions), anomalies_par_pos)
            return

        elif evolution == "sequence":
            duree_par_image = msk.get("duree_par_image", 30)
            n_sub = max(1, int(math.ceil(duree_phase / duree_par_image)))
            duree_sub = duree_phase / n_sub
            sous_paths = []
            for k in range(n_sub):
                img_path = images[k % len(images)]
                positions = charger_masque_en_positions(img_path, taille, seuil, inverser)
                print(f"  Sous-segment {k+1}/{n_sub} masque='{img_path}' "
                      f"({len(positions)} cellules)")
                anomalies_par_pos = tirer_anomalies_pour_positions(positions, mode_filtre)
                sous_path = config.WORK_DIR / f"sub_mask_{taille}_{k:03d}.mp4"
                debut_sub = debut_phase + k * duree_sub
                construire_sous_segment(video_normale, video_anomalie_externe,
                                         taille, debut_sub, duree_sub, sous_path,
                                         cell_w, cell_h, pad_x, pad_y,
                                         set(positions), anomalies_par_pos)
                sous_paths.append(sous_path)
            concat_segments_simple(sous_paths, output_path)
            return

        elif evolution == "apparition":
            duree_appar = msk.get("duree_apparition", 60)
            apparition_filtre = msk.get("apparition_filtre", "fixe")
            n_sub = max(2, min(20, int(duree_appar / 5)))
            duree_sub = duree_phase / n_sub
            positions_finales = charger_masque_en_positions(images[0], taille, seuil, inverser)
            print(f"  Apparition progressive : {len(positions_finales)} cellules cibles, "
                  f"{n_sub} étapes, filtre={apparition_filtre}")

            # En mode "fixe" : on tire UN filtre une seule fois ; en "change" :
            # nouveau tirage à chaque étape.
            filtre_fixe = None
            if apparition_filtre == "fixe":
                if mode_filtre == "video_externe":
                    filtre_fixe = ("video", None)
                else:
                    filtre_fixe = ("filtre", choisir_filtre())
                nom_filtre = filtre_fixe[1][0] if filtre_fixe[0] == "filtre" else "video_externe"
                print(f"  → Filtre fixe pour toute l'apparition : {nom_filtre}")

            sous_paths = []
            for k in range(n_sub):
                t_start = k * duree_sub
                fraction = min(1.0, (t_start + duree_sub) / duree_appar)
                positions_k = positions_pour_apparition(positions_finales, fraction)
                print(f"  Étape {k+1}/{n_sub} : {len(positions_k)}/{len(positions_finales)} cellules")

                if filtre_fixe is not None:
                    anomalies_par_pos = {pos: filtre_fixe for pos in positions_k}
                else:
                    anomalies_par_pos = tirer_anomalies_pour_positions(positions_k, mode_filtre)

                sous_path = config.WORK_DIR / f"sub_app_{taille}_{k:03d}.mp4"
                debut_sub = debut_phase + t_start
                construire_sous_segment(video_normale, video_anomalie_externe,
                                         taille, debut_sub, duree_sub, sous_path,
                                         cell_w, cell_h, pad_x, pad_y,
                                         positions_k, anomalies_par_pos)
                sous_paths.append(sous_path)
            concat_segments_simple(sous_paths, output_path)
            return

    # ============ MODE CLASSIQUE (sans masque) ============
    n_anomalies = nb_anomalies_pour_grille(n_cellules)

    if config.CHANGEMENT_ANOMALIE in ("phase", "fixe") or taille == 1:
        positions = set(random.sample(range(n_cellules), n_anomalies)) if n_anomalies > 0 else set()
        anomalies_par_pos = tirer_anomalies_pour_positions(positions)
        construire_sous_segment(video_normale, video_anomalie_externe,
                                 taille, debut_phase, duree_phase, output_path,
                                 cell_w, cell_h, pad_x, pad_y,
                                 positions, anomalies_par_pos)
        return

    n_sub = max(1, int(math.ceil(duree_phase / config.INTERVALLE_CHANGEMENT)))
    duree_sub = duree_phase / n_sub
    sous_paths = []
    for k in range(n_sub):
        positions = set(random.sample(range(n_cellules), n_anomalies)) if n_anomalies > 0 else set()
        anomalies_par_pos = tirer_anomalies_pour_positions(positions)
        sous_path = config.WORK_DIR / f"sub_{taille}_{k:03d}.mp4"
        debut_sub = debut_phase + k * duree_sub
        print(f"  Sous-segment {k+1}/{n_sub}")
        construire_sous_segment(video_normale, video_anomalie_externe,
                                 taille, debut_sub, duree_sub, sous_path,
                                 cell_w, cell_h, pad_x, pad_y,
                                 positions, anomalies_par_pos)
        sous_paths.append(sous_path)
    concat_segments_simple(sous_paths, output_path)


def concatener(segments, output_path):
    """
    Re-encode chaque segment à dimensions et framerate identiques (CFR),
    puis concatène. Évite les bugs de duplication / décalage de durée
    lors de la concaténation de segments générés à des étapes différentes.
    """
    target_w = config.pair_inf(config.FINAL_W)
    target_h = config.pair_inf(config.FINAL_H)
    segments_norm = []
    for i, seg in enumerate(segments):
        out = config.WORK_DIR / f"norm_{i}.mp4"
        run([
            "ffmpeg", "-y", "-i", str(seg),
            "-vf", f"scale={target_w}:{target_h},setsar=1",
            "-r", "30",
            "-fps_mode", "cfr",
            "-video_track_timescale", "30000",
            *config.args_encodage(),
            "-pix_fmt", "yuv420p",
            "-an",
            str(out)
        ])
        segments_norm.append(out)
    concat_segments_simple(segments_norm, output_path)
