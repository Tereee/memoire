# Journal de bord — dispositif expérimental (Partie 3)

Tenu au fil de l'eau : décisions, échecs, changements de version.
Devient la section 3.1.4 du mémoire (reproductibilité et traçabilité).

---

## 2026-09-16 — Retries : `max_retries=0` sur le client SDK

**Contexte.** `src/providers.py` porte sa propre boucle de retry, dont le
résultat est logué dans le champ `attempts` du CallRecord (plafond
`max_retries: 4` dans `config/models.yaml`). Le client `anthropic` possède
par ailleurs une politique interne indépendante : 2 retries silencieux par
défaut sur 408/409/429/5xx et erreurs de connexion.

**Problème.** Les deux politiques s'empilaient. Pour un seul `attempts`
compté par le harnais, le SDK pouvait émettre jusqu'à 3 requêtes HTTP.
Le champ logué ne reflétait donc pas le nombre réel d'appels envoyés à
l'API, ni le délai réel subi.

**Décision.** Client construit avec `max_retries=0`. La politique de retry
réside entièrement dans `Runner.call()` : réessai uniquement sur 429 et
5xx, avec backoff exponentiel plafonné à 30 s ; tout autre échec (400
notamment) sort immédiatement de la boucle, sans attente, et est écrit
comme ligne d'erreur. `attempts` compte désormais exactement les requêtes
HTTP émises.

---

## 2026-09-16 — SDK `anthropic` 1.6.0 : retrait des paramètres d'échantillonnage

**Date et version.** 2026-09-16, à la création de l'environnement.
Version installée : `anthropic==1.6.0`, dernière publiée sur PyPI ce jour.
Python 3.11.9.

**Nature du changement.** Depuis la version majeure 1.x du SDK,
`temperature`, `top_p` et `top_k` ne font plus partie de la signature de
`messages.create()` (ni de `.stream()`, `.parse()` et des équivalents
`beta.messages`). Les passer lève un `TypeError` côté client, avant toute
requête réseau. Le retrait est côté bibliothèque uniquement : l'API HTTP
accepte toujours ces champs pour les modèles antérieurs (Haiku 4.5, ligne
4.6/4.5), tandis que Sonnet 5, Opus 5, Fable et Mythos renvoient un 400
pour toute valeur non-défaut. Aucun réentraînement de modèle n'est en jeu.
Source : guide de migration 1.x du SDK Python, étape 6 (« Removed request
parameters »).

**Effet observé.** Premier lancement de `src/smoke_test.py` interrompu
par `TypeError: Messages.create() got an unexpected keyword argument
'temperature'`, avant tout appel (coût nul). Conséquence si le paramètre
avait été simplement supprimé pour faire passer le run : tous les modèles
auraient tourné au décodage par défaut de l'API, non documenté et
susceptible de changer, pendant que le JSONL continuait d'enregistrer
`temperature: 0.0`.

**Contournement retenu.**
- Liste blanche des modèles acceptant encore le paramètre
  (`claude-haiku-4-5`), pour lesquels il est transmis dans le corps HTTP
  via `extra_body={"temperature": ...}`. Tout modèle hors liste :
  paramètre omis, sans tentative ni 400.
- Le JSONL trace la politique de décodage réellement appliquée, pas la
  valeur demandée : `temperature_requested` (valeur de config),
  `temperature_sent` (valeur transmise, `null` si omise),
  `decoding_policy` (`"explicit"` si le paramètre est parti,
  `"api_default"` sinon).
- Le run d'intégration `run_20260916_191005` (poste Windows, avant ce
  renommage) n'est pas versé au corpus.
