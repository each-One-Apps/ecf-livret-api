"""
Lecture du journal d'évaluations.

Le journal est la concaténation brute des blocs produits par Fillout, séparés
par une ligne de tirets. C'est volontairement le format d'origine : Make se
contente de coller l'ancien journal et les nouveaux blocs bout à bout, sans
construire ni JSON ni séparateur — moins il y a de logique dans Make, moins il
y a d'endroits où ça casse.

Bloc type :

    Date : 2026-08-11
    Activité : 1. Contribuer à l'efficacité commerciale d'une unité marchande…
    Compétences évaluées : 1, 3
    Description des compétences : Test test test
    ----------
"""
import re

SEPARATEUR = re.compile(r"^\s*-{3,}\s*$", re.M)

CHAMPS_ATTENDUS = ("date", "activite", "competences", "description")

# Un libellé en début de ligne ouvre un champ ; sa valeur court jusqu'au libellé
# suivant. Une description sur plusieurs lignes passe donc sans traitement
# particulier. `lastgroup` donne directement la clé.
RE_LIBELLE = re.compile(
    r"^[ \t]*(?:"
    r"(?P<date>date)"
    r"|(?P<competences>comp[ée]tences\s+[ée]valu[ée]es)"
    r"|(?P<description>description\s+des\s+comp[ée]tences)"
    r"|(?P<activite>activit[ée])"
    r")[ \t]*:[ \t]*",
    re.I | re.M,
)

RE_ISO = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")
RE_FR = re.compile(r"^(\d{2})/(\d{2})/(\d{4})$")


class JournalInvalide(ValueError):
    """Le journal ne peut pas être lu — on refuse plutôt que de deviner."""


def _champs(bloc):
    """Découpe un bloc en {clé: valeur} d'après ses libellés."""
    trouves = list(RE_LIBELLE.finditer(bloc))
    champs = {}
    for i, m in enumerate(trouves):
        fin = trouves[i + 1].start() if i + 1 < len(trouves) else len(bloc)
        champs[m.lastgroup] = bloc[m.end():fin].strip()
    return champs


def _date(valeur, rang):
    valeur = valeur.strip()
    m = RE_ISO.match(valeur)
    if m:
        return f"{m.group(3)}/{m.group(2)}/{m.group(1)}"
    if RE_FR.match(valeur):
        return valeur
    raise JournalInvalide(
        f"évaluation {rang} : date « {valeur} » illisible (attendu AAAA-MM-JJ)"
    )


def _numero_activite(valeur, rang):
    m = re.match(r"\s*(\d+)\s*[.)-]", valeur)
    if not m:
        raise JournalInvalide(
            f"évaluation {rang} : l'activité « {valeur[:60]} » ne commence pas "
            f"par son numéro (attendu « 1. … »)"
        )
    return int(m.group(1))


def _competences(valeur, rang):
    nums = [int(n) for n in re.findall(r"\d+", valeur)]
    if not nums:
        raise JournalInvalide(f"évaluation {rang} : aucune compétence évaluée")
    vues = []
    for n in nums:  # dédoublonne en gardant l'ordre de saisie
        if n not in vues:
            vues.append(n)
    return vues


def lire_journal(journal):
    """Journal brut -> liste ordonnée d'évaluations.

    Les blocs vides sont ignorés : la concaténation côté Make peut en produire
    quand une seule des deux activités est renseignée.
    """
    if not journal or not journal.strip():
        return []

    evaluations = []
    for bloc in SEPARATEUR.split(journal):
        if not bloc.strip():
            continue
        rang = len(evaluations) + 1
        champs = _champs(bloc)

        manquants = [c for c in CHAMPS_ATTENDUS if not champs.get(c)]
        if manquants:
            raise JournalInvalide(
                f"évaluation {rang} : champ(s) {', '.join(manquants)} absent(s) ou vide(s)"
            )

        evaluations.append({
            "activite": _numero_activite(champs["activite"], rang),
            "intitule": champs["activite"].strip(),
            "date": _date(champs["date"], rang),
            "competences": _competences(champs["competences"], rang),
            "description": " ".join(champs["description"].split()),
        })
    return evaluations


def concatener(journal, *blocs):
    """Ajoute des blocs au journal. Utilisé par les tests et en secours.

    En production c'est Make qui concatène — les blocs Fillout se terminent
    déjà par leur séparateur.
    """
    morceaux = [journal.strip()] if journal and journal.strip() else []
    for bloc in blocs:
        if bloc and bloc.strip():
            morceaux.append(bloc.strip())
    return "\n----------\n".join(morceaux)
