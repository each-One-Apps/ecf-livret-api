"""Avis final : case résultat, textes libres, formateurs et signatures."""
import io
import json
import pathlib

import pytest
from pypdf import PdfReader

from ecf.livret import charger, construire
from ecf.render import SignatureIntrouvable

SATISFAIT = ("Avoir satisfait aux critères issus des référentiels du titre professionnel "
             "attendus pour la réalisation de cette activité-type.")
NON_SATISFAIT = ("Ne pas avoir satisfait aux critères issus des référentiels du titre "
                 "professionnel.")


@pytest.fixture(scope="module")
def signature(tmp_path_factory):
    """Un PNG minuscule servi en file:// — les tests ne touchent pas au réseau."""
    import zlib
    import struct

    def bloc(nom, donnees):
        return (struct.pack(">I", len(donnees)) + nom + donnees
                + struct.pack(">I", zlib.crc32(nom + donnees)))

    largeur = hauteur = 4
    brut = b"".join(b"\x00" + b"\x00\x00\x00\xff" * largeur for _ in range(hauteur))
    png = (b"\x89PNG\r\n\x1a\n"
           + bloc(b"IHDR", struct.pack(">IIBBBBB", largeur, hauteur, 8, 6, 0, 0, 0))
           + bloc(b"IDAT", zlib.compress(brut))
           + bloc(b"IEND", b""))
    chemin = tmp_path_factory.mktemp("sig") / "signature.png"
    chemin.write_bytes(png)
    return chemin.as_uri()


def avis(activite=1, resultat=SATISFAIT, points="", reevaluer="",
         f1="Camille A.", f2="", s1="", s2="", date="2026-08-11"):
    return (
        f"Avis activité : Activité-type {activite}\n"
        f"Résultat : {resultat}\n"
        f"Points d'attention : {points}\n"
        f"Compétences à réévaluer : {reevaluer}\n"
        f"Formateur 1 : {f1}\nFormateur 2 : {f2}\n"
        f"Date de l'avis : {date}\n"
        f"Signature 1 : {s1}\nSignature 2 : {s2}\n"
        "----------"
    )


def traits_sur(pdf, page_index):
    """Segments tracés sur une page — c'est ainsi qu'on dessine les croix."""
    import pdfplumber  # dépendance de développement

    with pdfplumber.open(io.BytesIO(pdf)) as doc:
        page = doc.pages[page_index]
        return page.height, list(page.lines)


def case_cochee(pdf, activite):
    """-> 'satisfait', 'non_satisfait', ou None si aucune croix."""
    _, coords, _ = charger("TP-00520")
    resultat = coords["activites"][str(activite)]["avis"]["resultat"]
    trouvees = []
    for cle, c in resultat.items():
        hauteur, traits = traits_sur(pdf, c["page"])
        x, y = c["centre"]
        top = hauteur - y
        proches = [l for l in traits
                   if abs((l["x0"] + l["x1"]) / 2 - x) < 6
                   and abs((l["top"] + l["bottom"]) / 2 - top) < 6]
        if proches:
            trouvees.append(cle)
    assert len(trouvees) <= 1, f"plusieurs cases cochées : {trouvees}"
    return trouvees[0] if trouvees else None


def texte(pdf, page_index):
    return "".join((PdfReader(io.BytesIO(pdf)).pages[page_index].extract_text() or "").split())


def test_la_bonne_case_est_cochee():
    assert case_cochee(construire(avis(resultat=SATISFAIT))[0], 1) == "satisfait"
    assert case_cochee(construire(avis(resultat=NON_SATISFAIT))[0], 1) == "non_satisfait"


def test_la_negation_ne_coche_pas_aussi_la_case_positive():
    """« Ne pas avoir satisfait… » CONTIENT « Avoir satisfait… »."""
    pdf, _ = construire(avis(resultat=NON_SATISFAIT))
    assert case_cochee(pdf, 1) == "non_satisfait"


def test_activite_2_les_cases_sont_sur_deux_pages():
    _, coords, _ = charger("TP-00520")
    res = coords["activites"]["2"]["avis"]["resultat"]
    assert res["satisfait"]["page"] == 5      # bas de la fiche
    assert res["non_satisfait"]["page"] == 6  # haut de la page suivante
    assert case_cochee(construire(avis(activite=2, resultat=NON_SATISFAIT))[0], 2) \
        == "non_satisfait"


def test_textes_et_formateurs_sur_la_page_d_avis():
    pdf, rapport = construire(avis(
        points="Le calcul des marges reste fragile.",
        reevaluer="Compétence 2 à revoir.",
        f1="Camille A.", f2="Imène B.",
    ))
    assert rapport["avis"] == 1
    page = texte(pdf, 3)  # page 4 : avis de l'activité 1
    for attendu in ("Lecalculdesmargesrestefragile.", "Compétence2àrevoir.",
                    "CamilleA.", "ImèneB.", "11/08/2026"):
        assert attendu in page, attendu


