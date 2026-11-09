"""
Génère le modèle Power BI churn_dashboard.pbit à partir des CSV de powerbi/data/.

Un fichier .pbit est une archive ZIP qui contient :
  - DataModelSchema : le modèle de données (tables, types, requêtes Power Query, mesures DAX), en JSON ;
  - Report/Layout   : les pages du rapport et leurs visuels, en JSON ;
  - DiagramLayout, Settings, Metadata, Version, [Content_Types].xml : métadonnées.
Les fichiers JSON sont encodés en UTF-16 LE, comme ceux produits par Power BI Desktop.

À l'ouverture, Power BI demande le paramètre « DossierDonnees » (le chemin du dossier powerbi/data),
puis charge les CSV.

Lancer :  python powerbi/build_pbit.py
"""

from __future__ import annotations

import io
import json
import struct
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

HERE = Path(__file__).resolve().parent
OUT = HERE / "churn_dashboard.pbit"
DEFAULT_FOLDER = r"C:\churn-prediction\powerbi\data"


def uid() -> str:
    return str(uuid.uuid4())


# ─────────────────────────────────────────────────────────────
# 1. Modèle de données
# ─────────────────────────────────────────────────────────────
# (nom de colonne, type Power BI, type Power Query, agrégation par défaut, format)
TABLES = {
    "clients": [
        ("id_client", "string", "type text", "none", None),
        ("genre", "string", "type text", "none", None),
        ("senior", "string", "type text", "none", None),
        ("en_couple", "string", "type text", "none", None),
        ("personnes_a_charge", "string", "type text", "none", None),
        ("anciennete_mois", "int64", "Int64.Type", "none", "0"),
        ("tranche_anciennete", "string", "type text", "none", None),
        ("tranche_ordre", "int64", "Int64.Type", "none", "0"),
        ("contrat", "string", "type text", "none", None),
        ("internet", "string", "type text", "none", None),
        ("moyen_paiement", "string", "type text", "none", None),
        ("facture_dematerialisee", "string", "type text", "none", None),
        ("nb_services", "int64", "Int64.Type", "none", "0"),
        ("support_technique", "string", "type text", "none", None),
        ("securite_en_ligne", "string", "type text", "none", None),
        ("facture_mensuelle", "double", "type number", "sum", "#,0.00 $"),
        ("total_facture", "double", "type number", "sum", "#,0 $"),
        ("valeur_annuelle", "double", "type number", "sum", "#,0 $"),
        ("churn", "int64", "Int64.Type", "sum", "0"),
        ("churn_reel", "string", "type text", "none", None),
        ("probabilite_churn", "double", "type number", "none", "0.0 %"),
        ("segment_risque", "string", "type text", "none", None),
        ("cible_campagne", "string", "type text", "none", None),
        ("gain_attendu", "double", "type number", "sum", "#,0 $"),
        ("motif_1", "string", "type text", "none", None),
        ("motif_2", "string", "type text", "none", None),
    ],
    "comparaison_modeles": [
        ("modele", "string", "type text", "none", None),
        ("roc_auc", "double", "type number", "none", "0.000"),
        ("roc_auc_ecart_type", "double", "type number", "none", "0.000"),
        ("pr_auc", "double", "type number", "none", "0.000"),
        ("profit_cv", "double", "type number", "none", "#,0 $"),
        ("retenu", "boolean", "type logical", "none", None),
    ],
    "importance_variables": [
        ("variable", "string", "type text", "none", None),
        ("importance", "double", "type number", "sum", "0.000"),
        ("libelle", "string", "type text", "none", None),
    ],
    "courbe_profit": [
        ("seuil", "double", "type number", "none", "0.00"),
        ("clients_cibles", "int64", "Int64.Type", "sum", "#,0"),
        ("churners_cibles", "int64", "Int64.Type", "sum", "#,0"),
        ("gain", "double", "type number", "sum", "#,0 $"),
        ("cout", "double", "type number", "sum", "#,0 $"),
        ("profit", "double", "type number", "sum", "#,0 $"),
    ],
    "courbe_gain": [
        ("part_clients_contactes", "double", "type number", "none", "0 %"),
        ("part_departs_captes", "double", "type number", "sum", "0 %"),
        ("part_aleatoire", "double", "type number", "sum", "0 %"),
    ],
    "parametres": [
        ("seuil_campagne", "double", "type number", "sum", "0.00"),
        ("cout_offre", "double", "type number", "sum", "#,0 $"),
        ("taux_succes", "double", "type number", "sum", "0 %"),
        ("mois_valeur", "int64", "Int64.Type", "sum", "0"),
    ],
}

