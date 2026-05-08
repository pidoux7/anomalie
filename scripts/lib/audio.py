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


def _duree_naturelle(entree_norm):
    """
    Durée que produira une entrée normalisée selon ses bornes :
      - aleatoire+duree : la valeur de duree
      - debut/fin       : fin - debut (ou jusqu'à la fin du fichier si fin
                          absent, ou depuis 0 si debut absent)
      - rien            : durée totale du fichier
    """
    fichier = entree_norm["fichier"]
    if entree_norm.get("aleatoire"):
        return entree_norm.get("duree") or get_duration(fichier)
    debut = entree_norm.get("debut")
    fin = entree_norm.get("fin")
    if debut is None and fin is None:
        return get_duration(fichier)
    duree_fichier = get_duration(fichier)
    debut_eff = debut if debut is not None else 0
    fin_eff = fin if fin is not None else duree_fichier
    return max(0.1, fin_eff - debut_eff)


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
    elif comportement == "enchaine":
        # Enchaîne les fichiers selon leur durée propre (bornes debut/fin
        # respectées). Si la playlist totale est plus courte que la vidéo,
        # on boucle ; si plus longue, on tronque.
        # `crossfade` (en s) : si > 0, fondu enchaîné entre chaque morceau.
        crossfade = float(audio_cfg.get("crossfade", 0))
        print(f"  Audio : enchaîne {len(entrees)} morceau(x) sur {duree_totale:.0f}s"
              + (f" (crossfade {crossfade}s)" if crossfade > 0 else ""))

        sub_audios = []
        # `cumul_net` = durée résultante (après absorption des crossfades).
        # Chaque morceau ajoute à cumul_net : d_voulue (si premier) ou
        # d_voulue - crossfade (chevauchement avec le précédent).
        cumul_net = 0.0
        slot = 0
        while cumul_net < duree_totale - 0.05:
            entree = entrees[slot % len(entrees)]
            d_nat = _duree_naturelle(entree)
            # Reste à couvrir, en tenant compte que ce morceau aura un
            # crossfade entrant (sauf le premier).
            chevauchement = crossfade if slot > 0 else 0.0
            reste = duree_totale - cumul_net + chevauchement
            d_voulue = min(d_nat, reste)
            if d_voulue <= max(0.1, chevauchement + 0.05):
                break
            sub_path = config.WORK_DIR / f"audio_play_{slot:03d}.aac"
            print(f"    [{slot}] {entree['fichier']} ({d_voulue:.1f}s)")
            preparer_extrait_audio(entree, sub_path, d_voulue)
            sub_audios.append(sub_path)
            cumul_net += d_voulue - chevauchement
            slot += 1

        if crossfade > 0 and len(sub_audios) >= 2:
            # Cascade d'acrossfade : [a0][a1]acrossfade[x1] ; [x1][a2]acrossfade[x2] ...
            inputs = []
            for a in sub_audios:
                inputs += ["-i", str(a)]
            fc_parts = []
            last = "0:a"
            for i in range(1, len(sub_audios)):
                label = f"x{i}"
                fc_parts.append(
                    f"[{last}][{i}:a]acrossfade=d={crossfade}:c1=tri:c2=tri[{label}]"
                )
                last = label
            run([
                "ffmpeg", "-y",
                *inputs,
                "-filter_complex", ";".join(fc_parts),
                "-map", f"[{last}]",
                "-c:a", "aac", "-b:a", "192k",
                str(musique_path)
            ])
        else:
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


# ============================================================
# Mode SCENARIO : timeline explicite avec pistes ancrées
# (phase / sequence / duree / fond) et conflit mix ou écrase.
# ============================================================
def _bornes_phase(plan, n):
    """Bornes absolues [debut, fin] (en s) de la phase n dans le plan."""
    debut = 0.0
    fin = None
    cumul = 0.0
    for (np, _t, d, _dir) in plan:
        if np == n and fin is None:
            debut = cumul
        cumul += d
        if np == n:
            fin = cumul
    if fin is None:
        print(f"ERREUR audio scenario : phase {n} introuvable dans le plan")
        sys.exit(1)
    return debut, fin


