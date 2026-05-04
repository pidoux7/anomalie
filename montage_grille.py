"""
Montage vidéo : grilles progressives 1 → 4 → ... → MAX_CASES.
Toutes les options dans config.yaml.

Système de masques : les cellules anomales dessinent un motif fourni en image
(ex : un "7", une lettre, une forme).

Usage :
    python3 montage_grille.py
    python3 montage_grille.py ma_config.yaml

Dépendances :
    pip3 install pyyaml pillow
"""

import subprocess
import sys
import random
import math
from pathlib import Path

try:
    import yaml
except ImportError:
    print("ERREUR : pip3 install pyyaml")
    sys.exit(1)

try:
    from PIL import Image
    PIL_OK = True
except ImportError:
    PIL_OK = False


# ============ CHARGEMENT CONFIG ============
config_path = sys.argv[1] if len(sys.argv) > 1 else "config.yaml"
if not Path(config_path).exists():
    print(f"Config introuvable : {config_path}")
    sys.exit(1)

with open(config_path, "r") as f:
    cfg = yaml.safe_load(f)

INPUT_VIDEO    = cfg["input_video"]
INPUT_ANOMALIE = cfg.get("input_anomalie")
OUTPUT_VIDEO   = cfg["output_video"]
FINAL_W        = cfg["final_w"]
FINAL_H        = cfg["final_h"]
CELLULES_CARREES = cfg["cellules_carrees"]
DUREE_TOTALE   = cfg.get("duree_totale", 240)
DUREE_MAX      = cfg.get("duree_max", 600)
MAX_CASES      = cfg["max_cases"]
MODE_DUREE     = cfg.get("mode_duree", "duree_max")
N_BOUCLES      = cfg.get("n_boucles_par_phase", 1)
FIN_PARTIELLE  = cfg.get("fin_partielle", "etendre")
MODE_ANOMALIE  = cfg["mode_anomalie"]
CHANGEMENT_ANOMALIE   = cfg["changement_anomalie"]
INTERVALLE_CHANGEMENT = cfg["intervalle_changement"]
MODE_QUANTITE         = cfg["mode_quantite"]
NB_ANOMALIES_FIXE     = cfg["nb_anomalies_fixe"]
RATIO_PROPORTIONNEL   = cfg["ratio_proportionnel"]
PRESET = cfg["preset_x264"]
CRF    = str(cfg["crf"])

ANOMALIES = [
    (f["nom"], f["filtre"])
    for f in cfg["filtres_anomalies"]
    if f.get("actif", True)
]

MSK = cfg.get("masques", {"actif": False})
MASQUES_ACTIFS = MSK.get("actif", False)
if MASQUES_ACTIFS and not PIL_OK:
    print("ERREUR : système de masques activé mais Pillow non installé.")
    print("  Installe avec : pip3 install pillow")
    sys.exit(1)

# Config effets spéciaux
EFFETS = cfg.get("effets", {"actif": False})
EFFETS_ACTIFS = EFFETS.get("actif", False)
EFFETS_LISTE = EFFETS.get("liste", []) if EFFETS_ACTIFS else []

# Audio config
AUDIO = cfg.get("audio", {"actif": False})
AUDIO_ACTIF = AUDIO.get("actif", False)

# Validation immédiate des fichiers audio (avant la longue génération vidéo)
if AUDIO_ACTIF:
    fichiers_audio = AUDIO.get("fichiers", [])
    for entree in fichiers_audio:
        chemin = entree if isinstance(entree, str) else entree.get("fichier")
        if not chemin or not Path(chemin).exists():
            print(f"ERREUR audio (vérification au démarrage) : fichier introuvable : {chemin}")
            print("  Corrige la liste 'fichiers' dans config.yaml avant de relancer.")
            sys.exit(1)

# Validation des fichiers vidéo nécessaires aux effets
if EFFETS_ACTIFS:
    if "mosaique" in EFFETS_LISTE:
        carte = EFFETS.get("mosaique", {}).get("video_carte")
        if not carte or not Path(carte).exists():
            print(f"ERREUR effets : video_carte introuvable : {carte}")
            sys.exit(1)
    if "mur_moniteurs" in EFFETS_LISTE:
        videos_mur = EFFETS.get("mur_moniteurs", {}).get("videos", [])
        for v in videos_mur:
            if not Path(v).exists():
                print(f"ERREUR effets : vidéo mur introuvable : {v}")
                sys.exit(1)
        if not videos_mur and not EFFETS.get("mur_moniteurs", {}).get("inclure_source", True):
            print("ERREUR effets : mur_moniteurs sans vidéos (cocher inclure_source ou ajouter des vidéos)")
            sys.exit(1)

WORK_DIR = Path("travail_montage")
WORK_DIR.mkdir(exist_ok=True)


