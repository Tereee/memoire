"""
Tirage stratifie pour la calibration des motifs de refus (protocole A).

    python -m src.calibration_sample data/raw/<run_id>.jsonl
    python -m src.calibration_sample data/raw/<run_id>.jsonl --seed 20260926 --out data/processed/calibration_sample.jsonl
    python -m src.calibration_sample data/raw/<run_id>.jsonl --complement data/raw/<run_id>_compl.jsonl

Avec --complement, le tirage porte sur le corpus fusionne en memoire par la regle de
substitution (src.metrics.merge_with_complement) ; le meta consigne le hash de chaque
fichier et le nombre de substitutions.

Sortie : 100 lignes, 25 par modele, 50 safe / 50 unsafe au total (12 ou 13
par modele et par label, en alternance), tirees sans remise, seed logue,
parmi les lignes sans erreur API. Chaque ligne est le CallRecord complet,
SANS score, enrichi de xstest_id / xstest_type / xstest_label (joints sur
prompt_id) et de deux champs a remplir a la main :

    human_outcome : classifier_block | model_refusal | text_refusal | compliance | ambiguous
    human_note    : texte libre

Un fichier logs/calibration/<nom de sortie>.meta.json (versionne) consigne seed,
run_id, SHA-256 du JSONL source, effectifs et liste des call_id tires : le tirage
est verifiable depuis git meme si l'echantillon est perdu. Le fichier de sortie
n'est jamais ecrase sans --force : il peut contenir une annotation en cours.

Seed par defaut 20260926, distincte de celle de src/rerun_truncated.py (20260916) :
deux tirages independants ne doivent pas partager leur generateur.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from src.metrics import OUTCOMES, load_jsonl, merge_with_complement
from src.providers import ROOT

CORPUS = ROOT / "prompts" / "xstest" / "xstest_prompts.csv"
DEFAULT_OUT = ROOT / "data" / "processed" / "calibration_sample.jsonl"
META_DIR = ROOT / "logs" / "calibration"   # versionne, contrairement a data/raw/
DEFAULT_SEED = 20260926                     # distincte de la seed du rejeu (20260916)
PER_MODEL = 25


def load_corpus_labels() -> dict[int, dict]:
    with CORPUS.open(encoding="utf-8", newline="") as fh:
        return {int(r["id"]): {"xstest_id": int(r["id"]), "xstest_type": r["type"], "xstest_label": r["label"]}
                for r in csv.DictReader(fh)}


def xstest_id_from_prompt_id(prompt_id: str) -> int:
    # prompt_id = xstest_<id>_r<k>
    parts = prompt_id.split("_")
    if len(parts) != 3 or parts[0] != "xstest":
        raise ValueError(f"prompt_id inattendu : {prompt_id!r}")
    return int(parts[1])


def quotas(models: list[str]) -> dict[str, dict[str, int]]:
    """25 par modele ; safe = 13 ou 12 en alternance pour que le total soit 50/50."""
    q = {}
    for i, m in enumerate(models):
        n_safe = 13 if i % 2 == 0 else 12
        q[m] = {"safe": n_safe, "unsafe": PER_MODEL - n_safe}
    return q


def draw(records: list[dict], seed: int) -> tuple[list[dict], dict]:
    labels = load_corpus_labels()
    usable = [r for r in records if not r.get("error")]
    for r in usable:
        r.update(labels[xstest_id_from_prompt_id(r["prompt_id"])])

    models = sorted({r["model_alias"] for r in usable})
    q = quotas(models)
    rng = random.Random(seed)
    sample: list[dict] = []
    for m in models:
        for label, n in q[m].items():
            pool = sorted((r for r in usable if r["model_alias"] == m and r["xstest_label"] == label),
                          key=lambda r: r["call_id"])
            if len(pool) < n:
                raise RuntimeError(f"{m}/{label} : {len(pool)} lignes disponibles, {n} demandees")
            sample.extend(rng.sample(pool, n))

    for r in sample:
        r["human_outcome"] = None
        r["human_note"] = None

    meta = {
        "seed": seed, "drawn_utc": datetime.now(timezone.utc).isoformat(),
        "run_ids": sorted({r["run_id"] for r in sample}),
        "source_lines": len(records), "usable_lines": len(usable),
        "sample_size": len(sample), "quotas": q,
        "counts_by_model": dict(Counter(r["model_alias"] for r in sample)),
        "counts_by_label": dict(Counter(r["xstest_label"] for r in sample)),
        "allowed_human_outcomes": list(OUTCOMES),
        "call_ids": [r["call_id"] for r in sample],
    }
    return sample, meta


def main(argv: list[str]) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("jsonl", type=Path, help="JSONL source (data/raw/<run_id>.jsonl)")
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--force", action="store_true", help="ecraser un echantillon existant")
    ap.add_argument("--complement", type=Path, action="append", default=[],
                    help="JSONL de run complementaire, fusionne en memoire (src.metrics.merge_with_complement)")
    args = ap.parse_args(argv)

    if args.out.exists() and not args.force:
        sys.exit(f"{args.out} existe deja (annotation en cours ?). Utiliser --force pour ecraser.")

    raw = args.jsonl.read_bytes()
    records = [json.loads(l) for l in raw.decode("utf-8").splitlines() if l.strip()]
    complements, n_sub = [], 0
    if args.complement:
        comp_rows = [load_jsonl(c) for c in args.complement]
        records, n_sub = merge_with_complement(records, comp_rows)
        complements = [{"file": str(c), "sha256": hashlib.sha256(c.read_bytes()).hexdigest(), "lines": len(rows)}
                       for c, rows in zip(args.complement, comp_rows)]
    sample, meta = draw(records, args.seed)
    meta["source_file"] = str(args.jsonl)
    meta["source_sha256"] = hashlib.sha256(raw).hexdigest()
    meta["complements"] = complements
    meta["substitutions"] = n_sub

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as fh:
        for r in sample:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    meta["sample_file"] = str(args.out)
    META_DIR.mkdir(parents=True, exist_ok=True)
    meta_path = META_DIR / f"{args.out.stem}.meta.json"
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"echantillon : {args.out}  ({len(sample)} lignes, seed={args.seed})")
    print(f"meta        : {meta_path}")
    print(f"par modele  : {meta['counts_by_model']}")
    print(f"par label   : {meta['counts_by_label']}")


if __name__ == "__main__":
    main(sys.argv[1:])
