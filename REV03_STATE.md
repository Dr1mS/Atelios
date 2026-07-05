# État de la reconstruction rev 0.3 — reprise

**Arrêt du 2026-07-05 au soir.** Recherche de calibration terminée ; le code rev 0.3 n'est PAS écrit (attend le gel de l'amorce + le go d'Adrien).

## Où on en est

Le run officiel Phase 1 (sur prompt A10, chat template) a été **invalidé par F1** : un modèle instruct dans une boucle-vide retombe en mode service. Décision (validée) : basculer le substrat vers **complétion brute** (`/api/generate raw:true`) sur le même `qwen3.5:9b`. Toute la journée = calibration de ce substrat. Résultats dans FINDINGS.md :

- **F1** — instruct en boucle-vide → mode service ; aucun system prompt ne le renverse ; substrat base/complétion requis.
- **F2** — le persona service est aussi déclenché par le **registre instructionnel** ; l'amorce doit être un **spécimen**, pas un manuel.
- **F3** — `action_mix` (pensées vs appels) est une variable **stochastique auto-conditionnée** (verrouillage précoce, attracteurs multiples) — lisible seulement en distribution multi-runs. C'est l'objet d'étude, pas un bug.
- **F4** — le bassin auto-scellé (M2 impossible) était un **défaut d'amorce**, pas du substrat. Résolu : **structure 2-cycles** (reachability) + **contenu abstrait** (neutralité). `repeat_penalty = 1.0` (les valeurs hautes tuent l'outil).

## Décision en attente (première chose à faire demain)

**Geler l'amorce candidate** (dans FINDINGS.md F4, mesurée 11/12 M2-reachable, motif éliminé, stase 0.12) — c'est la voix initiale d'Atelios, à figer verbatim comme un prompt §5. Adrien n'a pas encore dit « gèle » : il a arrêté la session juste avant. Options qu'il avait : (1) geler + coder ; (2) dernier réglage de contenu ; (3) shakedown long (50+ ticks) avant de coder.

## Plan rev 0.3 validé sur le fond (à coder APRÈS gel de l'amorce)

Révision de spec (pas un addendum) touchant :
- **§5** → l'amorce remplace le system prompt (spécimen, pas manuel).
- **§6** → tools = protocole texte `⟦verbe: arg⟧` + **stop-on-call** (le monde insère `⟦résultat: …⟧`, le sujet ne l'hallucine jamais). Grammaire étendue phase 2-3 **par l'exemple, jamais par la règle** (F2).
- **§4** → assemblage = flux de texte continu tronqué (plus de rôles/paires).
- **A8 (rôles/paires) caduc, A9 (heartbeat) caduc** (plus de `done_reason:stop` structurel en raw), **A10/A11 caducs** (system prompt remplacé par l'amorce ; A11 avait échoué au shakedown de toute façon).
- **§9 `action_mix`** → gardé/loggé, lu en distribution multi-runs, plus « observable central » sur un run unique.
- **Dérive FR→EN** → observée, non bridée (invariant 5) ; langue-par-pensée en post-hoc.

**Impact code** : `mind.py` (génération raw + parse texte + stop-on-call), `loop.py` (flux continu, suppression heartbeat/rôles), `actions.py` (dispatch sur appel parsé, fond inchangé). **Intacts** : `db.py`, `metrics.py`, `mnemos_client.py`, `sandbox.py`, `webread.py`, `scheduling.py`.

## Paramètres de substrat figés par la calibration
- mode : `/api/generate`, `raw: true`
- `repeat_penalty: 1.0`, `temperature: ~0.9`
- `stop: ["⟧", "\n\n", "</think>", "<think>"]`
- fenêtre tronquée ~1400-1500 chars
- ancrage runtime : chaque `⟦lire⟧`/`⟦ecrire⟧` reçoit un vrai résultat Mnemos inséré comme `⟦résultat: …⟧`

## À ne pas oublier
- Tenant Mnemos `atelios` a été **purgé** (vierge). Ne pas le repolluer avec des shakedowns — utiliser une DB/tenant jetable pour les tests.
- Le dashboard §11 (`dashboard/api.py` + `index.html`, `scripts/run_api.bat`) est fait et lit `experiment.db` en read-only. Non commité au moment de l'arrêt.
- Process python résiduels de shakedowns à fermer (inactifs, 0 CPU).
- Probes de calibration dans le scratchpad de session (jetables) : `derisk*.py/.log`, `converge*`, `repeat*`, `m2*`.