def _bornes_sequence(plan, n):
    """Bornes absolues [debut, fin] de la n-ième séquence (1-based)."""
    if n < 1 or n > len(plan):
        print(f"ERREUR audio scenario : sequence {n} hors plan (1-{len(plan)})")
        sys.exit(1)
    cumul = 0.0
    for i, (_np, _t, d, _dir) in enumerate(plan):
        if i + 1 == n:
            return cumul, cumul + d
        cumul += d


def _silence(sortie_path, duree):
    run([
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
        "-t", str(duree),
        "-c:a", "aac", "-b:a", "192k",
        str(sortie_path),
    ])


def _construire_timeline_ecrase(pistes_ecrase, duree_totale):
    """
    Découpe la timeline en slots non chevauchants. Les pistes plus tardives
    (dans l'ordre de déclaration) écrasent les plus anciennes sur leur fenêtre.
    Retourne une liste triée de tuples (debut, fin, piste_ou_None).
    """
    slots = [(0.0, duree_totale, None)]
    for piste in pistes_ecrase:
        d_p, f_p = piste["debut_abs"], piste["fin_abs"]
        new_slots = []
        for s_deb, s_fin, s_piste in slots:
            # Aucun chevauchement → on garde tel quel
            if s_fin <= d_p or s_deb >= f_p:
                new_slots.append((s_deb, s_fin, s_piste))
                continue
            # Portion à gauche
            if s_deb < d_p:
                new_slots.append((s_deb, d_p, s_piste))
            # Portion à droite
            if s_fin > f_p:
                new_slots.append((f_p, s_fin, s_piste))
        new_slots.append((d_p, f_p, piste))
        new_slots.sort()
        slots = new_slots
    return slots


def _resoudre_fenetre(entry, plan, duree_totale):
    """Calcule (debut_abs, fin_abs) à partir de l'ancre."""
    if not isinstance(entry, dict):
        print(f"ERREUR audio scenario : entrée mal formée (attendu un dict, "
              f"reçu {type(entry).__name__}) : {entry!r}")
        print("  → Vérifie l'indentation et les commentaires dans audio.scenario")
        sys.exit(1)
    ancre = entry.get("ancre")
    if ancre == "duree":
        debut = parser_timestamp(entry["de"])
        fin = parser_timestamp(entry["a"])
    elif ancre == "phase":
        debut, fin = _bornes_phase(plan, int(entry["n"]))
    elif ancre == "sequence":
        debut, fin = _bornes_sequence(plan, int(entry["n"]))
    elif ancre == "fond":
        debut, fin = 0.0, duree_totale
    else:
        print(f"ERREUR audio scenario : ancre inconnue '{ancre}' dans entrée : {entry}")
        print("  Ancres valides : phase | sequence | duree | fond")
        sys.exit(1)
    return debut, fin


