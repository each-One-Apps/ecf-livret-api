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

# Second type de bloc : l'avis final d'une activité-type. Reconnaissable à sa
# première ligne, qui n'existe pas dans un bloc d'évaluation.
RE_LIBELLE_AVIS = re.compile(
    r"^[ \t]*(?:"
    r"(?P<activite>avis\s+activit[ée])"
    r"|(?P<resultat>r[ée]sultat)"
    r"|(?P<points_attention>points?\s+d['’]attention)"
    r"|(?P<a_reevaluer>comp[ée]tences\s+à\s+r[ée][ée]valuer)"
    r"|(?P<formateur_1>formateur\s*1)"
    r"|(?P<formateur_2>formateur\s*2)"
    r"|(?P<date>date\s+de\s+l['’]avis)"
    r"|(?P<signature_1>signature\s*1)"
    r"|(?P<signature_2>signature\s*2)"
    r")[ \t]*:[ \t]*",
    re.I | re.M,
)
RE_EST_AVIS = re.compile(r"^[ \t]*avis\s+activit[ée][ \t]*:", re.I | re.M)


class JournalInvalide(ValueError):
    """Le journal ne peut pas être lu — on refuse plutôt que de deviner."""


def _champs(bloc, rang, regle=None):
    """Découpe un bloc en {clé: valeur} d'après ses libellés.

    Un libellé qui revient deux fois signale deux blocs collés faute de
    séparateur — cas réel quand on concatène plusieurs sources. Sans ce
    contrôle, la seconde valeur écrase la première et une évaluation
    disparaîtrait sans un mot.
    """
    trouves = list((regle or RE_LIBELLE).finditer(bloc))
    champs = {}
    for i, m in enumerate(trouves):
        cle = m.lastgroup
        if cle in champs:
            raise JournalInvalide(
                f"évaluation {rang} : le libellé « {m.group(0).strip()} » apparaît deux fois. "
                f"Deux blocs sont probablement collés — il manque une ligne « ---------- » "
                f"entre eux."
            )
        fin = trouves[i + 1].start() if i + 1 < len(trouves) else len(bloc)
        champs[cle] = bloc[m.end():fin].strip()
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


def _lire_avis(bloc, rang):
    """Bloc « Avis activité » -> dict, ou None s'il n'est pas encore rempli.

    Tant que le formateur n'a pas tranché, le résultat est vide : le bloc
    arrive quand même mais il n'y a rien à dessiner. On l'ignore plutôt que de
    le refuser — un avis se remplit après les évaluations, pas en même temps.
    """
    champs = _champs(bloc, rang, RE_LIBELLE_AVIS)

    # Rien à dessiner tant que le formateur n'a pas tranché. On sort AVANT de
    # réclamer le numéro d'activité : un bloc entièrement vide arrive à chaque
    # soumission qui ne concerne pas cette activité-là.
    if not champs.get("resultat"):
        return None

    numero = _numero_activite_libre(champs.get("activite", ""), rang)

    avis = {
        "activite": numero,
        "resultat": " ".join(champs["resultat"].split()),
        "points_attention": " ".join(champs.get("points_attention", "").split()),
        "a_reevaluer": " ".join(champs.get("a_reevaluer", "").split()),
        "formateurs": [],
    }
    date = champs.get("date", "").strip()
    avis["date"] = _date(date, rang) if date else ""
    for i in ("1", "2"):
        avis["formateurs"].append({
            "nom": " ".join(champs.get(f"formateur_{i}", "").split()),
            "signature": champs.get(f"signature_{i}", "").strip(),
        })
    return avis


def _numero_activite_libre(valeur, rang):
    """« Activité-type 1 », « 1. Contribuer… » ou « 1 » -> 1."""
    m = re.search(r"(\d+)", valeur or "")
    if not m:
        raise JournalInvalide(
            f"avis {rang} : activité introuvable (« {valeur[:40]} »). La ligne "
            f"« Avis activité » doit porter le choix du formateur, par exemple "
            f"« Activité-type 1 ». Un seul bloc d'avis par soumission : c'est ce "
            f"choix qui désigne la fiche."
        )
    return int(m.group(1))


def lire_journal(journal):
    """Journal brut -> (évaluations, avis).

    Les blocs vides sont ignorés : la concaténation côté Make peut en produire
    quand une seule des deux activités est renseignée.
    """
    if not journal or not journal.strip():
        return [], []

    evaluations, avis = [], []
    for bloc in SEPARATEUR.split(journal):
        if not bloc.strip():
            continue

        if RE_EST_AVIS.search(bloc):
            lu = _lire_avis(bloc, len(avis) + 1)
            if lu:
                avis.append(lu)
            continue

        rang = len(evaluations) + 1
        champs = _champs(bloc, rang)

        # Squelette d'évaluation : une soumission qui ne portait qu'un avis fait
        # quand même émettre le bloc, avec la date et l'activité pré-remplies mais
        # rien à écrire dans le tableau. Il n'y a rien à perdre, on l'ignore.
        #
        # `champs` non vide est essentiel : il prouve qu'on est bien dans notre
        # format. Un contenu étranger (une réflexion AFEST, par exemple) n'a aucun
        # libellé reconnu et doit être REFUSÉ, pas ignoré — sans quoi le scénario
        # écraserait le journal d'un apprenant par un livret vide.
        if champs and not champs.get("competences") and not champs.get("description"):
            continue

        manquants = [c for c in CHAMPS_ATTENDUS if not champs.get(c)]
        if manquants:
            raise JournalInvalide(
                f"évaluation {rang} : champ(s) {', '.join(manquants)} absent(s) ou vide(s). "
                f"Une évaluation partielle est refusée ; un bloc entièrement vide serait ignoré."
            )

        evaluations.append({
            "activite": _numero_activite(champs["activite"], rang),
            "intitule": champs["activite"].strip(),
            "date": _date(champs["date"], rang),
            "competences": _competences(champs["competences"], rang),
            "description": " ".join(champs["description"].split()),
        })
    return evaluations, avis


def ecrire_journal(evaluations, avis=()):
    """Évaluations -> journal canonique, au même format que l'entrée.

    C'est ce texte que l'appelant réécrit dans son champ : dédoublonné, trié,
    et toujours relisible par `lire_journal`. Sans ça, un émetteur qui renvoie
    son historique complet fait grossir le champ à chaque soumission.
    """
    blocs = []
    for ev in evaluations:
        blocs.append(
            "Date : {date}\n"
            "Activité : {intitule}\n"
            "Compétences évaluées : {competences}\n"
            "Description des compétences : {description}\n"
            "----------".format(
                date=ev["date"],
                intitule=ev["intitule"],
                competences=", ".join(str(n) for n in ev["competences"]),
                description=ev["description"],
            )
        )
    for a in avis:
        blocs.append(
            "Avis activité : {activite}\n"
            "Résultat : {resultat}\n"
            "Points d'attention : {points_attention}\n"
            "Compétences à réévaluer : {a_reevaluer}\n"
            "Formateur 1 : {n1}\n"
            "Formateur 2 : {n2}\n"
            "Date de l'avis : {date}\n"
            "Signature 1 : {s1}\n"
            "Signature 2 : {s2}\n"
            "----------".format(
                activite=a["activite"], resultat=a["resultat"],
                points_attention=a["points_attention"], a_reevaluer=a["a_reevaluer"],
                date=a["date"], n1=a["formateurs"][0]["nom"], n2=a["formateurs"][1]["nom"],
                s1=a["formateurs"][0]["signature"], s2=a["formateurs"][1]["signature"],
            )
        )
    return "\n".join(blocs) + ("\n" if blocs else "")


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
