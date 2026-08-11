"""
Dessine les évaluations sur le livret ECF vierge.

Le livret est TOUJOURS reconstruit depuis le template : la liste complète des
évaluations est la seule source de vérité (elle vit dans Airtable). Deux appels
avec la même liste produisent le même fichier, à l'octet près.
"""
import io

from pypdf import PdfReader, PdfWriter
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph

POLICE = "Helvetica"
CORPS_MAX = 9
CORPS_MIN = 6.5
ENCRE = (0.06, 0.06, 0.35)  # même bleu que le remplissage APNI


class LivretPlein(Exception):
    """Plus de ligne disponible pour cette activité."""


def _style(taille):
    return ParagraphStyle(
        "cellule",
        fontName=POLICE,
        fontSize=taille,
        leading=taille * 1.18,
        textColor=ENCRE,
    )


def _echappe(texte):
    return (
        texte.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("\n", "<br/>")
    )


def _dessine_description(c, cadre, texte):
    """Texte ajusté à la cellule : retour à la ligne, puis réduction du corps.

    Renvoie True si le texte a dû être tronqué — l'appelant doit le tracer :
    une évaluation coupée en silence sur un document de jury est inacceptable.
    """
    x0, y0, x1, y1 = cadre
    largeur, hauteur = x1 - x0, y1 - y0

    for taille in (CORPS_MAX, 8.0, 7.5, 7.0, CORPS_MIN):
        para = Paragraph(_echappe(texte), _style(taille))
        if para.wrap(largeur, 1e6)[1] <= hauteur:
            para.drawOn(c, x0, y1 - para.height)
            return False

    # Même au corps minimal ça déborde : on coupe au plus long qui tienne.
    style = _style(CORPS_MIN)
    bas, haut = 0, len(texte)
    while bas < haut:
        milieu = (bas + haut + 1) // 2
        para = Paragraph(_echappe(texte[:milieu].rstrip() + "…"), style)
        if para.wrap(largeur, 1e6)[1] <= hauteur:
            bas = milieu
        else:
            haut = milieu - 1
    para = Paragraph(_echappe(texte[:bas].rstrip() + "…"), style)
    para.wrap(largeur, 1e6)
    para.drawOn(c, x0, y1 - para.height)
    return True


def _dessine_date(c, cadre, texte):
    x0, y0, x1, y1 = cadre
    c.setFont(POLICE, 8)
    c.setFillColorRGB(*ENCRE)
    c.drawCentredString((x0 + x1) / 2, y1 - 10, texte)


def _dessine_croix(c, centre, cote=6.0):
    x, y = centre
    d = cote / 2
    c.setStrokeColorRGB(*ENCRE)
    c.setLineWidth(1.1)
    c.line(x - d, y - d, x + d, y + d)
    c.line(x - d, y + d, x + d, y - d)


def _emplacements(activite, blocs_autorises):
    """Aplatit les blocs d'une activité en une liste ordonnée de lignes."""
    places = []
    for bloc in activite["blocs"]:
        if bloc["type"] not in blocs_autorises:
            continue
        for ligne in bloc["lignes"]:
            places.append((bloc["page"], ligne))
    return places


def rendre(template_bytes, coords, evaluations, blocs_autorises=("principale",)):
    """evaluations : liste de dicts {activite, date, competences, description}.

    L'ordre de la liste fait l'ordre des lignes dans le livret.
    """
    lecteur = PdfReader(io.BytesIO(template_bytes))
    ecrivain = PdfWriter(clone_from=lecteur)

    par_page = {}
    tronques = []
    compteurs = {}

    for rang, ev in enumerate(evaluations, 1):
        cle = str(ev["activite"])
        activite = coords["activites"].get(cle)
        if activite is None:
            raise ValueError(f"évaluation {rang} : activité {cle} absente du livret")

        places = _emplacements(activite, blocs_autorises)
        i = compteurs.get(cle, 0)
        if i >= len(places):
            raise LivretPlein(
                f"activité {cle} : {i + 1}e évaluation, le livret n'accepte "
                f"que {len(places)} lignes"
            )
        compteurs[cle] = i + 1
        page, ligne = places[i]

        connues = activite["competences"]
        for n in ev["competences"]:
            if str(n) not in connues:
                raise ValueError(
                    f"évaluation {rang} : compétence {n} inconnue pour l'activité {cle} "
                    f"(1 à {len(connues)})"
                )

        par_page.setdefault(page, []).append((ligne, ev, rang))

    for page_index, entrees in par_page.items():
        page = ecrivain.pages[page_index]
        largeur, hauteur = float(page.mediabox.width), float(page.mediabox.height)
        tampon = io.BytesIO()
        c = canvas.Canvas(tampon, pagesize=(largeur, hauteur))

        for ligne, ev, rang in entrees:
            if _dessine_description(c, ligne["desc"], ev["description"]):
                tronques.append(rang)
            _dessine_date(c, ligne["date"], ev["date"])
            for n in ev["competences"]:
                _dessine_croix(c, ligne["cases"][str(n)])

        c.save()
        tampon.seek(0)
        page.merge_page(PdfReader(tampon).pages[0])

    # Sortie déterministe : même entrée -> mêmes octets.
    ecrivain.add_metadata({"/Producer": "each One ECF", "/Creator": "each One ECF"})
    sortie = io.BytesIO()
    ecrivain.write(sortie)
    return sortie.getvalue(), tronques
