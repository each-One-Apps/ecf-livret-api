import pytest

from ecf.parser import JournalInvalide, concatener, lire_journal


def lire_evaluations(journal):
    """lire_journal renvoie (évaluations, avis) ; ici seules les évaluations comptent."""
    return lire_journal(journal)[0]

# Bloc recopié tel quel de la sortie du webhook Make (exécution du 2026-08-11),
# espaces de fin de ligne compris.
BLOC_REEL = (
    "Date : 2026-08-11\n"
    "Activité : 1. Contribuer à l'efficacité commerciale d'une unité marchande "
    "dans un environnement omnicanal \n"
    "Compétences évaluées : 1, 3 \n"
    "Description des compétences : Test test test \n"
    "----------"
)


def test_bloc_reel_du_webhook():
    (ev,) = lire_evaluations(BLOC_REEL)
    assert ev["activite"] == 1
    assert ev["date"] == "11/08/2026"
    assert ev["competences"] == [1, 3]
    assert ev["description"] == "Test test test"


def test_journal_vide():
    assert lire_evaluations("") == []
    assert lire_evaluations("   \n  ") == []
    assert lire_evaluations("----------\n----------") == []


def test_deux_blocs_et_activite_2():
    journal = concatener(
        BLOC_REEL,
        "Date : 2026-08-18\n"
        "Activité : 2. Améliorer l'expérience client dans un environnement omnicanal\n"
        "Compétences évaluées : 4\n"
        "Description des compétences : Entretien de vente filmé",
    )
    evs = lire_evaluations(journal)
    assert [e["activite"] for e in evs] == [1, 2]
    assert evs[1]["competences"] == [4]


def test_description_multiligne():
    journal = (
        "Date : 2026-08-11\n"
        "Activité : 1. Peu importe\n"
        "Compétences évaluées : 2\n"
        "Description des compétences : Première ligne\n"
        "puis une deuxième\n"
        "et une troisième"
    )
    (ev,) = lire_evaluations(journal)
    assert ev["description"] == "Première ligne puis une deuxième et une troisième"


def test_ordre_des_libelles_indifferent():
    journal = (
        "Activité : 2. Peu importe\n"
        "Description des compétences : Texte\n"
        "Compétences évaluées : 1 et 3\n"
        "Date : 2026-01-05"
    )
    (ev,) = lire_evaluations(journal)
    assert (ev["activite"], ev["date"], ev["competences"]) == (2, "05/01/2026", [1, 3])


def test_competences_dedoublonnees():
    journal = (
        "Date : 2026-08-11\nActivité : 1. X\n"
        "Compétences évaluées : 3, 1, 3\nDescription des compétences : Y"
    )
    (ev,) = lire_evaluations(journal)
    assert ev["competences"] == [3, 1]


@pytest.mark.parametrize(
    "journal, extrait",
    [
        ("Date : 2026-08-11\nActivité : 1. X\nCompétences évaluées : 1", "description"),
        ("Date : hier\nActivité : 1. X\nCompétences évaluées : 1\n"
         "Description des compétences : Y", "illisible"),
        ("Date : 2026-08-11\nActivité : Contribuer à…\nCompétences évaluées : 1\n"
         "Description des compétences : Y", "numéro"),
        ("Date : 2026-08-11\nActivité : 1. X\nCompétences évaluées : aucune\n"
         "Description des compétences : Y", "aucune compétence"),
    ],
)
def test_journal_refuse(journal, extrait):
    with pytest.raises(JournalInvalide) as e:
        lire_journal(journal)
    assert extrait in str(e.value)


# Le champ qui porte le journal sert aussi à d'autres formulaires, dont les
# réflexions AFEST des apprenants. Contenu fictif, mais de la même FORME que le
# vrai : c'est la forme seule qui fait passer le test.
REFLEXION_AFEST = (
    "▪︎ La situation a été réalisé : \n"
    '"Seul.e"\n\n'
    "▪︎ Ce qu'il s'est passé :\n"
    '"Description libre de la situation de travail."\n\n'
    "▪︎ Quel est ton niveau de confiance ?\n"
    '"Bon"\n'
)


def test_une_reflexion_afest_est_refusee():
    """Garde-fou : si le scénario visait une ligne non-ECF, le service doit refuser.

    Make étant en stopOnHttpError, ce refus empêche d'écraser la réflexion d'un
    apprenant avec des blocs ECF.
    """
    with pytest.raises(JournalInvalide):
        lire_journal(REFLEXION_AFEST)


def test_des_blocs_ecf_colles_sur_une_reflexion_afest_sont_refuses():
    with pytest.raises(JournalInvalide):
        lire_journal(concatener(REFLEXION_AFEST, BLOC_REEL))


def test_separateur_normalise_par_le_rich_text():
    """Airtable stocke ce champ en markdown : `----------` peut revenir en `---`."""
    journal = concatener(BLOC_REEL, BLOC_REEL).replace("----------", "---")
    assert len(lire_evaluations(journal)) == 2


def test_le_rang_signale_est_celui_du_bloc_fautif():
    journal = concatener(BLOC_REEL, "Date : 2026-08-12\nActivité : 1. X\n"
                                    "Compétences évaluées : 1")
    with pytest.raises(JournalInvalide) as e:
        lire_journal(journal)
    assert "évaluation 2" in str(e.value)


def test_deux_blocs_colles_sont_refuses():
    """Sans séparateur, la seconde évaluation écrasait la première en silence."""
    colle = (
        "Date : 2026-08-11\nActivité : 1. X\nCompétences évaluées : 1\n"
        "Description des compétences : PREMIÈRE\n"
        "Date : 2026-08-12\nActivité : 1. Y\nCompétences évaluées : 2\n"
        "Description des compétences : SECONDE"
    )
    with pytest.raises(JournalInvalide) as e:
        lire_journal(colle)
    assert "deux fois" in str(e.value) and "----------" in str(e.value)