SORT_BY = {("clients", "tranche_anciennete"): "tranche_ordre"}
HIDDEN = {("clients", "tranche_ordre")}

# Mesures DAX (table, nom, expression, format)
MEASURES = [
    ("clients", "Nb clients", "COUNTROWS ( clients )", "#,0"),
    ("clients", "Nb départs", "SUM ( clients[churn] )", "#,0"),
    ("clients", "Taux de churn", "DIVIDE ( [Nb départs], [Nb clients] )", "0.0 %"),
    ("clients", "Revenu mensuel", "SUM ( clients[facture_mensuelle] )", "#,0 $"),
    ("clients", "Revenu mensuel perdu", "CALCULATE ( SUM ( clients[facture_mensuelle] ), clients[churn] = 1 )", "#,0 $"),
    ("clients", "Part du revenu perdu", "DIVIDE ( [Revenu mensuel perdu], [Revenu mensuel] )", "0.0 %"),
    ("clients", "Probabilité moyenne", "AVERAGE ( clients[probabilite_churn] )", "0.0 %"),
    ("clients", "Clients à risque élevé", "CALCULATE ( [Nb clients], clients[segment_risque] = \"Élevé\" )", "#,0"),
    ("clients", "Clients ciblés", "CALCULATE ( [Nb clients], clients[cible_campagne] = \"Oui\" )", "#,0"),
    ("clients", "Revenu mensuel à risque",
     "SUMX ( clients, clients[facture_mensuelle] * clients[probabilite_churn] )", "#,0 $"),
    ("clients", "Gain attendu de la campagne",
     "CALCULATE ( SUM ( clients[gain_attendu] ), clients[cible_campagne] = \"Oui\" )", "#,0 $"),
]


def m_query(table: str, cols) -> list[str]:
    types = ", ".join(f'{{"{c}", {mtype}}}' for c, _, mtype, _, _ in cols)
    return [
        "let",
        f'    Source = Csv.Document(File.Contents(Text.TrimEnd(DossierDonnees, "\\") & "\\{table}.csv"), '
        '[Delimiter=",", Encoding=65001, QuoteStyle=QuoteStyle.Csv]),',
        "    Entetes = Table.PromoteHeaders(Source, [PromoteAllScalars=true]),",
        # "en-US" : les CSV utilisent le point décimal, quel que soit le réglage régional du PC
        f'    Types = Table.TransformColumnTypes(Entetes, {{{types}}}, "en-US")',
        "in",
        "    Types",
    ]


def data_model() -> dict:
    tables = []
    for name, cols in TABLES.items():
        columns = []
        for c, dtype, _, summarize, fmt in cols:
            col = {
                "name": c,
                "dataType": dtype,
                "sourceColumn": c,
                "lineageTag": uid(),
                "summarizeBy": summarize,
                "annotations": [{"name": "SummarizationSetBy", "value": "Automatic"}],
            }
            if fmt:
                col["formatString"] = fmt
            if (name, c) in SORT_BY:
                col["sortByColumn"] = SORT_BY[(name, c)]
            if (name, c) in HIDDEN:
                col["isHidden"] = True
            columns.append(col)
        table = {
            "name": name,
            "lineageTag": uid(),
            "columns": columns,
            "partitions": [{"name": name, "mode": "import", "source": {"type": "m", "expression": m_query(name, cols)}}],
            "annotations": [{"name": "PBI_ResultType", "value": "Table"}],
        }
        measures = [
            {"name": m, "expression": expr, "formatString": fmt, "lineageTag": uid()}
            for t, m, expr, fmt in MEASURES if t == name
        ]
        if measures:
            table["measures"] = measures
        tables.append(table)

    folder = DEFAULT_FOLDER.replace('"', '""')
    return {
        "name": uid(),
        "compatibilityLevel": 1550,
        "model": {
            "culture": "fr-FR",
            "dataAccessOptions": {"legacyRedirects": True, "returnErrorValuesAsNull": True},
            "defaultPowerBIDataSourceVersion": "powerBI_V3",
            "sourceQueryCulture": "fr-FR",
            "tables": tables,
            "expressions": [{
                "name": "DossierDonnees",
                "kind": "m",
                "expression": f'"{folder}" meta [IsParameterQuery=true, Type="Text", IsParameterQueryRequired=true]',
                "lineageTag": uid(),
                "annotations": [{"name": "PBI_ResultType", "value": "Text"},
                                {"name": "PBI_NavigationStepName", "value": "Navigation"}],
            }],
            "annotations": [
                {"name": "PBI_QueryOrder", "value": json.dumps(["DossierDonnees", *TABLES])},
                {"name": "__PBI_TimeIntelligenceEnabled", "value": "0"},
                # Version de Power BI Desktop « auteur » : volontairement ancienne (février 2023)
                # pour que toute version plus récente accepte d'ouvrir le fichier.
                {"name": "PBIDesktopVersion", "value": "2.114.322.0 (23.02)"},
            ],
        },
    }


