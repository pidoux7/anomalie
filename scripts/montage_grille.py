"""
Montage vidéo : grilles progressives 1 → 4 → ... → MAX_CASES.
Toutes les options dans config.yaml.

Usage :
    python3 montage_grille.py                                     # rendu complet
    python3 montage_grille.py scenarios/sequence.yaml             # rendu complet d'un scenario
    python3 montage_grille.py scenarios/sequence.yaml --audio-only
        → réutilise travail_montage/video_sans_audio.mp4 et ne refait que
          la piste audio + le mux. Très rapide pour itérer sur les musiques.

Dépendances :
    pip3 install pyyaml pillow tqdm

Architecture :
    L'orchestration est minimale ici ; toute la logique vit dans le package
    `lib/` (config, commun, masques, grille, effets, phases, audio).
"""

import random
import sys
from pathlib import Path

# Capture les flags CLI avant l'import de lib.config (qui lit sys.argv[1])
AUDIO_ONLY = "--audio-only" in sys.argv
sys.argv = [a for a in sys.argv if a != "--audio-only"]

from lib import config
from lib.grille import preparer_video
from lib.phases import creer_phase, concatener
from lib.audio import (
    construire_piste_audio,
    construire_piste_audio_scenario,
    muxer_audio_video,
)


def _generer_video(video_temp):
    """Étapes 1-3 : préparation + séquences + concat → video_temp (sans audio)."""
    if not Path(config.INPUT_VIDEO).exists():
        print(f"Vidéo principale introuvable : {config.INPUT_VIDEO}")
        sys.exit(1)
    if config.INPUT_ANOMALIE and not Path(config.INPUT_ANOMALIE).exists():
        print(f"Vidéo d'anomalie introuvable : {config.INPUT_ANOMALIE}")
        sys.exit(1)
    if config.MODE_ANOMALIE in ("video", "mixte") and not config.INPUT_ANOMALIE:
        print("ERREUR : mode_anomalie nécessite input_anomalie défini.")
        sys.exit(1)

    if config.CHANGEMENT_ANOMALIE == "fixe":
        anomalie_fige = random.choice(config.ANOMALIES)
        print(f"\n⚙ Mode FIXE : '{anomalie_fige[0]}'")
        config.ANOMALIES = [anomalie_fige]

    print("\n=== Étape 1 : préparation des sources ===")
    base_full = config.WORK_DIR / "base.mp4"
    preparer_video(config.INPUT_VIDEO, base_full,
                    config.DUREE_TOTALE, config.FINAL_W, config.FINAL_H)

    anomalie_full = None
    if config.INPUT_ANOMALIE:
        anomalie_full = config.WORK_DIR / "anomalie.mp4"
        preparer_video(config.INPUT_ANOMALIE, anomalie_full,
                        config.DUREE_TOTALE, config.FINAL_W, config.FINAL_H)

    print("\n=== Étape 2 : création des séquences ===")
    segments = []
    debut = 0.0
    phase_courante = 0
    for i, (num_phase, taille, duree_seq, directives) in enumerate(config.PLAN):
        if num_phase != phase_courante:
            print(f"\n--- PHASE {num_phase}/{config.NB_PHASES_PLAN} (nouvelles anomalies) ---")
            phase_courante = num_phase
        seg = config.WORK_DIR / f"phase{num_phase:02d}_seq{i:03d}_t{taille}.mp4"
        creer_phase(base_full, anomalie_full, taille, seg, duree_seq, debut, directives)
        segments.append(seg)
        debut += duree_seq

    print("\n=== Étape 3 : assemblage final ===")
    concatener(segments, video_temp)


def _ajouter_audio(video_temp):
    """Étape 4 : construit la piste audio et la mux avec video_temp."""
    print("\n=== Étape 4 : ajout de l'audio ===")
    piste_audio = config.WORK_DIR / "piste_audio.aac"
    if config.AUDIO_MODE == "scenario":
        construire_piste_audio_scenario(piste_audio, config.PLAN,
                                          config.AUDIO_SCENARIO,
                                          config.AUDIO_CONFLIT_DEFAUT)
    else:
        durees_segments = [p[2] for p in config.PLAN]
        construire_piste_audio(piste_audio, durees_segments)
    muxer_audio_video(video_temp, piste_audio, config.OUTPUT_VIDEO)


def main():
    config.afficher_resume()

    video_temp = config.WORK_DIR / "video_sans_audio.mp4"

    if AUDIO_ONLY:
        # On réutilise la vidéo générée précédemment.
        if not video_temp.exists():
            print(f"\nERREUR --audio-only : {video_temp} introuvable.")
            print("  Lance d'abord un rendu complet pour générer la vidéo de base.")
            sys.exit(1)
        if not config.AUDIO_ACTIF:
            print("\nERREUR --audio-only : audio.actif=false dans la config.")
            sys.exit(1)
        print(f"\n=== Mode --audio-only : réutilise {video_temp} ===")
        _ajouter_audio(video_temp)
    elif config.AUDIO_ACTIF:
        _generer_video(video_temp)
        _ajouter_audio(video_temp)
    else:
        # Pas d'audio : on génère directement le fichier final
        _generer_video(config.OUTPUT_VIDEO)

    print(f"\n✓ Terminé : {config.OUTPUT_VIDEO} ({config.DUREE_TOTALE/60:.2f} min)")


if __name__ == "__main__":
    main()
