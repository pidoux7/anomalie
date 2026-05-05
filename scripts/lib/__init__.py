"""
Librairie interne du projet anomalie.

Modules :
    config   : chargement YAML, encodeur, plan, constantes globales
    commun   : utilitaires ffmpeg/ffprobe partagés
    masques  : motifs (image → positions) et tirage d'anomalies
    grille   : segments vidéo, cache, assemblage de la grille
    effets   : effets spéciaux (mosaïque, mur de moniteurs, LSD, masque)
    phases   : orchestration d'une phase (taille de grille fixée)
    audio    : préparation, mixage et mux de la piste audio
"""