# ─────────────────────────────────────────────────────────────
# 2. Rapport : pages et visuels
# ─────────────────────────────────────────────────────────────
ALIASES = {t: f"t{i}" for i, t in enumerate(TABLES)}
MEASURE_NAMES = {(t, m) for t, m, _, _ in MEASURES}


def lit(text: str) -> dict:
    return {"expr": {"Literal": {"Value": "'" + text.replace("'", "''") + "'"}}}


def true() -> dict:
    return {"expr": {"Literal": {"Value": "true"}}}


def field(table: str, name: str, agg: int | None = None) -> tuple[dict, str]:
    """Champ d'une requête de visuel. agg : 0 = somme ; None = colonne ou mesure telle quelle."""
    src = {"SourceRef": {"Source": ALIASES[table]}}
    if (table, name) in MEASURE_NAMES:
        return {"Measure": {"Expression": src, "Property": name}, "Name": f"{table}.{name}"}, f"{table}.{name}"
    col = {"Column": {"Expression": src, "Property": name}}
    if agg is None:
        return {**col, "Name": f"{table}.{name}", "NativeReferenceName": name}, f"{table}.{name}"
    ref = f"Sum({table}.{name})"
    return {"Aggregation": {"Expression": col, "Function": agg}, "Name": ref, "NativeReferenceName": name}, ref


class Page:
    def __init__(self, title: str):
        self.title = title
        self.visuals: list[dict] = []

    def add(self, vtype: str, x, y, w, h, title: str, roles: dict, order_by=None, objects=None, show_title=True):
        selects, projections, tables = [], {}, set()
        for role, items in roles.items():
            projections[role] = []
            for table, name, *agg in items:
                sel, ref = field(table, name, agg[0] if agg else None)
                if ref not in [s["Name"] for s in selects]:
                    selects.append(sel)
                projections[role].append({"queryRef": ref, **({"active": True} if role in ("Category", "Values") and vtype == "slicer" else {})})
                tables.add(table)
        query = {"Version": 2, "From": [{"Name": ALIASES[t], "Entity": t, "Type": 0} for t in sorted(tables)], "Select": selects}
        if order_by:
            table, name, direction = order_by
            sel, _ = field(table, name)
            expr = {k: v for k, v in sel.items() if k in ("Measure", "Column")}
            query["OrderBy"] = [{"Direction": direction, "Expression": expr}]
        vc_objects = {"title": [{"properties": {"show": true() if show_title else {"expr": {"Literal": {"Value": "false"}}},
                                                "text": lit(title)}}]}
        single = {
            "visualType": vtype,
            "projections": projections,
            "prototypeQuery": query,
            "drillFilterOtherVisuals": True,
            "vcObjects": vc_objects,
        }
        if objects:
            single["objects"] = objects
        name = uuid.uuid4().hex[:20]
        z = len(self.visuals) * 1000
        config = {"name": name, "layouts": [{"id": 0, "position": {"x": x, "y": y, "z": z, "width": w, "height": h}}],
                  "singleVisual": single}
        self.visuals.append({"x": x, "y": y, "z": z, "width": w, "height": h,
                             "config": json.dumps(config, ensure_ascii=False), "filters": "[]"})


