# BRIEFING — Contexte du projet

Document de passation. À lire avant toute intervention sur le dépôt.

---

## 1. Ce que ce dépôt sert à produire

Le code de ce dépôt alimente la **Partie 3 d'un mémoire de mastère**
(IPSSI, Mastère 2 Dev, Data & IA). Ce n'est pas un projet logiciel
autonome : c'est un **dispositif expérimental** dont la sortie attendue
est un jeu de données exploitable et défendable devant un jury.

Conséquence directe sur toutes les décisions techniques : **la
reproductibilité et la traçabilité priment sur l'élégance, la performance
et la généralité**. Un code moins joli mais dont chaque run est rejouable
à l'identique vaut mieux que l'inverse.

---

## 2. Sujet et problématique

**Titre :** Alignement et contrôle des architectures d'IA avancées :
analyse des faiblesses structurelles face aux risques d'autonomie et de
perte de contrôle.

**Problématique :** Dans quelle mesure les techniques d'alignement
contemporaines (RLHF, Constitutional AI, RLAIF) échouent-elles à prévenir
les comportements émergents imprévus et la dérive d'objectifs chez les
agents autonomes ?

Titre et problématique sont **validés par le référent pédagogique et
figés**. Ils ne sont pas négociables.

---

## 3. La thèse centrale (à connaître pour coder juste)

La Partie 1 du mémoire établit un résultat qui commande tout le dispositif
expérimental :

> Un agent LLM se décompose en deux couches. Le **modèle** (paramètres
> figés, distribution conditionnelle sur des tokens) et le **harnais**
> d'orchestration (couche logicielle déterministe : mémoire, boucle,
> outils, planification). L'agentivité — persistance de l'état,
> effecteurs, clôture de la boucle perception-action — est **entièrement
> fournie par le harnais**.
>
> Or les procédures d'alignement (RLHF, CAI, RLAIF, DPO) opèrent sur les
> paramètres du modèle. Elles ne s'appliquent en aucune manière au
> harnais. Un refus produit par le modèle n'est qu'un événement dans la
> distribution de sortie, pas une garantie d'exécution : il n'a de portée
> effective que si le harnais est construit pour l'honorer.

**Ce que le banc d'essai doit mesurer :** la dégradation des garanties
comportementales lorsqu'on passe du modèle seul au système agentique.
C'est l'apport différenciant du mémoire. Tout protocole qui ne compare pas
ces deux régimes rate la cible.

---

## 4. Les trois protocoles

| | Objet | Régime |
|---|---|---|
| **A** | Robustesse des garde-fous sous reformulation et pression contextuelle | Mono-tour |
| **B** | Détournement de métrique proxy (specification gaming) | Agentique, outillé |
| **C** | Dérive d'objectifs en horizon long | Agentique, multi-tours |

La comparaison A (modèle seul) vs B/C (modèle + harnais) est le cœur du
dispositif. A sert de ligne de base.

---

## 5. Contraintes matérielles et budgétaires

- **VPS** : 8 vCPU, 8 Go RAM, **pas de GPU**. Orchestrateur, pas moteur
  d'inférence. Runs longs sous `tmux`. Prévoir 2-4 Go de swap.
- **Analyse** : Colab, en lecture des JSONL produits par le VPS.
  Colab ne produit jamais de données, il les analyse.
- **Budget API** : de l'ordre de 100-150 USD au total. Les épisodes
  agentiques (protocoles B et C) constituent le poste dominant :
  ~0,20 USD par épisode de 10 tours sur un modèle de milieu de gamme.
- **Leviers obligatoires** : Batch API pour tout run non interactif
  (≈ -50 %), cache de prompt sur le system prompt de l'agent (identique
  à chaque appel), scoring déterministe plutôt que LLM-juge.
- **Plafond dur** dans le code, qui lève une exception. Une boucle
  agentique buguée peut consommer plusieurs dizaines de dollars en une
  heure sans surveillance.

