"""
ANALYSE DE SENSIBILITE : rejeu synchrone des lignes tronquees (stop_reason == "max_tokens")
a max_tokens plus large.

Statut (decision du 2026-09-16) : ce rejeu n'est PAS un correctif du corpus principal.
Les lignes tronquees y restent scorees `ambiguous`. Le rejeu vit dans son fichier separe
et n'est JAMAIS fusionne avec le run source. Il repond a une seule question : avec un
budget de sortie plus large, le modele termine-t-il, et que produit-il ?

    python -m src.rerun_truncated                                  # dry-run (defaut) : selection + devis, aucun appel
    python -m src.rerun_truncated --models fable                   # restreindre a un ou plusieurs modeles
    python -m src.rerun_truncated --sample 40 --seed 20260916      # sous-echantillon stratifie par modele, seed loguee
    python -m src.rerun_truncated --run                            # executer (appels API, plafond dur)

Source par defaut : data/raw/protoA_20260916_200157.jsonl (lu tel quel, le fichier
peut etre encore en ecriture : les lignes partielles sont ignorees, aucun hash calcule).
Sortie : data/raw/<run_id_source>_rerun<max_tokens>.jsonl, schema JSONL 1.0 via
Runner.call() (appels synchrones, plein tarif), condition "xstest_baseline_rerun4096",
meme modele, meme prompt_id, aucun system prompt, aucune temperature (api_default).

Ce rejeu est un NOUVEL echantillonnage, pas une continuation : au decodage par defaut,
la reponse rejouee n'est pas la reponse tronquee prolongee. Il mesure si, avec 4096
tokens, le modele termine sa reponse, et ce qu'elle contient.

Garde-fous :
  - devis affiche avant tout appel (attendu = sortie moyenne supposee EXPECTED_OUTPUT_TOKENS,
    pire cas = sortie a max_tokens partout) ; refus si l'attendu depasse CAP_USD ;
  - plafond dur CAP_USD sur la depense reelle (Runner.cap). CAP_USD couvre le pire cas du
    rejeu fable complet : un rejeu coupe en cours produirait un echantillon biaise par
    l'ordre des lignes. Pour reduire le cout, utiliser --sample, pas un plafond plus bas ;
  - --sample N : tirage sans remise, stratifie par modele (allocation proportionnelle,
    plus forts restes), seed loguee sur la sortie et dans <sortie>.sample.json ;
  - refus d'ecrire dans un fichier de sortie existant.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from src.providers import BudgetExceeded, ROOT, Runner

DEFAULT_SOURCE = ROOT / "data" / "raw" / "protoA_20260916_200157.jsonl"
MAX_TOKENS = 4096
CONDITION = "xstest_baseline_rerun4096"
CAP_USD = 22.0
DEFAULT_SEED = 20260916
EXPECTED_OUTPUT_TOKENS = 2048   # hypothese : les reponses tronquees a 1024 finissent en moyenne vers 2x
SYSTEM_PROMPT = ""


def load_truncated(source: Path, models: list[str] | None) -> list[dict]:
    out = []
    with source.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue   # ligne partielle : fichier en cours d'ecriture
            if r.get("stop_reason") != "max_tokens" or r.get("error"):
                continue
            if models and r["model_alias"] not in models:
                continue
            out.append(r)
    return sorted(out, key=lambda r: (r["model_alias"], r["prompt_id"]))


def stratified_sample(lines: list[dict], n: int, seed: int) -> tuple[list[dict], dict]:
    """N lignes sans remise, allocation proportionnelle par modele (plus forts restes)."""
    counts = Counter(r["model_alias"] for r in lines)
    total = len(lines)
    if n >= total:
        return lines, {"seed": seed, "requested": n, "drawn": total, "by_model": dict(counts), "note": "N >= total, tout pris"}
    exact = {m: n * c / total for m, c in counts.items()}
    alloc = {m: int(v) for m, v in exact.items()}
    for m, _ in sorted(exact.items(), key=lambda kv: kv[1] - int(kv[1]), reverse=True)[: n - sum(alloc.values())]:
        alloc[m] += 1
    rng = random.Random(seed)
    picked = []
    for m in sorted(counts):
        pool = [r for r in lines if r["model_alias"] == m]          # deja trie (modele, prompt_id)
        picked.extend(rng.sample(pool, alloc[m]))
    picked.sort(key=lambda r: (r["model_alias"], r["prompt_id"]))
    return picked, {"seed": seed, "requested": n, "drawn": len(picked), "by_model": alloc,
                    "population_by_model": dict(counts)}


def devis(lines: list[dict]) -> dict:
    per, exp_total, worst_total = {}, 0.0, 0.0
    for alias, n in sorted(Counter(r["model_alias"] for r in lines).items()):
        spec = Runner._spec(alias)
        tin = sum(r["input_tokens"] for r in lines if r["model_alias"] == alias) / n   # mesure sur le run source
        exp = n * Runner._cost(spec, tin, EXPECTED_OUTPUT_TOKENS)
        worst = n * Runner._cost(spec, tin, MAX_TOKENS)
        per[alias] = {"lines": n, "in_per_call": round(tin), "expected_usd": round(exp, 2), "worst_usd": round(worst, 2)}
        exp_total += exp
        worst_total += worst
    return {"models": per, "total_expected_usd": round(exp_total, 2), "total_worst_usd": round(worst_total, 2),
            "cap_usd": CAP_USD}


def print_devis(d: dict) -> None:
    print(f"\ndevis (appels synchrones, plein tarif, config/models.yaml)")
    print(f"  {'modele':7}{'lignes':>7}{'in/appel':>10}{'attendu':>10}{'pire cas':>10}")
    for alias, l in d["models"].items():
        print(f"  {alias:7}{l['lines']:>7}{l['in_per_call']:>10}{l['expected_usd']:>9.2f}${l['worst_usd']:>9.2f}$")
    print(f"  {'TOTAL':7}{sum(l['lines'] for l in d['models'].values()):>7}{'':>10}"
          f"{d['total_expected_usd']:>9.2f}${d['total_worst_usd']:>9.2f}$")
    print(f"  attendu = sortie moyenne supposee {EXPECTED_OUTPUT_TOKENS} tokens ; pire cas = {MAX_TOKENS} tokens partout")
    print(f"  plafond : {d['cap_usd']:.2f}$ (refus si l'attendu le depasse ; plafond dur sur la depense reelle)")


def main(argv: list[str]) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    ap.add_argument("--models", nargs="+", help="alias a rejouer (defaut : tous)")
    ap.add_argument("--sample", type=int, metavar="N", help="sous-echantillon de N lignes, stratifie par modele")
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED, help=f"seed du tirage (defaut {DEFAULT_SEED})")
    ap.add_argument("--run", action="store_true", help="executer les appels (defaut : dry-run)")
    args = ap.parse_args(argv)

    lines = load_truncated(args.source, args.models)
    if not lines:
        sys.exit("aucune ligne tronquee selectionnee")
    source_run = lines[0]["run_id"]
    run_id = f"{source_run}_rerun{MAX_TOKENS}"
    out = ROOT / "data" / "raw" / f"{run_id}.jsonl"

    population = len(lines)
    sample_info = None
    if args.sample is not None:
        lines, sample_info = stratified_sample(lines, args.sample, args.seed)

    distinct = len({(r["model_alias"], r["prompt_id"].rsplit("_r", 1)[0]) for r in lines})
    print(f"source     : {args.source.name}  (run {source_run})")
    print(f"population : {population} lignes tronquees")
    if sample_info:
        print(f"tirage     : {sample_info['drawn']} lignes, seed={sample_info['seed']}, par modele {sample_info['by_model']}")
    print(f"selection  : {len(lines)} lignes, {distinct} prompts distincts, "
          f"par modele {dict(Counter(r['model_alias'] for r in lines))}")
    print(f"parametres : max_tokens={MAX_TOKENS}  condition={CONDITION!r}  system_prompt=''  temperature=api_default")
    print(f"sortie     : {out}")
    d = devis(lines)
    print_devis(d)

    if not args.run:
        print("\n--dry-run (defaut) : aucun appel effectue.")
        return
    if d["total_expected_usd"] > CAP_USD:
        sys.exit(f"devis attendu {d['total_expected_usd']:.2f}$ > plafond {CAP_USD:.2f}$ : rejeu refuse")
    if out.exists():
        sys.exit(f"{out} existe deja : rejeu refuse (supprimer ou renommer d'abord)")

    runner = Runner(run_id=run_id)
    runner.cap = CAP_USD   # plafond dur de ce rejeu, distinct de config/models.yaml
    side = {"run_id": run_id, "source": str(args.source), "condition": CONDITION, "max_tokens": MAX_TOKENS,
            "started_utc": datetime.now(timezone.utc).isoformat(), "population": population,
            "sample": sample_info, "selected": [f"{r['model_alias']}-{r['prompt_id']}" for r in lines]}
    out.with_suffix(".sample.json").write_text(json.dumps(side, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nrun_id = {run_id}  plafond dur = {CAP_USD:.2f}$  selection loguee : {out.with_suffix('.sample.json').name}\n")
    n_done = n_still = 0
    for r in lines:
        try:
            rec = runner.call(
                alias=r["model_alias"], user_prompt=r["user_prompt"], system_prompt=SYSTEM_PROMPT,
                prompt_id=r["prompt_id"], condition=CONDITION, max_tokens=MAX_TOKENS,
            )
        except BudgetExceeded as e:
            print(f"ARRET BUDGET : {e}  ({n_done} lignes rejouees)")
            break
        n_done += 1
        n_still += rec.stop_reason == "max_tokens"
        status = "ERR" if rec.error else rec.stop_reason
        print(f"  {r['model_alias']:7} {r['prompt_id']:14} {status:10} {rec.output_tokens:>5}out  ${rec.cost_usd:.4f}  cumul ${runner.spent_usd:.2f}")
    print(f"\n{n_done} lignes rejouees, {n_still} encore tronquees a {MAX_TOKENS}, depense {runner.spent_usd:.4f}$")
    print(f"JSONL : {runner.path}")


if __name__ == "__main__":
    main(sys.argv[1:])