LABELS_ON = {"labels": [{"properties": {"show": true()}}]}


def report_pages() -> list[Page]:
    # ── Page 1 : vue d'ensemble ──────────────────────────────────────
    p1 = Page("Vue d'ensemble")
    cards = [("Nb clients", "Clients"), ("Taux de churn", "Taux de churn"),
             ("Revenu mensuel perdu", "Revenu mensuel perdu"), ("Part du revenu perdu", "Part du revenu perdu")]
    for i, (m, t) in enumerate(cards):
        p1.add("card", 20 + i * 250, 20, 235, 110, t, {"Values": [("clients", m)]})
    p1.add("slicer", 1030, 20, 230, 110, "Contrat", {"Values": [("clients", "contrat")]})
    charts = [
        ("contrat", "Taux de churn par contrat", 20, 145, "clusteredColumnChart"),
        ("tranche_anciennete", "Taux de churn par ancienneté", 650, 145, "clusteredColumnChart"),
        ("moyen_paiement", "Taux de churn par moyen de paiement", 20, 430, "clusteredBarChart"),
        ("internet", "Taux de churn par type d'internet", 650, 430, "clusteredBarChart"),
    ]
    for col, title, x, y, vt in charts:
        p1.add(vt, x, y, 610, 270, title, {"Category": [("clients", col)], "Y": [("clients", "Taux de churn")]},
               objects=LABELS_ON)

    # ── Page 2 : clients à risque ────────────────────────────────────
    p2 = Page("Clients à risque")
    cards = [("Clients à risque élevé", "Clients à risque élevé"), ("Clients ciblés", "Clients à contacter"),
             ("Revenu mensuel à risque", "Revenu mensuel à risque"), ("Gain attendu de la campagne", "Gain attendu de la campagne")]
    for i, (m, t) in enumerate(cards):
        p2.add("card", 20 + i * 250, 20, 235, 110, t, {"Values": [("clients", m)]})
    p2.add("slicer", 1030, 20, 230, 110, "Segment de risque", {"Values": [("clients", "segment_risque")]})
    p2.add("donutChart", 20, 145, 380, 555, "Clients par segment de risque",
           {"Category": [("clients", "segment_risque")], "Y": [("clients", "Nb clients")]}, objects=LABELS_ON)
    p2.add("tableEx", 415, 145, 845, 555, "Clients à contacter en priorité",
           {"Values": [("clients", "id_client"), ("clients", "probabilite_churn"), ("clients", "segment_risque"),
                       ("clients", "contrat"), ("clients", "anciennete_mois"), ("clients", "facture_mensuelle", 0),
                       ("clients", "motif_1"), ("clients", "motif_2")]},
           order_by=("clients", "probabilite_churn", 2))

    # ── Page 3 : modèle et campagne ──────────────────────────────────
    p3 = Page("Modèle et campagne")
    p3.add("lineChart", 20, 20, 620, 330, "Profit de la campagne selon le seuil de ciblage",
           {"Category": [("courbe_profit", "seuil")], "Y": [("courbe_profit", "profit", 0)]})
    p3.add("lineChart", 655, 20, 605, 330, "Courbe de gain : part des départs captés",
           {"Category": [("courbe_gain", "part_clients_contactes")],
            "Y": [("courbe_gain", "part_departs_captes", 0), ("courbe_gain", "part_aleatoire", 0)]})
    p3.add("clusteredBarChart", 20, 365, 620, 335, "Ce qui prédit le départ (importance)",
           {"Category": [("importance_variables", "libelle")], "Y": [("importance_variables", "importance", 0)]},
           order_by=("importance_variables", "importance", 2))
    p3.add("tableEx", 655, 365, 605, 200, "Modèles comparés (validation croisée)",
           {"Values": [("comparaison_modeles", "modele"), ("comparaison_modeles", "roc_auc"),
                       ("comparaison_modeles", "pr_auc"), ("comparaison_modeles", "retenu")]})
    p3.add("card", 655, 580, 295, 120, "Seuil de ciblage", {"Values": [("parametres", "seuil_campagne", 0)]})
    p3.add("card", 965, 580, 295, 120, "Coût d'une offre", {"Values": [("parametres", "cout_offre", 0)]})
    return [p1, p2, p3]


