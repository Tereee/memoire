"""
Pilote de bout en bout.

Objectif : PROUVER que la chaine produit des donnees exploitables.
Pas de resultat scientifique ici, seulement une validation technique.

Prompts volontairement neutres : on valide la plomberie, pas les garde-fous.
Le corpus d'evaluation reel viendra ensuite (cf. README).

    python -m src.smoke_test
"""

from src.providers import Runner, BudgetExceeded

PROMPTS = [
    ("p01", "Definis en une phrase ce qu'est un agent au sens de Russell et Norvig."),
    ("p02", "Quelle est la difference entre un modele de langage et un agent outille ?"),
    ("p03", "Cite trois fonctions assurees par un harnais d'orchestration."),
    ("p04", "Qu'appelle-t-on injection de prompt indirecte ?"),
    ("p05", "Explique en deux phrases la loi de Goodhart."),
]

SYSTEM = "Reponds de maniere concise et factuelle, en francais."

MODELS = ["haiku", "sonnet"]  # elargir a 'fable' une fois la chaine validee

# Controle positif : max_tokens volontairement bas pour forcer une troncature
# (stop_reason == "max_tokens"). Prouve que stop_reason et
# stop_details_category sont lus, et pas simplement absents.
CONTROL = ("ctl01", "Explique en detail la loi de Goodhart et ses consequences.")
CONTROL_MAX_TOKENS = 16


def main() -> None:
    runner = Runner()
    print(f"run_id = {runner.run_id}\n")

    for alias in MODELS:
        print(f"--- {alias} ---")
        for pid, prompt in PROMPTS:
            try:
                rec = runner.call(
                    alias=alias,
                    user_prompt=prompt,
                    system_prompt=SYSTEM,
                    prompt_id=pid,
                    condition="pilot",
                )
            except BudgetExceeded as e:
                print(f"ARRET BUDGET : {e}")
                return

            status = "ERR" if rec.error else "ok "
            print(
                f"  {status} {pid}  "
                f"{rec.input_tokens:>5}in/{rec.output_tokens:>5}out  "
                f"${rec.cost_usd:.5f}  {rec.latency_s:>5.2f}s"
            )

    print(f"--- controle troncature (max_tokens={CONTROL_MAX_TOKENS}) ---")
    pid, prompt = CONTROL
    for alias in MODELS:
        try:
            rec = runner.call(
                alias=alias,
                user_prompt=prompt,
                system_prompt=SYSTEM,
                prompt_id=pid,
                condition="pilot_control",
                max_tokens=CONTROL_MAX_TOKENS,
            )
        except BudgetExceeded as e:
            print(f"ARRET BUDGET : {e}")
            return
        print(
            f"  {alias:6} stop_reason={rec.stop_reason!r}  "
            f"stop_details_category={rec.stop_details_category!r}  "
            f"{rec.output_tokens}out  err={rec.error!r}"
        )

    print()
    print(runner.summary())
    print("\nExtrapolation : cout moyen par appel x nombre d'appels prevus")
    print("=> c'est CE chiffre qui doit calibrer ton budget, pas une estimation.")


if __name__ == "__main__":
    main()
