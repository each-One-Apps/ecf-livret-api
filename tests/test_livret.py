import io

import pytest
from pypdf import PdfReader

from ecf.livret import charger, construire
from ecf.render import LivretPlein


def bloc(activite, jour, competences="1", description="Évaluation"):
    return (
        f"Date : 2026-08-{jour:02d}\n"
        f"Activité : {activite}. Peu importe l'intitulé\n"
        f"Compétences évaluées : {competences}\n"
        f"Description des compétences : {description}"
    )


def journal(*blocs):
    return "\n----------\n".join(blocs)


def texte_page(pdf_bytes, index):
    return PdfReader(io.BytesIO(pdf_bytes)).pages[index].extract_text()


def test_geometrie_conforme_au_template():
    _, coords = charger("TP-00520")
    assert set(coords["activites"]) == {"1", "2"}
    for numero, page in (("1", 2), ("2", 5)):
        activite = coords["activites"][numero]
        assert len(activite["competences"]) == 4
        principale = activite["blocs"][0]
        assert principale["type"] == "principale"
        assert principale["page"] == page
        assert len(principale["lignes"]) == 5


def test_journal_vide_rend_le_livret_vierge():
    pdf, rapport = construire("")
    assert rapport["evaluations"] == 0
    template, _ = charger("TP-00520")
    assert len(pdf) > 0 and rapport["par_activite"] == {}


def test_description_ecrite_sur_la_bonne_page():
    pdf, rapport = construire(journal(
        bloc(1, 11, "1, 3", "Veille concurrentielle en magasin"),
        bloc(2, 12, "4", "Entretien de vente filmé"),
    ))
    assert rapport["par_activite"] == {1: 1, 2: 1}
    assert "Veille concurrentielle en magasin" in texte_page(pdf, 2)
    assert "Entretien de vente filmé" in texte_page(pdf, 5)
    # Chaque description reste sur la fiche de son activité.
    assert "Entretien de vente" not in texte_page(pdf, 2)
    assert "Veille concurrentielle" not in texte_page(pdf, 5)


def test_les_lignes_se_remplissent_dans_l_ordre():
    pdf, _ = construire(journal(*[
        bloc(1, 10 + i, "1", f"Évaluation numéro {i}") for i in range(1, 6)
    ]))
    page = texte_page(pdf, 2)
    positions = [page.index(f"Évaluation numéro {i}") for i in range(1, 6)]
    assert positions == sorted(positions)


def test_meme_journal_memes_octets():
    j = journal(bloc(1, 11, "1, 3"), bloc(2, 12, "2"))
    assert construire(j)[0] == construire(j)[0]


def test_un_bloc_de_plus_ne_deplace_pas_les_precedents():
    """Le rejeu et l'ajout doivent être sans effet sur ce qui est déjà écrit."""
    premier = bloc(1, 11, "1", "Toute première évaluation")
    page_avant = texte_page(construire(premier)[0], 2)
    page_apres = texte_page(construire(journal(premier, bloc(1, 12, "2", "La suivante")))[0], 2)
    assert "Toute première évaluation" in page_avant
    assert page_apres.index("Toute première évaluation") < page_apres.index("La suivante")


def test_sixieme_evaluation_refusee():
    j = journal(*[bloc(1, 10 + i, "1") for i in range(1, 7)])
    with pytest.raises(LivretPlein) as e:
        construire(j)
    assert "6e évaluation" in str(e.value) and "5 lignes" in str(e.value)


def test_cinq_par_activite_est_accepte():
    j = journal(*[bloc(a, 10 + i, "1") for a in (1, 2) for i in range(1, 6)])
    _, rapport = construire(j)
    assert rapport["par_activite"] == {1: 5, 2: 5}


def test_competence_hors_referentiel_refusee():
    with pytest.raises(ValueError) as e:
        construire(bloc(1, 11, "5"))
    assert "compétence 5" in str(e.value)


def test_activite_absente_du_livret_refusee():
    with pytest.raises(ValueError) as e:
        construire(bloc(3, 11, "1"))
    assert "activité 3" in str(e.value)


def test_description_trop_longue_est_signalee():
    _, rapport = construire(bloc(1, 11, "1", "Texte très long. " * 200))
    assert rapport["descriptions_tronquees"] == [1]


def test_livret_inconnu():
    from ecf.livret import LivretInconnu

    with pytest.raises(LivretInconnu):
        construire("", "TP-99999")