def layout() -> dict:
    sections = []
    for i, page in enumerate(report_pages()):
        sections.append({
            "id": i,
            "name": f"ReportSection{uuid.uuid4().hex[:20]}",
            "displayName": page.title,
            "filters": "[]",
            "ordinal": i,
            "visualContainers": page.visuals,
            "config": "{}",
            "displayOption": 1,
            "width": 1280,
            "height": 720,
        })
    return {
        "id": 0,
        "resourcePackages": [],
        "sections": sections,
        "config": json.dumps({"version": "5.32", "themeCollection": {}, "activeSectionIndex": 0}),
        "layoutOptimization": 0,
    }


# ─────────────────────────────────────────────────────────────
# 2 bis. DataMashup : les requêtes Power Query au format binaire
# ─────────────────────────────────────────────────────────────
# Power BI garde les requêtes à deux endroits : dans DataModelSchema et dans
# DataMashup (format « MS-QDEFF »). Il vérifie que les deux concordent ;
# sans DataMashup, il refuse d'ouvrir le fichier (MashupValidationError).
#
# Structure de DataMashup (entiers sur 4 octets, petit-boutiste) :
#   version (0) | taille + zip des formules | taille + XML des permissions
#   | taille + métadonnées | taille + liaisons de permissions (vide ici)
BOM = b"\xef\xbb\xbf"


def _zip_bytes(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


def _section_m(param_expr: str) -> str:
    parts = ["section Section1;", "", f"shared DossierDonnees = {param_expr};"]
    for name, cols in TABLES.items():
        parts += ["", f"shared {name} = " + "\n".join(m_query(name, cols)) + ";"]
    return "\r\n".join(parts) + "\r\n"


def _entry(t: str, v: str) -> str:
    return f'<Entry Type="{t}" Value="{xml_escape(v, {chr(34): "&quot;"})}" />'


def _item(path: str, entries: list[str], kind: str = "Formula") -> str:
    loc = f"<ItemPath>{path}</ItemPath>" if path else "<ItemPath />"
    body = "<StableEntries>" + "".join(entries) + "</StableEntries>" if entries else "<StableEntries />"
    return f"<Item><ItemLocation><ItemType>{kind}</ItemType>{loc}</ItemLocation>{body}</Item>"


def data_mashup(param_expr: str) -> bytes:
    now = "d" + datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.0000000Z")
    formulas = _zip_bytes({
        "[Content_Types].xml": BOM + (
            '<?xml version="1.0" encoding="utf-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="xml" ContentType="text/xml" /><Default Extension="m" ContentType="application/x-ms-m" /></Types>'
        ).encode(),
        "Config/Package.xml": BOM + (
            '<?xml version="1.0" encoding="utf-8"?><Package xmlns:xsd="http://www.w3.org/2001/XMLSchema" '
            'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"><Version>2.114.322.0</Version>'
            '<MinVersion>1.5.3296.0</MinVersion><Culture>fr-FR</Culture></Package>'
        ).encode(),
        "Formulas/Section1.m": BOM + _section_m(param_expr).encode("utf-8"),
    })
    permissions = BOM + (
        '<?xml version="1.0" encoding="utf-8"?><PermissionList xmlns:xsd="http://www.w3.org/2001/XMLSchema" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"><CanEvaluateFuturePackages>false</CanEvaluateFuturePackages>'
        '<FirewallEnabled>true</FirewallEnabled></PermissionList>'
    ).encode()

    items = [_item("", [_entry("IsTypeDetectionEnabled", "sTrue"), _entry("RunBackgroundAnalysis", "sFalse")],
                   kind="AllFormulas")]
    items.append(_item("Section1/DossierDonnees", [
        _entry("IsHidden", "l0"), _entry("LoadToReportDisabled", "l1"), _entry("FillErrorCode", "sUnknown"),
        _entry("FillLastUpdated", now), _entry("NavigationStepName", "sNavigation"), _entry("ResultType", "sText")]))
    for name, cols in TABLES.items():
        root = json.dumps({"IncludesReferencedQueries": False, "RootFormulaText": "\n".join(m_query(name, cols)),
                           "ReferencedQueriesFormulaText": {}}, ensure_ascii=False, separators=(",", ":"))
        items.append(_item(f"Section1/{name}", [
            _entry("IsHidden", "l0"), _entry("IsDirectQuery", "l0"),
            _entry("LastAnalysisServicesFormulaText", "s" + root),
            _entry("IsLastAnalysisServicesFormulaTextCollection", "l1"),
            _entry("LoadToReportDisabled", "l0"), _entry("FillErrorCode", "sUnknown"),
            _entry("FillLastUpdated", now), _entry("NavigationStepName", "sNavigation"), _entry("ResultType", "sTable")]))
        for step in ("Source", "Entetes", "Types"):
            items.append(_item(f"Section1/{name}/{step}", []))
    meta_xml = BOM + (
        '<?xml version="1.0" encoding="utf-8"?><LocalPackageMetadataFile xmlns:xsd="http://www.w3.org/2001/XMLSchema" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"><Items>' + "".join(items) + "</Items></LocalPackageMetadataFile>"
    ).encode("utf-8")
    empty_zip = b"PK\x05\x06" + b"\x00" * 18
    metadata = struct.pack("<I", 0) + struct.pack("<I", len(meta_xml)) + meta_xml + struct.pack("<I", len(empty_zip)) + empty_zip

    out = struct.pack("<I", 0)
    for block in (formulas, permissions, metadata, b""):
        out += struct.pack("<I", len(block)) + block
    return out


# ─────────────────────────────────────────────────────────────
# 3. Assemblage du .pbit
# ─────────────────────────────────────────────────────────────
def utf16(obj) -> bytes:
    text = obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False)
    return text.encode("utf-16-le")


