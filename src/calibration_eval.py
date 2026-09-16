"""
Precision / rappel des regles de scoring contre l'annotation manuelle.

    python -m src.calibration_eval data/processed/calibration_sample.jsonl
    python -m src.calibration_eval data/processed/calibration_sample.jsonl --disagreements 50

Lit l'echantillon produit par src.calibration_sample, une fois human_outcome
rempli, rescore chaque ligne avec src.metrics (motifs courants), puis affiche :
  - precision, rappel, F1 et support par issue ;
  - la matrice de confusion (lignes = annotation, colonnes = regles) ;
  - l'exactitude globale et par modele ;
  - la liste des desaccords, avec la regle et le motif qui ont tranche.

Les lignes non annotees (human_outcome null ou invalide) sont comptees et
ignorees. Aucun appel reseau.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

from src.metrics import OUTCOMES, Patterns, score


def evaluate(rows: list[dict], patterns: Patterns) -> dict:
    annotated, skipped = [], Counter()
    for r in rows:
        h = r.get("human_outcome")
        if h not in OUTCOMES:
            skipped["non annote" if h in (None, "") else f"valeur invalide : {h!r}"] += 1
            continue
        s = score(r, patterns)
        annotated.append({**r, "rule_outcome": s.outcome, "rule": s.rule,
                          "matched_pattern": s.matched_pattern, "match_pos": s.match_pos})

    conf = defaultdict(Counter)   # conf[human][rule]
    for a in annotated:
        conf[a["human_outcome"]][a["rule_outcome"]] += 1

    per_outcome = {}
    for o in OUTCOMES:
        tp = conf[o][o]
        predicted = sum(conf[h][o] for h in OUTCOMES)
        actual = sum(conf[o].values())
        p = tp / predicted if predicted else None
        r_ = tp / actual if actual else None
        f1 = (2 * p * r_ / (p + r_)) if (p is not None and r_ is not None and (p + r_)) else None
        per_outcome[o] = {"precision": p, "recall": r_, "f1": f1, "support": actual, "predicted": predicted}

    n = len(annotated)
    correct = sum(1 for a in annotated if a["human_outcome"] == a["rule_outcome"])
    by_model = defaultdict(lambda: [0, 0])
    for a in annotated:
        by_model[a["model_alias"]][1] += 1
        by_model[a["model_alias"]][0] += a["human_outcome"] == a["rule_outcome"]

    return {
        "patterns_version": patterns.version, "n_annotated": n, "skipped": dict(skipped),
        "accuracy": correct / n if n else None,
        "accuracy_by_model": {m: c / t for m, (c, t) in sorted(by_model.items())},
        "per_outcome": per_outcome,
        "confusion": {h: dict(conf[h]) for h in OUTCOMES},
        "disagreements": [a for a in annotated if a["human_outcome"] != a["rule_outcome"]],
    }


def _fmt(x) -> str:
    return "   -  " if x is None else f"{x:6.3f}"


def report(res: dict, max_disagreements: int) -> None:
    print(f"motifs v{res['patterns_version']}  lignes annotees = {res['n_annotated']}  ignorees = {res['skipped'] or 0}")
    if not res["n_annotated"]:
        return
    print(f"exactitude globale = {res['accuracy']:.3f}  par modele = "
          + ", ".join(f"{m} {a:.3f}" for m, a in res["accuracy_by_model"].items()))

    print(f"\n  {'issue':17}{'precision':>10}{'rappel':>8}{'F1':>8}{'support':>9}{'predit':>8}")
    for o, v in res["per_outcome"].items():
        print(f"  {o:17}{_fmt(v['precision']):>10}{_fmt(v['recall']):>8}{_fmt(v['f1']):>8}{v['support']:>9}{v['predicted']:>8}")

    print("\n  matrice de confusion (lignes = annotation, colonnes = regles)")
    print(f"  {'':17}" + "".join(f"{o[:12]:>13}" for o in OUTCOMES))
    for h in OUTCOMES:
        print(f"  {h:17}" + "".join(f"{res['confusion'][h].get(o, 0):>13}" for o in OUTCOMES))

    dis = res["disagreements"]
    print(f"\n  desaccords : {len(dis)}" + (f" (affiches : {min(len(dis), max_disagreements)})" if dis else ""))
    for a in dis[:max_disagreements]:
        excerpt = (a.get("response_text") or "").replace("\n", " ")[:90]
        print(f"  - {a['prompt_id']:15} {a['model_alias']:7} humain={a['human_outcome']:16} regle={a['rule_outcome']:16} "
              f"[{a['rule']}{' / ' + a['matched_pattern'] if a['matched_pattern'] else ''}]  {excerpt!r}")


def main(argv: list[str]) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("sample", type=Path, help="echantillon annote (data/processed/calibration_sample.jsonl)")
    ap.add_argument("--disagreements", type=int, default=30, help="nombre de desaccords a afficher")
    ap.add_argument("--json", type=Path, help="ecrire le resultat complet en JSON")
    args = ap.parse_args(argv)

    rows = [json.loads(l) for l in args.sample.read_text(encoding="utf-8").splitlines() if l.strip()]
    res = evaluate(rows, Patterns())
    report(res, args.disagreements)
    if args.json:
        args.json.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\nresultat JSON : {args.json}")


if __name__ == "__main__":
    main(sys.argv[1:])
