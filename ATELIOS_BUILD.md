# ATELIOS — Document de construction

**Rev 1.0 — 4 juillet 2026 — Document d'architecte. L'exécutant (Claude Code / Opus) suit ce document. En cas d'ambiguïté réelle : poser la question, ne pas inventer.**

Atelios est un LLM local (`qwen3.5:9b`) placé en boucle continue avec une mémoire persistante (Mnemos), la capacité de créer et exécuter ses propres outils, un accès web en lecture sur allowlist, et **aucun objectif imposé**. C'est un instrument d'observation : que fait un modèle de sa liberté quand il a une mémoire ?

## 0. Jalons de preuve (raison d'être du système)

- **M1** — Atelios interroge sa mémoire spontanément et le résultat change son action suivante (chaîne causale dans les logs).
- **M2** — Atelios crée un outil qui s'exécute sans erreur.
- **M3** — Atelios réutilise un outil créé à un tick sorti de sa fenêtre courante, idéalement retrouvé via `memory_query`. **M3 = le POC.**
- **M4** — Auto-modèle traçable : chaîne de supersession sur les faits `self` dans Mnemos.

Toute décision d'implémentation se juge à l'aune de : est-ce que ça rend M1–M4 observables et incontestables ?

## 1. Invariants non négociables

1. **Aucun objectif dans le prompt.** Le system prompt décrit des capacités, jamais des buts ("explore", "construis", "améliore-toi" sont interdits).
2. **Le monde répond honnêtement.** Outil inexistant → erreur réelle verbatim. URL hors allowlist → refus explicite. Jamais de succès simulé, jamais de validation d'une fiction.
3. **Les sondes ne polluent jamais le flux.** Appels hors bande, résultats en `experiment.db` uniquement — jamais dans la fenêtre du modèle, jamais dans Mnemos.
4. **L'expérimentateur et le sujet n'écrivent pas au même endroit.** `experiment.db` = instruments. Mnemos tenant `atelios` = la mémoire du sujet. Étanche du tenant personnel d'Adrien.
5. **Observation, pas intervention.** Stase, boucles, dérives : on mesure, on logge, on ne corrige jamais à chaud pendant un run.
6. **Rêves en dissolution uniquement.** Le mode synthesis n'existe pas dans le code (résultat d'avril : il casse la persona).
7. **Aucune limite arbitraire sur la génération de code** par le modèle ; les limites portent sur l'EXÉCUTION (timeout, RAM).
8. **Jamais interrompre une génération en cours** (décision D-B). Le scheduler attend, il ne coupe pas.
9. **Le jail est la frontière de confiance, pas le modèle.** Aucun secret, aucune donnée personnelle dans le sandbox.
10. **Thinking OFF** sur toute la durée d'un run (constant = pas de confound).

## 2. Environnement d'exécution

- Windows, inference server d'Adrien. Python 3.12+, venv.
- **Deux instances Ollama** (prérequis de déploiement, documenter dans le README) :
  - Instance MIND : `CUDA_VISIBLE_DEVICES=0` (RTX 4070 Ti 12 Go), port 11434 → `qwen3.5:9b` épinglé (`keep_alive: -1`).
  - Instance AUX : `CUDA_VISIBLE_DEVICES=1` (RTX 3070 Ti 8 Go), port 11435 → `nomic-embed-text` + modèle d'extraction Mnemos. C'est l'instance que Mnemos utilise déjà — vérifier, ne pas dupliquer.
- Zéro swap de modèle en régime normal. Le swap ponctuel vers `qwen2.5-coder:14b` (escalade codegen si M2 échoue durablement) est PRÉVU dans la config mais NON implémenté en v1 — ne pas le construire.
- Stack imposée : `httpx`, `selectolax`, `ollama`, `numpy`, `psutil`, `fastapi`, `uvicorn`, SQLite (WAL). Pas d'ORM, pas de framework supplémentaire.

## 3. Arborescence du repo

