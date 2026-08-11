FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .
COPY ecf/ ./ecf/

# Garde-fou de démarrage : si un template ou une géométrie manque dans l'image,
# le build échoue ici plutôt que de livrer un conteneur qui répondra 404 en
# production.
RUN python -c "from ecf.livret import codes_disponibles, charger; \
    codes = codes_disponibles(); assert codes, 'aucun livret embarqué'; \
    [charger(c) for c in codes]; print('livrets embarqués :', codes)"

EXPOSE 8000
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]
