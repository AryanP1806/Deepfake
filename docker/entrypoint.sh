#!/bin/sh
# First start: fetch the detection models into the persistent /app/models volume (once), then run the app.
set -e
MARK=/app/models/.models_ready_v1
if [ ! -f "$MARK" ]; then
  echo "=============================================================================="
  echo " First start: downloading the detection models (about 3 GB, one time only)."
  echo " This can take 5-20 minutes. The app opens at http://localhost:8000 when done."
  echo "=============================================================================="
  if python /app/scripts/setup_models.py --yes yunet spai trufor sbi ufd whisper; then
    touch "$MARK"
  else
    echo "WARNING: some models could not be downloaded. The app will start with fewer detectors;"
    echo "         restart the container to try the download again."
  fi
fi
cd /app/backend
exec uvicorn dfir.main:app --host 0.0.0.0 --port 8000
