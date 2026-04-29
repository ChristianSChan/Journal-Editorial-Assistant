#!/bin/bash

cd "$(dirname "$0")" || exit 1

APP_URL="http://localhost:8501/"
INSTALL_MARKER=".venv/.requirements-installed"

finish() {
  status=$?
  if [ "$status" -ne 0 ]; then
    echo
    echo "The app did not start successfully."
    echo "If the message above mentions Python or Streamlit, send it to Codex and I can help fix it."
    echo
    read -r -p "Press Return to close this window."
  fi
}
trap finish EXIT

echo "Starting Journal Editorial Assistant..."
echo "Project folder: $(pwd)"
echo

if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 was not found. Please install Python 3, then run this launcher again."
  echo
  read -r -p "Press Return to close this window."
  exit 1
fi

if [ ! -d ".venv" ]; then
  echo "Creating local Python environment..."
  python3 -m venv .venv || exit 1
fi

source ".venv/bin/activate"

if [ ! -f "$INSTALL_MARKER" ]; then
  echo "First launch: installing required packages..."
  python -m pip install --upgrade pip
  python -m pip install -r requirements.txt || exit 1
  touch "$INSTALL_MARKER"
else
  echo "Using existing local Python environment."
fi

echo
echo "Opening app at $APP_URL"
echo "Keep this Terminal window open while using the app."
echo

(sleep 3; open "$APP_URL") &

python -m streamlit run app.py --server.port 8501 --server.address localhost