def construire_piste_audio_scenario(piste_path, plan, scenario_cfg,
                                     conflit_par_defaut="ecrase"):
    """
    Construit la piste audio à partir d'une liste de directives.
    Chaque entrée : {ancre, fichier, ..., mode: "mix"|"ecrase"}
    """
    # Filtre les entrées None / vides (par ex. provenant d'une ligne YAML
    # mal indentée parmi les commentaires).
    scenario_cfg = [e for e in (scenario_cfg or []) if isinstance(e, dict)]
    if not scenario_cfg:
        print("ERREUR audio scenario : 'audio.scenario' est vide ou n'a "
              "aucune entrée valide.")
        print("  → Si tu n'as pas encore défini de timeline, mets "
              "audio.mode: \"auto\".")
        sys.exit(1)

    duree_totale = sum(p[2] for p in plan)

    # 1. Normalisation des pistes
    pistes = []
    for entry in scenario_cfg:
        debut, fin = _resoudre_fenetre(entry, plan, duree_totale)
        if fin <= debut:
            print(f"ERREUR audio scenario : fenêtre vide pour {entry}")
            sys.exit(1)
        ancre = entry.get("ancre")
        # Une piste 'fond' couvre tout et par défaut se mixe.
        mode_default = "mix" if ancre == "fond" else conflit_par_defaut
        pistes.append({
            "fichier": entry["fichier"],
            "debut_abs": debut,
            "fin_abs": fin,
            "debut_extrait": parser_timestamp(entry.get("debut")),
            "fin_extrait": parser_timestamp(entry.get("fin")),
            "aleatoire": entry.get("aleatoire", False),
            "volume": float(entry.get("volume", 1.0)),
            "mode": entry.get("mode", mode_default),
            "ancre": ancre,
        })
        if not Path(entry["fichier"]).exists():
            print(f"ERREUR audio scenario : fichier introuvable : {entry['fichier']}")
            sys.exit(1)

    pistes_ecrase = [p for p in pistes if p["mode"] == "ecrase"]
    pistes_mix    = [p for p in pistes if p["mode"] == "mix"]

    print(f"  Audio scenario : {len(pistes_ecrase)} piste(s) écrase, "
          f"{len(pistes_mix)} piste(s) mix, durée totale {duree_totale:.0f}s")

    # 2. Construction de la timeline "écrase" (slots non chevauchants)
    slots = _construire_timeline_ecrase(pistes_ecrase, duree_totale)

    sub_audios = []
    for idx, (s_deb, s_fin, s_piste) in enumerate(slots):
        d = s_fin - s_deb
        sub_path = config.WORK_DIR / f"audio_slot_{idx:03d}.aac"
        if s_piste is None:
            print(f"    [slot {idx}] {s_deb:.1f}-{s_fin:.1f}s : silence")
            _silence(sub_path, d)
        else:
            print(f"    [slot {idx}] {s_deb:.1f}-{s_fin:.1f}s : {s_piste['fichier']}")
            entree_norm = {
                "fichier": s_piste["fichier"],
                "debut": s_piste["debut_extrait"],
                "fin": s_piste["fin_extrait"],
                "aleatoire": s_piste["aleatoire"],
                "duree": None,
            }
            preparer_extrait_audio(entree_norm, sub_path, d)
            # Volume éventuel
            if s_piste["volume"] != 1.0:
                sub_vol = config.WORK_DIR / f"audio_slot_{idx:03d}_vol.aac"
                run([
                    "ffmpeg", "-y", "-i", str(sub_path),
                    "-filter:a", f"volume={s_piste['volume']}",
                    "-c:a", "aac", "-b:a", "192k",
                    str(sub_vol),
                ])
                sub_path = sub_vol
        sub_audios.append(sub_path)

    # 3. Concaténation des slots → timeline.aac
    timeline_path = config.WORK_DIR / "audio_timeline.aac"
    liste = config.WORK_DIR / "liste_audio_scenario.txt"
    with open(liste, "w") as f:
        for a in sub_audios:
            f.write(f"file '{Path(a).resolve()}'\n")
    run([
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0",
        "-i", str(liste),
        "-c", "copy",
        str(timeline_path),
    ])

    # 4. Mixage des pistes "mix" par-dessus
    if not pistes_mix:
        shutil.copy(str(timeline_path), str(piste_path))
        return

    print(f"  Mixage de {len(pistes_mix)} piste(s) overlay...")
    inputs = ["-i", str(timeline_path)]
    fc_parts = []
    labels_amix = ["[0:a]"]

    for idx, p in enumerate(pistes_mix):
        d_piste = p["fin_abs"] - p["debut_abs"]
        extrait = config.WORK_DIR / f"audio_mix_{idx:02d}.aac"
        entree_norm = {
            "fichier": p["fichier"],
            "debut": p["debut_extrait"],
            "fin": p["fin_extrait"],
            "aleatoire": p["aleatoire"],
            "duree": None,
        }
        preparer_extrait_audio(entree_norm, extrait, d_piste)
        inputs += ["-i", str(extrait)]
        delai_ms = int(p["debut_abs"] * 1000)
        fc_parts.append(
            f"[{idx+1}:a]adelay={delai_ms}|{delai_ms},"
            f"volume={p['volume']}[m{idx}]"
        )
        labels_amix.append(f"[m{idx}]")

    fc_parts.append(
        f"{''.join(labels_amix)}amix=inputs={len(labels_amix)}"
        f":duration=first:dropout_transition=0[out]"
    )
    run([
        "ffmpeg", "-y",
        *inputs,
        "-filter_complex", ";".join(fc_parts),
        "-map", "[out]",
        "-c:a", "aac", "-b:a", "192k",
        str(piste_path),
    ])


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
