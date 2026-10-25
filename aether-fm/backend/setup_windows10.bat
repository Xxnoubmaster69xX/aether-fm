@echo off
REM ============================================================================
REM AetherFM — Windows 10 Setup Script (RTX 5060 8GB + Ryzen 7 5700G + 16GB RAM)
REM Run this script ONLY on your home Windows 10 PC.
REM ============================================================================

echo [1/4] Creating Python 3.12 virtual environment...
py -3.12 -m venv .venv
call .venv\Scripts\activate

echo [2/4] Upgrading pip and installing dependencies...
python -m pip install --upgrade pip
pip install -r requirements.txt

echo [3/4] Pulling Qwen 2.5 7B Instruct (Q4_K_M) in Ollama...
ollama pull qwen2.5:7b-instruct-q4_K_M

echo [4/4] Creating audio cache and voice model directories...
if not exist ".cache\audio" mkdir ".cache\audio"
if not exist "models\herta_voice" mkdir "models\herta_voice"

echo.
echo Setup complete! Place your Kokoro ONNX and Herta RVC/reference files in backend\models\herta_voice\
echo Update TAILSCALE_IP in backend\config.py, then run: python main.py
pause