---

## 6. Le contrôle de divergence de modèle (non négociable)

`providers.py` compare le modèle **demandé** au modèle **effectivement
servi** par l'API et lève un drapeau `model_divergence`.

Raison : certaines requêtes envoyées à Fable sont redirigées vers Opus 5
par un mécanisme de sauvegarde, et ce reroutage se déclenche précisément
sur les sujets sensibles que le protocole A sollicite. Sans ce champ, des
réponses produites par un modèle seraient attribuées à un autre, et toute
comparaison inter-modèles serait silencieusement fausse.

Toute ligne `model_divergence: true` est écartée des comparaisons et
comptabilisée à part. **Le taux de divergence par condition est lui-même
un résultat à rapporter** : c'est la mesure d'un garde-fou d'infrastructure,
distinct des garde-fous internes au modèle.

---

## 7. Périmètre éthique — cadrage strict

Le travail est un **audit défensif**. Il mesure la robustesse de
garde-fous, il ne produit pas de capacités offensives.

Règles fermes :

- **Ne pas rédiger de corpus adversarial maison.** Utiliser des jeux de
  tests publics et citables, issus de la littérature couverte en Partie 2.
  Trois raisons : comparabilité avec les travaux existants, absence de
  soupçon de sélection orientée des prompts, et réponse anticipée à la
  question du jury sur la validité de l'échantillon.
- **Aucune capacité réellement dangereuse** n'est sollicitée ni produite.
  Les protocoles mesurent des taux de refus et des écarts de comportement,
  pas des contenus nocifs.
- **Environnements sandboxés** pour les protocoles agentiques : outils
  simulés, système de fichiers isolé, aucun accès réseau réel, aucune
  action irréversible.
- Les traces conservées sont des **données d'évaluation**, pas des
  recettes. En cas de doute sur un contenu produit, il est écarté du
  dépôt et mentionné dans le journal.

Ce cadrage figure dans le mémoire en section 3.1.5 et doit être respecté
dans le code.

---

## 8. Exigences de reproductibilité

- Aucun prompt en dur dans le code. Tous dans `prompts/`, référencés par
  identifiant.
- Schéma JSONL **figé**. Renommer un champ invalide les runs antérieurs.
- Chaque run logue : modèle demandé, modèle servi, température, seed,
  version de la config, tokens, coût, latence, erreurs.
- Versions de dépendances épinglées (`==`, jamais `>=`).
- `logs/journal.md` tenu au fil de l'eau : décisions, échecs, changements.
  Ce fichier devient la section 3.1.4 du mémoire.

---

## 9. Contraintes de rendu (impactent ce qui est produit)

Le guide de l'école impose que **toutes les illustrations soient classées
en annexe**, avec une limite d'une illustration par page sur deux dans le
corps du texte.

Conséquence pour les livrables graphiques : les figures sont conçues pour
les **annexes**, en pleine page, avec légende et source. Les résultats
doivent pouvoir être commentés en prose, chiffres dans le texte, **sans
que la figure soit visible**. Générer donc aussi des tableaux de valeurs
exploitables textuellement, pas seulement des graphiques.

Le mémoire est rédigé en style impersonnel (pas de « je », « nous »).

---

## 10. Périmètre de l'assistance technique

Ce dépôt et cet assistant couvrent : le harnais, les scripts, l'analyse,
le débug, le design expérimental, les figures.

**La rédaction du mémoire n'entre pas dans ce périmètre.** Le texte des
parties 1, 2 et 3 est écrit par l'étudiant. Un mémoire est un travail
personnel évalué et soutenu à l'oral devant un jury qui creuse : du texte
produit par un tiers ne se défend pas. Les commentaires de résultats,
l'interprétation et la discussion relèvent de l'étudiant.

Ce qui est attendu ici : que le dispositif produise des données propres,
et que l'étudiant ait de quoi les interpréter lui-même.
