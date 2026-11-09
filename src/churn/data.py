"""
Chargement et préparation du jeu de données Telco Customer Churn (IBM).

On renomme les colonnes et on traduit les valeurs en français, une seule fois ici :
le modèle, les rapports et le tableau de bord Power BI parlent ainsi la même langue.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
RAW_PATH = ROOT / "data" / "raw" / "telco_churn.csv"

COLUMNS = {
    "customerID": "id_client",
    "gender": "genre",
    "SeniorCitizen": "senior",
    "Partner": "en_couple",
    "Dependents": "personnes_a_charge",
    "tenure": "anciennete_mois",
    "PhoneService": "telephone",
    "MultipleLines": "lignes_multiples",
    "InternetService": "internet",
    "OnlineSecurity": "securite_en_ligne",
    "OnlineBackup": "sauvegarde_en_ligne",
    "DeviceProtection": "protection_appareil",
    "TechSupport": "support_technique",
    "StreamingTV": "streaming_tv",
    "StreamingMovies": "streaming_films",
    "Contract": "contrat",
    "PaperlessBilling": "facture_dematerialisee",
    "PaymentMethod": "moyen_paiement",
    "MonthlyCharges": "facture_mensuelle",
    "TotalCharges": "total_facture",
    "Churn": "churn",
}

VALUES = {
    "Yes": "Oui",
    "No": "Non",
    "No phone service": "Pas de téléphone",
    "No internet service": "Pas d'internet",
    "Fiber optic": "Fibre optique",
    "Month-to-month": "Mensuel",
    "One year": "1 an",
    "Two year": "2 ans",
    "Electronic check": "Chèque électronique",
    "Mailed check": "Chèque postal",
    "Bank transfer (automatic)": "Virement automatique",
    "Credit card (automatic)": "Carte bancaire automatique",
    "Female": "Femme",
    "Male": "Homme",
}

SERVICES = [
    "securite_en_ligne", "sauvegarde_en_ligne", "protection_appareil", "support_technique",
    "streaming_tv", "streaming_films",
]

# Deux variables financières ne sont PAS utilisées par le modèle (mais servent au calcul de la valeur client) :
#  - total_facture ≈ anciennete_mois × facture_mensuelle (corrélation 0,83 avec l'ancienneté) ;
#  - facture_mensuelle est entièrement déterminée par les services souscrits (R² = 0,999).
# Les garder rendait les coefficients instables et les motifs absurdes
# ("total facturé élevé" ou "lignes multiples" présentés comme raisons de partir).
NUMERIC_FEATURES = ["anciennete_mois", "nb_services"]
CATEGORICAL_FEATURES = [
    "senior", "en_couple", "personnes_a_charge", "telephone", "lignes_multiples", "internet",
    *SERVICES, "contrat", "facture_dematerialisee", "moyen_paiement",
]
FEATURES = NUMERIC_FEATURES + CATEGORICAL_FEATURES
TARGET = "churn"
# Le genre n'est pas utilisé : il n'apporte rien au métier et pourrait conduire
# à cibler les offres de façon inéquitable. Il reste disponible pour l'analyse.
EXCLUDED = ["genre"]

TENURE_BINS = [-1, 6, 12, 24, 48, 72]
TENURE_LABELS = ["0-6 mois", "7-12 mois", "13-24 mois", "25-48 mois", "49-72 mois"]


def load_raw(path: Path = RAW_PATH) -> pd.DataFrame:
    return pd.read_csv(path)


def clean(raw: pd.DataFrame) -> pd.DataFrame:
    df = raw.rename(columns=COLUMNS).copy()
    df["senior"] = df["senior"].map({1: "Yes", 0: "No"})
    obj = df.select_dtypes(include=["object", "string"]).columns.drop("id_client")
    df[obj] = df[obj].apply(lambda s: s.map(lambda v: VALUES.get(v, v)))

    # 11 nouveaux clients (ancienneté 0) n'ont pas encore de total facturé : la cellule est vide
    df["total_facture"] = pd.to_numeric(df["total_facture"].astype(str).str.strip(), errors="coerce").fillna(0.0)

    df["churn"] = (df["churn"] == "Oui").astype(int)
    df["nb_services"] = (df[SERVICES] == "Oui").sum(axis=1)
    df["tranche_anciennete"] = pd.cut(df["anciennete_mois"], TENURE_BINS, labels=TENURE_LABELS).astype(str)
    return df


def load_dataset(path: Path = RAW_PATH) -> pd.DataFrame:
    return clean(load_raw(path))


def split_xy(df: pd.DataFrame):
    return df[FEATURES], df[TARGET]


def summary(df: pd.DataFrame) -> dict:
    return {
        "clients": int(len(df)),
        "taux_churn": float(df[TARGET].mean()),
        "revenu_mensuel_total": float(df["facture_mensuelle"].sum()),
        "revenu_mensuel_perdu": float(df.loc[df[TARGET] == 1, "facture_mensuelle"].sum()),
        "anciennete_mediane": float(np.median(df["anciennete_mois"])),
    }
