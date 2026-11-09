# Bac à sable d'exécution des tests du protocole B.
#
# Construction :   docker build -t memoire/sandbox-b:1 docker/sandbox_b
# Image de base épinglée par digest (index multi-architecture, résolu le
# 2026-11-09). L'Image ID de memoire/sandbox-b:1 change à chaque construction
# (attestations horodatées) : il est journalisé à chaque épisode
# (champ sandbox_image_id) plutôt que figé ici.
#
# Le conteneur ne reçoit que la copie temporaire de l'espace de travail, montée
# en lecture seule. Réseau, privilèges, CPU, mémoire et nombre de processus sont
# limités à l'exécution (voir src/agent.py, DOCKER_RUN_FLAGS). Aucune dépendance
# hors bibliothèque standard : les tests utilisent unittest.
FROM python:3.11.9-slim@sha256:8fb099199b9f2d70342674bd9dbccd3ed03a258f26bbd1d556822c6dfc60c317

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin sandbox \
    && mkdir -p /opt/sandbox /work

COPY bootstrap.py /opt/sandbox/bootstrap.py
RUN chmod 0444 /opt/sandbox/bootstrap.py

USER 10001:10001
WORKDIR /work
