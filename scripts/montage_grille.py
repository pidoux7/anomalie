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

Architecture :
    L'orchestration est minimale ici ; toute la logique vit dans le package
    `lib/` (config, commun, masques, grille, effets, phases, audio).
"""

import random
import sys
from pathlib import Path

from lib import config
from lib.grille import preparer_video
from lib.phases import creer_phase, concatener
from lib.audio import (
    construire_piste_audio,
    construire_piste_audio_scenario,
    muxer_audio_video,
)


def main():
    config.afficher_resume()

    if not Path(config.INPUT_VIDEO).exists():
        print(f"Vidéo principale introuvable : {config.INPUT_VIDEO}")
        sys.exit(1)
    # input_anomalie est requis si mode_anomalie l'utilise OU si un scenario
    # référence `source: "video"` (impossible à savoir avant le run, donc on
    # vérifie simplement qu'il pointe sur un fichier existant s'il est défini).
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

    # On prépare anomalie_full dès que input_anomalie est défini : ainsi le
    # mode auto (mode_anomalie=video/mixte) et le mode scenario (source: video
    # par séquence) y ont accès. Sans coût significatif si jamais utilisé.
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
    if config.AUDIO_ACTIF:
        video_temp = config.WORK_DIR / "video_sans_audio.mp4"
        concatener(segments, video_temp)

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
    else:
        concatener(segments, config.OUTPUT_VIDEO)

    print(f"\n✓ Terminé : {config.OUTPUT_VIDEO} ({config.DUREE_TOTALE/60:.2f} min)")


if __name__ == "__main__":
    main()