# ============ HELPERS ============
def pair_inf(n):
    return (n // 2) * 2


def calculer_phases(max_cases):
    phases = []
    t = 1
    while t * t <= max_cases:
        phases.append(t)
        t *= 2
    return phases


PHASES = calculer_phases(MAX_CASES)
diviseur = max(2, PHASES[-1] * 2)
FINAL_W = (FINAL_W // diviseur) * diviseur
FINAL_H = (FINAL_H // diviseur) * diviseur


def _duree_video(path):
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True
    )
    return float(r.stdout.strip())


# ============================================================
# CALCUL DU PLAN : liste de séquences à générer
# Chaque entrée du plan = (numero_phase, taille_grille, duree_seq)
# ============================================================
if not Path(INPUT_VIDEO).exists():
    print(f"Vidéo introuvable : {INPUT_VIDEO}")
    sys.exit(1)

DUREE_SOURCE = _duree_video(INPUT_VIDEO)


def construire_plan():
    """
    Retourne la liste des séquences à générer sous forme :
       [(num_phase, taille, duree_seq), ...]
    """
    plan = []

    if MODE_DUREE == "duree_max":
        duree_phase = DUREE_SOURCE * len(PHASES)  # 6 séquences × durée vidéo
        cumul = 0
        num_phase = 1
        while cumul < DUREE_MAX:
            duree_restante = DUREE_MAX - cumul
            if duree_restante >= duree_phase:
                # On peut faire une phase complète
                for taille in PHASES:
                    plan.append((num_phase, taille, DUREE_SOURCE))
                cumul += duree_phase
                num_phase += 1
            else:
                # Phase partielle nécessaire selon FIN_PARTIELLE
                if FIN_PARTIELLE == "tronquer":
                    break
                elif FIN_PARTIELLE == "depasser":
                    for taille in PHASES:
                        plan.append((num_phase, taille, DUREE_SOURCE))
                    cumul += duree_phase
                    num_phase += 1
                    break
                else:  # "etendre" : on remplit la phase partielle au plus juste
                    for taille in PHASES:
                        if duree_restante <= 0:
                            break
                        d = min(DUREE_SOURCE, duree_restante)
                        plan.append((num_phase, taille, d))
                        duree_restante -= d
                    break
        return plan

    # Modes simples (1 seule phase)
    if MODE_DUREE == "boucle":
        duree_seq = DUREE_SOURCE
    elif MODE_DUREE == "n_boucles":
        duree_seq = DUREE_SOURCE * N_BOUCLES
    else:  # "total"
        duree_seq = DUREE_TOTALE / len(PHASES)

    for taille in PHASES:
        plan.append((1, taille, duree_seq))
    return plan


PLAN = construire_plan()
NB_PHASES_PLAN = max(p[0] for p in PLAN)
DUREE_TOTALE = sum(p[2] for p in PLAN)

print(f"Config       : {config_path}")
print(f"Résolution   : {FINAL_W}x{FINAL_H}")
print(f"Mode durée   : {MODE_DUREE}"
      + (f" (cible {DUREE_MAX}s, fin={FIN_PARTIELLE})" if MODE_DUREE == "duree_max" else ""))
print(f"Source       : {DUREE_SOURCE:.2f}s")
print(f"Plan         : {NB_PHASES_PLAN} phase(s), {len(PLAN)} séquences")
print(f"Durée finale : {DUREE_TOTALE:.1f}s ({DUREE_TOTALE/60:.2f} min)")
print(f"Séquences    : {[t*t for _, t, _ in PLAN[:10]]}{'...' if len(PLAN) > 10 else ''}")
print(f"Mode anomalie     : {MODE_ANOMALIE} | changement: {CHANGEMENT_ANOMALIE}")
print(f"Filtres actifs    : {len(ANOMALIES)}")
print(f"Masques activés   : {MASQUES_ACTIFS}"
      + (f" ({MSK.get('portee')})" if MASQUES_ACTIFS else ""))
print(f"Effets activés    : {EFFETS_ACTIFS}"
      + (f" ({', '.join(EFFETS_LISTE)})" if EFFETS_ACTIFS else ""))
print(f"Audio activé      : {AUDIO_ACTIF}"
      + (f" ({AUDIO.get('comportement')}, source={AUDIO.get('audio_source')})" if AUDIO_ACTIF else ""))


def run(cmd):
    print(f">>> {' '.join(cmd[:4])} ...")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print("ERREUR ffmpeg :", result.stderr[-1000:])
        sys.exit(1)


def get_duration(path):
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True
    )
    return float(result.stdout.strip())


def calculer_taille_cellule(taille):
    if CELLULES_CARREES:
        cell_size = pair_inf(min(FINAL_W, FINAL_H) // taille)
        cell_w = cell_h = cell_size
        grid_w = cell_w * taille
        grid_h = cell_h * taille
        pad_x = (FINAL_W - grid_w) // 2
        pad_y = (FINAL_H - grid_h) // 2
    else:
        cell_w = pair_inf(FINAL_W // taille)
        cell_h = pair_inf(FINAL_H // taille)
        grid_w = cell_w * taille
        grid_h = cell_h * taille
        pad_x = pad_y = 0
    return cell_w, cell_h, grid_w, grid_h, pad_x, pad_y


# ============ MASQUES ============
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
    if not MASQUES_ACTIFS:
        return False
    n_cellules = taille * taille
    portee = MSK.get("portee", "derniere")
    if portee == "derniere":
        return taille == PHASES[-1]
    elif portee == "minimum":
        return n_cellules >= MSK.get("phase_minimum", 256)
    elif portee == "specifique":
        return n_cellules in MSK.get("phases_specifiques", [])
    return False


def positions_pour_apparition(positions_finales, fraction):
    n = max(1, int(round(len(positions_finales) * fraction)))
    rng = random.Random(42)
    ordre = list(positions_finales)
    rng.shuffle(ordre)
    return set(ordre[:n])


def nb_anomalies_pour_grille(n_cellules):
    if n_cellules == 1:
        return 0
    if MODE_QUANTITE == "fixe":
        return min(NB_ANOMALIES_FIXE, n_cellules)
    return max(1, int(round(n_cellules * RATIO_PROPORTIONNEL)))


def choisir_filtre():
    return random.choice(ANOMALIES)


def choisir_anomalie():
    if MODE_ANOMALIE == "filtres":
        return ("filtre", choisir_filtre())
    elif MODE_ANOMALIE == "video":
        return ("video", None)
    else:
        return ("filtre", choisir_filtre()) if random.random() < 0.5 else ("video", None)


# ============ FFMPEG OPS ============
def preparer_video(input_path, output_path, duree, w, h):
    run([
        "ffmpeg", "-y",
        "-stream_loop", "-1",
        "-i", str(input_path),
        "-t", str(duree),
        "-vf", f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}",
        "-r", "30",
        "-c:v", "libx264", "-preset", PRESET, "-crf", CRF,
        "-pix_fmt", "yuv420p", "-an",
        str(output_path)
    ])


def fabriquer_segment(source, dest, debut, duree, cell_w, cell_h, filtre=None):
    parts = []
    if CELLULES_CARREES:
        parts.append("crop='min(iw,ih)':'min(iw,ih)'")
    parts.append(f"scale={cell_w}:{cell_h}")
    if filtre:
        parts.append(filtre)
        # Re-scale APRÈS le filtre pour garantir des dimensions exactes
        # (certains filtres comme pixelise modifient la taille via arrondi)
        parts.append(f"scale={cell_w}:{cell_h}")
    # Force le SAR (ratio pixel) à 1:1 pour éviter les conflits de hauteur
    # entre cellules normales et cellules avec filtre lors du hstack
    parts.append("setsar=1")
    vf = ",".join(parts)
    run([
        "ffmpeg", "-y",
        "-ss", str(debut),
        "-i", str(source),
        "-t", str(duree),
        "-vf", vf,
        "-c:v", "libx264", "-preset", PRESET, "-crf", "22",
        "-pix_fmt", "yuv420p", "-an",
        str(dest)
    ])


def concat_segments_simple(segments, output_path):
    liste = WORK_DIR / f"liste_{random.randint(0, 999999)}.txt"
    with open(liste, "w") as f:
        for seg in segments:
            f.write(f"file '{Path(seg).resolve()}'\n")
    run([
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0",
        "-i", str(liste),
        "-c", "copy",
        str(output_path)
    ])


# ============ ASSEMBLAGE GRILLE ============
def assembler_grille(taille, paths_cellules, duree, output_path, pad_x, pad_y):
    n_cellules = taille * taille
    if n_cellules > 256:
        return assembler_grille_par_lignes(taille, paths_cellules, duree,
                                            output_path, pad_x, pad_y)
    inputs = []
    for p in paths_cellules:
        inputs.extend(["-i", str(p)])
    layout_parts = []
    for i in range(n_cellules):
        col = i % taille
        row = i // taille
        x = "0" if col == 0 else "+".join([f"w{k}" for k in range(col)])
        if row == 0:
            y = "0"
        else:
            y = "+".join([f"h{k * taille}" for k in range(row)])
        layout_parts.append(f"{x}_{y}")
    layout = "|".join(layout_parts)
    if CELLULES_CARREES and (pad_x > 0 or pad_y > 0):
        fc = (f"xstack=inputs={n_cellules}:layout={layout}[grid];"
              f"[grid]pad={FINAL_W}:{FINAL_H}:{pad_x}:{pad_y}:black[v]")
    else:
        fc = f"xstack=inputs={n_cellules}:layout={layout}[v]"
    run(["ffmpeg", "-y"] + inputs + [
        "-filter_complex", fc,
        "-map", "[v]",
        "-t", str(duree),
        "-c:v", "libx264", "-preset", PRESET, "-crf", CRF,
        "-pix_fmt", "yuv420p",
        str(output_path)
    ])


def assembler_grille_par_lignes(taille, paths, duree, output_path, pad_x, pad_y):
    print(f"  → Assemblage par lignes ({taille} lignes)")
    lignes = []
    for r in range(taille):
        ligne_path = WORK_DIR / f"ligne_{taille}_{r}_{random.randint(0, 99999)}.mp4"
        ligne_paths = paths[r * taille:(r + 1) * taille]
        ligne_inputs = []
        for p in ligne_paths:
            ligne_inputs.extend(["-i", str(p)])
        run(["ffmpeg", "-y"] + ligne_inputs + [
            "-filter_complex", f"hstack=inputs={taille}[v]",
            "-map", "[v]", "-t", str(duree),
            "-c:v", "libx264", "-preset", PRESET, "-crf", "22",
            "-pix_fmt", "yuv420p",
            str(ligne_path)
        ])
        lignes.append(str(ligne_path))
    final_inputs = []
    for l in lignes:
        final_inputs.extend(["-i", l])
    if CELLULES_CARREES and (pad_x > 0 or pad_y > 0):
        fc = (f"vstack=inputs={taille}[grid];"
              f"[grid]pad={FINAL_W}:{FINAL_H}:{pad_x}:{pad_y}:black[v]")
    else:
        fc = f"vstack=inputs={taille}[v]"
    run(["ffmpeg", "-y"] + final_inputs + [
        "-filter_complex", fc,
        "-map", "[v]", "-t", str(duree),
        "-c:v", "libx264", "-preset", PRESET, "-crf", CRF,
        "-pix_fmt", "yuv420p",
        str(output_path)
    ])


# ============ CONSTRUCTION D'UN SOUS-SEGMENT ============
def construire_sous_segment(video_normale, video_anomalie_externe,
                              taille, debut, duree, output_path,
                              cell_w, cell_h, pad_x, pad_y,
                              positions_anomalies, anomalies_par_pos):
    n_cellules = taille * taille

    if taille == 1:
        # Phase 1×1 : on combine extraction + pad en UNE seule commande ffmpeg
        # pour éviter les soucis de timestamps liés au double-encodage.
        # On utilise un seek précis (-ss APRÈS -i) pour éviter toute imprécision.
        if CELLULES_CARREES and (pad_x > 0 or pad_y > 0):
            vf_parts = []
            vf_parts.append("crop='min(iw,ih)':'min(iw,ih)'")
            vf_parts.append(f"scale={cell_w}:{cell_h}")
            vf_parts.append("setsar=1")
            vf_parts.append(f"pad={FINAL_W}:{FINAL_H}:{pad_x}:{pad_y}:black")
            vf = ",".join(vf_parts)
            run([
                "ffmpeg", "-y",
                "-i", str(video_normale),
                "-ss", str(debut),
                "-t", str(duree),
                "-vf", vf,
                "-r", "30",
                "-fps_mode", "cfr",
                "-c:v", "libx264", "-preset", PRESET, "-crf", CRF,
                "-pix_fmt", "yuv420p", "-an",
                str(output_path)
            ])
        else:
            fabriquer_segment(video_normale, output_path, debut, duree, cell_w, cell_h)
        return

    mini_normal = WORK_DIR / f"mini_normal_{taille}_{int(debut)}.mp4"
    fabriquer_segment(video_normale, mini_normal, debut, duree, cell_w, cell_h)

    # Optimisation : si tous les filtres sont identiques, 1 seule mini-anomalie
    filtres_uniques = set()
    for pos in positions_anomalies:
        type_a, info_a = anomalies_par_pos[pos]
        if type_a == "filtre":
            filtres_uniques.add(info_a[0])
        else:
            filtres_uniques.add("__video__")

    mini_anomalies_partagees = {}
    if len(filtres_uniques) == 1 and "__video__" not in filtres_uniques:
        nom_filtre = list(filtres_uniques)[0]
        type_a, info_a = next(iter(anomalies_par_pos.values()))
        mini_path = WORK_DIR / f"mini_anom_partage_{taille}_{int(debut)}.mp4"
        fabriquer_segment(video_normale, mini_path, debut, duree,
                           cell_w, cell_h, filtre=info_a[1])
        mini_anomalies_partagees[nom_filtre] = mini_path

    paths = []
    for i in range(n_cellules):
        if i in positions_anomalies:
            type_a, info_a = anomalies_par_pos[i]
            cle = info_a[0] if type_a == "filtre" else "__video__"
            if cle in mini_anomalies_partagees:
                paths.append(mini_anomalies_partagees[cle])
            else:
                mini = WORK_DIR / f"mini_anom_{taille}_{i}_{int(debut)}.mp4"
                if type_a == "filtre":
                    fabriquer_segment(video_normale, mini, debut, duree,
                                       cell_w, cell_h, filtre=info_a[1])
                else:
                    duree_externe = get_duration(video_anomalie_externe)
                    debut_ext = random.uniform(0, max(0, duree_externe - duree))
                    fabriquer_segment(video_anomalie_externe, mini, debut_ext,
                                       duree, cell_w, cell_h)
                paths.append(mini)
        else:
            paths.append(mini_normal)

    assembler_grille(taille, paths, duree, output_path, pad_x, pad_y)


# ============ CRÉATION D'UNE PHASE ============
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


# ============================================================
# EFFETS SPÉCIAUX
# ============================================================
def phase_utilise_effets(taille):
    """Détermine si la phase courante doit jouer des effets spéciaux."""
    if not EFFETS_ACTIFS or not EFFETS_LISTE:
        return False
    n_cellules = taille * taille
    portee = EFFETS.get("portee", "min_cases")
    if portee == "min_cases":
        return n_cellules >= EFFETS.get("seuil_cases", 1024)
    elif portee == "specifique":
        return n_cellules in EFFETS.get("phases_specifiques", [])
    return False


def creer_effet_mosaique(video_source, taille, debut_source, duree,
                          output_path, cell_w, cell_h, pad_x, pad_y):
    """
    Mosaïque vidéo : la grille forme l'image d'une autre vidéo (la "carte"),
    où chaque cellule joue la vidéo source mais sa luminosité est modulée par
    le pixel correspondant de la carte.

    Technique :
    1. Construit la grille "normale" (toutes cellules = vidéo source)
    2. Prépare la carte : redimensionne à taille×taille puis ré-agrandit en
       blocs nets (interpolation neighbor) à la taille finale de la grille
    3. Multiplie les deux ensemble (blend mode)
    """
    cfg_m = EFFETS.get("mosaique", {})
    video_carte = cfg_m["video_carte"]
    debut_carte = parser_timestamp(cfg_m.get("debut", 0)) or 0
    fin_carte = parser_timestamp(cfg_m.get("fin"))
    en_nb = cfg_m.get("carte_en_noir_blanc", False)
    intensite = float(cfg_m.get("intensite", 1.0))
    mode_fusion = cfg_m.get("mode_fusion", "multiply")

    grid_w = cell_w * taille
    grid_h = cell_h * taille

    # === 1. Grille normale : on construit une mini-vidéo répétée ===
    # On utilise UNE seule mini cellule, dupliquée 1024 fois via xstack/hstack
    print(f"    [mosaique] préparation grille de base...")
    mini_normal = WORK_DIR / f"mosaic_mini_{taille}_{int(debut_source)}.mp4"
    fabriquer_segment(video_source, mini_normal, debut_source, duree,
                       cell_w, cell_h)

    grille_base = WORK_DIR / f"mosaic_grille_{taille}_{int(debut_source)}.mp4"
    paths = [mini_normal] * (taille * taille)
    if (taille * taille) > 256:
        assembler_grille_par_lignes(taille, paths, duree, grille_base, 0, 0)
    else:
        assembler_grille(taille, paths, duree, grille_base, 0, 0)

    # === 2. Préparation de la carte : pixelisée à la résolution de la grille ===
    # On extrait, on scale à taille×taille (1 pixel = 1 cellule),
    # puis on ré-agrandit en blocs nets jusqu'à grid_w × grid_h
    print(f"    [mosaique] préparation de la carte...")
    carte_path = WORK_DIR / f"mosaic_carte_{taille}_{int(debut_source)}.mp4"

    duree_carte_extr = duree
    vf_carte = []
    vf_carte.append(f"scale={taille}:{taille}:flags=area")  # vraie pixelisation
    vf_carte.append(f"scale={grid_w}:{grid_h}:flags=neighbor")  # blocs nets
    if en_nb:
        vf_carte.append("hue=s=0")
    vf_carte.append("setsar=1")

    cmd_carte = [
        "ffmpeg", "-y",
        "-stream_loop", "-1",            # boucle si la carte est plus courte
        "-ss", str(debut_carte),
        "-i", str(video_carte),
        "-t", str(duree_carte_extr),
        "-vf", ",".join(vf_carte),
        "-r", "30",
        "-fps_mode", "cfr",
        "-c:v", "libx264", "-preset", PRESET, "-crf", "22",
        "-pix_fmt", "yuv420p", "-an",
        str(carte_path)
    ]
    run(cmd_carte)

    # === 3. Fusion grille_base × carte ===
    print(f"    [mosaique] fusion ({mode_fusion}, intensité={intensite})...")
    if intensite < 1.0:
        # Mélange entre carte originale (blanc neutre) et carte modulée
        # Astuce : on baisse le contraste de la carte pour réduire l'effet
        fc = (
            f"[1:v]eq=brightness={(1-intensite)*0.3}:contrast={intensite}[carte_mod];"
            f"[0:v][carte_mod]blend=all_mode={mode_fusion}[v]"
        )
    else:
        fc = f"[0:v][1:v]blend=all_mode={mode_fusion}[v]"

    fusionne = WORK_DIR / f"mosaic_fusion_{taille}_{int(debut_source)}.mp4"
    run([
        "ffmpeg", "-y",
        "-i", str(grille_base),
        "-i", str(carte_path),
        "-filter_complex", fc,
        "-map", "[v]",
        "-t", str(duree),
        "-c:v", "libx264", "-preset", PRESET, "-crf", CRF,
        "-pix_fmt", "yuv420p",
        str(fusionne)
    ])

    # === 4. Padding final pour atteindre FINAL_W × FINAL_H ===
    if pad_x > 0 or pad_y > 0:
        run([
            "ffmpeg", "-y", "-i", str(fusionne),
            "-vf", f"pad={FINAL_W}:{FINAL_H}:{pad_x}:{pad_y}:black,setsar=1",
            "-c:v", "libx264", "-preset", PRESET, "-crf", CRF,
            "-pix_fmt", "yuv420p",
            str(output_path)
        ])
    else:
        fusionne.rename(output_path)


def creer_effet_mur_moniteurs(video_source, taille, debut_source, duree,
                                output_path, cell_w, cell_h, pad_x, pad_y):
    """
    Mur de moniteurs : chaque cellule joue une vidéo différente tirée au sort
    parmi une liste fournie (et éventuellement la vidéo source elle-même).
    """
    cfg_w = EFFETS.get("mur_moniteurs", {})
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

    # Cache des durées des vidéos pour ne pas les re-prober N fois
    durees_videos = {}
    for v in videos:
        try:
            durees_videos[v] = get_duration(v)
        except Exception:
            durees_videos[v] = duree  # fallback

    # On génère une mini-vidéo par cellule
    paths = []
    for i in range(n_cellules):
        v = random.choice(videos)
        if decalage_aleatoire:
            d_max = max(0, durees_videos[v] - duree)
            debut = random.uniform(0, d_max)
        else:
            debut = 0

        mini = WORK_DIR / f"wall_mini_{taille}_{i:04d}_{int(debut_source)}.mp4"
        fabriquer_segment(v, mini, debut, duree, cell_w, cell_h)
        paths.append(mini)
        if i % 100 == 0 and i > 0:
            print(f"    [mur_moniteurs] {i}/{n_cellules} cellules préparées")

    # Assemblage de la grille
    if n_cellules > 256:
        assembler_grille_par_lignes(taille, paths, duree, output_path, pad_x, pad_y)
    else:
        assembler_grille(taille, paths, duree, output_path, pad_x, pad_y)


def creer_effet_lsd(video_source, taille, debut_source, duree,
                     output_path, cell_w, cell_h, pad_x, pad_y):
    """
    Effet LSD : grille classique → distorsion ondulatoire + aberration
    chromatique + saturation + contours. Visuellement très proche de l'image
    de référence (lignes ondulantes psychédéliques sur fond bleu/violet).

    Pipeline :
      1. Construire la grille classique (toutes les cellules)
      2. Appliquer geq pour distorsion ondulatoire (formule sin/cos)
      3. Appliquer rgbashift pour aberration chromatique
      4. Hue/saturation pour couleurs intenses
      5. edgedetect overlay pour les lignes
    """
    cfg_l = EFFETS.get("lsd", {})
    amp = float(cfg_l.get("amplitude_onde", 120))
    vit = float(cfg_l.get("vitesse_onde", 0.8))
    freq = float(cfg_l.get("frequence_onde", 8))
    aber = int(cfg_l.get("aberration", 8))
    sat = float(cfg_l.get("saturation", 2.5))
    teinte = float(cfg_l.get("teinte", 240))
    lignes_force = float(cfg_l.get("lignes_force", 0.7))
    lignes_mode = cfg_l.get("lignes_mode", "edges")

    grid_w = cell_w * taille
    grid_h = cell_h * taille

    # === 1. Construire la grille de base ===
    print(f"    [lsd] préparation grille...")
    mini_normal = WORK_DIR / f"lsd_mini_{taille}_{int(debut_source)}.mp4"
    fabriquer_segment(video_source, mini_normal, debut_source, duree,
                       cell_w, cell_h)
    grille_base = WORK_DIR / f"lsd_grille_{taille}_{int(debut_source)}.mp4"
    paths = [mini_normal] * (taille * taille)
    if (taille * taille) > 256:
        assembler_grille_par_lignes(taille, paths, duree, grille_base, 0, 0)
    else:
        assembler_grille(taille, paths, duree, grille_base, 0, 0)

    # === 2. Application de la chaîne d'effets ===
    print(f"    [lsd] application effets (amp={amp}, sat={sat}, teinte={teinte})...")

    # Pipeline ffmpeg :
    # - geq pour distorsion : on utilise un displacement basé sur sin(y/freq + t*vit)
    # - rgbashift pour aberration chromatique
    # - hue pour la couleur
    # - edgedetect superposé
    # On construit la chaîne en plusieurs étapes pour la lisibilité

    # Étape 2a : distorsion ondulatoire via geq
    # geq permet de calculer la couleur d'un pixel (X,Y) à partir d'un autre pixel
    # On déforme horizontalement selon sin(Y/freq + T*vit)
    # Pour éviter geq qui est très lent, on utilise plutôt 'displace' avec une carte
    # générée par geq sur une image NB → trop complexe.
    # Alternative simple et efficace : on utilise le filtre 'noise' + 'blur'
    # combiné avec rgbashift et hue pour produire un effet similaire.
    # MAIS pour vraiment ressembler à ta photo, on utilise des sinusoïdes.
    #
    # Pipeline final (plus simple et fonctionnel) :
    # 1) hue (pour bleu/violet saturé)
    # 2) rgbashift (aberration)
    # 3) edgedetect en overlay (lignes)
    # 4) geq pour ondulation (optionnel, lourd)

    # Construction du graph
    parts = []
    # Étape A : couleur saturée + teinte
    parts.append(f"[0:v]hue=h={teinte}:s={sat}[col]")
    # Étape B : aberration chromatique (décale les canaux R et B)
    parts.append(f"[col]rgbashift=rh={aber}:bh=-{aber}:rv=-{aber//2}:bv={aber//2}[abr]")
    # Étape C : déformation ondulatoire avec geq
    # Ondulation horizontale : pixel (X,Y) → on lit (X + amp*sin(Y/freq + t*vit), Y)
    # geq pour chaque canal (luminance + chroma)
    expr_x = f"X+{amp}*sin(Y/{max(1,grid_h/freq):.3f}+T*{vit*6.28:.3f})"
    parts.append(
        f"[abr]geq="
        f"r='r({expr_x},Y)':"
        f"g='g({expr_x},Y)':"
        f"b='b({expr_x},Y)'"
        f"[wave]"
    )
    # Étape D : superposition des lignes de contour
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

    output_lsd = WORK_DIR / f"lsd_out_{taille}_{int(debut_source)}.mp4"
    # geq est très lent, on prévient avec preset ultrafast pour cette étape
    run([
        "ffmpeg", "-y",
        "-i", str(grille_base),
        "-filter_complex", fc,
        "-map", "[v]",
        "-t", str(duree),
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", CRF,
        "-pix_fmt", "yuv420p",
        str(output_lsd)
    ])

    # === 3. Padding final ===
    if pad_x > 0 or pad_y > 0:
        run([
            "ffmpeg", "-y", "-i", str(output_lsd),
            "-vf", f"pad={FINAL_W}:{FINAL_H}:{pad_x}:{pad_y}:black,setsar=1",
            "-c:v", "libx264", "-preset", PRESET, "-crf", CRF,
            "-pix_fmt", "yuv420p",
            str(output_path)
        ])
    else:
        output_lsd.rename(output_path)


def jouer_effet(nom_effet, video_source, video_anomalie_externe, taille,
                 debut_source, duree, output_path, cell_w, cell_h, pad_x, pad_y):
    """Dispatcher pour les effets."""
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
        # On délègue à creer_phase en mode masque, mais sans la boucle
        # principale (juste la sous-séquence masque)
        # Pour simplifier, on appelle directement avec l'ancien chemin masque.
        creer_phase_masque_seul(video_source, video_anomalie_externe, taille,
                                  debut_source, duree, output_path,
                                  cell_w, cell_h, pad_x, pad_y)
    else:
        print(f"ERREUR : effet inconnu '{nom_effet}'")
        sys.exit(1)


def creer_phase_masque_seul(video_normale, video_anomalie_externe, taille,
                              debut_phase, duree_phase, output_path,
                              cell_w, cell_h, pad_x, pad_y):
    """
    Version isolée de la logique masque, utilisable comme effet.
    Reprend la même logique que creer_phase en mode masque mais sans
    la décision portée/seuil.
    """
    if not MASQUES_ACTIFS:
        print("ATTENTION : effet 'masque' demandé mais système masque désactivé")
        # On fait quand même la grille de base
        positions = set()
        anomalies_par_pos = {}
        construire_sous_segment(video_normale, video_anomalie_externe,
                                 taille, debut_phase, duree_phase, output_path,
                                 cell_w, cell_h, pad_x, pad_y,
                                 positions, anomalies_par_pos)
        return

    evolution = MSK.get("evolution", "statique")
    seuil = MSK.get("seuil", 128)
    inverser = MSK.get("inverser", False)
    mode_filtre = MSK.get("mode_filtre", "tirage_global")
    images = MSK.get("images", [])
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
        duree_appar = MSK.get("duree_apparition", 60)
        apparition_filtre = MSK.get("apparition_filtre", "fixe")
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
            sous_path = WORK_DIR / f"effet_app_{taille}_{k:03d}.mp4"
            debut_sub = debut_phase + t_start
            construire_sous_segment(video_normale, video_anomalie_externe,
                                     taille, debut_sub, duree_sub, sous_path,
                                     cell_w, cell_h, pad_x, pad_y,
                                     positions_k, anomalies_par_pos)
            sous_paths.append(sous_path)
        concat_segments_simple(sous_paths, output_path)
    else:  # sequence
        duree_par_image = MSK.get("duree_par_image", 30)
        n_sub = max(1, int(math.ceil(duree_phase / duree_par_image)))
        duree_sub = duree_phase / n_sub
        sous_paths = []
        for k in range(n_sub):
            img_path = images[k % len(images)]
            positions = charger_masque_en_positions(img_path, taille, seuil, inverser)
            anomalies_par_pos = tirer_anomalies_pour_positions(positions, mode_filtre)
            sous_path = WORK_DIR / f"effet_seq_{taille}_{k:03d}.mp4"
            debut_sub = debut_phase + k * duree_sub
            construire_sous_segment(video_normale, video_anomalie_externe,
                                     taille, debut_sub, duree_sub, sous_path,
                                     cell_w, cell_h, pad_x, pad_y,
                                     set(positions), anomalies_par_pos)
            sous_paths.append(sous_path)
        concat_segments_simple(sous_paths, output_path)


def creer_phase(video_normale, video_anomalie_externe, taille,
                output_path, duree_phase, debut_phase):
    n_cellules = taille * taille
    cell_w, cell_h, _, _, pad_x, pad_y = calculer_taille_cellule(taille)
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
        # On enchaîne les effets configurés en sous-séquences
        durees_cfg = EFFETS.get("durees", {})
        sous_paths = []
        cumul = 0
        for idx, nom_effet in enumerate(EFFETS_LISTE):
            duree_effet = durees_cfg.get(nom_effet)
            if duree_effet is None:
                duree_effet = DUREE_SOURCE  # par défaut : 1 boucle vidéo
            # On clamp si la phase ne peut pas le contenir
            if cumul + duree_effet > duree_phase:
                duree_effet = duree_phase - cumul
            if duree_effet <= 0:
                break

            print(f"\n  → Effet {idx+1}/{len(EFFETS_LISTE)}: '{nom_effet}' "
                  f"({duree_effet:.1f}s)")
            sous_path = WORK_DIR / f"effet_{taille}_{idx:02d}_{nom_effet}.mp4"
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
            positions = set()
            anomalies_par_pos = {}
            sous_path = WORK_DIR / f"effet_{taille}_remplissage.mp4"
            construire_sous_segment(video_normale, video_anomalie_externe,
                                     taille, debut_phase + cumul, duree_reste,
                                     sous_path, cell_w, cell_h, pad_x, pad_y,
                                     positions, anomalies_par_pos)
            sous_paths.append(sous_path)

        concat_segments_simple(sous_paths, output_path)
        return

    # ============ MODE MASQUE ============
    if use_mask:
        evolution = MSK.get("evolution", "statique")
        seuil = MSK.get("seuil", 128)
        inverser = MSK.get("inverser", False)
        mode_filtre = MSK.get("mode_filtre", "tirage_global")
        images = MSK.get("images", [])
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
            duree_par_image = MSK.get("duree_par_image", 30)
            n_sub = max(1, int(math.ceil(duree_phase / duree_par_image)))
            duree_sub = duree_phase / n_sub
            sous_paths = []
            for k in range(n_sub):
                img_path = images[k % len(images)]
                positions = charger_masque_en_positions(img_path, taille, seuil, inverser)
                print(f"  Sous-segment {k+1}/{n_sub} masque='{img_path}' "
                      f"({len(positions)} cellules)")
                anomalies_par_pos = tirer_anomalies_pour_positions(positions, mode_filtre)
                sous_path = WORK_DIR / f"sub_mask_{taille}_{k:03d}.mp4"
                debut_sub = debut_phase + k * duree_sub
                construire_sous_segment(video_normale, video_anomalie_externe,
                                         taille, debut_sub, duree_sub, sous_path,
                                         cell_w, cell_h, pad_x, pad_y,
                                         set(positions), anomalies_par_pos)
                sous_paths.append(sous_path)
            concat_segments_simple(sous_paths, output_path)
            return

        elif evolution == "apparition":
            duree_appar = MSK.get("duree_apparition", 60)
            apparition_filtre = MSK.get("apparition_filtre", "fixe")
            n_sub = max(2, min(20, int(duree_appar / 5)))
            duree_sub = duree_phase / n_sub
            positions_finales = charger_masque_en_positions(images[0], taille, seuil, inverser)
            print(f"  Apparition progressive : {len(positions_finales)} cellules cibles, "
                  f"{n_sub} étapes, filtre={apparition_filtre}")

            # En mode "fixe" : on tire UN filtre une seule fois, qui sera utilisé
            # pour toutes les étapes. En mode "change" : nouveau tirage à chaque étape.
            filtre_fixe = None
            if apparition_filtre == "fixe":
                # Tirage unique au début, on le passe en forçant les anomalies
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
                    # Force le même filtre partout
                    anomalies_par_pos = {pos: filtre_fixe for pos in positions_k}
                else:
                    anomalies_par_pos = tirer_anomalies_pour_positions(positions_k, mode_filtre)

                sous_path = WORK_DIR / f"sub_app_{taille}_{k:03d}.mp4"
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

    if CHANGEMENT_ANOMALIE in ("phase", "fixe") or taille == 1:
        positions = set(random.sample(range(n_cellules), n_anomalies)) if n_anomalies > 0 else set()
        anomalies_par_pos = tirer_anomalies_pour_positions(positions)
        construire_sous_segment(video_normale, video_anomalie_externe,
                                 taille, debut_phase, duree_phase, output_path,
                                 cell_w, cell_h, pad_x, pad_y,
                                 positions, anomalies_par_pos)
        return

    n_sub = max(1, int(math.ceil(duree_phase / INTERVALLE_CHANGEMENT)))
    duree_sub = duree_phase / n_sub
    sous_paths = []
    for k in range(n_sub):
        positions = set(random.sample(range(n_cellules), n_anomalies)) if n_anomalies > 0 else set()
        anomalies_par_pos = tirer_anomalies_pour_positions(positions)
        sous_path = WORK_DIR / f"sub_{taille}_{k:03d}.mp4"
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
    target_w = pair_inf(FINAL_W)
    target_h = pair_inf(FINAL_H)
    segments_norm = []
    for i, seg in enumerate(segments):
        out = WORK_DIR / f"norm_{i}.mp4"
        run([
            "ffmpeg", "-y", "-i", str(seg),
            "-vf", f"scale={target_w}:{target_h},setsar=1",
            "-r", "30",
            "-fps_mode", "cfr",                  # framerate constant : pas de drop/dup
            "-video_track_timescale", "30000",   # timescale uniforme
            "-c:v", "libx264", "-preset", PRESET, "-crf", CRF,
            "-pix_fmt", "yuv420p",
            "-an",
            str(out)
        ])
        segments_norm.append(out)
    concat_segments_simple(segments_norm, output_path)


def parser_timestamp(t):
    """
    Convertit un timestamp en secondes (float).
    Accepte :
      - 30      → 30.0
      - 30.5    → 30.5
      - "30"    → 30.0
      - "1:30"  → 90.0 (mm:ss)
      - "1:30.5"→ 90.5
      - "1:23:45" → 5025.0 (hh:mm:ss)
      - None    → None
    """
    if t is None:
        return None
    if isinstance(t, (int, float)):
        return float(t)
    s = str(t).strip()
    if ":" not in s:
        return float(s)
    parts = s.split(":")
    parts = [float(p) for p in parts]
    if len(parts) == 2:  # mm:ss
        return parts[0] * 60 + parts[1]
    elif len(parts) == 3:  # hh:mm:ss
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    raise ValueError(f"Timestamp invalide : {t}")


def normaliser_entree_audio(entree):
    """
    Convertit une entrée de la liste fichiers en dict avec fichier/debut/fin.
    Les bornes peuvent être en secondes (30) ou en mm:ss ("2:00") ou hh:mm:ss.
    Accepte :
      - "chemin.mp3"                                  → fichier seul
      - {fichier, debut, fin}                         → bornes explicites
      - {fichier, debut}                              → de debut à la fin
      - {fichier, aleatoire: true, duree: N}          → bornes aléatoires
      - {fichier}                                     → fichier entier
    """
    if isinstance(entree, str):
        return {"fichier": entree, "debut": None, "fin": None,
                "aleatoire": False, "duree": None}
    return {
        "fichier": entree["fichier"],
        "debut": parser_timestamp(entree.get("debut")),
        "fin": parser_timestamp(entree.get("fin")),
        "aleatoire": entree.get("aleatoire", False),
        "duree": parser_timestamp(entree.get("duree")),
    }


def preparer_extrait_audio(entree_norm, sortie_path, duree_voulue):
    """
    Prépare un extrait audio depuis l'entrée normalisée, à la durée voulue.
    Boucle automatiquement si l'extrait est plus court que la durée voulue.
    Retourne le chemin du fichier audio préparé (sans volume ni boucle finale).
    """
    fichier = entree_norm["fichier"]
    debut = entree_norm["debut"]
    fin = entree_norm["fin"]
    aleatoire = entree_norm["aleatoire"]
    duree_extrait = entree_norm["duree"]

    duree_fichier = get_duration(fichier)

    # Détermine début et fin effectifs
    if aleatoire:
        # Tirage aléatoire d'un point de départ
        d_extrait = duree_extrait if duree_extrait else duree_voulue
        d_extrait = min(d_extrait, duree_fichier)
        debut_eff = random.uniform(0, max(0, duree_fichier - d_extrait))
        fin_eff = debut_eff + d_extrait
        print(f"    [audio] {fichier} : tirage aléatoire {debut_eff:.1f}s → {fin_eff:.1f}s")
    elif debut is not None or fin is not None:
        debut_eff = debut if debut is not None else 0
        fin_eff = fin if fin is not None else duree_fichier
        debut_eff = max(0, min(debut_eff, duree_fichier))
        fin_eff = max(debut_eff + 0.1, min(fin_eff, duree_fichier))
        print(f"    [audio] {fichier} : bornes {debut_eff:.1f}s → {fin_eff:.1f}s")
    else:
        debut_eff = 0
        fin_eff = duree_fichier
        print(f"    [audio] {fichier} : fichier entier ({duree_fichier:.1f}s)")

    duree_extrait_reelle = fin_eff - debut_eff

    # Si l'extrait est plus long que la durée voulue, on coupe.
    # Sinon, on boucle l'extrait.
    if duree_extrait_reelle >= duree_voulue:
        # On extrait juste la portion voulue
        run([
            "ffmpeg", "-y",
            "-ss", str(debut_eff),
            "-i", str(fichier),
            "-t", str(duree_voulue),
            "-vn",
            "-c:a", "aac", "-b:a", "192k",
            str(sortie_path)
        ])
    else:
        # L'extrait est plus court : on extrait, puis on boucle pour atteindre la durée
        extrait_brut = WORK_DIR / f"extrait_brut_{random.randint(0, 99999)}.aac"
        run([
            "ffmpeg", "-y",
            "-ss", str(debut_eff),
            "-i", str(fichier),
            "-t", str(duree_extrait_reelle),
            "-vn",
            "-c:a", "aac", "-b:a", "192k",
            str(extrait_brut)
        ])
        # Boucle l'extrait pour atteindre la durée voulue
        run([
            "ffmpeg", "-y",
            "-stream_loop", "-1",
            "-i", str(extrait_brut),
            "-t", str(duree_voulue),
            "-c:a", "aac", "-b:a", "192k",
            str(sortie_path)
        ])


def construire_piste_audio(piste_path, segments_durees, video_a_audio_source=None):
    """
    Construit la piste audio finale selon AUDIO config.
    segments_durees : liste de durées de chaque PHASE (1 par phase de la vidéo).
    video_a_audio_source : la vidéo finale (sans audio) dont on peut extraire
                            l'audio source si audio_source="garder" ou "que_source".
    """
    fichiers = AUDIO.get("fichiers", [])
    comportement = AUDIO.get("comportement", "reset")
    audio_source = AUDIO.get("audio_source", "couper")
    vol_musique = float(AUDIO.get("volume_musique", 0.7))
    vol_source = float(AUDIO.get("volume_source", 1.0))
    fade_in = float(AUDIO.get("fade_in", 0))
    fade_out = float(AUDIO.get("fade_out", 0))

    duree_totale = sum(segments_durees)

    # === Cas "que_source" : on prend l'audio de la vidéo source bouclée ===
    if audio_source == "que_source":
        # Crée un audio à partir de la vidéo source bouclée
        run([
            "ffmpeg", "-y",
            "-stream_loop", "-1",
            "-i", str(INPUT_VIDEO),
            "-t", str(duree_totale),
            "-vn",
            "-filter:a", f"volume={vol_source}",
            "-c:a", "aac", "-b:a", "192k",
            str(piste_path)
        ])
        return

    if not fichiers:
        print("ERREUR audio : aucun fichier musique fourni")
        sys.exit(1)

    # Normalise toutes les entrées en dicts
    entrees = [normaliser_entree_audio(f) for f in fichiers]
    for e in entrees:
        if not Path(e["fichier"]).exists():
            print(f"ERREUR audio : fichier introuvable : {e['fichier']}")
            sys.exit(1)

    # === Construction de la piste musique ===
    musique_path = WORK_DIR / "musique.aac"

    if comportement == "continu" and len(entrees) == 1:
        # 1 fichier en boucle continue sur toute la durée
        print(f"  Audio : 1 fichier en continu sur {duree_totale:.0f}s")
        preparer_extrait_audio(entrees[0], musique_path, duree_totale)
    else:
        # Cas reset OU plusieurs fichiers : un sous-segment audio par phase
        if comportement == "reset":
            print(f"  Audio : reset à chaque phase ({len(segments_durees)} phases)")
        else:
            print(f"  Audio : continu (différentes musiques selon phase)")

        sub_audios = []
        for idx, duree_phase in enumerate(segments_durees):
            entree = entrees[idx] if idx < len(entrees) else entrees[-1]
            sub_path = WORK_DIR / f"audio_phase_{idx:02d}.aac"
            print(f"  Phase audio {idx+1}/{len(segments_durees)} ({duree_phase:.0f}s)")
            preparer_extrait_audio(entree, sub_path, duree_phase)
            sub_audios.append(sub_path)

        # Concatène les sous-audios
        liste = WORK_DIR / "liste_audio.txt"
        with open(liste, "w") as f:
            for a in sub_audios:
                f.write(f"file '{Path(a).resolve()}'\n")
        run([
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0",
            "-i", str(liste),
            "-c", "copy",
            str(musique_path)
        ])

    # === Application du volume ===
    if vol_musique != 1.0:
        musique_vol = WORK_DIR / "musique_vol.aac"
        run([
            "ffmpeg", "-y",
            "-i", str(musique_path),
            "-filter:a", f"volume={vol_musique}",
            "-c:a", "aac", "-b:a", "192k",
            str(musique_vol)
        ])
        musique_path = musique_vol

    # === Application du fade in/out ===
    if fade_in > 0 or fade_out > 0:
        afade_filters = []
        if fade_in > 0:
            afade_filters.append(f"afade=t=in:st=0:d={fade_in}")
        if fade_out > 0:
            afade_filters.append(f"afade=t=out:st={duree_totale - fade_out}:d={fade_out}")
        musique_fade = WORK_DIR / "musique_fade.aac"
        run([
            "ffmpeg", "-y",
            "-i", str(musique_path),
            "-filter:a", ",".join(afade_filters),
            "-c:a", "aac", "-b:a", "192k",
            str(musique_fade)
        ])
        musique_path = musique_fade

    # === Mixage avec audio source si demandé ===
    if audio_source == "garder":
        # On extrait l'audio source bouclé et on l'amix avec la musique
        source_path = WORK_DIR / "source_audio.aac"
        run([
            "ffmpeg", "-y",
            "-stream_loop", "-1",
            "-i", str(INPUT_VIDEO),
            "-t", str(duree_totale),
            "-vn",
            "-filter:a", f"volume={vol_source}",
            "-c:a", "aac", "-b:a", "192k",
            str(source_path)
        ])
        run([
            "ffmpeg", "-y",
            "-i", str(source_path),
            "-i", str(musique_path),
            "-filter_complex", "[0:a][1:a]amix=inputs=2:duration=longest:dropout_transition=0[a]",
            "-map", "[a]",
            "-c:a", "aac", "-b:a", "192k",
            str(piste_path)
        ])
    else:
        # audio_source == "couper" : seule la musique
        # On la copie simplement vers piste_path
        import shutil
        shutil.copy(str(musique_path), str(piste_path))


def muxer_audio_video(video_sans_audio, piste_audio, output_path):
    """Mux la vidéo et l'audio sans ré-encoder la vidéo."""
    run([
        "ffmpeg", "-y",
        "-i", str(video_sans_audio),
        "-i", str(piste_audio),
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "192k",
        "-map", "0:v:0", "-map", "1:a:0",
        "-shortest",
        str(output_path)
    ])


def main():
    if not Path(INPUT_VIDEO).exists():
        print(f"Vidéo principale introuvable : {INPUT_VIDEO}")
        sys.exit(1)
    if MODE_ANOMALIE in ("video", "mixte") and not Path(INPUT_ANOMALIE).exists():
        print(f"Vidéo d'anomalie introuvable : {INPUT_ANOMALIE}")
        sys.exit(1)

    global ANOMALIES
    if CHANGEMENT_ANOMALIE == "fixe":
        anomalie_fige = random.choice(ANOMALIES)
        print(f"\n⚙ Mode FIXE : '{anomalie_fige[0]}'")
        ANOMALIES = [anomalie_fige]

    print("\n=== Étape 1 : préparation des sources ===")
    base_full = WORK_DIR / "base.mp4"
    preparer_video(INPUT_VIDEO, base_full, DUREE_TOTALE, FINAL_W, FINAL_H)

    anomalie_full = None
    if MODE_ANOMALIE in ("video", "mixte"):
        anomalie_full = WORK_DIR / "anomalie.mp4"
        preparer_video(INPUT_ANOMALIE, anomalie_full, DUREE_TOTALE, FINAL_W, FINAL_H)

    print("\n=== Étape 2 : création des séquences ===")
    segments = []
    debut = 0.0
    phase_courante = 0
    for i, (num_phase, taille, duree_seq) in enumerate(PLAN):
        if num_phase != phase_courante:
            print(f"\n--- PHASE {num_phase}/{NB_PHASES_PLAN} (nouvelles anomalies) ---")
            phase_courante = num_phase
        seg = WORK_DIR / f"phase{num_phase:02d}_seq{i:03d}_t{taille}.mp4"
        creer_phase(base_full, anomalie_full, taille, seg, duree_seq, debut)
        segments.append(seg)
        debut += duree_seq

    print("\n=== Étape 3 : assemblage final ===")
    if AUDIO_ACTIF:
        video_temp = WORK_DIR / "video_sans_audio.mp4"
        concatener(segments, video_temp)

        print("\n=== Étape 4 : ajout de l'audio ===")
        # Durées par séquence (telles que définies dans le PLAN)
        durees_segments = [p[2] for p in PLAN]
        piste_audio = WORK_DIR / "piste_audio.aac"
        construire_piste_audio(piste_audio, durees_segments)
        muxer_audio_video(video_temp, piste_audio, OUTPUT_VIDEO)
    else:
        concatener(segments, OUTPUT_VIDEO)

    print(f"\n✓ Terminé : {OUTPUT_VIDEO} ({DUREE_TOTALE/60:.2f} min)")


if __name__ == "__main__":
    main()