def test_avis_sans_resultat_est_ignore():
    """Le bloc arrive dès la première soumission ; il ne se remplit qu'à la fin."""
    _, rapport = construire(avis(resultat="", f1=""))
    assert rapport["avis"] == 0


def test_resultat_non_reconnu_refuse():
    with pytest.raises(ValueError) as e:
        construire(avis(resultat="Peut mieux faire"))
    assert "ne correspond à aucune" in str(e.value)


def test_avis_sur_une_activite_absente_refuse():
    with pytest.raises(ValueError) as e:
        construire(avis(activite=7))
    assert "activité 7" in str(e.value)


def test_signature_incrustee(signature):
    pdf, _ = construire(avis(s1=signature))
    assert len(pdf) > 0 and case_cochee(pdf, 1) == "satisfait"


def test_signature_injoignable_refuse(tmp_path):
    """Mieux vaut un refus visible qu'un livret de jury sans sa signature."""
    manquante = (tmp_path / "absente.png").as_uri()
    with pytest.raises(SignatureIntrouvable) as e:
        construire(avis(s1=manquante))
    assert "signature 1" in str(e.value)


def test_le_journal_canonique_conserve_l_avis(signature):
    from ecf.parser import lire_journal

    pdf, rapport = construire(avis(points="Un point", f2="Imène B.", s1=signature))
    _, relus, _ = lire_journal(rapport["journal"])
    assert len(relus) == 1
    assert relus[0]["activite"] == 1
    assert relus[0]["formateurs"][1]["nom"] == "Imène B."
    assert relus[0]["formateurs"][0]["signature"] == signature


def test_l_url_du_livret_refuse_aussi_une_signature_injoignable(tmp_path):
    """Le GET régénère : il doit échouer comme le POST, sans NameError."""
    from fastapi.testclient import TestClient
    from main import app

    client = TestClient(app, raise_server_exceptions=False)
    manquante = (tmp_path / "absente.png").as_uri()
    r = client.post("/update-ecf-assessment", json={"log": avis(s1=manquante)})
    assert r.status_code == 502

    ok = client.post("/update-ecf-assessment", json={"log": avis()})
    chemin = "/livret.pdf" + ok.json()["attachment"][0]["url"].split("/livret.pdf", 1)[1]
    assert client.get(chemin).status_code == 200


def test_bloc_avis_entierement_vide_ignore():
    """Une soumission qui ne concerne pas cette activité émet un bloc vide."""
    vide = ("Avis activité : \nRésultat : \nPoints d'attention : \n"
            "Compétences à réévaluer : \nFormateur 1 : \nFormateur 2 : \n"
            "Date de l'avis : \nSignature 1 : \nSignature 2 : \n----------")
    _, rapport = construire(vide)
    assert rapport["avis"] == 0


def test_avis_rempli_sans_activite_refuse():
    """Un résultat sans savoir quelle fiche cocher doit échouer, pas deviner."""
    bancal = avis().replace("Avis activité : Activité-type 1", "Avis activité : ")
    with pytest.raises(ValueError) as e:
        construire(bancal)
    assert "activité introuvable" in str(e.value)


def test_refaire_un_avis_remplace_le_precedent():
    """Il n'y a qu'un avis final par activité : le dernier reçu gagne."""
    journal = avis(resultat=SATISFAIT, f1="Premier") + "\n" \
        + avis(resultat=NON_SATISFAIT, f1="Second")
    pdf, rapport = construire(journal)
    assert rapport["avis"] == 1
    assert rapport["avis_remplaces"] == [1]
    # C'est bien le SECOND qui est dessiné, pas le premier.
    assert case_cochee(pdf, 1) == "non_satisfait"
    page = texte(pdf, 3)
    assert "Second" in page and "Premier" not in page


def test_le_journal_ne_conserve_que_l_avis_retenu():
    from ecf.parser import lire_journal

    journal = avis(f1="Premier") + "\n" + avis(f1="Second")
    _, rapport = construire(journal)
    _, relus, _ = lire_journal(rapport["journal"])
    assert len(relus) == 1
    assert relus[0]["formateurs"][0]["nom"] == "Second"


def test_un_avis_par_activite_coexistent():
    journal = avis(activite=1, f1="Un") + "\n" + avis(activite=2, f1="Deux")
    _, rapport = construire(journal)
    assert rapport["avis"] == 2 and rapport["avis_remplaces"] == []
