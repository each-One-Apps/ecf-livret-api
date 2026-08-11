"""
API de mise à jour du livret d'évaluations ECF.

POST /update-ecf-assessment
  -> le livret rempli, en binaire (application/pdf)

Deux façons d'envoyer le journal, au choix de l'appelant :

  text/plain       le corps EST le journal, `record_id` et `livret` en paramètres
                   d'URL. À privilégier : le journal contient des sauts de ligne,
                   et Make n'a pas de fonction d'échappement JSON — construire un
                   corps JSON à la main y produit du JSON invalide dès qu'une
                   description tient sur plusieurs lignes.
  application/json {"log": "…", "record_id": "rec…", "livret": "TP-00520"}

Le journal est la concaténation de TOUS les blocs Fillout reçus depuis le début,
y compris ceux de la soumission en cours. Le livret est reconstruit entièrement
à chaque appel : deux appels avec le même journal produisent le même fichier, à
l'octet près. Un rejeu du scénario Make ne peut donc pas dupliquer une ligne.

Service volontairement séparé de `apni-bulletin-api` : un démarrage raté ici ne
doit pas emporter la génération des bulletins APNI.
"""
import base64
import json
import logging
import re
import zlib
from urllib.parse import quote, urlencode

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from ecf.livret import DEFAUT, LivretInconnu, codes_disponibles, construire
from ecf.parser import JournalInvalide
from ecf.render import LivretPlein

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("ecf")

RE_RECORD = re.compile(r"^rec[A-Za-z0-9]{14}$")

# Airtable télécharge les pièces jointes depuis une URL : impossible de lui
# pousser des octets. Plutôt que d'héberger le PDF quelque part — ce qui
# imposerait un stockage et un secret à un service qui n'en a aucun — on lui
# renvoie une URL vers ce même service, portant le journal compressé. Le rendu
# étant déterministe, le GET reproduit le fichier à l'octet près.
URL_MAX = 8000


def _encoder_journal(journal):
    brut = zlib.compress(journal.encode("utf-8"), 9)
    return base64.urlsafe_b64encode(brut).decode("ascii").rstrip("=")


def _decoder_journal(encode):
    rembourrage = "=" * (-len(encode) % 4)
    try:
        return zlib.decompress(base64.urlsafe_b64decode(encode + rembourrage)).decode("utf-8")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"paramètre « j » illisible : {e}")


def _base_url(request):
    """URL publique du service, en tenant compte du proxy de l'hébergeur."""
    hote = request.headers.get("x-forwarded-host") or request.headers.get("host")
    protocole = request.headers.get("x-forwarded-proto") or request.url.scheme
    if not hote:
        return str(request.base_url).rstrip("/")
    return f"{protocole}://{hote}"

app = FastAPI(title="ECF — livret d'évaluations")


@app.get("/health")
def health():
    return {"status": "ok", "livrets": codes_disponibles()}


async def _lire_requete(request: Request, record_id, livret):
    """Extrait (journal, record_id, livret) quel que soit le format d'envoi."""
    type_contenu = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
    corps = await request.body()

    if type_contenu == "application/json":
        try:
            charge = json.loads(corps.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as e:
            raise HTTPException(status_code=400, detail=f"corps JSON illisible : {e}")
        if not isinstance(charge, dict):
            raise HTTPException(status_code=400, detail="corps JSON : objet attendu")
        return (
            charge.get("log") or "",
            charge.get("record_id") or record_id,
            charge.get("livret") or livret,
        )

    # Texte brut : le corps est le journal, tel quel.
    return corps.decode("utf-8", errors="replace"), record_id, livret


@app.get("/livret.pdf")
def livret_pdf(j: str = "", livret: str = DEFAUT, nom: str = "livret_ecf.pdf"):
    """Régénère le livret depuis le journal encodé. C'est cette URL qu'Airtable télécharge."""
    journal = _decoder_journal(j) if j else ""
    try:
        pdf, _ = construire(journal, livret)
    except LivretInconnu as e:
        raise HTTPException(status_code=404, detail=str(e))
    except (JournalInvalide, LivretPlein, ValueError) as e:
        raise HTTPException(status_code=422, detail=str(e))
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{nom}"'},
    )


@app.post("/update-ecf-assessment")
async def update_ecf_assessment(
    request: Request, record_id: str = "", livret: str = DEFAUT, format: str = "airtable"
):
    log, record_id, livret = await _lire_requete(request, record_id, livret)
    ref = record_id if RE_RECORD.match(record_id or "") else "record inconnu"

    try:
        pdf, rapport = construire(log, livret)
    except LivretInconnu as e:
        logger.warning("%s — livret inconnu : %s", ref, e)
        raise HTTPException(status_code=404, detail=str(e))
    except (JournalInvalide, LivretPlein, ValueError) as e:
        # 422 : la demande est bien formée mais son contenu est refusé.
        # stopOnHttpError côté Make empêche alors toute écriture dans Airtable.
        logger.warning("%s — refusé : %s", ref, e)
        raise HTTPException(status_code=422, detail=str(e))

    if rapport["descriptions_tronquees"]:
        logger.warning(
            "%s — descriptions tronquées, évaluations %s : le texte dépassait la cellule",
            ref, rapport["descriptions_tronquees"],
        )
    logger.info(
        "%s — %d évaluation(s) %s, %d octets",
        ref, rapport["evaluations"], rapport["par_activite"], len(pdf),
    )

    nom = f"livret_ecf_{record_id}.pdf" if RE_RECORD.match(record_id or "") \
        else "livret_ecf.pdf"
    entetes = {
        "X-ECF-Evaluations": str(rapport["evaluations"]),
        "X-ECF-Tronquees": ",".join(str(n) for n in rapport["descriptions_tronquees"]),
    }

    if format == "pdf":
        entetes["Content-Disposition"] = f'attachment; filename="{nom}"'
        return Response(content=pdf, media_type="application/pdf", headers=entetes)

    # Format Airtable : le champ attachment attend [{url, filename}].
    encode = _encoder_journal(log)
    if len(encode) > URL_MAX:
        raise HTTPException(
            status_code=422,
            detail=f"journal trop volumineux pour être transporté par URL "
                   f"({len(encode)} caractères une fois compressé, maximum {URL_MAX})",
        )
    url = "{}/livret.pdf?{}".format(
        _base_url(request), urlencode({"j": encode, "livret": livret, "nom": nom}, quote_via=quote)
    )
    return JSONResponse(content=[{"url": url, "filename": nom}], headers=entetes)
