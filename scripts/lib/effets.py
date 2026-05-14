"""
Effets spéciaux jouables sur une phase :
    - mosaique     : grille modulée par une vidéo carte
    - mur_moniteurs : chaque cellule joue une vidéo différente
    - lsd          : distorsion ondulatoire + aberration + saturation
    - masque       : utilise le système de masques en effet (creer_phase_masque_seul)
    - transition_smooth : transition cellule-par-cellule via mask animé

`jouer_effet` est le dispatcher utilisé par `phases.creer_phase`.
"""

import math
import random
import sys
from pathlib import Path

from PIL import Image

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

    # Déformation 3D : 3 ondes simultanées à orientations différentes pour
    # casser l'aspect unidirectionnel et donner une impression de profondeur.
    #   - Onde 1 (horizontale) : varie selon Y, modulée par X (existante)
    #   - Onde 2 (diagonale)   : variants selon (X+Y) et (X-Y)
    #   - Onde 3 (radiale)     : pulsation depuis le centre de l'image
    cx = grid_w / 2.0
    cy = grid_h / 2.0
    f_h = max(1, grid_h / freq)
    f_w = max(1, grid_w / max(0.7, freq * 0.7))
    f_diag = max(1, grid_h / max(0.7, freq * 1.3))
    f_radial = max(1, min(grid_w, grid_h) / max(0.5, freq * 1.5))
    dist = f"sqrt((X-{cx:.1f})*(X-{cx:.1f})+(Y-{cy:.1f})*(Y-{cy:.1f}))"

    onde1_x = f"{amp}*sin(Y/{f_h:.3f}+T*{vit*6.28:.3f})"
    onde1_mod = f"(0.5+0.5*sin(X/{max(1,grid_w/3):.0f}+T*{vit*2.5:.3f}))"
    onde2_x = f"{amp/2}*sin((X+Y)/{f_diag:.3f}+T*{vit*3.5:.3f})"
    onde2_y = f"{amp/2}*cos((X-Y)/{f_diag:.3f}+T*{vit*5.2:.3f})"
    onde1_y = f"{amp/3}*cos(X/{f_w:.3f}+T*{vit*4.5:.3f})"
    radial_amp = f"{amp/2.5}*sin({dist}/{f_radial:.3f}+T*{vit*3.0:.3f})"
    radial_x = f"{radial_amp}*(X-{cx:.1f})/max({dist},1)"
    radial_y = f"{radial_amp}*(Y-{cy:.1f})/max({dist},1)"

    expr_x = f"X+{onde1_x}*{onde1_mod}+{onde2_x}+{radial_x}"
    expr_y = f"Y+{onde1_y}+{onde2_y}+{radial_y}"
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
# Transition cellule par cellule via mask animé
# ============================================================
def creer_effet_transition_smooth(video_b, video_a, taille, debut, duree,
                                    output_path, cell_w, cell_h, pad_x, pad_y):
    """
    Transition pixel par pixel : la vidéo A apparaît cellule par cellule
    par-dessus la vidéo B. Au lieu de N paliers ffmpeg discrets, on génère
    une vidéo de mask (un PNG par frame) qui révèle progressivement les
    cellules dans un ordre aléatoire déterministe, puis on compose A/B
    via maskedmerge en UN seul ffmpeg.

    À 30 fps, sur duree secondes, on a ~30*duree frames disponibles. Si
    n_cellules > n_frames, plusieurs cellules apparaissent par frame ;
    sinon on a vraiment 1 cellule (ou rien) par frame.
    """
    n_cellules = taille * taille
    grid_w = cell_w * taille
    grid_h = cell_h * taille
    fps = 30
    n_frames = max(1, int(duree * fps))

    print(f"    [transition_smooth] grille A (vidéo qui apparaît) : {video_a}")
    mini_a = config.WORK_DIR / f"trans_mini_a_{taille}_{int(debut)}.mp4"
    fabriquer_segment(video_a, mini_a, debut, duree, cell_w, cell_h)
    grille_a = config.WORK_DIR / f"trans_grille_a_{taille}_{int(debut)}.mp4"
    paths = [mini_a] * n_cellules
    if n_cellules > 256:
        assembler_grille_par_lignes(taille, paths, duree, grille_a, 0, 0)
    else:
        assembler_grille(taille, paths, duree, grille_a, 0, 0)

    print(f"    [transition_smooth] grille B (fond) : {video_b}")
    mini_b = config.WORK_DIR / f"trans_mini_b_{taille}_{int(debut)}.mp4"
    fabriquer_segment(video_b, mini_b, debut, duree, cell_w, cell_h)
    grille_b = config.WORK_DIR / f"trans_grille_b_{taille}_{int(debut)}.mp4"
    paths = [mini_b] * n_cellules
    if n_cellules > 256:
        assembler_grille_par_lignes(taille, paths, duree, grille_b, 0, 0)
    else:
        assembler_grille(taille, paths, duree, grille_b, 0, 0)

    print(f"    [transition_smooth] mask animé "
          f"({n_cellules} cellules sur {n_frames} frames "
          f"≈ {n_cellules/n_frames:.1f}/frame)")

    # Ordre de révélation des cellules (déterministe par seed)
    rng = random.Random(f"transition_smooth|{taille}|{int(debut)}|{int(duree)}")
    ordre = list(range(n_cellules))
    rng.shuffle(ordre)

    mask_dir = config.WORK_DIR / f"trans_mask_{taille}_{int(debut)}"
    mask_dir.mkdir(parents=True, exist_ok=True)
    # Nettoie d'éventuels frames d'un run précédent (sinon le %05d glob
    # pourrait inclure des frames résiduelles avec un autre n_frames).
    for old in mask_dir.glob("frame_*.png"):
        old.unlink()

    for f_idx in range(n_frames):
        n_revelees = int((f_idx + 1) / n_frames * n_cellules)
        img = Image.new("L", (taille, taille), 0)
        pixels = img.load()
        for c in ordre[:n_revelees]:
            x, y = c % taille, c // taille
            pixels[x, y] = 255
        img.save(mask_dir / f"frame_{f_idx:05d}.png")

    mask_video = config.WORK_DIR / f"trans_mask_{taille}_{int(debut)}.mp4"
    run([
        "ffmpeg", "-y",
        "-framerate", str(fps),
        "-i", str(mask_dir / "frame_%05d.png"),
        "-vf", f"scale={grid_w}:{grid_h}:flags=neighbor",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "10",
        "-pix_fmt", "yuv420p",
        str(mask_video)
    ])

    # Composition : on utilise alphamerge + overlay (pattern classique
    # bien documenté de ffmpeg).
    #   - extractplanes=y : isole le plan luma du mask → grayscale propre
    #   - alphamerge : applique ce grayscale comme canal alpha sur la
    #     vidéo A (foreground). Pixel mask blanc → A opaque ; noir → A
    #     transparent.
    #   - overlay : compose A (transparent par endroit) sur B (fond).
    fusionne = config.WORK_DIR / f"trans_fusion_{taille}_{int(debut)}.mp4"
    run([
        "ffmpeg", "-y",
        "-i", str(grille_b),
        "-i", str(grille_a),
        "-i", str(mask_video),
        "-filter_complex",
            "[2:v]extractplanes=y[mask_y];"
            "[1:v]format=yuva420p[fg];"
            "[fg][mask_y]alphamerge[fg_alpha];"
            "[0:v][fg_alpha]overlay=format=auto[v]",
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
# Kaléidoscope
# ============================================================
def creer_effet_kaleidoscope(video_source, taille, debut_source, duree,
                              output_path, cell_w, cell_h, pad_x, pad_y):
    """
    Effet kaléidoscope : on prépare la grille pleine (toutes cellules =
    source), puis on en garde un quart (haut-gauche) que l'on réfléchit
    horizontalement et verticalement pour reconstituer une image
    symétrique miroir. Paramètre `secteurs` : 2 (miroir vertical) ou
    4 (miroir vertical+horizontal).
    """
    cfg_k = config.EFFETS.get("kaleidoscope", {})
    secteurs = int(cfg_k.get("secteurs", 4))

    print(f"    [kaleidoscope] secteurs={secteurs}, préparation grille…")
    mini = config.WORK_DIR / f"kal_mini_{taille}_{int(debut_source)}.mp4"
    fabriquer_segment(video_source, mini, debut_source, duree, cell_w, cell_h)
    grille = config.WORK_DIR / f"kal_grille_{taille}_{int(debut_source)}.mp4"
    paths = [mini] * (taille * taille)
    if (taille * taille) > 256:
        assembler_grille_par_lignes(taille, paths, duree, grille, 0, 0)
    else:
        assembler_grille(taille, paths, duree, grille, 0, 0)

    if secteurs == 2:
        # Miroir vertical : moitié gauche + son reflet
        fc = (
            "[0:v]crop=iw/2:ih:0:0[l_src];"
            "[l_src]split[la][lb];"
            "[lb]hflip[r];"
            "[la][r]hstack[v]"
        )
    elif secteurs == 4:
        # Miroir vertical + horizontal : quart haut-gauche réfléchi
        fc = (
            "[0:v]crop=iw/2:ih/2:0:0[tl_src];"
            "[tl_src]split[tla][tlb];"
            "[tlb]hflip[tr];"
            "[tla][tr]hstack[top_src];"
            "[top_src]split[topa][topb];"
            "[topb]vflip[bot];"
            "[topa][bot]vstack[v]"
        )
    elif secteurs == 6:
        # "Œil d'insecte" : tile 3x3 avec miroirs alternés sur chaque
        # facette. La centrale est doublement flippée pour casser la
        # symétrie évidente.
        fc = (
            "[0:v]scale=iw/3:ih/3,split=9[a][b][c][d][e][f][g][h][i];"
            "[b]hflip[b2];"
            "[d]vflip[d2];"
            "[f]vflip[f2];"
            "[h]hflip[h2];"
            "[e]hflip,vflip[e2];"
            "[a][b2][c]hstack=3[r1];"
            "[d2][e2][f2]hstack=3[r2];"
            "[g][h2][i]hstack=3[r3];"
            "[r1][r2][r3]vstack=3[v]"
        )
    else:
        print(f"ERREUR : 'secteurs' kaléidoscope doit valoir 2, 4 ou 6 (reçu {secteurs})")
        sys.exit(1)

    out_path = config.WORK_DIR / f"kal_out_{taille}_{int(debut_source)}.mp4"
    run([
        "ffmpeg", "-y", "-i", str(grille),
        "-filter_complex", fc,
        "-map", "[v]", "-t", str(duree),
        *config.args_encodage(),
        "-pix_fmt", "yuv420p",
        str(out_path)
    ])

    if pad_x > 0 or pad_y > 0:
        run([
            "ffmpeg", "-y", "-i", str(out_path),
            "-vf", f"pad={config.FINAL_W}:{config.FINAL_H}:{pad_x}:{pad_y}:black,setsar=1",
            *config.args_encodage(),
            "-pix_fmt", "yuv420p",
            str(output_path)
        ])
    else:
        out_path.rename(output_path)


# ============================================================
# Audio-réactif (visualisation audio superposée)
# ============================================================
def _rms_buckets(fichier_audio, duree, n_buckets,
                  debut=0, percentile=0.95, puissance=1.0, lissage=0):
    """
    Extrait N valeurs RMS de l'audio sur la durée donnée, normalisées,
    accentuées et optionnellement lissées.

    Args:
        debut      : décalage en secondes dans le fichier audio (= -ss)
        percentile : normalisation par percentile (0.95 = 95e perc → 1.0)
        puissance  : exposant appliqué après normalisation (>1 = pics accentués)
        lissage    : fenêtre de moyenne mobile autour de chaque bucket
                     (0 = aucun, 2 = moyenne sur 5 buckets centrés)
    """
    import audioop
    import subprocess
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error",
         "-ss", str(debut),
         "-i", fichier_audio, "-t", str(duree),
         "-f", "s16le", "-ac", "1", "-ar", "44100", "-"],
        capture_output=True
    )
    pcm = proc.stdout
    n_samples = len(pcm) // 2
    if n_samples == 0:
        return [0.0] * n_buckets
    samples_per_b = max(1, n_samples // n_buckets)
    bytes_per_b = samples_per_b * 2
    rms_vals = []
    for i in range(n_buckets):
        start = i * bytes_per_b
        end = min(start + bytes_per_b, len(pcm))
        chunk = pcm[start:end]
        rms_vals.append(audioop.rms(chunk, 2) if chunk else 0)

    # Normalisation par percentile (au lieu du max strict)
    if any(rms_vals):
        sorted_vals = sorted(rms_vals)
        idx = max(0, min(len(sorted_vals) - 1, int(len(sorted_vals) * percentile)))
        pivot = sorted_vals[idx] or max(rms_vals)
        rms = [min(1.0, r / pivot) for r in rms_vals]
    else:
        rms = [0.0] * len(rms_vals)

    # Puissance : accentue ou adoucit les contrastes
    if puissance != 1.0:
        rms = [r ** puissance for r in rms]

    # Lissage : moyenne mobile centrée
    if lissage > 0:
        lisse = []
        for i in range(len(rms)):
            window = rms[max(0, i - lissage):min(len(rms), i + lissage + 1)]
            lisse.append(sum(window) / len(window))
        rms = lisse

    return rms


def creer_effet_audioreactif(video_source, taille, debut_source, duree,
                              output_path, cell_w, cell_h, pad_x, pad_y):
    """
    Audio-réactif : 4 modes.
      - "rms"      : vraiment audio-réactif — analyse le RMS du fichier
                     audio par tranches et applique un zoom proportionnel.
      - "pulse"    : zoom pulsé synthétique selon un BPM donné (pas de
                     vraie analyse audio, juste un rythme constant).
      - "waves"    : forme d'onde overlay
      - "spectrum" : spectre CQT overlay
    Paramètres dans effets.audioreactif :
        mode      : "rms" | "pulse" | "waves" | "spectrum"
        fichier   : audio à analyser/visualiser
        intensite : amplitude du zoom (mode rms/pulse, default 0.3)
        rotation  : rad/s additionnels (rms/pulse, default 0)
        buckets   : nb tranches d'analyse RMS (mode rms, default = duree*5)
        bpm       : pulsations par minute (mode pulse, default 120)
        opacite   : 0-1, force de la superposition (waves/spectrum)
        couleur   : couleur du tracé (waves uniquement)
    """
    cfg_ar = config.EFFETS.get("audioreactif", {})
    mode = cfg_ar.get("mode", "rms")

    grid_w = cell_w * taille
    grid_h = cell_h * taille

    print(f"    [audioreactif] mode={mode}, préparation grille…")
    mini = config.WORK_DIR / f"ar_mini_{taille}_{int(debut_source)}.mp4"
    fabriquer_segment(video_source, mini, debut_source, duree, cell_w, cell_h)
    grille = config.WORK_DIR / f"ar_grille_{taille}_{int(debut_source)}.mp4"
    paths = [mini] * (taille * taille)
    if (taille * taille) > 256:
        assembler_grille_par_lignes(taille, paths, duree, grille, 0, 0)
    else:
        assembler_grille(taille, paths, duree, grille, 0, 0)

    out_path = config.WORK_DIR / f"ar_out_{taille}_{int(debut_source)}.mp4"

    def _audio_par_defaut():
        fichiers = config.AUDIO.get("fichiers", []) or []
        for f0 in fichiers:
            chemin = f0 if isinstance(f0, str) else f0.get("fichier")
            if chemin and Path(chemin).exists():
                return chemin
        return None

    if mode == "rms":
        # Vrai audio-réactif : on analyse le RMS du fichier audio par
        # tranches, puis on construit une expression zoompan qui mappe
        # le numéro de frame à la valeur RMS correspondante.
        fichier_audio = cfg_ar.get("fichier") or _audio_par_defaut()
        if not fichier_audio or not Path(fichier_audio).exists():
            print(f"ERREUR audioreactif/rms : fichier audio introuvable : {fichier_audio}")
            sys.exit(1)
        intensite = float(cfg_ar.get("intensite", 0.3))
        rotation = float(cfg_ar.get("rotation", 0.0))
        # Buckets : par défaut 5 par seconde (= 200 ms de résolution),
        # suffisant pour suivre le rythme sans expression trop grosse.
        n_buckets = int(cfg_ar.get("buckets") or max(10, int(duree * 5)))
        puissance = float(cfg_ar.get("puissance", 2.0))
        percentile = float(cfg_ar.get("percentile", 0.9))

        print(f"    [audioreactif/rms] analyse {fichier_audio}, "
              f"{n_buckets} buckets, intensite={intensite}, "
              f"puissance={puissance}")
        rms = _rms_buckets(fichier_audio, duree, n_buckets,
                            percentile=percentile, puissance=puissance)

        # Construit l'expression imbriquée : à chaque seuil de frame, le
        # zoom correspond à 1 + intensite * rms[i].
        # On part de la fin (dernière valeur) et on enroule.
        bucket_frames = (30.0 * duree) / n_buckets
        expr = f"1+{intensite}*{rms[-1]:.4f}"
        for i in range(n_buckets - 2, -1, -1):
            seuil = (i + 1) * bucket_frames
            expr = (f"if(lt(on,{seuil:.0f}),"
                    f"1+{intensite}*{rms[i]:.4f},{expr})")

        fc = f"[0:v]zoompan=z='{expr}':d=1:s={grid_w}x{grid_h}:fps=30[zoomed]"
        if rotation != 0:
            fc += f";[zoomed]rotate={rotation}*t:c=black:ow={grid_w}:oh={grid_h}[v]"
            map_label = "[v]"
        else:
            map_label = "[zoomed]"

        run([
            "ffmpeg", "-y", "-i", str(grille),
            "-filter_complex", fc,
            "-map", map_label,
            "-t", str(duree),
            *config.args_encodage(),
            "-pix_fmt", "yuv420p",
            str(out_path)
        ])
    elif mode == "pulse":
        bpm = float(cfg_ar.get("bpm", 120))
        intensite = float(cfg_ar.get("intensite", 0.2))
        rotation = float(cfg_ar.get("rotation", 0.0))
        # Période en frames (30 fps). Zoom = 1 + i*(sin(2π * on / period)+1)/2
        # `on` est le numéro de frame, plus stable que `t` dans zoompan.
        period_frames = 30.0 / (bpm / 60.0)
        z_expr = f"1+{intensite}*(sin(2*PI*on/{period_frames:.3f})+1)/2"
        print(f"    [audioreactif/pulse] bpm={bpm}, intensite={intensite}, "
              f"rotation={rotation}")
        vf_parts = [f"zoompan=z='{z_expr}':d=1:s={grid_w}x{grid_h}:fps=30"]
        if rotation != 0:
            # Rotation continue par-dessus le zoom pulsé
            vf_parts.append(
                f"rotate={rotation}*t:c=black:ow={grid_w}:oh={grid_h}"
            )
        run([
            "ffmpeg", "-y", "-i", str(grille),
            "-vf", ",".join(vf_parts),
            "-t", str(duree),
            *config.args_encodage(),
            "-pix_fmt", "yuv420p",
            str(out_path)
        ])
    else:
        # Modes overlay (waves/spectrum) : ancienne implémentation
        fichier_audio = cfg_ar.get("fichier")
        if not fichier_audio:
            fichiers = config.AUDIO.get("fichiers", []) or []
            if fichiers:
                f0 = fichiers[0]
                fichier_audio = f0 if isinstance(f0, str) else f0.get("fichier")
        if not fichier_audio or not Path(fichier_audio).exists():
            print(f"ERREUR audioreactif : fichier audio introuvable : {fichier_audio}")
            sys.exit(1)
        opacite = float(cfg_ar.get("opacite", 0.6))
        couleur = cfg_ar.get("couleur", "white")

        if mode == "spectrum":
            viz = f"showcqt=s={grid_w}x{grid_h}"
        else:
            viz = f"showwaves=s={grid_w}x{grid_h}:mode=cline:colors={couleur}"

        run([
            "ffmpeg", "-y",
            "-i", str(grille),
            "-i", str(fichier_audio),
            "-filter_complex",
                f"[1:a]{viz}[viz];"
                f"[0:v][viz]blend=all_mode=screen:all_opacity={opacite}[v]",
            "-map", "[v]", "-t", str(duree),
            *config.args_encodage(),
            "-pix_fmt", "yuv420p",
            str(out_path)
        ])

    if pad_x > 0 or pad_y > 0:
        run([
            "ffmpeg", "-y", "-i", str(out_path),
            "-vf", f"pad={config.FINAL_W}:{config.FINAL_H}:{pad_x}:{pad_y}:black,setsar=1",
            *config.args_encodage(),
            "-pix_fmt", "yuv420p",
            str(output_path)
        ])
    else:
        out_path.rename(output_path)


# ============================================================
# Datamosh "soft" : tmix + bruit + compression agressive
# ============================================================
def creer_effet_datamosh(video_source, taille, debut_source, duree,
                          output_path, cell_w, cell_h, pad_x, pad_y):
    """
    Glitch authentique sans manipulation bit-level :
      - tmix : moyenne pondérée des N dernières frames (smearing temporel)
      - noise : grain coloré qui suit le smearing
      - encode libx264 -b:v très bas + GOP énorme → blocking + artifacts
    """
    cfg_d = config.EFFETS.get("datamosh", {})
    traine  = max(2, int(cfg_d.get("traine", 8)))
    bruit   = float(cfg_d.get("bruit", 15))
    bitrate = str(cfg_d.get("bitrate", "300k"))

    print(f"    [datamosh] traine={traine}, bruit={bruit}, bitrate={bitrate}")
    mini = config.WORK_DIR / f"dm_mini_{taille}_{int(debut_source)}.mp4"
    fabriquer_segment(video_source, mini, debut_source, duree, cell_w, cell_h)
    grille = config.WORK_DIR / f"dm_grille_{taille}_{int(debut_source)}.mp4"
    paths = [mini] * (taille * taille)
    if (taille * taille) > 256:
        assembler_grille_par_lignes(taille, paths, duree, grille, 0, 0)
    else:
        assembler_grille(taille, paths, duree, grille, 0, 0)

    # Poids dégressifs (la frame courante est la plus faible → traînée
    # dominée par les frames précédentes).
    weights = " ".join(str(2 ** max(0, traine - i - 1)) for i in range(traine))
    vf = f"tmix=frames={traine}:weights={weights}"
    if bruit > 0:
        vf += f",noise=alls={bruit}:allf=t+u"

    out_path = config.WORK_DIR / f"dm_out_{taille}_{int(debut_source)}.mp4"
    # Encode forcé libx264 pour pouvoir spécifier le bitrate bas + GOP long.
    run([
        "ffmpeg", "-y", "-i", str(grille),
        "-vf", vf,
        "-c:v", "libx264", "-b:v", bitrate,
        "-g", "999", "-bf", "0", "-keyint_min", "999",
        "-preset", "fast",
        "-pix_fmt", "yuv420p",
        "-t", str(duree),
        str(out_path)
    ])

    if pad_x > 0 or pad_y > 0:
        run([
            "ffmpeg", "-y", "-i", str(out_path),
            "-vf", f"pad={config.FINAL_W}:{config.FINAL_H}:{pad_x}:{pad_y}:black,setsar=1",
            *config.args_encodage(),
            "-pix_fmt", "yuv420p",
            str(output_path)
        ])
    else:
        out_path.rename(output_path)


# ============================================================
# Rotation + zoom statique de la grille
# ============================================================
def creer_effet_rotation(video_source, taille, debut_source, duree,
                          output_path, cell_w, cell_h, pad_x, pad_y):
    """
    La grille pleine est zoomée (constant) puis tourne autour du centre.
    Le zoom constant sert à cacher les bords noirs qui apparaîtraient
    sinon dans les coins quand on tourne (un cercle inscrit dans le
    carré ne couvre pas les coins).

    Paramètres :
      - vitesse  : vitesse angulaire en rad/s (0.1 = lent, 1.0 = rapide)
      - zoom     : facteur de zoom statique (1.0 = aucun, 1.5 = +50%)
      - sens     : "horaire" ou "anti" (anti-horaire)
    """
    cfg_r = config.EFFETS.get("rotation", {})
    vitesse = float(cfg_r.get("vitesse", 0.2))
    zoom = float(cfg_r.get("zoom", 1.4))
    sens = cfg_r.get("sens", "horaire")

    signe = -1 if sens == "anti" else 1

    grid_w = cell_w * taille
    grid_h = cell_h * taille

    print(f"    [rotation] vitesse={vitesse} rad/s, zoom={zoom}, sens={sens}")
    mini = config.WORK_DIR / f"rot_mini_{taille}_{int(debut_source)}.mp4"
    fabriquer_segment(video_source, mini, debut_source, duree, cell_w, cell_h)
    grille = config.WORK_DIR / f"rot_grille_{taille}_{int(debut_source)}.mp4"
    paths = [mini] * (taille * taille)
    if (taille * taille) > 256:
        assembler_grille_par_lignes(taille, paths, duree, grille, 0, 0)
    else:
        assembler_grille(taille, paths, duree, grille, 0, 0)

    vf = (f"scale=iw*{zoom}:ih*{zoom},"
          f"crop={grid_w}:{grid_h},"
          f"rotate={signe}*{vitesse}*t:c=black:ow={grid_w}:oh={grid_h}")

    out_path = config.WORK_DIR / f"rot_out_{taille}_{int(debut_source)}.mp4"
    run([
        "ffmpeg", "-y", "-i", str(grille),
        "-vf", vf,
        "-t", str(duree),
        *config.args_encodage(),
        "-pix_fmt", "yuv420p",
        str(out_path)
    ])

    if pad_x > 0 or pad_y > 0:
        run([
            "ffmpeg", "-y", "-i", str(out_path),
            "-vf", f"pad={config.FINAL_W}:{config.FINAL_H}:{pad_x}:{pad_y}:black,setsar=1",
            *config.args_encodage(),
            "-pix_fmt", "yuv420p",
            str(output_path)
        ])
    else:
        out_path.rename(output_path)


# ============================================================
# LSD audio-réactif : params modulés par le RMS audio
# ============================================================
def creer_effet_lsd_audio(video_source, taille, debut_source, duree,
                           output_path, cell_w, cell_h, pad_x, pad_y):
    """
    LSD vraiment audio-réactif : la VIDÉO elle-même est déformée plus ou
    moins fort selon le RMS de la piste audio. Un seul appel geq sur
    toute la durée, avec une expression `st(0, facteur_T); X+amp*ld(0)*...`
    qui multiplie l'amplitude des ondes par un facteur dépendant de T.

    Quand l'audio est silencieux, le facteur ≈ 0 → ondes plates → vidéo
    quasi-intacte. Quand ça monte, l'amplitude réelle des ondes monte
    proportionnellement → la vidéo se déforme. Pas de blend, pas de
    calque, pas de bucket distinct : une seule passe continue.
    """
    cfg_la = config.EFFETS.get("lsd_audio", {})
    fichiers = config.AUDIO.get("fichiers", []) or []
    fichier_audio = cfg_la.get("fichier")
    if not fichier_audio:
        for f0 in fichiers:
            c = f0 if isinstance(f0, str) else f0.get("fichier")
            if c and Path(c).exists():
                fichier_audio = c
                break
    if not fichier_audio or not Path(fichier_audio).exists():
        print(f"ERREUR lsd_audio : fichier audio introuvable : {fichier_audio}")
        sys.exit(1)

    n_buckets = int(cfg_la.get("buckets") or max(4, int(duree * 4)))
    puissance = float(cfg_la.get("puissance", 2.0))
    percentile = float(cfg_la.get("percentile", 0.9))
    lissage = int(cfg_la.get("lissage", 2))
    debut_audio = parser_timestamp(cfg_la.get("debut", 0)) or 0
    # Facteur d'amplitude : silence → repos_amplitude, pic → 1.0+mod_amp
    repos_amp = float(cfg_la.get("repos_amplitude", 0.05))
    mod_amp = float(cfg_la.get("mod_amplitude", 1.5))
    mod_vit = float(cfg_la.get("mod_vitesse", 0.5))
    mod_sat = float(cfg_la.get("mod_saturation", 0.5))
    mod_aber = float(cfg_la.get("mod_aberration", 1.0))

    cfg_lsd_base = dict(config.EFFETS.get("lsd", {}))
    amp_base = float(cfg_lsd_base.get("amplitude_onde", 120))
    vit_base = float(cfg_lsd_base.get("vitesse_onde", 0.4))
    sat_base = float(cfg_lsd_base.get("saturation", 2.5))
    aber_base = int(cfg_lsd_base.get("aberration", 8))
    teinte_base = float(cfg_lsd_base.get("teinte", 240))
    freq_base = float(cfg_lsd_base.get("frequence_onde", 8))
    bruit_base = float(cfg_lsd_base.get("bruit", 30))
    lignes_force = float(cfg_lsd_base.get("lignes_force", 0.7))
    lignes_mode = cfg_lsd_base.get("lignes_mode", "edges")

    rms = _rms_buckets(fichier_audio, duree, n_buckets,
                       debut=debut_audio,
                       percentile=percentile, puissance=puissance,
                       lissage=lissage)
    duree_bucket = duree / n_buckets

    print(f"    [lsd_audio] {n_buckets} buckets × {duree_bucket:.2f}s, "
          f"audio={fichier_audio} @ {debut_audio}s, "
          f"repos={repos_amp}, mod_amp={mod_amp}")

    # 1) Grille normale (vidéo source en N×N cellules)
    mini = config.WORK_DIR / f"lsda_mini_{taille}_{int(debut_source)}.mp4"
    fabriquer_segment(video_source, mini, debut_source, duree, cell_w, cell_h)
    grille = config.WORK_DIR / f"lsda_grille_{taille}_{int(debut_source)}.mp4"
    paths = [mini] * (taille * taille)
    if (taille * taille) > 256:
        assembler_grille_par_lignes(taille, paths, duree, grille, 0, 0)
    else:
        assembler_grille(taille, paths, duree, grille, 0, 0)

    # 2) Expression du facteur d'amplitude en fonction de T :
    #    facteur(silence=0) = repos_amp ; facteur(pic=1) = repos_amp + mod_amp
    def _fact(r):
        return repos_amp + mod_amp * r
    fact_expr = f"{_fact(rms[-1]):.4f}"
    for i in range(n_buckets - 2, -1, -1):
        t_seuil = (i + 1) * duree_bucket
        fact_expr = (f"if(lt(T\\,{t_seuil:.3f})\\,"
                     f"{_fact(rms[i]):.4f}\\,{fact_expr})")

    # 3) Construire l'expression geq avec amplitude = amp_base × ld(0)
    grid_w = cell_w * taille
    grid_h = cell_h * taille
    cx = grid_w / 2.0
    cy = grid_h / 2.0
    f_h = max(1, grid_h / freq_base)
    f_w = max(1, grid_w / max(0.7, freq_base * 0.7))
    f_diag = max(1, grid_h / max(0.7, freq_base * 1.3))
    f_radial = max(1, min(grid_w, grid_h) / max(0.5, freq_base * 1.5))
    dist = f"sqrt((X-{cx:.1f})*(X-{cx:.1f})+(Y-{cy:.1f})*(Y-{cy:.1f}))"
    # amp[T] = amp_base × facteur(T). On stocke `facteur` dans le slot 0.
    init = f"st(0\\,{fact_expr})"
    A = f"{amp_base:.2f}*ld(0)"

    onde1_x = f"({A})*sin(Y/{f_h:.3f}+T*{vit_base*6.28:.3f})"
    onde1_mod = f"(0.5+0.5*sin(X/{max(1,grid_w/3):.0f}+T*{vit_base*2.5:.3f}))"
    onde2_x = f"({A}/2)*sin((X+Y)/{f_diag:.3f}+T*{vit_base*3.5:.3f})"
    onde2_y = f"({A}/2)*cos((X-Y)/{f_diag:.3f}+T*{vit_base*5.2:.3f})"
    onde1_y = f"({A}/3)*cos(X/{f_w:.3f}+T*{vit_base*4.5:.3f})"
    radial_amp = f"({A}/2.5)*sin({dist}/{f_radial:.3f}+T*{vit_base*3.0:.3f})"
    radial_x = f"({radial_amp})*(X-{cx:.1f})/max({dist}\\,1)"
    radial_y = f"({radial_amp})*(Y-{cy:.1f})/max({dist}\\,1)"

    expr_x = f"{init}\\;X+{onde1_x}*{onde1_mod}+{onde2_x}+{radial_x}"
    expr_y = f"{init}\\;Y+{onde1_y}+{onde2_y}+{radial_y}"

    # 4) Pipeline complet : hue (couleur), rgbashift (aberration), geq
    sat_eff = sat_base * (1 + mod_sat * 0.6)
    aber_eff = max(1, int(aber_base * (1 + mod_aber * 0.6)))
    parts = [
        f"[0:v]hue=h={teinte_base}:s={sat_eff}[col]",
        (f"[col]rgbashift=rh={aber_eff}:bh=-{aber_eff}"
         f":rv=-{aber_eff//2}:bv={aber_eff//2}[abr]"),
    ]
    if bruit_base > 0:
        parts.append(f"[abr]noise=alls={bruit_base}:allf=t+u[abr2]")
        last_label = "abr2"
    else:
        last_label = "abr"
    parts.append(
        f"[{last_label}]geq="
        f"r='r({expr_x}\\,{expr_y})':"
        f"g='g({expr_x}\\,{expr_y})':"
        f"b='b({expr_x}\\,{expr_y})'[wave]"
    )
    if lignes_force > 0:
        edge_mode = "wires" if lignes_mode == "wires" else "canny"
        parts.append(
            f"[wave]split[w1][w2];"
            f"[w2]edgedetect=mode={edge_mode}:low=0.1:high=0.4,"
            f"hue=h={teinte_base}:s=3,eq=brightness={lignes_force*0.3}[edges];"
            f"[w1][edges]blend=all_mode=screen:all_opacity={lignes_force}[v]"
        )
    else:
        parts.append("[wave]copy[v]")
    fc = ";".join(parts)

    fusionne = config.WORK_DIR / f"lsda_out_{taille}_{int(debut_source)}.mp4"
    run([
        "ffmpeg", "-y", "-i", str(grille),
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
# Rampe avec crossfade entre étages
# ============================================================
def creer_effet_rampe_fade(video_source, taille, debut_source, duree,
                            output_path, cell_w, cell_h, pad_x, pad_y,
                            etages, duree_par, crossfade):
    """
    Rend chaque étage de la rampe à sa propre taille de grille, puis
    enchaîne avec un fondu xfade en cascade entre étages successifs.
    """
    from .grille import calculer_taille_cellule, construire_sous_segment

    print(f"    [rampe_fade] {len(etages)} étages, {duree_par}s chacun, "
          f"crossfade={crossfade}s")
    segments = []
    for idx, t_etage in enumerate(etages):
        cw, ch, _, _, px, py = calculer_taille_cellule(t_etage)
        seg = config.WORK_DIR / f"rfade_{idx:02d}_t{t_etage}_{int(debut_source)}.mp4"
        # On rend chaque étage comme une grille "neutre" (toutes cellules =
        # source, pas d'anomalie). Le pad est inclus pour que tous les
        # segments aient les dimensions FINAL_W × FINAL_H, condition
        # nécessaire pour xfade.
        construire_sous_segment(
            video_source, None, t_etage, debut_source, duree_par, seg,
            cw, ch, px, py, set(), {}
        )
        segments.append(seg)

    # Cascade xfade : à chaque étage suivant, on overlap `crossfade` secondes.
    # offset_n = n * (duree_par - crossfade)
    inputs = []
    for s in segments:
        inputs += ["-i", str(s)]
    fc_parts = []
    last = "0:v"
    for i in range(1, len(segments)):
        offset = i * (duree_par - crossfade)
        label = f"x{i}"
        fc_parts.append(
            f"[{last}][{i}:v]xfade=transition=fade:"
            f"duration={crossfade}:offset={offset:.3f}[{label}]"
        )
        last = label

    run([
        "ffmpeg", "-y",
        *inputs,
        "-filter_complex", ";".join(fc_parts),
        "-map", f"[{last}]",
        "-t", str(duree),
        *config.args_encodage(),
        "-pix_fmt", "yuv420p",
        str(output_path)
    ])


# ============================================================
# Dispatcher
# ============================================================
def jouer_effet(nom_effet, video_source, video_anomalie_externe, taille,
                 debut_source, duree, output_path, cell_w, cell_h, pad_x, pad_y,
                 directives=None):
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
    elif nom_effet == "transition_smooth":
        if video_anomalie_externe is None:
            print("ERREUR : effet 'transition_smooth' nécessite input_anomalie")
            sys.exit(1)
        # video_source = la vidéo de fond (B), video_anomalie_externe = vidéo
        # qui apparaît cellule par cellule (A).
        creer_effet_transition_smooth(video_source, video_anomalie_externe,
                                        taille, debut_source, duree,
                                        output_path, cell_w, cell_h,
                                        pad_x, pad_y)
    elif nom_effet == "kaleidoscope":
        creer_effet_kaleidoscope(video_source, taille, debut_source, duree,
                                  output_path, cell_w, cell_h, pad_x, pad_y)
    elif nom_effet == "audioreactif":
        creer_effet_audioreactif(video_source, taille, debut_source, duree,
                                  output_path, cell_w, cell_h, pad_x, pad_y)
    elif nom_effet == "datamosh":
        creer_effet_datamosh(video_source, taille, debut_source, duree,
                              output_path, cell_w, cell_h, pad_x, pad_y)
    elif nom_effet == "rotation":
        creer_effet_rotation(video_source, taille, debut_source, duree,
                              output_path, cell_w, cell_h, pad_x, pad_y)
    elif nom_effet == "lsd_audio":
        creer_effet_lsd_audio(video_source, taille, debut_source, duree,
                               output_path, cell_w, cell_h, pad_x, pad_y)
    elif nom_effet == "rampe_fade":
        if not directives:
            print("ERREUR : rampe_fade nécessite des directives (paramètres internes)")
            sys.exit(1)
        creer_effet_rampe_fade(
            video_source, taille, debut_source, duree, output_path,
            cell_w, cell_h, pad_x, pad_y,
            directives["_rampe_etages"],
            directives["_rampe_duree_par"],
            directives["_rampe_crossfade"],
        )
    else:
        print(f"ERREUR : effet inconnu '{nom_effet}'")
        sys.exit(1)
