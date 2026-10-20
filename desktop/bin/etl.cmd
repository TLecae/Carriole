@echo off
rem Ligne de commande « etl » de Carriole : lance le Python embarqué (ressources et variables posées par etl_engine.install).
"%~dp0..\runtime\python\python.exe" -X utf8 -m etl_engine.cli %*
exit /b %ERRORLEVEL%
