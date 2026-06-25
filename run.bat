@echo off
REM Start the BharatStocks analyzer, then open it in your browser.
cd /d "%~dp0"
start "" http://127.0.0.1:5000
python app.py
