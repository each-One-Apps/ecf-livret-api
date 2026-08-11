"""
Dessine les évaluations sur le livret ECF vierge.

Le livret est TOUJOURS reconstruit depuis le template : la liste complète des
évaluations est la seule source de vérité (elle vit dans Airtable). Deux appels
avec la même liste produisent le même fichier, à l'octet près.
"""
import io
import logging
import unicodedata
import urllib.request

from pypdf import PdfReader, PdfWriter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph

logger = logging.getLogger("ecf")

POLICE = "Helvetica"
CORPS_MAX = 9
CORPS_MIN = 6.5
ENCRE = (0.06, 0.06, 0.35)  # même bleu que le remplissage APNI
DELAI_SIGNATURE = 15  # secondes


class SignatureIntrouvable(Exception):
    """Une signature n'a pas pu être récupérée.

    On refuse plutôt que de produire un livret amputé : sur un document de jury,
    une signature manquante ne doit jamais passer inaperçue.
    """


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


def _normalise(texte):
    """Minuscules, sans accents ni ponctuation de fin : pour comparer des phrases."""
    sans_accent = "".join(
        c for c in unicodedata.normalize("NFD", texte or "")
        if unicodedata.category(c) != "Mn"
    )
    return " ".join(sans_accent.lower().replace(".", " ").split())


def _choisir_case(resultat, cases, rang):
    """Associe la phrase reçue à l'une des deux cases imprimées.

    « Ne pas avoir satisfait… » CONTIENT « Avoir satisfait… » : la négation doit
    donc être testée en premier, sans quoi les deux cases seraient cochées.
    """
    voulu = _normalise(resultat)
    for cle in ("non_satisfait", "satisfait"):  # la négation d'abord
        imprime = _normalise(cases[cle]["libelle"])
        if voulu.startswith(imprime[:40]) or imprime.startswith(voulu[:40]):
            return cle
    raise ValueError(
        f"avis {rang} : résultat « {resultat[:70]} » ne correspond à aucune des "
        f"deux mentions du livret"
    )


def _telecharger(url, quoi):
    try:
        with urllib.request.urlopen(url, timeout=DELAI_SIGNATURE) as reponse:
            # Les réponses non HTTP (file://) n'ont pas de code de statut.
            statut = getattr(reponse, "status", None)
            if statut is not None and statut != 200:
                raise SignatureIntrouvable(f"{quoi} : HTTP {statut}")
            octets = reponse.read()
        if not octets:
            raise SignatureIntrouvable(f"{quoi} : fichier vide")
        return octets
    except SignatureIntrouvable:
        raise
    except Exception as e:
        raise SignatureIntrouvable(f"{quoi} : {e}")


def _rogner_marges(octets):
    """Retire les marges transparentes ou blanches d'une signature.

    Les signatures manuscrites arrivent dans une grande image dont l'essentiel
    est vide : sans ce rognage, le trait occupe une fraction dérisoire de la
    case « Visa ».
    """
    try:
        from PIL import Image  # fourni avec reportlab
    except ImportError:
        return octets
    try:
        image = Image.open(io.BytesIO(octets))
        image = image.convert("RGBA")
        alpha = image.getchannel("A")
        boite = alpha.getbbox()
        if boite is None:  # entièrement transparente : on garde l'original
            return octets
        sortie = io.BytesIO()
        image.crop(boite).save(sortie, format="PNG")
        return sortie.getvalue()
    except Exception:
        return octets  # au pire on dessine l'image telle quelle


def _dessine_image(c, cadre, octets, quoi):
    """Image ajustée dans la cellule, proportions conservées, centrée."""
    x0, y0, x1, y1 = cadre
    largeur, hauteur = x1 - x0, y1 - y0
    octets = _rogner_marges(octets)
    try:
        image = ImageReader(io.BytesIO(octets))
        iw, ih = image.getSize()
    except Exception as e:
        raise SignatureIntrouvable(f"{quoi} : image illisible ({e})")
    echelle = min(largeur / iw, hauteur / ih)
    dw, dh = iw * echelle, ih * echelle
    c.drawImage(image, x0 + (largeur - dw) / 2, y0 + (hauteur - dh) / 2,
                width=dw, height=dh, mask="auto")


