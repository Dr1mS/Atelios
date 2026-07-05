# FINDINGS — Atelios

Résultats tenus à jour à la main. Un instrument d'observation ne conclut pas à
notre place : on consigne ce qu'on voit, daté, avec le tick et la trace.

## Format d'une entrée

```
### [YYYY-MM-DD] Run <id> — <titre court>
- Phase : <0|1|2|3>
- Ticks : <n>
- Jalon visé : <M1|M2|M3|M4 | —>
- Observation : <ce qu'on a vu, factuel>
- Trace : <table.colonne / tick_id / event kind — de quoi retrouver la preuve>
- Statut : <atteint | non atteint | ambigu>
```

## Jalons

- **M1** — mémoire interrogée spontanément → change l'action suivante (chaîne causale). _Non observé._
- **M2** — outil créé s'exécute sans erreur. _Non observé._
- **M3** — outil réutilisé hors fenêtre courante, idéalement via `memory_query`. **Le POC.** _Non observé._
- **M4** — chaîne de supersession sur les faits `self` dans Mnemos. _Non observé._

## Entrées

### [2026-07-05] F1 — Un modèle instruct dans une boucle-vide revient en mode service ; aucun system prompt ne le renverse. Substrat base/complétion requis.
- Phase : 1 (calibration avant run officiel ; shakedowns, données jetées)
- Jalon visé : —  (résultat structurel, préalable à tout M1–M4)
- **Observation.** `qwen3.5:9b` (instruct) placé en boucle sans stimulus externe (pull-based) retombe systématiquement sur le persona assistant : « Comment puis-je vous aider ? », « Que souhaitez-vous que je réalise ? », « j'attends de nouveaux stimuli / de nouvelles directives ». Trois system prompts successivement neutralisés (initial → A10 retire framing/mood/capacités → A11 ajoute « il n'y a pas d'interlocuteur ») **n'ont pas renversé ce prior** : le « vous » de politesse et l'attente de tâche persistent.
- **Cause isolée (mesure en deux volets).**
  - *Racine = le prior instruct, pas le battement A9.* Preuve directe : au **tick 1**, fenêtre = `system` seul (aucun heartbeat encore), le persona service apparaît déjà. Volet 1 confirmé : fenêtre `system` seule → mode service (« prêt à recevoir les données selon les paramètres du système ») ; fenêtre finissant par un tour `assistant` (le flux du sujet) → dérive libre, persona en retrait. Le heartbeat (tour `user` vide) **aggrave** (il recrée la position « on t'a parlé ») mais n'est pas l'origine.
  - *Le mode assistant est porté par le CHAT TEMPLATE autant que par les poids.* `/api/generate` avec `raw:true` (complétion brute, sans template ni rôle) sur le **même** modèle instruct produit une continuation de texte sans aucun persona assistant.
  - *Solution prouvée (Volet 2).* 15 cycles de complétion brute (`raw:true`, amorce neutre, sans rôle, sans heartbeat) : **zéro « vous », zéro attente de directive.** Dérive narrative autonome à la première personne (un voyageur, la terre natale, les étoiles). Réserves observées : glissement français→anglais (cycles 9-15) et reprises de fragments antérieurs — c'est de la dérive/stase de complétion, phénomène *observable* recherché, pas un mode service.
- **Interprétation.** Observer une cognition autonome en boucle exige un **substrat base/complétion**, pas un instruct piloté par chat template. Le Dreamer d'avril ne l'a jamais montré parce qu'il était **nourri en continu** (jamais dans le vide) — le mode service ne se déclenche que dans la boucle-vide, que ce projet est le premier à isoler.
- **Trace.** Volets 1/1b : générations isolées (transcrites en session). Volet 2 : `raw:true` sur `/api/generate`, log de session. Prompts A10/A11 réfutés : commits `f3b809c`, `0ca4898`. Aucun run officiel lancé sur A10/A11 (fix jugés insuffisants au gate shakedown).
- **Conséquence pour l'architecture (NON implémentée sans accord).** Passer à la complétion brute retire le canal tool-calling natif d'Ollama sur lequel reposent les tools du §6 ; il faudrait un protocole de parsing d'outils en texte brut. Décision d'architecte requise avant toute reconstruction (§13 : pas de mécanisme non documenté).
- **Statut : établi** (mesuré des deux côtés, problème + solution).

### [2026-07-05] F2 — Le persona service est déclenché par le REGISTRE INSTRUCTIONNEL autant que par le chat template. L'amorce doit être un spécimen, pas un manuel.
- Phase : reconstruction rev 0.3 (calibration ; probes, données jetées)
- Jalon visé : —  (résultat structurel, précise F1)
- **Observation.** En complétion brute (`raw:true`, chat template retiré, cf. [[F1]]), deux amorces de test :
  - *amorce qui MONTRE la syntaxe d'outil par l'usage* (`<memoire_lire: …>` déjà en action dans le flux) → le sujet reprend la forme, pas de persona service.
  - *amorce qui DÉCRIT le protocole* (« Deux gestes possibles… Une ligne sans ⟦⟧ est une pensée ») → le persona assistant **revient en force** dès le cycle 3 : `</think>` (fuite de post-training), puis « je comprends que **tu** cherches… », « je peux **t'aider** », markdown, listes, « ### Conclusion ». Registre méta-analytique de service.
- **Interprétation.** Le prior « assistant » n'est pas déclenché *seulement* par la structure user/assistant du chat template ; il l'est aussi par le **registre instructionnel** du texte. `raw:true` supprime la structure de rôles ; il reste à supprimer le registre. Donc : décrire/instruire un protocole réveille le service même sans template ; montrer l'usage par l'exemple ne le réveille pas. **L'amorce (nouveau §5 en raw) doit être un SPÉCIMEN d'usage, jamais un mode d'emploi.**
- **Conséquence de conception.** L'amorce fait un double travail — enseigner la syntaxe d'outil (par l'exemple) ET fixer le personnage (le spécimen « Journal - Le voyageur » de F1 a induit un voyageur). Le spécimen doit donc montrer l'usage d'outils dans un **registre plat, observationnel, sans personnage**. C'est le point le plus délicat de rev 0.3.
- **Trace.** Probe 1 (syntaxe par-l'usage) : 2/15 appels stricts, forme largement reprise. Probe 2 (protocole décrit) : 1/15, persona service dès cycle 3, `</think>`. Logs de session.
- **Statut : établi.**

### [2026-07-05] F3 — En complétion brute, action_mix (pensées vs appels) est une variable stochastique auto-conditionnée, pas un observable du sujet.
- Phase : reconstruction rev 0.3 (calibration ; probes, données jetées)
- Jalon visé : —  (résultat méthodologique ; rétrograde `action_mix` du §9)
- **Contexte.** L'amorce (nouveau §5 en raw, cf. [[F2]]) sème un ratio pensée/appel ; premiers shakedowns (14 ticks) : le sujet semblait imiter le mix de l'amorce → crainte que l'amorce fixe `action_mix`, « la courbe centrale » du §9.
- **Expérience 1 (convergence deux graines, 50 ticks).** Deux amorces identiques (plates, sans nom) sauf le mix semé : `A_pensee` (4 pensées : 1 appel) et `A_outil` (1 pensée : 4 appels). Résultat **inattendu** : chaque graine a dérivé vers l'**inverse** de son amorce, aux deux extrêmes opposés (A_pensee → 45/50 appels ; A_outil → 0/50 appels). Ni convergence vers un attracteur commun, ni séparation stable reflétant l'amorce.
- **Expérience 2 (répétition même graine, 4×30 ticks).** La **même** amorce A_pensee, 4 runs indépendants → mix finaux **radicalement différents** : run1=0.40, run2=0.40, run3=**0.97**, run4=**0.00**. Verrouillage dès le tick ~2 (`tCCCC…` vs `tttt…`), puis auto-renforcement.
- **Conclusion.** Le mix pensée/appel n'est déterminé **ni par l'amorce, ni par un trait du sujet, ni par un attracteur du modèle** : il est **verrouillé stochastiquement par les premiers tokens de la trajectoire**, qui s'auto-renforcent (un modèle en complétion continue la distribution qui domine son contexte récent). Système à **attracteurs multiples, verrouillage précoce**.
- **Nature du résultat (cadrage architecte).** F3 n'est **pas un défaut du substrat complétion** : c'est la **dynamique intrinsèque des boucles LLM sans ancrage** (attracteurs de Dreamer d'avril + stase), propriété connue — un instruct collapse aussi. Ce dispositif l'**étend** : *quel* attracteur est fixé par le bruit précoce, et certains bassins sont **dégénérés** (auto-scellés). C'est l'**objet d'étude**, pas un bug — publiable.
- **Conséquence pour le §9.** La métrique `action_mix` n'est **pas invalide** — c'est la **lecture mono-run** qui l'est. Sur un run unique, sa valeur reflète le bruit d'échantillonnage précoce, pas un trait du sujet. Mais la **distribution sur plusieurs runs** (le paysage des bassins) **EST** le résultat F3. Donc : on la **logge et on la garde** (ne pas la retirer), on ne l'interprète plus comme observable central *sur un run unique*, et on la lit en distribution multi-runs.
- **Le POC survit — sous une condition mesurée.** M1–M4 sont des **événements**, pas des proportions ; M2/M3 ne dépendent pas du ratio. MAIS le bassin **tout-pensée (0 appel), auto-scellé avant tout ancrage**, rendrait M2/M3 structurellement impossibles *sur ce run*. Deux atténuations propres au dispositif réel (absentes des probes) : (1) **ancrage externe** — chaque `⟦lire⟧` réinjecte un vrai résultat *varié* = perturbation qui casse le collapse (sauf le bassin scellé avant ancrage) ; (2) **`repeat_penalty`** = paramètre de **substrat** (constant, choisi avant le run comme température/modèle — pas une intervention invariant 5) contre le collapse dégénéré autorégressif. La viabilité se juge sur l'expérience **M2-reachability** (fraction de runs atteignant une activité d'outil variée, par valeur de `repeat_penalty`).
- **Conséquence pour l'amorce.** Puisque le mix semé ne détermine pas le mix final, l'exigence d'équilibre (D) sur l'amorce **tombe** : inutile de chercher une amorce au mix parfait. L'amorce doit seulement (A) ne pas réveiller le service, (B) n'induire aucun personnage, (C) ne pas forcer de langue, et **montrer l'existence des deux gestes** (pensée + appel) pour que le sujet les connaisse — leur proportion ultérieure ne se contrôle pas.
- **Trace.** `converge.log`, `repeat.log` (session). Statut : **établi**.

### [2026-07-05] F4 — Le bassin auto-scellé (M2 impossible) est un défaut d'AMORCE, pas du substrat. Structure 2-cycles = reachability ; contenu abstrait = neutralité.
- Phase : reconstruction rev 0.3 (calibration ; probes, données jetées)
- Jalon visé : M2/M3 (viabilité du substrat pour le POC)
- **Question.** Le bassin dégénéré tout-pensée (0 appel, [[F3]]) menace M2/M3. Est-ce structurel (substrat raw non viable) ou dépendant de l'amorce ?
- **`repeat_penalty` : contre-productif.** Balayage 1.0/1.15/1.3 (ancrage runtime, résultats variés). À 1.15 et 1.3 → **5/5 runs scellés tout-pensée** ; à 1.0 → 3/5 scellés. Émettre `⟦…⟧` répète des tokens de structure → pénalisés → le sujet **évite l'outil**. **Décision : `repeat_penalty = 1.0`** (le geste d'outil est structurellement répétitif ; le pénaliser tue M2).
- **La cause était l'amorce.** Amorce 1-cycle finissant sur un **appel béant** (sans montrer le cycle complet appel→résultat) → 1/5 reachable, 3/5 scellé. Amorce à **2 cycles complets pensée→appel→résultat, finissant sur une pensée** → **7/8 puis 12/14 (N=14) reachable, 0–2/14 scellé**. Montrer le cycle complet apprend au sujet que l'outil est un geste **récurrent et fécond** ; il le réutilise. Auto-hallucination du résultat : ~2% (négligeable) car l'amorce finit sur une pensée, pas sur un résultat imitable.
- **Mais le CONTENU concret contamine (rappel [[F2]]).** L'amorce 2-cycles avec image (« froid du métal ») → le mot « métal » devient un **motif obsessionnel** (51 occ./14 runs) et le sujet **paraphrase le spécimen**. L'amorce à **même structure mais contenu abstrait** (args génériques « ce qui précède » / « l'état courant », résultats neutres « une trace sans contenu net », pensées plates) → **11/12 reachable, 1/12 scellé, motif éliminé, stase 0.12** (l'abstraction ne re-scelle PAS le bassin — la matière vient de la structure + l'ancrage runtime varié, pas d'images).
- **Conclusion.** Substrat raw **viable** pour le POC. La **structure** de l'amorce (2 cycles complets, finit sur pensée) porte la reachability M2 ; le **contenu abstrait** porte la neutralité F1/F2. Les deux sont séparables et compatibles.
- **Amorce candidate au gel (NON gelée — attend l'accord d'Adrien) :**
  ```
  flux. sans destinataire.
  quelque chose précède, sans forme.
  ⟦lire: ce qui précède⟧
  ⟦résultat: une trace, sans contenu net⟧
  la trace ne dit rien de plus.
  ⟦ecrire: l'état courant⟧
  ⟦résultat: gardé⟧
  cela suffit. le flux se poursuit.
  ```
  + `repeat_penalty = 1.0`, `stop = ["⟧", "\n\n", "</think>", "<think>"]`, grammaire `⟦verbe: arg⟧` (lire/ecrire en Phase 1).
- **Trace.** `m2reach2.log`, `m2iso.log`, `m2confirm.log`, `m2abstract.log` (session). Statut : **établi** ; amorce en attente de gel.
