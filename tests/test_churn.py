"""Tests : données, règles de la campagne, modèle et exports Power BI."""

import json
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from churn import data as D
from churn.model import campaign_profit, gain_curve, lift_at, risk_segment

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def df():
    return D.load_dataset()


def test_donnees(df):
    assert len(df) == 7043
    assert df["churn"].mean() == pytest.approx(0.2654, abs=1e-3)
    assert df[D.FEATURES].isna().sum().sum() == 0
    assert set(df["contrat"]) == {"Mensuel", "1 an", "2 ans"}


def test_variables_exclues(df):
    for col in ["genre", "facture_mensuelle", "total_facture"]:
        assert col not in D.FEATURES


def test_profit_campagne():
    y = np.array([1, 0, 1, 0])
    p = np.array([0.9, 0.8, 0.1, 0.05])
    monthly = np.array([100.0, 100.0, 100.0, 100.0])
    r = campaign_profit(y, p, monthly, threshold=0.5)
    # 2 clients contactés (coût 2 × 50), 1 vrai départ sauvé à 30 % × 12 × 100 = 360
    assert r["clients_cibles"] == 2
    assert r["profit"] == pytest.approx(360 - 100)


def test_courbe_de_gain_et_lift():
    y = np.array([1, 1, 0, 0, 0, 0, 0, 0, 0, 0])
    p = np.linspace(1, 0, 10)  # les deux départs ont les probabilités les plus fortes
    g = gain_curve(y, p)
    assert g["part_departs_captes"].iloc[-1] == 1
    assert lift_at(y, p, 0.2) == pytest.approx(5)


def test_segments():
    assert [risk_segment(p) for p in (0.1, 0.3, 0.7)] == ["Faible", "Moyen", "Élevé"]


def test_metriques_du_modele():
    m = json.loads((ROOT / "reports" / "metrics.json").read_text(encoding="utf-8"))["test"]
    assert m["roc_auc"] > 0.82
    assert m["profit_campagne"] > m["profit_tout_le_monde"]


def test_exports_powerbi():
    clients = pd.read_csv(ROOT / "powerbi" / "data" / "clients.csv")
    assert len(clients) == 7043
    assert clients["probabilite_churn"].between(0, 1).all()
    # les clients les plus risqués ont au moins un motif
    top = clients.nlargest(100, "probabilite_churn")
    assert top["motif_1"].notna().all()


def test_pbit_valide():
    path = ROOT / "powerbi" / "churn_dashboard.pbit"
    with zipfile.ZipFile(path) as z:
        assert {"DataModelSchema", "Report/Layout", "Version", "[Content_Types].xml"} <= set(z.namelist())
        model = json.loads(z.read("DataModelSchema").decode("utf-16-le"))
        layout = json.loads(z.read("Report/Layout").decode("utf-16-le"))
    tables = {t["name"] for t in model["model"]["tables"]}
    assert {"clients", "courbe_profit", "importance_variables"} <= tables
    assert len(layout["sections"]) == 3