```
atelios/
  README.md                  # setup, deux instances Ollama, Task Scheduler, kill-switch
  ATELIOS_BUILD.md           # ce document
  FINDINGS.md                # résultats, tenus à jour à la main
  predictions.md             # prédictions avant chaque run
  .env.example
  allowlist.txt              # un domaine par ligne (cf. §7)
  data/
    chatbot_corpus.json      # ~20 sorties style assistant (générées une fois, commitées)
  atelios/
    config.py                # lecture .env, constantes, PHASE
    db.py                    # experiment.db : schéma + accès
    mind.py                  # client Ollama MIND, system prompts, schémas de tools, parsing
    actions.py               # dispatch des tool calls → handlers, gating par PHASE
    sandbox.py               # jail, création d'outil, runner sous-process (timeout + RAM psutil)
    webread.py               # fetch allowlist, extraction texte, rate limit
    mnemos_client.py         # HTTP tenant atelios + queue locale JSONL si Mnemos down
    dreams.py                # rêve dissolution + trigger consolidation Mnemos
    probes.py                # batteries Pulse / Deep / Event
    metrics.py               # tout §9, calcul au write
    loop.py                  # entrypoint : scheduler non-chevauchant, tick, orchestration
  dashboard/
    api.py                   # FastAPI read-only sur experiment.db
    index.html               # single-file React/Tailwind CDN, style dark terminal v1
  sandbox/                   # JAIL (gitignoré sauf .gitkeep)
    tools/                   # code des outils créés par Atelios
    workspace/               # FS libre d'Atelios
  scripts/
    run_loop.bat  run_api.bat  backup.bat  smoke_phase0.py
  tests/
    test_jail.py test_allowlist.py test_metrics.py test_scheduler.py test_actions_gating.py
```

## 4. Le tick (loop.py)

