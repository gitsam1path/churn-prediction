@echo off
chcp 65001 >nul
title Churn prediction
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Installation de l'environnement Python ^(une seule fois^)...
  python -m venv .venv || (echo Python introuvable : installez Python 3.11+ depuis python.org & pause & exit /b 1)
  ".venv\Scripts\python.exe" -m pip install --upgrade pip >nul
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt || (pause & exit /b 1)
)
echo.
echo [1/2] Entrainement du modele et export des donnees Power BI...
pushd src
"..\.venv\Scripts\python.exe" -m churn.train || (popd & pause & exit /b 1)
popd
echo [2/2] Generation du modele Power BI...
".venv\Scripts\python.exe" powerbi\build_pbip.py
echo.
echo Termine. Ouvrez powerbi\churn_dashboard.pbip puis cliquez sur Actualiser.
echo Les donnees sont integrees au rapport : aucun chemin a configurer.
echo.
pause
