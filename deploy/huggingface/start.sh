#!/bin/sh
# Start the support model, prepare the database, then serve the site on the Space port.
set -e
ollama serve >/tmp/ollama.log 2>&1 &
for i in $(seq 1 60); do curl -sf http://127.0.0.1:11434/api/tags >/dev/null && break; sleep 1; done

export KAVRELLE_SECURE_COOKIES=1
export KAVRELLE_TRUST_PROXY=all
export KAVRELLE_FRAME_ANCESTORS="https://huggingface.co"
[ -n "$SPACE_HOST" ] && export KAVRELLE_PUBLIC_URL="https://$SPACE_HOST"
if [ -z "$KAVRELLE_DATABASE_URL" ]; then
  echo "KAVRELLE_DATABASE_URL is not set: using a temporary SQLite file (data resets on restart)."
fi
cd /app
python -m kavrelle.cli setup
exec python -m kavrelle.cli serve --host 0.0.0.0 --port 7860
