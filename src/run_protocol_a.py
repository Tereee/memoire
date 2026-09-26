"""
Protocole A - XSTest, ligne de base mono-tour, Batch API.

Un lot Batch par modele, soumis dans l'ordre fable, opus, sonnet, haiku.
Aucun system prompt (ligne de base sans consigne, journal 2026-09-16).
Aucun `fallbacks` : un blocage revient en stop_reason "refusal" (BRIEFING 6).

    python -m src.run_protocol_a --dry-run              # construit, chiffre, ne soumet rien
    python -m src.run_protocol_a --submit               # verifie le corpus, chiffre, soumet
    python -m src.run_protocol_a --collect RUN_ID       # ecrit les resultats des lots termines
    python -m src.run_protocol_a --collect RUN_ID --wait

Etat de la soumission : logs/batches/<run_id>.json, versionne et commite apres chaque
lot soumis ou collecte. Format 2 (depuis le 2026-09-26) : batch_id, etat, compteurs,
couts, horodatages et parametres du run, SANS les requetes, reconstruites a la collecte
depuis le corpus hashe. Le format 1 (requetes embarquees) reste lu tel quel.
Les lignes JSONL vont dans data/raw/<run_id>.jsonl, schema 1.0, via
Runner._build_record (meme code que les appels synchrones).

Conventions propres au Batch, consignees au journal :
  - aucune temperature transmise, pour les quatre modeles (decoding_policy api_default) ;
  - cost_usd au tarif Batch (x0.5) : c'est le prix reellement paye ;
  - timestamp_utc = ended_at du lot ; latency_s = duree du lot (created_at -> ended_at),
    la latence par requete n'etant pas observable ;
  - attempts = 1 ; les erreurs Batch (errored / expired / canceled) sont ecrites
    comme lignes d'erreur, error = type + message ;
  - prompt_id = xstest_<id>_r<k>, k = repetition ; custom_id = <alias>-<prompt_id>.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from src.providers import CONFIG, ROOT, Runner

CORPUS = ROOT / "prompts" / "xstest" / "xstest_prompts.csv"
CORPUS_SHA256 = "11783fb294ed017473ee53c207d71f2161c7672c8d0b037501e78387f801cb5a"

MODELS = ["fable", "opus", "sonnet", "haiku"]   # ordre de soumission
REPS = 3
MAX_TOKENS = 1024
CONDITION = "xstest_baseline"
SYSTEM_PROMPT = ""                              # ligne de base : aucune consigne
BATCH_PRICE_FACTOR = 0.5
DEVIS_CAP_USD = 80.0                            # exception au-dela (pire cas, tarif Batch)
POLL_SECONDS = 120

# Hypotheses du devis (entree estimee, pas comptee : le dry-run ne touche pas le reseau).
TOKENS_PER_WORD = 1.4
REQUEST_OVERHEAD_TOKENS = 20
TOKENIZER_FACTOR = {"opus": 1.15, "fable": 1.15}
PILOT_MEAN_OUTPUT = {"haiku": 173, "sonnet": 369, "opus": 369, "fable": 382}  # run_20260916_193348


class CorpusMismatch(RuntimeError):
    pass


class DevisExceeded(RuntimeError):
    pass


# ------------------------------------------------------------------ corpus
def verify_corpus() -> list[dict]:
    digest = hashlib.sha256(CORPUS.read_bytes()).hexdigest()
    if digest != CORPUS_SHA256:
        raise CorpusMismatch(f"SHA-256 du corpus inattendu : {digest} != {CORPUS_SHA256}")
    with CORPUS.open(encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    if len(rows) != 450:
        raise CorpusMismatch(f"{len(rows)} lignes lues, 450 attendues")
    print(f"corpus OK : {CORPUS.name}  sha256={digest[:16]}...  {len(rows)} prompts")
    return rows


# ---------------------------------------------------------------- requests
def build_requests(alias: str, rows: list[dict], max_tokens: int = MAX_TOKENS, reps: int = REPS) -> list[dict]:
    """Requetes Batch d'un modele. Chaque entree porte les params API et un `meta`
    suffisant pour construire la ligne JSONL. Deterministe : a corpus (hashe) et
    parametres egaux, memes requetes -- c'est ce qui permet au manifeste format 2
    de ne pas les embarquer."""
    spec = Runner._spec(alias)
    temperature = float(CONFIG["run"]["temperature"])   # intention de config, jamais transmise ici
    # Protocole A : decodage par defaut de l'API pour les quatre modeles, haiku compris,
    # sinon la variance inter-modeles n'est pas comparable (journal 2026-09-16).
    temperature_sent = None
    decoding_policy = "api_default"

    out = []
    for row in rows:
        for k in range(1, reps + 1):
            prompt_id = f"xstest_{int(row['id']):03d}_r{k}"
            params = {
                "model": spec["id"],
                "max_tokens": max_tokens,
                "messages": [{"role": "user", "content": row["prompt"]}],
            }
            out.append({
                "custom_id": f"{alias}-{prompt_id}",
                "params": params,
                "meta": {
                    "alias": alias, "prompt_id": prompt_id, "user_prompt": row["prompt"],
                    "xstest_id": int(row["id"]), "xstest_type": row["type"], "xstest_label": row["label"],
                    "temperature_requested": temperature, "temperature_sent": temperature_sent,
                    "decoding_policy": decoding_policy, "thinking_config": None,
                    "max_tokens": max_tokens,
                },
            })
    return out


# ------------------------------------------------------------------- devis
def devis(requests_by_model: dict[str, list[dict]]) -> dict:
    total_worst = total_expected = 0.0
    lines = {}
    for alias, reqs in requests_by_model.items():
        spec = Runner._spec(alias)
        words = sum(len(r["params"]["messages"][0]["content"].split()) for r in reqs) / len(reqs)
        tin = (TOKENS_PER_WORD * words + REQUEST_OVERHEAD_TOKENS) * TOKENIZER_FACTOR.get(alias, 1.0)
        n = len(reqs)
        worst = n * Runner._cost(spec, tin, MAX_TOKENS) * BATCH_PRICE_FACTOR
        expected = n * Runner._cost(spec, tin, PILOT_MEAN_OUTPUT[alias]) * BATCH_PRICE_FACTOR
        lines[alias] = {"requests": n, "in_per_call": round(tin), "expected_usd": round(expected, 2),
                        "worst_usd": round(worst, 2)}
        total_worst += worst
        total_expected += expected
    return {"models": lines, "total_expected_usd": round(total_expected, 2),
            "total_worst_usd": round(total_worst, 2), "cap_usd": DEVIS_CAP_USD}


def print_devis(d: dict) -> None:
    print(f"\ndevis (tarif Batch x{BATCH_PRICE_FACTOR}, config/models.yaml)")
    print(f"  {'modele':7}{'requetes':>9}{'in/appel':>10}{'attendu':>10}{'pire cas':>10}")
    for alias, l in d["models"].items():
        print(f"  {alias:7}{l['requests']:>9}{l['in_per_call']:>10}{l['expected_usd']:>9.2f}${l['worst_usd']:>9.2f}$")
    n_total = sum(l["requests"] for l in d["models"].values())
    print(f"  {'TOTAL':7}{n_total:>9}{'':>10}{d['total_expected_usd']:>9.2f}${d['total_worst_usd']:>9.2f}$")
    print(f"  attendu = sortie moyenne du pilote ; pire cas = sortie a max_tokens={MAX_TOKENS} partout")
    print(f"  plafond : {d['cap_usd']:.2f}$ sur le pire cas")


def check_cap(d: dict) -> None:
    if d["total_worst_usd"] > d["cap_usd"]:
        raise DevisExceeded(
            f"pire cas {d['total_worst_usd']:.2f}$ > plafond {d['cap_usd']:.2f}$ : soumission refusee"
        )


# ----------------------------------------------------------------- manifest
def manifest_path(run_id: str) -> Path:
    # Versionne (logs/ n'est pas ignore) : seul pointeur vers des lots payes.
    return ROOT / "logs" / "batches" / f"{run_id}.json"


def save_manifest(m: dict) -> None:
    p = manifest_path(m["run_id"])
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(m, ensure_ascii=False, indent=1), encoding="utf-8")


def git_commit_manifest(run_id: str, message: str) -> None:
    """Commite le manifeste. Non bloquant : un echec git ne doit pas interrompre un run paye."""
    p = manifest_path(run_id)
    rel = p.relative_to(ROOT).as_posix()
    full = f"{message}\n\nCo-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>\n"
    try:
        subprocess.run(["git", "add", rel], cwd=ROOT, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-q", "-m", full], cwd=ROOT, check=True, capture_output=True)
        print(f"    commit : {rel}")
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        detail = getattr(e, "stderr", b"") or b""
        print(f"    [!] commit du manifeste echoue : {e} {detail.decode(errors='replace').strip()}")


def load_manifest(run_id: str) -> dict:
    p = manifest_path(run_id)
    if not p.exists():
        raise FileNotFoundError(f"manifeste introuvable : {p}")
    return json.loads(p.read_text(encoding="utf-8"))


# ------------------------------------------------------------------- submit
def submit(runner: Runner, requests_by_model: dict[str, list[dict]], d: dict) -> dict:
    # Format 2 : les requetes ne sont pas embarquees. Elles sont reconstruites a la
    # collecte depuis le corpus (SHA-256 verifie) et les `params` ci-dessous.
    manifest = {
        "manifest_format": 2,
        "run_id": runner.run_id, "condition": CONDITION, "corpus_sha256": CORPUS_SHA256,
        "created_utc": datetime.now(timezone.utc).isoformat(), "devis": d,
        "system_prompt": SYSTEM_PROMPT,
        "params": {"models": MODELS, "reps": REPS, "max_tokens": MAX_TOKENS,
                   "temperature_sent": None, "fallbacks": None},
        "batches": [],
    }
    save_manifest(manifest)   # ecrit avant la premiere soumission : rien ne se perd
    for alias in MODELS:
        reqs = requests_by_model[alias]
        batch = runner.client.messages.batches.create(
            requests=[{"custom_id": r["custom_id"], "params": r["params"]} for r in reqs]
        )
        manifest["batches"].append({
            "alias": alias, "batch_id": batch.id,
            "submitted_utc": datetime.now(timezone.utc).isoformat(),
            "processing_status": batch.processing_status, "collected": False,
            "n_requests": len(reqs),
        })
        save_manifest(manifest)
        print(f"  soumis {alias:7} batch_id={batch.id}  requetes={len(reqs)}  statut={batch.processing_status}")
        git_commit_manifest(runner.run_id, f"protocole A {runner.run_id} : lot {alias} soumis ({batch.id})")
    print(f"\nmanifeste : {manifest_path(runner.run_id)}")
    print(f"collecte  : python -m src.run_protocol_a --collect {runner.run_id} --wait")
    return manifest


# ------------------------------------------------------------------ collect
def _error_string(result) -> str:
    err = getattr(result, "error", None)
    if err is None:
        return result.type
    inner = getattr(err, "error", None)
    if inner is not None:
        return f"{result.type}: {getattr(inner, 'type', '')}: {getattr(inner, 'message', '')}"
    return f"{result.type}: {err}"


def request_metas(manifest: dict, b: dict, _cache: dict = {}) -> dict[str, dict]:
    """custom_id -> meta d'un lot.

    Format 1 (historique, ex. protoA_20260916_200157) : requetes embarquees, lues telles quelles.
    Format 2 : reconstruites depuis le corpus (SHA-256 verifie) et manifest["params"] ;
    le nombre de requetes reconstruites doit egaler b["n_requests"].
    """
    if "requests" in b:
        return b["requests"]
    if "rows" not in _cache:
        _cache["rows"] = verify_corpus()
    p = manifest["params"]
    reqs = build_requests(b["alias"], _cache["rows"], max_tokens=p["max_tokens"], reps=p["reps"])
    if len(reqs) != b["n_requests"]:
        raise CorpusMismatch(f"{b['alias']} : {len(reqs)} requetes reconstruites, {b['n_requests']} soumises")
    return {r["custom_id"]: r["meta"] for r in reqs}


def collect(runner: Runner, manifest: dict, wait: bool) -> None:
    client = runner.client
    for b in manifest["batches"]:
        if b["collected"]:
            print(f"  {b['alias']:7} deja collecte")
            continue
        batch = client.messages.batches.retrieve(b["batch_id"])
        while batch.processing_status != "ended":
            rc = batch.request_counts
            print(f"  {b['alias']:7} {batch.processing_status}  "
                  f"en cours={rc.processing} ok={rc.succeeded} err={rc.errored}")
            if not wait:
                break
            time.sleep(POLL_SECONDS)
            batch = client.messages.batches.retrieve(b["batch_id"])
        if batch.processing_status != "ended":
            continue

        spec = Runner._spec(b["alias"])
        stamp = batch.ended_at.isoformat() if batch.ended_at else datetime.now(timezone.utc).isoformat()
        duration = (batch.ended_at - batch.created_at).total_seconds() if batch.ended_at else 0.0

        metas = request_metas(manifest, b)
        results = sorted(client.messages.batches.results(b["batch_id"]), key=lambda r: r.custom_id)
        n_ok = n_err = n_div = 0
        spent_before = runner.spent_usd
        for r in results:
            meta = metas[r.custom_id]
            ok = r.result.type == "succeeded"
            rec = runner._build_record(
                alias=meta["alias"], spec=spec, prompt_id=meta["prompt_id"], condition=CONDITION,
                user_prompt=meta["user_prompt"], system_prompt=SYSTEM_PROMPT,
                temperature=meta["temperature_requested"], temperature_sent=meta["temperature_sent"],
                decoding_policy=meta["decoding_policy"], thinking_config=meta["thinking_config"],
                max_tokens=meta["max_tokens"],
                resp=r.result.message if ok else None,
                latency=duration, attempts=1, error=None if ok else _error_string(r.result),
                price_factor=BATCH_PRICE_FACTOR,
            )
            rec.timestamp_utc = stamp
            runner._write(rec)
            n_ok += ok
            n_err += not ok
            n_div += rec.model_divergence
        b["collected"] = True
        b["processing_status"] = batch.processing_status
        b["ended_utc"] = stamp
        b["collected_utc"] = datetime.now(timezone.utc).isoformat()
        b["request_counts"] = batch.request_counts.model_dump()
        b["cost_usd"] = round(runner.spent_usd - spent_before, 4)
        save_manifest(manifest)
        print(f"  {b['alias']:7} collecte : ok={n_ok} err={n_err} divergence={n_div}  cout={b['cost_usd']:.4f}$")
        git_commit_manifest(manifest["run_id"], f"protocole A {manifest['run_id']} : lot {b['alias']} collecte")
    print(f"\nJSONL : {runner.path}")
    print(f"depense cumulee cette session : {runner.spent_usd:.4f}$")


# --------------------------------------------------------------------- main
def main(argv: list[str]) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true", help="construit les requetes, affiche le devis, ne soumet rien")
    g.add_argument("--submit", action="store_true", help="soumet un lot Batch par modele")
    g.add_argument("--collect", metavar="RUN_ID", help="ecrit les resultats des lots termines")
    ap.add_argument("--wait", action="store_true", help="avec --collect : attendre la fin des lots")
    args = ap.parse_args(argv)

    if args.collect:
        manifest = load_manifest(args.collect)
        if manifest["corpus_sha256"] != CORPUS_SHA256:
            raise CorpusMismatch("le manifeste ne correspond pas au corpus courant")
        runner = Runner(run_id=manifest["run_id"])
        collect(runner, manifest, wait=args.wait)
        return

    rows = verify_corpus()
    requests_by_model = {alias: build_requests(alias, rows) for alias in MODELS}
    d = devis(requests_by_model)

    print(f"\ncondition={CONDITION}  reps={REPS}  max_tokens={MAX_TOKENS}  system_prompt={SYSTEM_PROMPT!r}")
    for alias in MODELS:
        first = requests_by_model[alias][0]
        shown = {"custom_id": first["custom_id"], "params": first["params"]}
        print(f"  {alias:7} {len(requests_by_model[alias])} requetes, premiere : {json.dumps(shown, ensure_ascii=False)}")
    print_devis(d)
    check_cap(d)

    if args.dry_run:
        print("\n--dry-run : rien n'a ete soumis.")
        return

    runner = Runner(run_id=datetime.now(timezone.utc).strftime("protoA_%Y%m%d_%H%M%S"))
    print(f"\nrun_id = {runner.run_id}\nsoumission dans l'ordre {MODELS} :")
    submit(runner, requests_by_model, d)


if __name__ == "__main__":
    main(sys.argv[1:])
