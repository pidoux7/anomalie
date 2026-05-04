"""
Pipeline audio :
    - normaliser_entree_audio : nettoie une entrée de la liste fichiers
    - preparer_extrait_audio  : extrait/boucle un fichier à la durée voulue
    - construire_piste_audio  : assemble la piste finale (musique + source)
    - muxer_audio_video       : muxe la piste audio sur la vidéo finale

Indépendant de la chaîne vidéo : peut être appelé séparément pour ne
re-générer que l'audio sur une vidéo déjà produite.
"""

import random
import shutil
import sys
from pathlib import Path

from . import config
from .commun import run, get_duration, parser_timestamp


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
    """
    fichier = entree_norm["fichier"]
    debut = entree_norm["debut"]
    fin = entree_norm["fin"]
    aleatoire = entree_norm["aleatoire"]
    duree_extrait = entree_norm["duree"]

    duree_fichier = get_duration(fichier)

    if aleatoire:
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

    if duree_extrait_reelle >= duree_voulue:
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
        # L'extrait est plus court : extrait, puis boucle pour atteindre la durée
        extrait_brut = config.WORK_DIR / f"extrait_brut_{random.randint(0, 99999)}.aac"
        run([
            "ffmpeg", "-y",
            "-ss", str(debut_eff),
            "-i", str(fichier),
            "-t", str(duree_extrait_reelle),
            "-vn",
            "-c:a", "aac", "-b:a", "192k",
            str(extrait_brut)
        ])
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
    audio_cfg = config.AUDIO
    fichiers = audio_cfg.get("fichiers", [])
    comportement = audio_cfg.get("comportement", "reset")
    audio_source = audio_cfg.get("audio_source", "couper")
    vol_musique = float(audio_cfg.get("volume_musique", 0.7))
    vol_source = float(audio_cfg.get("volume_source", 1.0))
    fade_in = float(audio_cfg.get("fade_in", 0))
    fade_out = float(audio_cfg.get("fade_out", 0))

    duree_totale = sum(segments_durees)

    # === Cas "que_source" : on prend l'audio de la vidéo source bouclée ===
    if audio_source == "que_source":
        run([
            "ffmpeg", "-y",
            "-stream_loop", "-1",
            "-i", str(config.INPUT_VIDEO),
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

    entrees = [normaliser_entree_audio(f) for f in fichiers]
    for e in entrees:
        if not Path(e["fichier"]).exists():
            print(f"ERREUR audio : fichier introuvable : {e['fichier']}")
            sys.exit(1)

    # === Construction de la piste musique ===
    musique_path = config.WORK_DIR / "musique.aac"

    if comportement == "continu" and len(entrees) == 1:
        print(f"  Audio : 1 fichier en continu sur {duree_totale:.0f}s")
        preparer_extrait_audio(entrees[0], musique_path, duree_totale)
    else:
        if comportement == "reset":
            print(f"  Audio : reset à chaque phase ({len(segments_durees)} phases)")
        else:
            print(f"  Audio : continu (différentes musiques selon phase)")

        sub_audios = []
        for idx, duree_phase in enumerate(segments_durees):
            entree = entrees[idx] if idx < len(entrees) else entrees[-1]
            sub_path = config.WORK_DIR / f"audio_phase_{idx:02d}.aac"
            print(f"  Phase audio {idx+1}/{len(segments_durees)} ({duree_phase:.0f}s)")
            preparer_extrait_audio(entree, sub_path, duree_phase)
            sub_audios.append(sub_path)

        liste = config.WORK_DIR / "liste_audio.txt"
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

    # === Volume ===
    if vol_musique != 1.0:
        musique_vol = config.WORK_DIR / "musique_vol.aac"
        run([
            "ffmpeg", "-y",
            "-i", str(musique_path),
            "-filter:a", f"volume={vol_musique}",
            "-c:a", "aac", "-b:a", "192k",
            str(musique_vol)
        ])
        musique_path = musique_vol

    # === Fade in/out ===
    if fade_in > 0 or fade_out > 0:
        afade_filters = []
        if fade_in > 0:
            afade_filters.append(f"afade=t=in:st=0:d={fade_in}")
        if fade_out > 0:
            afade_filters.append(f"afade=t=out:st={duree_totale - fade_out}:d={fade_out}")
        musique_fade = config.WORK_DIR / "musique_fade.aac"
        run([
            "ffmpeg", "-y",
            "-i", str(musique_path),
            "-filter:a", ",".join(afade_filters),
            "-c:a", "aac", "-b:a", "192k",
            str(musique_fade)
        ])
        musique_path = musique_fade

    # === Mix avec la source si demandé ===
    if audio_source == "garder":
        source_path = config.WORK_DIR / "source_audio.aac"
        run([
            "ffmpeg", "-y",
            "-stream_loop", "-1",
            "-i", str(config.INPUT_VIDEO),
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
