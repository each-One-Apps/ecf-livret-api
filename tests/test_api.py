from fastapi.testclient import TestClient

from main import app
from tests.test_parser import BLOC_REEL

client = TestClient(app)

RECORD = "recAbCdEfGhIjKlMn"


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert "TP-00520" in r.json()["livrets"]


def chemin_de(url):
    return "/livret.pdf" + url.split("/livret.pdf", 1)[1]


def test_appel_nominal_renvoie_le_format_attachment_airtable():
    """Airtable télécharge ses pièces jointes : il lui faut une URL, pas des octets."""
    r = client.post("/update-ecf-assessment", json={"log": BLOC_REEL, "record_id": RECORD})
    assert r.status_code == 200
    (piece,) = r.json()["attachment"]
    assert set(piece) == {"url", "filename"}
    assert piece["filename"] == f"livret_ecf_{RECORD}.pdf"
    assert "/livret.pdf?" in piece["url"]
    assert r.headers["X-ECF-Evaluations"] == "1"
    assert r.headers["X-ECF-Tronquees"] == ""


def test_l_url_renvoyee_produit_le_pdf():
    r = client.post("/update-ecf-assessment", json={"log": BLOC_REEL, "record_id": RECORD})
    g = client.get(chemin_de(r.json()["attachment"][0]["url"]))
    assert g.status_code == 200
    assert g.headers["content-type"] == "application/pdf"
    assert g.content[:5] == b"%PDF-"


def test_l_url_reproduit_le_rendu_direct_a_l_octet_pres():
    r = client.post("/update-ecf-assessment", json={"log": BLOC_REEL})
    par_url = client.get(chemin_de(r.json()["attachment"][0]["url"])).content
    direct = client.post("/update-ecf-assessment?format=pdf", json={"log": BLOC_REEL}).content
    assert par_url == direct


def test_format_pdf_renvoie_toujours_le_binaire():
    r = client.post(f"/update-ecf-assessment?format=pdf&record_id={RECORD}",
                    json={"log": BLOC_REEL})
    assert r.headers["content-type"] == "application/pdf"
    assert r.content[:5] == b"%PDF-"
    assert RECORD in r.headers["content-disposition"]


def test_url_illisible_en_400():
    assert client.get("/livret.pdf?j=pas-du-tout-du-zlib").status_code == 400


def test_journal_vide_dans_l_url_donne_le_livret_vierge():
    g = client.get("/livret.pdf")
    assert g.status_code == 200 and g.content[:5] == b"%PDF-"


def test_l_url_refuse_aussi_un_livret_plein():
    r = client.post("/update-ecf-assessment?format=pdf", json={"log": six_evaluations()})
    assert r.status_code == 422


def test_journal_vide_accepte():
    """Un livret sans évaluation reste un livret valide, pas une erreur."""
    r = client.post("/update-ecf-assessment", json={"log": "", "record_id": RECORD})
    assert r.status_code == 200 and r.headers["X-ECF-Evaluations"] == "0"


def test_journal_illisible_refuse_en_422():
    r = client.post("/update-ecf-assessment",
                    json={"log": "Date : hier\nActivité : 1. X\n"
                                 "Compétences évaluées : 1\n"
                                 "Description des compétences : Y"})
    assert r.status_code == 422
    assert "illisible" in r.json()["detail"]


def six_evaluations():
    """Six blocs DISTINCTS : des blocs identiques seraient dédoublonnés."""
    return "\n----------\n".join(
        f"Date : 2026-08-{10 + i:02d}\nActivité : 1. X\n"
        f"Compétences évaluées : 1\nDescription des compétences : Évaluation {i}"
        for i in range(1, 7)
    )


def test_livret_plein_refuse_en_422():
    r = client.post("/update-ecf-assessment", json={"log": six_evaluations()})
    assert r.status_code == 422
    assert "5 lignes" in r.json()["detail"]


