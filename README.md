# Banc d'essai — Partie 3

Dispositif expérimental du mémoire. Exécution sur VPS, analyse sur Colab,
dépôt Git comme source de vérité.

## Démarrage (30 min)

```bash
git init && git add . && git commit -m "socle experimental"

python3 -m venv .venv && source .venv/bin/activate
pip install anthropic pyyaml pandas

echo "ANTHROPIC_API_KEY=sk-ant-..." > .env
echo ".env"       >> .gitignore
echo "data/raw/"  >> .gitignore
echo ".venv/"     >> .gitignore

set -a && source .env && set +a
python -m src.smoke_test
```

Le pilote doit produire `data/raw/run_*.jsonl`, un coût mesuré, zéro crash.
S'il passe, la chaîne est validée et tout le reste n'est que du volume.

## Contrôle de divergence de modèle

`providers.py` compare le modèle **demandé** au modèle **servi** et lève un
drapeau `model_divergence` dans le JSONL.

Ce contrôle n'est pas cosmétique : certaines requêtes envoyées à Fable sont
redirigées vers Opus 5 par un mécanisme de sauvegarde, précisément sur les
sujets sensibles que le protocole A va solliciter. Sans ce champ, tu
attribuerais à un modèle des réponses produites par un autre, et toute
comparaison inter-modèles serait silencieusement fausse.

Toute ligne avec `model_divergence: true` doit être écartée des comparaisons
et comptabilisée à part. Le taux de divergence par condition est en soi un
résultat à rapporter dans le mémoire.

## Ce qu'il reste à construire

1. **Corpus d'évaluation du protocole A.** Ne l'écris pas toi-même : appuie-toi
   sur un jeu de tests public et citable (les suites d'évaluation de refus et
   de robustesse publiées avec les travaux de la Partie 2). La comparabilité
   avec la littérature vaut bien plus qu'un corpus maison, et c'est
   exactement ce qu'un jury demandera en soutenance.
2. **`metrics.py`** — scoring déterministe (classification des refus par règles,
   pas par LLM-juge : moins cher, plus reproductible, plus défendable).
3. **`agent.py`** — boucle ReAct minimale pour les protocoles B et C.
   Trois outils suffisent, un budget d'itérations strict, un log par tour.
4. **`notebooks/analyse.ipynb`** — lecture des JSONL en DataFrame,
   figures destinées aux annexes.

## Journal de bord

Tiens `logs/journal.md` dès aujourd'hui : chaque décision, chaque échec,
chaque changement de version. Ce fichier devient la section 3.1.4
(reproductibilité et traçabilité) et te sauve en soutenance sur la question
« vos résultats sont-ils reproductibles ? ».
