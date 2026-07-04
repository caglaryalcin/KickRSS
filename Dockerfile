FROM python:3.12-slim

WORKDIR /KickRSS

COPY ./KickRSS/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY ./KickRSS/ ./

ENTRYPOINT ["/bin/sh", "entrypoint.sh"]
