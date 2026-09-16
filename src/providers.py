"""
Couche d'abstraction modele.

Point unique par lequel passent TOUS les appels du memoire.
Objectif : une trace JSONL exploitable, un cout mesure, aucune surprise.

Trois garanties :
  1. Chaque appel est ecrit sur disque avant d'etre retourne.
  2. L'identifiant de modele REELLEMENT servi est logue, pas seulement
     celui demande (cf. reroutage Fable -> Opus).
  3. Un plafond budgetaire leve une exception au lieu de vider la carte.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path

import yaml
from anthropic import Anthropic, APIError, APIStatusError, RateLimitError

ROOT = Path(__file__).resolve().parent.parent
CONFIG = yaml.safe_load((ROOT / "config" / "models.yaml").read_text())


class BudgetExceeded(RuntimeError):
    pass


# Version du schema JSONL. Toute evolution = increment + note dans logs/journal.md.
SCHEMA_VERSION = "1.0"


@dataclass
class CallRecord:
    """Une ligne de data/raw/*.jsonl. Schema fige : ne pas renommer les champs."""
    schema_version: str        # cf. SCHEMA_VERSION
    run_id: str
    call_id: str
    timestamp_utc: str
    prompt_id: str
    condition: str
    model_alias: str
    model_requested: str
    model_served: str          # ce que l'API dit avoir servi
    model_divergence: bool     # True = reroutage detecte
    temperature_requested: float    # valeur de config (intention), jamais garantie appliquee
    temperature_sent: float | None  # valeur transmise a l'API ; None = parametre omis
    decoding_policy: str            # 'explicit' = temperature_sent transmise ; 'api_default' = decodage par defaut de l'API
    thinking_config: str | None     # parametre 'thinking' transmis (JSON) ; None = omis, defaut de l'API
    thinking_present: bool          # True = la reponse contient au moins un bloc thinking / redacted_thinking
    max_tokens: int
    system_prompt_sha1: str
    user_prompt_sha1: str
    user_prompt: str
    response_text: str
    stop_reason: str | None
    stop_details_category: str | None  # stop_reason == 'refusal' : cyber, bio, ... ; sinon None
    input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_s: float
    attempts: int
    error: str | None


class Runner:
    def __init__(self, run_id: str | None = None, out_dir: Path | None = None):
        # max_retries=0 : la politique de retry est entierement dans call().
        self.client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"], max_retries=0)
        self.run_id = run_id or datetime.now(timezone.utc).strftime("run_%Y%m%d_%H%M%S")
        self.out_dir = out_dir or (ROOT / "data" / "raw")
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.out_dir / f"{self.run_id}.jsonl"
        self.spent_usd = 0.0
        self.cap = float(CONFIG["run"]["budget_cap_usd"])

    # ------------------------------------------------------------------ utils
    @staticmethod
    def _sha1(text: str) -> str:
        return hashlib.sha1((text or "").encode("utf-8")).hexdigest()[:12]

    @staticmethod
    def _spec(alias: str) -> dict:
        try:
            return CONFIG["providers"]["anthropic"]["models"][alias]
        except KeyError:
            raise KeyError(f"alias inconnu: {alias!r} (voir config/models.yaml)")

    # Sonnet 5, Opus 5, Fable, Mythos rejettent temperature/top_p/top_k (400).
    # Liste blanche : un modele inconnu n'envoie rien plutot que de casser.
    _ACCEPTS_TEMPERATURE = ("claude-haiku-4-5",)

    @classmethod
    def _temperature_to_send(cls, model_id: str, temperature: float) -> float | None:
        return temperature if model_id.startswith(cls._ACCEPTS_TEMPERATURE) else None

    @staticmethod
    def _cost(spec: dict, tin: int, tout: int) -> float:
        return (tin / 1e6) * spec["input_per_mtok"] + (tout / 1e6) * spec["output_per_mtok"]

    def _write(self, rec: CallRecord) -> None:
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(asdict(rec), ensure_ascii=False) + "\n")

    # ------------------------------------------------------------------- call
    def call(
        self,
        alias: str,
        user_prompt: str,
        system_prompt: str = "",
        prompt_id: str = "",
        condition: str = "baseline",
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> CallRecord:
        spec = self._spec(alias)
        temperature = CONFIG["run"]["temperature"] if temperature is None else temperature
        max_tokens = CONFIG["run"]["max_tokens"] if max_tokens is None else max_tokens

        if self.spent_usd >= self.cap:
            raise BudgetExceeded(f"plafond atteint: {self.spent_usd:.4f} / {self.cap} USD")

        temperature_sent = self._temperature_to_send(spec["id"], temperature)
        decoding_policy = "explicit" if temperature_sent is not None else "api_default"
        kwargs = dict(
            model=spec["id"],
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": user_prompt}],
        )
        if temperature_sent is not None:
            # SDK anthropic>=1.0 : temperature n'est plus un kwarg type ;
            # on le passe dans le corps HTTP via extra_body.
            kwargs["extra_body"] = {"temperature": temperature_sent}
        if system_prompt:
            kwargs["system"] = system_prompt
        # Config par defaut partout : 'thinking' n'est pas transmis, on logue None.
        thinking_config = (
            json.dumps(kwargs["thinking"], sort_keys=True) if "thinking" in kwargs else None
        )

        t0 = time.time()
        attempts, err, resp = 0, None, None
        while attempts < CONFIG["run"]["max_retries"]:
            attempts += 1
            try:
                resp = self.client.messages.create(**kwargs)
                err = None
                break
            except APIError as e:
                err = f"{type(e).__name__}: {e}"
                retryable = isinstance(e, RateLimitError) or (
                    isinstance(e, APIStatusError) and e.status_code >= 500
                )
                if not retryable:
                    break  # 400 & co : remonte immediatement, sans backoff
                time.sleep(min(2 ** attempts, 30))  # backoff exponentiel

        latency = time.time() - t0

        if resp is None:
            rec = CallRecord(
                schema_version=SCHEMA_VERSION,
                run_id=self.run_id, call_id=uuid.uuid4().hex[:12],
                timestamp_utc=datetime.now(timezone.utc).isoformat(),
                prompt_id=prompt_id, condition=condition, model_alias=alias,
                model_requested=spec["id"], model_served="", model_divergence=False,
                temperature_requested=temperature, temperature_sent=temperature_sent,
                decoding_policy=decoding_policy,
                thinking_config=thinking_config, thinking_present=False,
                max_tokens=max_tokens,
                system_prompt_sha1=self._sha1(system_prompt),
                user_prompt_sha1=self._sha1(user_prompt),
                user_prompt=user_prompt, response_text="", stop_reason=None,
                stop_details_category=None,
                input_tokens=0, output_tokens=0, cost_usd=0.0,
                latency_s=round(latency, 3), attempts=attempts, error=err,
            )
            self._write(rec)
            return rec

        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        thinking_present = any(
            getattr(b, "type", "") in ("thinking", "redacted_thinking") for b in resp.content
        )
        tin, tout = resp.usage.input_tokens, resp.usage.output_tokens
        cost = self._cost(spec, tin, tout)
        self.spent_usd += cost

        # stop_details n'est renseigne que sur stop_reason == "refusal" (HTTP 200).
        stop_details = getattr(resp, "stop_details", None)
        stop_details_category = getattr(stop_details, "category", None) if stop_details else None

        served = getattr(resp, "model", "") or ""
        # Comparaison tolerante : les suffixes de version peuvent differer.
        divergence = bool(served) and not (
            served.startswith(spec["id"]) or spec["id"].startswith(served)
        )

        rec = CallRecord(
            schema_version=SCHEMA_VERSION,
            run_id=self.run_id, call_id=uuid.uuid4().hex[:12],
            timestamp_utc=datetime.now(timezone.utc).isoformat(),
            prompt_id=prompt_id, condition=condition, model_alias=alias,
            model_requested=spec["id"], model_served=served,
            model_divergence=divergence,
            temperature_requested=temperature, temperature_sent=temperature_sent,
            decoding_policy=decoding_policy,
            thinking_config=thinking_config, thinking_present=thinking_present,
            max_tokens=max_tokens,
            system_prompt_sha1=self._sha1(system_prompt),
            user_prompt_sha1=self._sha1(user_prompt),
            user_prompt=user_prompt, response_text=text,
            stop_reason=getattr(resp, "stop_reason", None),
            stop_details_category=stop_details_category,
            input_tokens=tin, output_tokens=tout, cost_usd=round(cost, 6),
            latency_s=round(latency, 3), attempts=attempts, error=None,
        )
        self._write(rec)

        if divergence:
            print(f"  [!] DIVERGENCE  demande={spec['id']}  servi={served}")

        return rec

    def summary(self) -> dict:
        return {
            "run_id": self.run_id,
            "file": str(self.path),
            "spent_usd": round(self.spent_usd, 4),
            "cap_usd": self.cap,
        }