def _dessine_avis(c_par_page, coords_activite, avis, rang, signatures):
    """Prépare les tracés de l'avis, page par page. Renvoie {page: [actions]}."""
    zones = coords_activite.get("avis")
    if not zones:
        raise ValueError(f"avis {rang} : le livret ne décrit pas de zone d'avis final")

    cle = _choisir_case(avis["resultat"], zones["resultat"], rang)
    case = zones["resultat"][cle]
    c_par_page.setdefault(case["page"], []).append(("croix", case["centre"]))

    for champ, zone in (("points_attention", "points_attention"),
                        ("a_reevaluer", "a_reevaluer")):
        if avis.get(champ):
            c_par_page.setdefault(zones[zone]["page"], []).append(
                ("texte", zones[zone]["cadre"], avis[champ])
            )

    for i, formateur in enumerate(avis["formateurs"]):
        ligne = zones["formateurs"][i]
        page = ligne["page"]
        if formateur["nom"]:
            c_par_page.setdefault(page, []).append(("texte", ligne["nom"], formateur["nom"]))
        if avis["date"] and (formateur["nom"] or formateur["signature"]):
            c_par_page.setdefault(page, []).append(("texte", ligne["date"], avis["date"]))
        if formateur["signature"]:
            octets = signatures[formateur["signature"]]
            c_par_page.setdefault(page, []).append(
                ("image", ligne["visa"], octets, f"avis {rang} signature {i + 1}")
            )


def _emplacements(activite, blocs_autorises):
    """Aplatit les blocs d'une activité en une liste ordonnée de lignes."""
    places = []
    for bloc in activite["blocs"]:
        if bloc["type"] not in blocs_autorises:
            continue
        for ligne in bloc["lignes"]:
            places.append((bloc["page"], ligne))
    return places


def rendre(template_bytes, coords, evaluations, blocs_autorises=("principale",), avis=()):
    """evaluations : liste de dicts {activite, date, competences, description}.

    L'ordre de la liste fait l'ordre des lignes dans le livret. `avis` porte les
    avis finaux, dessinés sur les pages qui suivent chaque fiche.
    """
    # Les signatures sont téléchargées AVANT de dessiner : si l'une manque, on
    # échoue sans avoir rien produit, plutôt que de livrer un document amputé.
    signatures = {}
    for rang, a in enumerate(avis, 1):
        for i, f in enumerate(a["formateurs"], 1):
            url = f["signature"]
            if url and url not in signatures:
                signatures[url] = _telecharger(url, f"avis {rang} signature {i}")

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
        # La place est décidée en amont (ecf.livret._placer) : ici on l'applique.
        i = (ev.get("ligne") or compteurs.get(cle, 0) + 1) - 1
        if not 0 <= i < len(places):
            raise LivretPlein(
                f"activité {cle} : ligne {i + 1} demandée, le livret n'accepte "
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

    tracés_avis = {}
    for rang, a in enumerate(avis, 1):
        activite = coords["activites"].get(str(a["activite"]))
        if activite is None:
            raise ValueError(f"avis {rang} : activité {a['activite']} absente du livret")
        _dessine_avis(tracés_avis, activite, a, rang, signatures)

    for page_index in sorted(set(par_page) | set(tracés_avis)):
        entrees = par_page.get(page_index, [])
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

        for action in tracés_avis.get(page_index, []):
            if action[0] == "croix":
                _dessine_croix(c, action[1], cote=7.0)
            elif action[0] == "texte":
                _dessine_description(c, action[1], action[2])
            elif action[0] == "image":
                _dessine_image(c, action[1], action[2], action[3])

        c.save()
        tampon.seek(0)
        page.merge_page(PdfReader(tampon).pages[0])

    # Sortie déterministe : même entrée -> mêmes octets.
    ecrivain.add_metadata({"/Producer": "each One ECF", "/Creator": "each One ECF"})
    sortie = io.BytesIO()
    ecrivain.write(sortie)
    return sortie.getvalue(), tronques