def test_livret_inconnu_en_404():
    r = client.post("/update-ecf-assessment", json={"log": "", "livret": "TP-99999"})
    assert r.status_code == 404


def test_texte_brut_le_corps_est_le_journal():
    """Mode privilégié par Make : aucun JSON à construire, donc rien à échapper."""
    r = client.post(
        f"/update-ecf-assessment?record_id={RECORD}",
        content=BLOC_REEL.encode("utf-8"),
        headers={"Content-Type": "text/plain; charset=utf-8"},
    )
    assert r.status_code == 200
    assert r.headers["X-ECF-Evaluations"] == "1"
    (piece,) = r.json()["attachment"]
    assert piece["filename"] == f"livret_ecf_{RECORD}.pdf"
    assert client.get(chemin_de(piece["url"])).content[:5] == b"%PDF-"


def test_texte_brut_et_json_donnent_le_meme_fichier():
    a = client.post(f"/update-ecf-assessment?record_id={RECORD}",
                    content=BLOC_REEL.encode("utf-8"),
                    headers={"Content-Type": "text/plain"}).content
    b = client.post("/update-ecf-assessment",
                    json={"log": BLOC_REEL, "record_id": RECORD}).content
    assert a == b


def test_texte_brut_avec_description_multiligne():
    """Le cas qui casserait un corps JSON construit à la main dans Make."""
    journal = (
        "Date : 2026-08-11\n"
        "Activité : 1. Peu importe\n"
        "Compétences évaluées : 2\n"
        'Description des compétences : Première ligne\navec des "guillemets"\net une '
        "barre oblique inverse \\ pour finir"
    )
    r = client.post("/update-ecf-assessment", content=journal.encode("utf-8"),
                    headers={"Content-Type": "text/plain"})
    assert r.status_code == 200 and r.headers["X-ECF-Evaluations"] == "1"


def test_texte_brut_refuse_aussi_le_livret_plein():
    r = client.post("/update-ecf-assessment", content=six_evaluations().encode("utf-8"),
                    headers={"Content-Type": "text/plain"})
    assert r.status_code == 422


def test_json_illisible_en_400():
    r = client.post("/update-ecf-assessment", content=b"{pas du json",
                    headers={"Content-Type": "application/json"})
    assert r.status_code == 400


def test_deux_appels_identiques_donnent_le_meme_fichier():
    corps = {"log": BLOC_REEL, "record_id": RECORD}
    a = client.post("/update-ecf-assessment", json=corps).content
    b = client.post("/update-ecf-assessment", json=corps).content
    assert a == b


def test_le_journal_renvoye_est_dedoublonne_et_trie():
    """C'est ce texte que l'appelant réécrit : sans lui, son champ grossit sans fin."""
    from tests.test_parser import lire_evaluations
    bloc = lambda j, d: (f"Date : 2026-08-{j}\nActivité : 1. Peu importe\n"
                         f"Compétences évaluées : 1\nDescription des compétences : {d}")
    envoye = "\n----------\n".join([bloc("13", "Tardive"), bloc("11", "Ancienne"),
                                     bloc("13", "Tardive")])
    r = client.post("/update-ecf-assessment", json={"log": envoye})
    corps = r.json()
    assert corps["evaluations"] == 2 and corps["doublons_ignores"] == [3]
    relu = lire_evaluations(corps["journal"])
    assert [e["date"] for e in relu] == ["11/08/2026", "13/08/2026"]


def test_le_journal_renvoye_est_stable_si_on_le_renvoie():
    """Rejouer le journal canonique ne doit plus rien changer."""
    r1 = client.post("/update-ecf-assessment", json={"log": BLOC_REEL}).json()
    r2 = client.post("/update-ecf-assessment", json={"log": r1["journal"]}).json()
    assert r2["journal"] == r1["journal"]
    assert r2["attachment"] == r1["attachment"]
    assert r2["doublons_ignores"] == []
