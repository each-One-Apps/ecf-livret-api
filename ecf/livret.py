"""
Chargement des livrets disponibles et assemblage journal -> PDF.

Un « livret » = un template PDF vierge + sa géométrie extraite. Le nom du
fichier porte le code du titre professionnel : ajouter un titre revient à
déposer `template_<CODE>.pdf` et `coords_<CODE>.json` dans ce dossier.
"""
import json
import os
from functools import lru_cache

from .parser import lire_journal
from .render import LivretPlein, rendre

DOSSIER = os.path.dirname(__file__)
DEFAUT = "TP-00520"

# Seule la fiche principale est utilisée (5 lignes par activité). La page
# « évaluations complémentaires » est déjà cartographiée : passer à
# ("principale", "complementaire") suffirait à porter la capacité à 9.
BLOCS_AUTORISES = ("principale",)


class LivretInconnu(ValueError):
    pass


def codes_disponibles():
    return sorted(
        f[len("coords_"):-len(".json")]
        for f in os.listdir(DOSSIER)
        if f.startswith("coords_") and f.endswith(".json")
    )


@lru_cache(maxsize=8)
def charger(code):
    chemin_coords = os.path.join(DOSSIER, f"coords_{code}.json")
    chemin_pdf = os.path.join(DOSSIER, f"template_{code}.pdf")
    if not (os.path.exists(chemin_coords) and os.path.exists(chemin_pdf)):
        raise LivretInconnu(
            f"livret « {code} » inconnu (disponibles : {', '.join(codes_disponibles())})"
        )
    with open(chemin_coords, encoding="utf-8") as f:
        coords = json.load(f)
    with open(chemin_pdf, "rb") as f:
        template = f.read()
    return template, coords


def construire(journal, code=DEFAUT):
    """journal brut -> (pdf, rapport). Lève JournalInvalide / LivretPlein / ValueError."""
    template, coords = charger(code)
    evaluations = lire_journal(journal)
    pdf, tronquees = rendre(template, coords, evaluations, BLOCS_AUTORISES)

    par_activite = {}
    for ev in evaluations:
        par_activite[ev["activite"]] = par_activite.get(ev["activite"], 0) + 1

    return pdf, {
        "livret": code,
        "evaluations": len(evaluations),
        "par_activite": par_activite,
        "descriptions_tronquees": tronquees,
    }


__all__ = ["construire", "charger", "codes_disponibles", "LivretInconnu", "LivretPlein"]
