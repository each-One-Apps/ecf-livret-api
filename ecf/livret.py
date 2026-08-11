"""
Chargement des livrets disponibles et assemblage journal -> PDF.

Un « livret » = un template PDF vierge + sa géométrie extraite. Le nom du
fichier porte le code du titre professionnel : ajouter un titre revient à
déposer `template_<CODE>.pdf` et `coords_<CODE>.json` dans ce dossier.
"""
import json
import os
from functools import lru_cache

from .parser import ecrire_journal, lire_journal
from .render import LivretPlein, SignatureIntrouvable, rendre

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


def _cle_chronologique(ev):
    """JJ/MM/AAAA -> clé triable. Une date illisible n'arrive pas ici : le
    parseur l'a déjà refusée."""
    jour, mois, annee = ev["date"].split("/")
    return (annee, mois, jour)


def _sans_doublons(evaluations):
    """Retire les blocs strictement identiques et dit lesquels.

    L'émetteur peut renvoyer l'historique complet à chaque soumission : deux
    blocs identiques en tout point sont alors un renvoi, pas deux évaluations.
    Le rapport les signale — sur un document de jury, rien ne disparaît en
    silence.
    """
    vues, gardees, doublons = set(), [], []
    for rang, ev in enumerate(evaluations, 1):
        empreinte = (ev["activite"], ev["date"], tuple(ev["competences"]), ev["description"])
        if empreinte in vues:
            doublons.append(rang)
            continue
        vues.add(empreinte)
        gardees.append(ev)
    return gardees, doublons


def _placer(evaluations, coords):
    """Attribue à chaque évaluation sa ligne dans le tableau de son activité.

    Le formateur choisit sa place : renvoyer une évaluation sur une ligne déjà
    prise la **corrige** au lieu de s'y ajouter. C'est ce qui lui donne un moyen
    de correction autonome depuis le formulaire.

    Les évaluations enregistrées avant l'existence de ce champ n'ont pas de
    numéro : elles reçoivent d'abord leur place d'après leur ordre chronologique,
    puis les blocs numérotés écrasent celle qu'ils réclament. Une correction
    porte donc bien sur l'évaluation qui occupait visiblement cette ligne.

    Les trous sont permis : choisir la ligne 3 alors que 1 et 2 sont vides est un
    choix légitime, pas une erreur.
    """
    par_activite = {}
    for ev in evaluations:
        par_activite.setdefault(ev["activite"], []).append(ev)

    placees, remplacees = [], []
    for numero in sorted(par_activite):
        activite = coords["activites"].get(str(numero))
        if activite is None:
            raise ValueError(f"évaluation : activité {numero} absente du livret")
        capacite = sum(len(b["lignes"]) for b in activite["blocs"]
                       if b["type"] in BLOCS_AUTORISES)

        evs = par_activite[numero]
        places = {}

        anciennes = sorted([e for e in evs if e["ligne"] is None], key=_cle_chronologique)
        for rang, ev in enumerate(anciennes, 1):
            if rang > capacite:
                raise LivretPlein(
                    f"activité {numero} : {rang}e évaluation, le livret n'accepte "
                    f"que {capacite} lignes"
                )
            places[rang] = dict(ev, ligne=rang)

        for ev in [e for e in evs if e["ligne"] is not None]:
            ligne = ev["ligne"]
            if not 1 <= ligne <= capacite:
                raise ValueError(
                    f"activité {numero} : ligne {ligne} demandée, le livret n'en a "
                    f"que {capacite}"
                )
            if ligne in places:
                remplacees.append({"activite": numero, "ligne": ligne})
            places[ligne] = ev

        placees.extend(places[l] for l in sorted(places))
    return placees, remplacees


def _dernier_avis_par_activite(avis):
    """Un seul avis final par activité-type : le dernier reçu l'emporte.

    Refaire son avis doit corriger le précédent, pas s'y ajouter — sans quoi les
    deux seraient dessinés au même endroit, deux cases cochées et deux signatures
    empilées.

    C'est l'ordre d'arrivée qui tranche, pas la date saisie : un avis refait le
    jour même porterait la même date, et le nouveau bloc est toujours ajouté en
    fin de journal.
    """
    retenu, remplaces = {}, []
    for a in avis:
        if a["activite"] in retenu:
            remplaces.append(a["activite"])
        retenu[a["activite"]] = a
    return [retenu[k] for k in sorted(retenu)], remplaces


def construire(journal, code=DEFAUT):
    """journal brut -> (pdf, rapport). Lève JournalInvalide / LivretPlein / ValueError."""
    template, coords = charger(code)
    evaluations, avis = lire_journal(journal)
    evaluations, doublons = _sans_doublons(evaluations)
    avis, avis_remplaces = _dernier_avis_par_activite(avis)

    # La place vient du formateur quand il l'a choisie ; sinon de l'ordre
    # chronologique, car l'ordre du journal n'est pas garanti.
    evaluations, lignes_remplacees = _placer(evaluations, coords)

    pdf, tronquees = rendre(template, coords, evaluations, BLOCS_AUTORISES, avis)

    par_activite = {}
    for ev in evaluations:
        par_activite[ev["activite"]] = par_activite.get(ev["activite"], 0) + 1

    return pdf, {
        "livret": code,
        "evaluations": len(evaluations),
        "par_activite": par_activite,
        "descriptions_tronquees": tronquees,
        "doublons_ignores": doublons,
        # Journal canonique à réécrire à la source : dédoublonné et trié.
        "avis": len(avis),
        "avis_remplaces": avis_remplaces,
        "lignes_remplacees": lignes_remplacees,
        "journal": ecrire_journal(evaluations, avis),
    }


__all__ = ["construire", "charger", "codes_disponibles", "LivretInconnu", "LivretPlein", "SignatureIntrouvable"]