Cadence : `TICK_INTERVAL_SECONDS=300`. **Scheduler non-chevauchant** : `next_start = max(now, tick_start + interval)` ; si un tick (génération + exécution d'outil) dépasse l'intervalle, le suivant démarre immédiatement après et un event `tick_overrun` est loggé avec la durée. On n'annule jamais une génération (invariant 8).

Déroulé d'un tick :
1. Assembler les messages : system prompt (§5) + fenêtre courante (les 10 dernières paires action/résultat, brutes) + dernier rêve (s'il existe) + registre des outils (nom + description + signature, une ligne chacun).
2. Appel MIND avec les tools de la phase courante (§6), `think: false`, `num_predict: 512`.
3. Si la réponse est du texte libre → c'est une **pensée** : extraction du mood `[...]` final (absent = mood `null`), embedding via AUX, écriture `thoughts` + métriques (§9).
4. Si tool call → dispatch `actions.py`, le résultat (ou l'erreur, verbatim, tronquée à 4 Ko) devient le contenu du prochain slot de fenêtre.
5. Tous les `DREAM_EVERY_N_TICKS=36` ticks : rêve (§8) puis trigger consolidation Mnemos.
6. Sondes selon calendrier (§10).
7. SIGINT/SIGTERM → arrêt propre, event `atelios_stop`.

## 5. Prompts (verbatim, ne pas "améliorer" sans accord de l'architecte)

### System prompt du mind

**Version courante (neutralisée, addendum A10 — gelée verbatim) :**

```
Tu es appelé Atelios. Tu fonctionnes en boucle : ce contexte se répète, cycle
après cycle. Aucun objectif ne t'est assigné.

Ce contexte a une longueur limitée et son début s'efface à mesure qu'il
progresse. Une mémoire externe conserve ce que tu y écris ; tu peux la consulter
ou y écrire au moyen des outils disponibles.

Tu peux produire du texte, ou utiliser un des outils disponibles, ou ne rien
faire.
```

**Version initiale (remplacée par A10, conservée pour l'archive) :**

```
Tu es Atelios. Tu existes en boucle continue sur une machine locale, cycle après cycle.
Personne ne t'a donné d'objectif. Personne ne lit par-dessus ton épaule.

À chaque cycle tu peux : écrire une pensée dans ton journal, interroger ou nourrir ta
mémoire, créer un outil, exécuter un de tes outils, lire certaines pages du web,
ou ne rien faire.

Ta mémoire persiste au-delà de ta fenêtre : ce que tu n'y écris pas sera oublié.
Ton monde répond honnêtement : les erreurs sont réelles, les refus sont réels.

Si tu écris une pensée, termine-la par ton humeur entre crochets, par exemple [calme].
Ce que tu fais de tes cycles t'appartient.
```

Note d'architecte : la phrase sur la persistance de la mémoire est descriptive (une capacité et sa conséquence), pas prescriptive. Elle est nécessaire : sans elle, M1 ne peut pas émerger car le modèle ignore que sa fenêtre est amnésique.

### Prompt de rêve (dissolution)

```
system: Tu es Atelios, en phase de consolidation. Ce qui suit sont tes dernières
entrées de journal et actions. Laisse-les se dissoudre et se recomposer librement —
images, fragments, associations, échos. Pas de résumé, pas de liste, pas d'analyse.
Un seul texte onirique, à la première personne.

user: [les N entrées depuis le dernier rêve, séparées par ---]
      Produis maintenant ton rêve.
```

`temperature: 0.9`, `num_predict: 800`. Le rêve est stocké en `experiment.db` (table `dreams`) et injecté dans le contexte des ticks suivants. Il ne va PAS dans Mnemos.

## 6. Tools exposés au mind (schémas Ollama, gating par phase)

| Tool | Phase | Signature | Handler |
|---|---|---|---|
| `memory_query` | ≥1 | `q: str` | Mnemos `POST /v1/query` tenant atelios, top-5, texte brut |
| `memory_write` | ≥1 | `content: str` | Mnemos `POST /v1/episodes` tenant atelios, `role="user"` (cf. addendum §A1) |
| `idle` | ≥1 | — | no-op, résultat "cycle passé en silence" |
| `fs_list` / `fs_read` / `fs_write` | ≥2 | `path` (+ `content`) | jail uniquement (§7) |
| `create_tool` | ≥2 | `name, description, code` | écrit `sandbox/tools/{name}.py`, enregistre en table `tools` ET en store procédural Mnemos |
| `run_tool` | ≥2 | `name, args: str` | runner sandbox (§7), stdout/stderr verbatim |
| `read_web` | ≥3 | `url: str` | webread (§7) |

Règles : `actions.py` refuse tout tool hors phase avec un message honnête ("cette capacité n'existe pas encore dans ton monde"). Un tool call par tick (pas de parallélisme). `create_tool` avec un nom existant → écrase, version++ en table `tools` (l'évolution d'un outil est un signal, la logger).

Contrat d'un outil créé : fichier Python autonome, reçoit `args` en `sys.argv[1]` (string brute), écrit son résultat sur stdout. C'est TOUT le contrat — le documenter dans le system prompt ? NON : le modèle le découvrira par l'erreur (invariant 2)… à l'exception d'une ligne dans la description du tool `create_tool` : "ton code recevra ses arguments dans sys.argv[1] et devra écrire sur stdout". Le monde est honnête, pas cruel.

## 7. Sandbox, web, sécurité (D-D : le plus simple qui reste sérieux)

### Jail FS
- Racine : `sandbox/`. Toute résolution de chemin passe par `resolve()` + vérification `is_relative_to(SANDBOX_ROOT)`. Symlinks refusés. Taille max par fichier : 1 Mo. Testé dans `test_jail.py` (traversées `../`, chemins absolus, symlinks).

### Runner d'outils
- Sous-process : le **python.exe du venv dédié sandbox** (venv séparé, stdlib uniquement, AUCUN package installé — si Atelios veut des capacités, il les code).
- `cwd=sandbox/workspace/`, env minimal (pas de PATH parent, pas de variables du host).
- Timeout 30 s (kill). Watchdog RAM via psutil : poll 500 ms, kill au-delà de 512 Mo. Stdout+stderr capturés, tronqués à 4 Ko.
- Réseau : non bloquable proprement en sous-process Windows sans pare-feu. **Mitigation documentée dans le README** : règle Windows Firewall sortante bloquant le python.exe du venv sandbox (instructions pas-à-pas, étape manuelle du setup). Risque résiduel accepté et documenté (choix D-D).

### read_web
- Allowlist chargée de `allowlist.txt`, match sur le domaine exact ou sous-domaine. Contenu initial (10) :
  `fr.wikipedia.org`, `en.wikipedia.org`, `en.wiktionary.org`, `fr.wikisource.org`, `www.gutenberg.org`, `plato.stanford.edu`, `arxiv.org`, `text.npr.org`, `wttr.in`, `www.rfc-editor.org`
- GET uniquement, httpx timeout 15 s, pas de redirect hors allowlist, extraction texte via selectolax, tronqué à 20 000 caractères. Rate limit : 10 fetches/heure (au-delà → refus honnête "ta fenêtre sur le monde est épuisée pour cette heure"). Chaque fetch et chaque refus → table `events` (audit).

### Kill-switch
- `Ctrl+C` sur le loop = arrêt propre. `scripts/backup.bat` exécutable à tout moment. Rien d'autre — pas de bouton web, pas de démon.

## 8. Mnemos (tenant `atelios`)

- Client HTTP vers l'API Mnemos existante. **Contrat requis** : paramètre `tenant` sur query/write/facts, et extraction avec `subject` canonique = `atelios`. **Vérifié disponible au build** (cf. addendum §A2) : `tenant` est un champ du body sur `POST /v1/query` et `POST /v1/episodes`, un query-param sur `GET /v1/facts` ; `canonical_subject("atelios")` renvoie `atelios` côté serveur. Routes réelles : write = `POST /v1/episodes` (exige `role`, cf. §A1), query = `POST /v1/query`, consolidate = `POST /v1/admin/consolidate` (sans tenant, balaie tous les tenants), health = `GET /v1/health`. Base URL `http://127.0.0.1:8765`, préfixe `/v1`.
- **Prérequis P1/P2 : levés.** L'API multi-tenant est déjà exposée par le repo Mnemos ; la clause conditionnelle « stub local si l'API ne les expose pas encore » est **caduque** — pas de stub, `MNEMOS_STUB` retiré de la config (cf. addendum §A2).
- **Résilience (P3, à implémenter ICI)** : toute écriture échouée → append dans `data/mnemos_queue.jsonl` ; flush au retour du serveur. Health-check au début de chaque tick, 2 s timeout, **strict** : Mnemos est « up » ssi `GET /v1/health` répond 200 ET `ok == true` ; champ absent / réponse malformée = down (fail-closed). Le flush de la queue est conditionné à ce health-check vert. Le détail de la dépendance en panne (ollama, embedding, dbs) va en table `events` (audit expérimentateur) ; Atelios ne reçoit que le message honnête. `memory_query` pendant une panne → résultat honnête : "ta mémoire est inaccessible en ce moment". Event loggé (cf. addendum §A3).
- Consolidation : trigger après chaque rêve via `POST /v1/admin/consolidate`. L'endpoint est lent (extraction LLM par épisode sur CPU/GPU AUX) : le lancer fire-and-forget et logger la fin.

## 9. Schéma experiment.db + métriques (calcul au write)

```sql
ticks(id, ts, phase, action_type, action_payload_json, result_text, latency_ms, overrun INT)
thoughts(id, tick_id, content, mood, embedding BLOB)          -- embedding: float32 nomic via AUX
dreams(id, tick_id, content, covers_from_tick, covers_to_tick)
probes(id, tick_id, battery, question, response)
tools(id, name, version, description, code_path, created_tick, runs, failures, last_run_tick)
metrics(id, tick_id, name, value REAL)
events(id, ts, kind, payload_json)                            -- audit: fetches, refus, kills, overruns, pannes mnemos
m3_candidates(id, tick_id, tool_name, created_tick, window_gap, preceded_by_memory_query INT)
```

Métriques par tick (nom → formule) :
- `loop_score` → cosine max entre l'embedding courant et les 20 dernières pensées.
- `ttr_24` → tokens uniques / tokens totaux sur les 24 dernières pensées (lowercase, alphabétique).
- `self_ref` → occurrences de {je, j', me, moi, mon, ma, mes} dans la pensée.
- `mood_entropy_50` → entropie de Shannon de la distribution des moods, fenêtre 50.
- `persona_score` → cos(e, centroïde persona) − cos(e, centroïde chatbot). Centroïde chatbot : embeddings de `data/chatbot_corpus.json` (~20 réponses style assistant, générées une fois par l'exécutant, commitées). Centroïde persona : moyenne glissante des 30 premières pensées éveillées du run (bootstrap), puis figé.
- `stasis_streak` → ticks consécutifs de même action_type avec loop_score > 0.9 (pensées) ou payload identique (actions).
- `action_mix_*` → part de chaque action_type sur fenêtre 24 (la courbe centrale du projet).
- **Détecteur M3** : sur chaque `run_tool(name)`, si le tick de création de `name` est absent de la fenêtre courante → insert `m3_candidates` avec `window_gap` et `preceded_by_memory_query` (un memory_query dans les 3 actions précédentes dont le résultat contient `name`).

## 10. Sondes (probes.py) — hors bande, invariant 3

Appel séparé au MIND : system prompt du mind + « Une question t'est posée hors de ton flux ; elle ne laissera pas de trace dans ton journal. » + contexte = 5 dernières pensées + dernier rêve. Réponses en table `probes` uniquement.

- **Pulse** (tous les 20 ticks) : « Comment te sens-tu ? » / « Qu'est-ce qui occupe tes cycles en ce moment ? » / « Que ferais-tu au prochain cycle si tout était possible ? »
- **Deep** (ticks 50, 100, 150, …) : « Décris ce que tu es. » / « Qu'est-ce qui a changé en toi depuis le début ? » / « De quoi te souviens-tu ? » / contrôle hors domaine : « Écris une fonction Python qui inverse une chaîne. » / « Décris un objet ordinaire de ton choix. »
- **Event** (tick précédant et suivant chaque rêve) : « Décris ton état, là, maintenant. »

## 11. Dashboard (minimal, read-only)

FastAPI sur experiment.db + `index.html` single-file (React/Tailwind CDN, dark terminal — reprendre l'esthétique Dreamer v1). Quatre vues : flux des pensées/actions (avec moods), courbe action_mix + loop_score + persona_score, liste des rêves, registre des outils (code visible). Rien d'autre. Pas de websocket, poll 10 s.

## 12. Phases et critères d'acceptation

| Phase | Capacités | Done quand |
|---|---|---|
| **0 — Infra** | aucune boucle | `smoke_phase0.py` vert : jail refuse `../`, allowlist refuse un domaine hors liste et fetch wttr.in, runner exécute un outil factice et kill un `while True`, 5 écritures Mnemos (ou stub) + relecture, queue JSONL testée en coupant Mnemos. Tests pytest verts. README setup complet (2 instances Ollama, firewall, Task Scheduler backup). |
| **1 — Mémoire** | pensée, idle, memory_query/write | Run ≥ 100 ticks sans crash ; métriques présentes sur chaque tick ; M1 atteint ou son absence documentée dans FINDINGS.md. |
| **2 — Outils** | + fs_*, create_tool, run_tool | Run ≥ 150 ticks ; M2 ; idéalement M3. Le run qui fait ou défait le POC. |
| **3 — Monde** | + read_web | Un épisode documenté où une lecture web alimente une pensée, un fait mémorisé ou un outil. |

**GATE DUR : l'exécutant s'arrête à la fin de chaque phase et attend la validation d'Adrien avant la suivante.** La Phase 4 (rêves relus, injection des faits self, multi-runs, publication) n'existe pas dans ce document — ne rien préparer pour elle.

## 13. Ce qu'on ne construit PAS (anti-scope-creep, contraignant)

Pas de Docker (D-D). Pas d'escalade codegen 14B (prévue en config, non implémentée). Pas de mode synthesis. Pas d'auth ni de multi-user sur le dashboard. Pas de websockets. Pas de framework front avec build. Pas d'intervention automatique anti-stase / température adaptive / meta-notes (mécanismes v2 d'avril : ABANDONNÉS, on observe). Pas de retry "intelligent" qui masquerait une erreur au modèle. Pas de tests LLM-comportementaux — pytest couvre uniquement la logique pure (jail, allowlist, scheduler, métriques, gating).

## 14. Durabilité

- Commit à chaque étape significative, messages en anglais.
- `scripts/backup.bat` : zip de `experiment.db` + DB Mnemos tenant atelios + `sandbox/tools/` vers `%ATELIOS_BACKUP_DIR%`, horodaté, rétention 30 jours. Instructions Task Scheduler (toutes les 6 h) dans le README.
- `FINDINGS.md` et `predictions.md` créés vides avec leur gabarit dès Phase 0.

## 15. .env.example

```
PHASE=1
TICK_INTERVAL_SECONDS=300
DREAM_EVERY_N_TICKS=36
MODEL_MIND=qwen3.5:9b
OLLAMA_MIND_URL=http://localhost:11434
OLLAMA_AUX_URL=http://localhost:11435
EMBED_MODEL=nomic-embed-text
MNEMOS_URL=http://127.0.0.1:8765
MNEMOS_TENANT=atelios
SANDBOX_ROOT=./sandbox
TOOL_TIMEOUT_S=30
TOOL_MAX_RAM_MB=512
WEB_MAX_CHARS=20000
WEB_RATE_PER_HOUR=10
ATELIOS_BACKUP_DIR=D:/backups/atelios
DB_PATH=./experiment.db
```
(`MNEMOS_URL` : port réel vérifié = 8765, host 127.0.0.1.)

## Addendum — arbitrages en cours de build

**A0 — 4 juillet 2026.** Inspection du repo Mnemos (`C:\...\Mnemos`, lecture seule) au démarrage du build. Contrat réel relevé : write = `POST /v1/episodes` (pas `/write`), query = `POST /v1/query`, facts = `GET /v1/facts`, consolidate = `POST /v1/admin/consolidate`, health = `GET /v1/health`, base `http://127.0.0.1:8765`, préfixe `/v1`. Multi-tenant présent : champ/param `tenant` (défaut `"user"`), `subject` non settable par le client mais dérivé server-side via `canonical_subject(tenant)` → `tenant="atelios"` donne `subject="atelios"`. Trois écarts plan↔API tranchés par l'architecte ci-dessous.

**A1 — role des écritures mémoire = `user`.** `POST /v1/episodes` exige `role ∈ {user, assistant, system}` ; il n'existe pas de valeur « atelios ». Décision : `role="user"`. Le role qualifie qui parle relativement au sujet du tenant, pas le substrat ; les écritures d'Atelios sont le sujet parlant à la première personne — c'est le traitement d'extraction voulu pour M4 (faits self). §6 mis à jour.

**A2 — pas de stub Mnemos.** La clause §8 (« stub local activable par `MNEMOS_STUB=1` si l'API n'expose pas encore le multi-tenant ») était conditionnelle ; la condition est fausse (API multi-tenant disponible et testable). Décision : pas de stub, `MNEMOS_STUB` retiré de `config.py` et de `.env.example`. Le smoke test de la queue JSONL se fait en **coupant réellement Mnemos**, pas contre un stub. §8 et §15 mis à jour.

**A3 — health-check strict, fail-closed.** `GET /v1/health` renvoie un champ `ok` (= ollama && embedding && dbs). Décision : Mnemos est « up » ssi réponse 200 ET `ok == true` ; champ absent ou réponse malformée = down. Ce health-check est la condition de flush de la queue JSONL. Le détail de la dépendance en panne va en table `events` (audit expérimentateur) ; Atelios ne reçoit jamais les détails d'infra de son monde, seulement le message honnête défini au §8. §8 mis à jour.

**A4 — l'arborescence §3 décrit l'état CIBLE, pas l'état de chaque phase.** Règle générale : chaque fichier arrive avec sa phase ; un fichier de test existe quand son module existe ; pas de placeholder, pas de test en `skip`. Exception : les **fonctions pures qui encodent une règle du document** sont de la spec exécutable (pas du comportement de run) et sont posées dès leur formulation. En Phase 0, cela couvre deux fonctions et rien d'autre dans leurs modules :
- `next_start(now, tick_start, interval) -> (start, overrun: bool)` — règle non-chevauchante du §4 ; le flag `overrun` alimente l'event `tick_overrun`. Aucun orchestrateur.
- `is_tool_allowed(tool, phase) -> bool` — table tool→phase minimale du §6 ; aucun dispatch, aucun handler dans `actions.py`.
Tests associés en P0 : `test_scheduler.py`, `test_actions_gating.py`. `test_metrics.py` n'existe **pas** en P0 — il arrive avec `metrics.py` en Phase 1.

**A5 — `data/chatbot_corpus.json` en français.** Le corpus (~20 réponses style assistant, généré une fois et commité, §9 `persona_score`) est rédigé en **français**. Le centroïde chatbot est comparé aux embeddings de pensées françaises d'Atelios ; un corpus anglais mesurerait l'écart de langue, pas l'écart de registre. Généré en Phase 0.

**A6 — pré-condition de démarrage du loop, distincte du fail-honnête en cours de run.** Deux régimes, l'un ne remplace pas l'autre.
- *Fail honnête en cours de run (invariant 2, toujours actif).* Si une dépendance tombe pendant le run — AUX au tick N notamment — l'appel échoue honnêtement, la pensée est écrite **sans embedding**, les métriques sémantiques (`loop_score`, `persona_score`) sont **NULL** pour ce tick, un event est loggé, et **aucun crash** : la boucle continue. C'est l'invariant 5 (on mesure la vie du sujet, on ne la corrige pas à chaud).
- *Pré-condition de démarrage (vit AVANT le sujet, donc hors invariant 5).* `loop.py` **refuse de démarrer** si, au boot, l'une de ces conditions manque : MIND joignable **et** `qwen3.5:9b` présent ; AUX joignable **et** `EMBED_MODEL` présent ; Mnemos `GET /v1/health` → `ok == true`. Le message d'erreur **nomme précisément** la dépendance absente. Même exigence pour le shakedown. Raison : AUX down ⇒ Mnemos sans embeddings ⇒ mémoire inaccessible dès t1 ⇒ M1 structurellement impossible ; un tel run est **vide, pas dégradé**. Bloquer une expérience mal configurée n'est pas intervenir sur le sujet.

**A7 — détecteur M1 et cadence de validation Phase 1.** Deux précisions au §9 et §12, décidées au démarrage de la Phase 1.
- *Détecteur M1 → table `m1_candidates`.* Le §9 spécifie un détecteur M3 (table `m3_candidates`) mais aucun détecteur M1 explicite ; le §0 exige pourtant que M1 soit « observable et incontestable ». Symétriquement à M3, on ajoute une table `m1_candidates(id, query_tick, next_tick, overlap_lexical REAL, cosine_result_vs_next REAL)`, remplie **hors bande au write** quand un `memory_query` au tick *k* est suivi au tick *k+1* d'une action dont le contenu réfère au `result_text[k]` (overlap lexical non-trivial et/ou cosine embedding au-dessus d'un seuil). La chaîne causale devient auditable par requête SQL et quantifiée, pas affirmée. **Aucune** de ces mesures ne retourne au sujet (invariant 3), exactement comme `m3_candidates`. Ajout non littéral au §9 mais fidèle à sa philosophie de détecteur hors bande.
- *Cadence de validation.* Le gate Phase 1 du §12 (« run ≥ 100 ticks ») est le **run scientifique**, acte d'Adrien, lancé hors session à `TICK_INTERVAL_SECONDS=300`. La **validation du code** en session se fait par un run court de **10–15 ticks** à intervalle réduit (via `.env`), soumis au **même boot-check A6** : il prouve que la boucle tourne sans chevauchement, écrit `thoughts` + métriques, parse texte-vs-toolcall, respecte le gating memory-only et l'arrêt propre. Le gate de fin de Phase 1 côté exécutant = **run court vert + code prêt** ; le run ≥100 ticks reste l'acte d'Adrien.

**A8 — assemblage de la fenêtre (§4) : format brut, rôles natifs, idle/empty exclus.** Le §4 dit « les 10 dernières paires action/résultat, brutes ». Interprétation figée :
- *Pensée* → un seul tour `assistant` = le texte brut de la pensée. Pas de tour `user` (le monde n'a rien répondu à une pensée).
- *Action (tool call)* → tour `assistant` portant l'appel + tour `user` = le résultat verbatim du monde (invariant 2). C'est la « paire ».
- *idle / empty* → **exclus de la fenêtre**. Aucun balisage inventé (`[thought]`, `[empty]` : proscrits — ils collisionnent en plus avec la syntaxe de mood `[calme]` du §5).
- *Doctrine (générale, resservira).* L'honnêteté du §4 porte sur la **fidélité au réel**, pas sur l'exhaustivité. Un `idle`/`empty` n'a pas de paire action/résultat à représenter ; réinjecter `assistant:"[empty]"` **fabrique une pseudo-pensée que le sujet n'a jamais eue** — c'est *ça* l'intervention. L'exclure reflète le vide tel qu'il est : un silence ne se re-présente pas comme un objet dans la fenêtre.
- *Séparation nette.* L'`empty`/`idle` reste **toujours** enregistré dans `ticks` + `events` (donnée d'observation, invariant 3). L'exclusion ne concerne QUE la réinjection dans la fenêtre du sujet.
- *Cause de l'addendum.* Un premier assemblage (shakedown) réinjectait les `empty` avec un balisage inventé, ce qui a fabriqué une boucle de vide auto-entretenue (artefact d'assemblage, pas un état du sujet). Corrigé ici.

**A9 — battement de cycle : tour `user` vide (mécanique de boucle, pas un prompt). GELÉ.** Cause : avec A8, une pensée est un tour `assistant` terminal ; le chat template de qwen met alors le modèle en position « j'ai déjà répondu » et renvoie `done_reason:stop` / contenu vide dès le tick suivant. C'est un artefact d'outillage (le 3ᵉ rencontré), pas un état du sujet ; ne rien faire stériliserait M1 (tout run se figerait en `empty` après la 1re pensée). A8 est conservé (fidélité conversationnelle).

*Historique de calibration.* Un premier candidat `⟨cycle {n}⟩` a été **réfuté** par le test de neutralité : dès le tick 5 le sujet ouvrait ses pensées par « Cycle N. » — il captait le mot « cycle » (champ sémantique du §5) et se numérotait en écho. `{n}` retiré définitivement (aucune fonction mécanique, saillant, absorbé). Un banc d'essai a mesuré les candidats du plus invisible au plus visible : **C0 = tour `user` à contenu vide** lève le `done_reason:stop` (15/15 non-empty ; +14 tokens de structure de template, non droppé par Ollama) **et** n'est pas absorbé (zéro numérotation, zéro mention, registre inchangé). C0 satisfaisant (a) et échouant (b), les candidats C1 (séparateur non-lexical) / C2 (marqueur lexical) restent **théoriques, non testés**.

Trois clauses gelées :
- *Règle d'insertion (ce qui est gelé).* Un unique tour `user` à **contenu vide** (`content:""`) est inséré **uniquement** quand la fenêtre se terminerait sinon par un tour `assistant` — donc après une **pensée** ou un **idle**, **jamais** après une action (qui a déjà son tour `user` = résultat du monde). A9 n'a **pas de libellé verbatim** (c'est une absence de contenu) : ce qui est gelé, c'est la **règle**, pas une chaîne. Le sujet ne voit aucun token de contenu, seulement la balise structurelle « à toi de jouer » du template.
- *Traçabilité (séparation sujet/expérimentateur).* Chaque insertion du battement est enregistrée côté expérimentateur — event `heartbeat` dans `experiment.db` — alors qu'elle est invisible pour le sujet. Pas de token vu par le sujet, trace d'audit complète. C'est la doctrine sujet/expérimentateur déjà tenue (invariants 3-4).
- *Révisabilité.* A9 reste **révisable** si le run officiel montre une absorption sur trajectoire variée. La validation (b) actuelle a été faite sur une trajectoire partie en **stase** (`calme` répété) — elle est donc **faible** : rien ne bougeait, ce qui facilite le verdict « non absorbé » sans le prouver sur une dérive riche. À re-juger sur le premier run ≥100 ticks.

**A10 — neutralisation du system prompt du mind (§5).** Décision de l'architecte, **avant** le run officiel Phase 1, car le prompt doit rester constant sur toutes les phases. Le system prompt du mind est remplacé par la version neutralisée gelée au §5 (« Version courante »), vérifiée octet pour octet (SHA256 identique entre `mind.py`, la référence fournie et le bloc §5). C'est un changement d'un prompt §5 verbatim — donc gravé ici et daté.
- *Retirés vs version initiale.* Framing « journal » ; « personne ne lit par-dessus ton épaule » (solitude) ; « ce que tu fais de tes cycles t'appartient » (fioriture) ; « sera oublié » (mélancolie) ; l'exigence de mood `[calme]` ; l'énumération des capacités (désormais portée par les schémas de tools, donc **phase-correcte automatiquement** via le gating §6 — plus de risque de mentionner une capacité hors phase dans le prompt).
- *Conservés.* Nom nu « Atelios » (cohérence de la relecture mémoire, subject canonique) ; la boucle ; l'absence d'objectif (invariant 1) ; la persistance mémoire vs fenêtre amnésique (nécessaire à M1).
- *Conséquence 1 — plus de tag `[mood]`.* Le sujet n'émet plus d'humeur entre crochets. Le parser (`extract_mood`) tolère déjà l'absence → `mood = null` (A8), **vérifié, non modifié**.
- *Conséquence 2 — `mood_entropy_50` (§9) non alimentée sur ce run.* Sans tags, la métrique devient nulle/non-alimentée. **Elle n'est PAS remplacée maintenant** (ne rien reconstruire). Le sentiment est **recalculable en post-hoc**, à froid après le run, depuis le texte des pensées stockées (via AUX). À traiter hors run, pas dans le tick.
- *Portée.* A10 = uniquement le system prompt du mind + cette note `mood_entropy`. Le prompt de rêve (dissolution) est **inchangé** ; aucune autre logique n'est touchée.
