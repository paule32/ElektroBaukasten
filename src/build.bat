:: ---------------------------------------------------------------------------
:: File:   build.bat
:: Author: (c) 2024, 2025, 2026 Jens Kallup - paule32
:: All rights reserved
:: ---------------------------------------------------------------------------
@echo on
pyrcc5 elektro_symbole.qrc -o elektro_symbole.py
python -m compileall elektro_symbole.py
python -m compileall qt5_mdi_schaltplan_editor_v12.py

python    qt5_mdi_schaltplan_editor_v12.py