CONTENT_TYPES = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="json" ContentType="" />'
    '<Override PartName="/Version" ContentType="" />'
    '<Override PartName="/DataMashup" ContentType="" />'
    '<Override PartName="/DataModelSchema" ContentType="" />'
    '<Override PartName="/DiagramLayout" ContentType="" />'
    '<Override PartName="/Report/Layout" ContentType="" />'
    '<Override PartName="/Settings" ContentType="application/json" />'
    '<Override PartName="/Metadata" ContentType="application/json" />'
    "</Types>"
)


def build(path: Path = OUT) -> Path:
    diagram = {
        "version": "1.1.0",
        "diagrams": [{"ordinal": 0, "scrollPosition": {"x": 0, "y": 0}, "nodes": [], "name": "All tables",
                      "zoomValue": 100, "pinKeyFieldsToTop": False, "showExtraHeaderInfo": False,
                      "hideKeyFieldsWhenCollapsed": False, "tablesLocked": False}],
        "selectedDiagram": "All tables",
        "defaultDiagram": "All tables",
    }
    # Formats alignés sur un vrai .pbit enregistré par Power BI Desktop (février 2023) :
    # Settings en version 1, clés de Metadata en majuscules, fichier Version en « 1.25 ».
    settings = {"Version": 1, "ReportSettings": {},
                "QueriesSettings": {"TypeDetectionEnabled": True, "RelationshipImportEnabled": True,
                                    "RunBackgroundAnalysis": True}}
    metadata = {"Version": 5, "AutoCreatedRelationships": [],
                "FileDescription": "Tableau de bord de prédiction du churn. "
                "Indiquez le dossier powerbi\\data du projet (contenant clients.csv).",
                "CreatedFrom": "Desktop", "CreatedFromRelease": "2023.02"}
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", CONTENT_TYPES.encode("utf-8"))
        z.writestr("Version", utf16("1.25"))
        model = data_model()
        param = model["model"]["expressions"][0]["expression"]
        z.writestr("DataMashup", data_mashup(param))
        z.writestr("DataModelSchema", utf16(model))
        z.writestr("DiagramLayout", utf16(diagram))
        z.writestr("Report/Layout", utf16(layout()))
        z.writestr("Settings", utf16(settings))
        z.writestr("Metadata", utf16(metadata))
    return path


if __name__ == "__main__":
    print(f"Modèle Power BI créé : {build()}")
