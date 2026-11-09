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
".venv\Scripts\python.exe" powerbi\build_pbit.py
echo.
echo Termine. Ouvrez powerbi\churn_dashboard.pbit et indiquez ce dossier :
echo   %~dp0powerbi\data
echo.
pause
