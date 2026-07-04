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

<!-- première entrée après le premier run Phase 1 -->
