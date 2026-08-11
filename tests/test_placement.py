"""Le formateur choisit la ligne : renvoyer sur une place prise CORRIGE."""
import io

import pytest
from pypdf import PdfReader

from ecf.livret import construire
from ecf.render import LivretPlein


def bloc(description, jour=11, ligne=None, activite=1, competences="1"):
    entete = f"Ligne : {ligne}\n" if ligne else ""
    return (
        f"{entete}Date : 2026-08-{jour:02d}\n"
        f"Activité : {activite}. Peu importe\n"
        f"Compétences évaluées : {competences}\n"
        f"Description des compétences : {description}\n"
        "----------"
    )


def texte(pdf, page=2):
    brut = PdfReader(io.BytesIO(pdf)).pages[page].extract_text() or ""
    return "".join(brut.split())


def ligne_occupee(pdf, texte_cherche, activite=1):
    """-> numéro de ligne du tableau où ce texte a été dessiné."""
    import pdfplumber

    from ecf.livret import charger

    _, coords = charger("TP-00520")
    bloc_principal = coords["activites"][str(activite)]["blocs"][0]
    with pdfplumber.open(io.BytesIO(pdf)) as doc:
        page = doc.pages[bloc_principal["page"]]
        cible = [c for c in page.chars if c["text"] == texte_cherche[0]]
        for i, l in enumerate(bloc_principal["lignes"], 1):
            _, y_bas, _, y_haut = l["desc"]
            for c in cible:
                y = page.height - c["top"]
                if y_bas <= y <= y_haut:
                    return i
    return None


def test_la_ligne_choisie_est_respectee_et_le_trou_permis():
    """Choisir la ligne 3 alors que 1 et 2 sont vides est un choix légitime."""
    pdf, rapport = construire(bloc("Zorro sur la troisieme", ligne=3))
    assert rapport["evaluations"] == 1
    assert ligne_occupee(pdf, "Z") == 3


def test_renvoyer_sur_une_ligne_prise_corrige():
    journal = bloc("Version fausse", ligne=2) + "\n" + bloc("Version corrigee", jour=12, ligne=2)
    pdf, rapport = construire(journal)
    assert rapport["evaluations"] == 1
    assert rapport["lignes_remplacees"] == [{"activite": 1, "ligne": 2}]
    page = texte(pdf)
    assert "Versioncorrigee" in page and "Versionfausse" not in page


def test_les_anciennes_sans_numero_gardent_leur_ordre():
    """Les évaluations enregistrées avant ce champ ne doivent pas bouger."""
    journal = "\n".join([bloc("Plus tardive", jour=20), bloc("Plus ancienne", jour=11)])
    _, rapport = construire(journal)
    from ecf.parser import lire_journal
    relues, _ = lire_journal(rapport["journal"])
    assert [(e["ligne"], e["description"]) for e in relues] == [
        (1, "Plus ancienne"), (2, "Plus tardive")]


def test_un_bloc_numerote_ecrase_l_ancienne_de_cette_place():
    journal = "\n".join([
        bloc("Ancienne une", jour=11),
        bloc("Ancienne deux", jour=12),
        bloc("Correction de la deux", jour=13, ligne=2),
    ])
    pdf, rapport = construire(journal)
    assert rapport["lignes_remplacees"] == [{"activite": 1, "ligne": 2}]
    page = texte(pdf)
    assert "Ancienneune" in page and "Correctiondeladeux" in page
    assert "Anciennedeux" not in page


def test_ligne_hors_capacite_refusee():
    with pytest.raises(ValueError) as e:
        construire(bloc("Trop loin", ligne=6))
    assert "ligne 6" in str(e.value)


def test_les_activites_ont_chacune_leurs_cinq_lignes():
    journal = "\n".join([bloc(f"A1-{i}", jour=10 + i, ligne=i) for i in range(1, 6)]
                        + [bloc(f"A2-{i}", jour=10 + i, ligne=i, activite=2) for i in range(1, 6)])
    _, rapport = construire(journal)
    assert rapport["par_activite"] == {1: 5, 2: 5}
    assert rapport["lignes_remplacees"] == []


def test_le_journal_canonique_porte_le_numero_de_ligne():
    _, rapport = construire(bloc("Sur la quatre", ligne=4))
    assert "Ligne : 4" in rapport["journal"]


def test_trop_d_anciennes_sans_numero_refuse():
    journal = "\n".join(bloc(f"Éval {i}", jour=10 + i) for i in range(1, 7))
    with pytest.raises(LivretPlein):
        construire(journal)
