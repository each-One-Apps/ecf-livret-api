"""
API de mise à jour du livret d'évaluations ECF.

POST /update-ecf-assessment
  Body : {"log": "<journal complet>", "record_id": "rec…", "livret": "TP-00520"}
  -> le livret rempli, en binaire (application/pdf)

Le journal est la concaténation de TOUS les blocs Fillout reçus depuis le début,
y compris ceux de la soumission en cours. Le livret est reconstruit entièrement
à chaque appel : deux appels avec le même journal produisent le même fichier, à
l'octet près. Un rejeu du scénario Make ne peut donc pas dupliquer une ligne.

Service volontairement séparé de `apni-bulletin-api` : un démarrage raté ici ne
doit pas emporter la génération des bulletins APNI.
"""
import logging
import re

from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from ecf.livret import DEFAUT, LivretInconnu, codes_disponibles, construire
from ecf.parser import JournalInvalide
from ecf.render import LivretPlein

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("ecf")

RE_RECORD = re.compile(r"^rec[A-Za-z0-9]{14}$")

app = FastAPI(title="ECF — livret d'évaluations")


class RequeteLivret(BaseModel):
    log: str = Field(default="", description="Journal complet des évaluations")
    record_id: str = Field(default="", description="Record Airtable, pour la traçabilité")
    livret: str = Field(default=DEFAUT, description="Code du titre professionnel")


@app.get("/health")
def health():
    return {"status": "ok", "livrets": codes_disponibles()}


@app.post("/update-ecf-assessment")
def update_ecf_assessment(req: RequeteLivret):
    ref = req.record_id if RE_RECORD.match(req.record_id or "") else "record inconnu"

    try:
        pdf, rapport = construire(req.log, req.livret)
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

    nom = f"livret_ecf_{req.record_id}.pdf" if RE_RECORD.match(req.record_id or "") \
        else "livret_ecf.pdf"
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{nom}"',
            "X-ECF-Evaluations": str(rapport["evaluations"]),
            "X-ECF-Tronquees": ",".join(str(n) for n in rapport["descriptions_tronquees"]),
        },
    )
