"""
Construction de segments vidéo et de la grille.

Contient :
    - calculer_taille_cellule : géométrie des cellules d'une grille N×N
    - preparer_video : préparation de la source globale
    - cache_key / fabriquer_segment / fabriquer_segments_paralleles
    - concat_segments_simple
    - assembler_grille / assembler_grille_par_lignes
    - construire_sous_segment : assemble une grille avec ses cellules
      "anomales" tirées de filtres ou d'une vidéo externe.
"""

import hashlib
import os
import random
import shutil
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from . import config
from .commun import run, run_lenient, get_duration


# ============================================================
# Géométrie
# ============================================================
def calculer_taille_cellule(taille):
    if config.CELLULES_CARREES:
        cell_size = config.pair_inf(min(config.FINAL_W, config.FINAL_H) // taille)
        cell_w = cell_h = cell_size
        grid_w = cell_w * taille
        grid_h = cell_h * taille
        pad_x = (config.FINAL_W - grid_w) // 2
        pad_y = (config.FINAL_H - grid_h) // 2
    else:
        cell_w = config.pair_inf(config.FINAL_W // taille)
        cell_h = config.pair_inf(config.FINAL_H // taille)
        grid_w = cell_w * taille
        grid_h = cell_h * taille
        pad_x = pad_y = 0
    return cell_w, cell_h, grid_w, grid_h, pad_x, pad_y


# ============================================================
# Préparation de la source globale (boucle, scale, crop)
# ============================================================
def preparer_video(input_path, output_path, duree, w, h):
    run([
        "ffmpeg", "-y",
        "-stream_loop", "-1",
        "-i", str(input_path),
        "-t", str(duree),
        "-vf", f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}",
        "-r", "30",
        *config.args_encodage(),
        "-pix_fmt", "yuv420p", "-an",
        str(output_path)
    ])


# ============================================================
# Cache
# ============================================================
def cache_key(source, debut, duree, cell_w, cell_h, filtre):
    """Calcule un hash unique pour un fabriquer_segment."""
    try:
        st = os.stat(str(source))
        src_sig = f"{source}_{st.st_size}_{st.st_mtime_ns}"
    except OSError:
        src_sig = str(source)
    blob = (f"{src_sig}|{debut:.6f}|{duree:.6f}|{cell_w}|{cell_h}"
            f"|{filtre or ''}|{config.ENCODEUR}|{config.CRF}")
    return hashlib.sha1(blob.encode()).hexdigest()[:16]


# ============================================================
# Segments individuels
# ============================================================
def _fichier_utilisable(path):
    """Un fichier mp4 valide fait au moins ~2 KB (header + 1 frame)."""
    return Path(path).exists() and Path(path).stat().st_size > 2048


def _publier_dans_cache(source_path, cache_path):
    """
    Copie source_path → cache_path de manière atomique (tempfile + os.replace),
    pour éviter qu'un autre thread voie un fichier de cache mi-écrit. Si la
    copie échoue, on ignore silencieusement (le cache n'est qu'une optimisation).
    """
    try:
        tmp_fd, tmp_path = tempfile.mkstemp(
            dir=str(cache_path.parent), suffix=".tmp"
        )
        os.close(tmp_fd)
        shutil.copy(str(source_path), tmp_path)
        os.replace(tmp_path, str(cache_path))
    except OSError:
        try:
            os.remove(tmp_path)
        except Exception:
            pass


def fabriquer_segment(source, dest, debut, duree, cell_w, cell_h, filtre=None):
    """
    Génère un mini-segment vidéo et le copie au besoin dans le cache.

    On écrit toujours d'abord sur `dest` (chemin unique par tâche), puis on
    publie une copie dans le cache de manière atomique. Cela évite qu'avec
    le parallélisme, deux threads ayant le même cache_key (même filtre,
    même cellule) écrivent simultanément sur le même fichier de cache et
    produisent une sortie corrompue.

    Si un filtre fait planter ffmpeg ou produit un fichier illisible, on
    retombe automatiquement sur la même cellule sans filtre.
    """
    cache_path = None
    if config.CACHE_ACTIF:
        key = cache_key(source, debut, duree, cell_w, cell_h, filtre)
        cache_path = config.CACHE_DIR / f"{key}.mp4"
        if _fichier_utilisable(cache_path):
            shutil.copy(str(cache_path), str(dest))
            return

    parts = []
    if config.CELLULES_CARREES:
        parts.append("crop='min(iw,ih)':'min(iw,ih)'")
    parts.append(f"scale={cell_w}:{cell_h}")
    if filtre:
        parts.append(filtre)
        parts.append(f"scale={cell_w}:{cell_h}")
    parts.append("setsar=1")
    vf = ",".join(parts)
    # -r 30 / -fps_mode cfr : force un timing fixe en sortie, indépendant
    # des filtres manipulant les PTS (setpts, framestep, etc.) qui sinon
    # peuvent produire des fichiers vides ou très courts.
    cmd = [
        "ffmpeg", "-y",
        "-ss", str(debut),
        "-i", str(source),
        "-t", str(duree),
        "-vf", vf,
        "-r", "30", "-fps_mode", "cfr",
        *config.args_encodage("22"),
        "-pix_fmt", "yuv420p", "-an",
        str(dest),
    ]

    if filtre:
        rc, err = run_lenient(cmd)
        if rc != 0 or not _fichier_utilisable(dest):
            raison = "ffmpeg a échoué" if rc != 0 else "fichier produit invalide"
            print(f"  [warn] filtre '{filtre}' : {raison} ({cell_w}x{cell_h}) → "
                  f"fallback sans filtre")
            if rc != 0 and err.strip():
                print(f"    {err.strip().splitlines()[-1]}")
            if Path(dest).exists():
                Path(dest).unlink()
            return fabriquer_segment(source, dest, debut, duree,
                                       cell_w, cell_h, filtre=None)
    else:
        run(cmd)

    if config.CACHE_ACTIF and cache_path is not None:
        _publier_dans_cache(dest, cache_path)


def fabriquer_segments_paralleles(taches):
    """
    Lance plusieurs fabriquer_segment en parallèle.
    `taches` est une liste de tuples : (source, dest, debut, duree, cw, ch, filtre)
    """
    if config.NB_PARALLEL <= 1 or len(taches) <= 1:
        for t in taches:
            fabriquer_segment(*t)
        return

    print(f"    [parallèle] {len(taches)} tâches sur {config.NB_PARALLEL} threads")
    with ThreadPoolExecutor(max_workers=config.NB_PARALLEL) as ex:
        futures = [ex.submit(fabriquer_segment, *t) for t in taches]
        for f in as_completed(futures):
            f.result()


# ============================================================
# Concaténation simple (lossless)
# ============================================================
def concat_segments_simple(segments, output_path):
    liste = config.WORK_DIR / f"liste_{random.randint(0, 999999)}.txt"
    with open(liste, "w") as f:
        for seg in segments:
            f.write(f"file '{os.path.abspath(seg)}'\n")
    run([
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0",
        "-i", str(liste),
        "-c", "copy",
        str(output_path)
    ])


# ============================================================
# Assemblage de la grille (xstack ou par lignes pour les grosses grilles)
# ============================================================
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
    if config.CELLULES_CARREES and (pad_x > 0 or pad_y > 0):
        fc = (f"xstack=inputs={n_cellules}:layout={layout}[grid];"
              f"[grid]pad={config.FINAL_W}:{config.FINAL_H}:{pad_x}:{pad_y}:black[v]")
    else:
        fc = f"xstack=inputs={n_cellules}:layout={layout}[v]"
    run(["ffmpeg", "-y"] + inputs + [
        "-filter_complex", fc,
        "-map", "[v]",
        "-t", str(duree),
        *config.args_encodage(),
        "-pix_fmt", "yuv420p",
        str(output_path)
    ])


def assembler_grille_par_lignes(taille, paths, duree, output_path, pad_x, pad_y):
    print(f"  → Assemblage par lignes ({taille} lignes)")
    lignes = []
    for r in range(taille):
        ligne_path = config.WORK_DIR / f"ligne_{taille}_{r}_{random.randint(0, 99999)}.mp4"
        ligne_paths = paths[r * taille:(r + 1) * taille]
        ligne_inputs = []
        for p in ligne_paths:
            ligne_inputs.extend(["-i", str(p)])
        run(["ffmpeg", "-y"] + ligne_inputs + [
            "-filter_complex", f"hstack=inputs={taille}[v]",
            "-map", "[v]", "-t", str(duree),
            *config.args_encodage("22"),
            "-pix_fmt", "yuv420p",
            str(ligne_path)
        ])
        lignes.append(str(ligne_path))
    final_inputs = []
    for l in lignes:
        final_inputs.extend(["-i", l])
    if config.CELLULES_CARREES and (pad_x > 0 or pad_y > 0):
        fc = (f"vstack=inputs={taille}[grid];"
              f"[grid]pad={config.FINAL_W}:{config.FINAL_H}:{pad_x}:{pad_y}:black[v]")
    else:
        fc = f"vstack=inputs={taille}[v]"
    run(["ffmpeg", "-y"] + final_inputs + [
        "-filter_complex", fc,
        "-map", "[v]", "-t", str(duree),
        *config.args_encodage(),
        "-pix_fmt", "yuv420p",
        str(output_path)
    ])


# ============================================================
# Construction d'un sous-segment (grille avec cellules anomales)
# ============================================================
def construire_sous_segment(video_normale, video_anomalie_externe,
                              taille, debut, duree, output_path,
                              cell_w, cell_h, pad_x, pad_y,
                              positions_anomalies, anomalies_par_pos):
    n_cellules = taille * taille

    if taille == 1:
        # Phase 1×1 : extraction + pad en UNE seule commande ffmpeg pour
        # éviter les soucis de timestamps liés au double-encodage.
        if config.CELLULES_CARREES and (pad_x > 0 or pad_y > 0):
            vf_parts = [
                "crop='min(iw,ih)':'min(iw,ih)'",
                f"scale={cell_w}:{cell_h}",
                "setsar=1",
                f"pad={config.FINAL_W}:{config.FINAL_H}:{pad_x}:{pad_y}:black",
            ]
            vf = ",".join(vf_parts)
            run([
                "ffmpeg", "-y",
                "-i", str(video_normale),
                "-ss", str(debut),
                "-t", str(duree),
                "-vf", vf,
                "-r", "30",
                "-fps_mode", "cfr",
                *config.args_encodage(),
                "-pix_fmt", "yuv420p", "-an",
                str(output_path)
            ])
        else:
            fabriquer_segment(video_normale, output_path, debut, duree, cell_w, cell_h)
        return

    mini_normal = config.WORK_DIR / f"mini_normal_{taille}_{int(debut)}.mp4"
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
        mini_path = config.WORK_DIR / f"mini_anom_partage_{taille}_{int(debut)}.mp4"
        fabriquer_segment(video_normale, mini_path, debut, duree,
                           cell_w, cell_h, filtre=info_a[1])
        mini_anomalies_partagees[nom_filtre] = mini_path

    # Génère les minis (parallélisé)
    paths = []
    taches_anomalies = []  # (source, dest, debut, duree, cw, ch, filtre)

    # ffprobe la vidéo externe une seule fois (évite N appels pour N cellules)
    duree_externe = None
    if any(anomalies_par_pos[p][0] == "video" for p in positions_anomalies):
        duree_externe = get_duration(video_anomalie_externe)

    for i in range(n_cellules):
        if i in positions_anomalies:
            type_a, info_a = anomalies_par_pos[i]
            cle = info_a[0] if type_a == "filtre" else "__video__"
            if cle in mini_anomalies_partagees:
                paths.append(mini_anomalies_partagees[cle])
            else:
                mini = config.WORK_DIR / f"mini_anom_{taille}_{i}_{int(debut)}.mp4"
                if type_a == "filtre":
                    taches_anomalies.append(
                        (video_normale, mini, debut, duree, cell_w, cell_h, info_a[1])
                    )
                else:
                    debut_ext = random.uniform(0, max(0, duree_externe - duree))
                    taches_anomalies.append(
                        (video_anomalie_externe, mini, debut_ext, duree, cell_w, cell_h, None)
                    )
                paths.append(mini)
        else:
            paths.append(mini_normal)

    if taches_anomalies:
        fabriquer_segments_paralleles(taches_anomalies)

    assembler_grille(taille, paths, duree, output_path, pad_x, pad_y)
