"""Page de garde et pied de page : informations candidat et constantes du titre."""
import io
import json
import pathlib

import pytest
from pypdf import PdfReader

from ecf.livret import construire

CANDIDAT = (
    "Candidat :\n"
    "Civilité : Mme\n"
    "Nom : MARTINEZ\n"
    "Prénom : Sofia\n"
    "Date de naissance : 14/03/1998\n"
    "Organisme de formation : each One\n"
    "Lieu de formation : Paris 18e\n"
    "----------"
)


def texte(pdf, page=0):
    return "".join((PdfReader(io.BytesIO(pdf)).pages[page].extract_text() or "").split())


def test_les_infos_candidat_sont_ecrites_en_page_de_garde():
    pdf, rapport = construire(CANDIDAT)
    page = texte(pdf)
    for attendu in ("MARTINEZ", "Sofia", "14/03/1998", "eachOne", "Paris18e"):
        assert attendu in page, attendu
    assert rapport["candidat"]["nom"] == "MARTINEZ"


def test_la_civilite_coche_la_bonne_case():
    from ecf.livret import charger
    import pdfplumber

    _, coords, _ = charger("TP-00520")
    cases = coords["garde"]["civilite"]
    for civilite, attendue in (("Mme", "mme"), ("M.", "m")):
        pdf, _ = construire(CANDIDAT.replace("Civilité : Mme", f"Civilité : {civilite}"))
        with pdfplumber.open(io.BytesIO(pdf)) as doc:
            p = doc.pages[0]
            cochees = []
            for cle, (x, y) in cases.items():
                top = p.height - y
                if [l for l in p.lines if abs((l["x0"] + l["x1"]) / 2 - x) < 6
                        and abs((l["top"] + l["bottom"]) / 2 - top) < 6]:
                    cochees.append(cle)
            assert cochees == [attendue], (civilite, cochees)


def test_civilite_non_reconnue_refusee():
    with pytest.raises(ValueError) as e:
        construire(CANDIDAT.replace("Civilité : Mme", "Civilité : Docteur"))
    assert "non reconnue" in str(e.value)


def test_un_champ_vide_n_ecrit_rien():
    """Jamais de texte de remplissage sur un document de jury."""
    partiel = CANDIDAT.replace("Prénom : Sofia", "Prénom : ")
    pdf, _ = construire(partiel)
    page = texte(pdf)
    assert "MARTINEZ" in page and "Sofia" not in page


def test_les_constantes_du_titre_sont_vides_par_defaut():
    """Le fichier de libellés est livré vide : rien ne doit s'imprimer."""
    chemin = pathlib.Path("ecf/libelles_TP-00520.json")
    valeurs = {k: v for k, v in json.loads(chemin.read_text(encoding="utf-8")).items()
               if not k.startswith("_")}
    assert set(valeurs) == {"arrete_du", "jo_du", "date_effet", "date_jo", "date_maj"}
    pdf, _ = construire("")
    assert "TODO" not in texte(pdf)


def test_le_journal_conserve_les_infos_candidat():
    from ecf.parser import lire_journal

    _, rapport = construire(CANDIDAT)
    _, _, relu = lire_journal(rapport["journal"])
    assert relu["nom"] == "MARTINEZ" and relu["lieu"] == "Paris 18e"


def _avec_libelles(valeurs, journal=""):
    """Rend un livret avec des constantes de titre, sans modifier le fichier livré."""
    from ecf import livret

    chemin = pathlib.Path(__file__).parent.parent / "ecf" / "libelles_TP-00520.json"
    sauvegarde = chemin.read_text(encoding="utf-8")
    essai = json.loads(sauvegarde)
    essai.update(valeurs)
    chemin.write_text(json.dumps(essai, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        livret.charger.cache_clear()
        return livret.construire(journal)[0]
    finally:
        chemin.write_text(sauvegarde, encoding="utf-8")
        livret.charger.cache_clear()


def test_le_pied_de_page_est_rempli_sur_toutes_les_pages():
    pdf = _avec_libelles({"date_jo": "02/08/2022", "date_maj": "18/10/2022"})
    doc = PdfReader(io.BytesIO(pdf))
    for i in range(len(doc.pages)):
        page = "".join((doc.pages[i].extract_text() or "").split())
        assert "02/08/2022" in page and "18/10/2022" in page, f"page {i + 1}"


def test_la_date_jo_n_ecrase_pas_le_millesime():
    """Le bord de page décalait les colonnes : la date s'écrivait sur « 05 »."""
    pdf = _avec_libelles({"date_jo": "02/08/2022"})
    page = "".join((PdfReader(io.BytesIO(pdf)).pages[0].extract_text() or "").split())
    assert "TP-0052005" in page          # code titre puis millésime, intacts
    assert "02/08/2022" in page


def test_les_dates_du_titre_sur_la_page_de_garde():
    pdf = _avec_libelles({"arrete_du": "21 juillet 2022", "jo_du": "2 août 2022",
                          "date_effet": "1er septembre 2022"})
    page = texte(pdf)
    assert "21juillet2022" in page and "2août2022" in page and "1erseptembre2022" in page


def test_deux_natures_dans_un_bloc_refusees():
    """Séparateur oublié : l'avis serait avalé par le bloc candidat, en silence."""
    from ecf.parser import JournalInvalide, lire_journal

    colle = (
        "Avis activité : Activité-type 1\n"
        "Résultat : Avoir satisfait aux critères issus des référentiels du titre "
        "professionnel attendus pour la réalisation de cette activité-type.\n"
        "Formateur 1 : Camille\n"
        "Candidat :\nNom : MARTINEZ\n----------"
    )
    with pytest.raises(JournalInvalide) as e:
        lire_journal(colle)
    assert "----------" in str(e.value)


def test_la_date_de_naissance_est_mise_au_format_francais():
    pdf, rapport = construire(CANDIDAT.replace("14/03/1998", "1991-09-05"))
    assert rapport["candidat"]["naissance"] == "05/09/1991"
    assert "05/09/1991" in texte(pdf)
