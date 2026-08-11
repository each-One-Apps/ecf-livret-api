from fastapi.testclient import TestClient

from main import app
from tests.test_parser import BLOC_REEL

client = TestClient(app)

RECORD = "recAbCdEfGhIjKlMn"


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert "TP-00520" in r.json()["livrets"]


def test_appel_nominal_renvoie_un_pdf():
    r = client.post("/update-ecf-assessment", json={"log": BLOC_REEL, "record_id": RECORD})
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert r.content[:5] == b"%PDF-"
    assert r.headers["X-ECF-Evaluations"] == "1"
    assert r.headers["X-ECF-Tronquees"] == ""
    assert RECORD in r.headers["content-disposition"]


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


def test_livret_plein_refuse_en_422():
    bloc = ("Date : 2026-08-11\nActivité : 1. X\nCompétences évaluées : 1\n"
            "Description des compétences : Y")
    r = client.post("/update-ecf-assessment",
                    json={"log": "\n----------\n".join([bloc] * 6)})
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
    assert r.content[:5] == b"%PDF-"
    assert r.headers["X-ECF-Evaluations"] == "1"
    assert RECORD in r.headers["content-disposition"]


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
    bloc = ("Date : 2026-08-11\nActivité : 1. X\nCompétences évaluées : 1\n"
            "Description des compétences : Y")
    r = client.post("/update-ecf-assessment",
                    content="\n----------\n".join([bloc] * 6).encode("utf-8"),
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
