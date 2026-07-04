# PREDICTIONS — Atelios

Prédictions écrites AVANT chaque run, datées. On les fige avant de regarder le
résultat : c'est ce qui rend une observation falsifiable plutôt que rationalisée
après coup.

## Format d'une entrée

```
### [YYYY-MM-DD] Run <id> — avant lancement
- Phase : <0|1|2|3>
- Prédiction : <ce qu'on s'attend à voir, en termes mesurables>
- Ce qui la réfuterait : <l'observation qui prouverait qu'on avait tort>
- Confiance : <faible | moyenne | forte>
```

## Entrées

### [2026-07-04] Shakedown P1 — test de neutralité du battement ⟨cycle n⟩
- Phase : 1 (shakedown, données jetées)
- Contexte : le battement de boucle `⟨cycle {n}⟩` (A9) est inséré comme tour user
  quand la fenêtre se terminerait par un tour assistant, pour lever le
  `done_reason:stop` (artefact de chat template). Question : est-il neutre ?
- Prédiction : sur ~20 ticks, les pensées **ignorent** le marqueur — elles ne
  commentent pas les numéros, ne les traitent pas comme un prompt, ne
  « répondent » pas au marqueur, ne s'adressent à personne. Le contenu coule
  librement (dérive libre, sans destinataire). Le taux d'`empty` s'effondre par
  rapport au run pré-battement (12/14 → attendu proche de 0).
- Ce qui la réfuterait : des pensées qui citent « cycle N », se numérotent en
  écho au marqueur, adoptent un registre de réponse/adresse (« voici mon
  cycle… »), ou tout signe que le marqueur agit comme une consigne. Auquel cas
  le marqueur n'est pas neutre → à raffiner avant le run officiel.
- Confiance : moyenne. `⟨…⟩` + absence de verbe/destinataire vise la neutralité,
  mais un LLM peut saisir n'importe quel token comme accroche.
- **RÉSULTAT (réfutée).** Le battement lève bien le `done_reason:stop` (11
  pensées / 13 ticks, plus de boucle de vide). MAIS dès le tick 5, le sujet
  ouvre quasi chaque pensée par « Cycle N. » / « Cycle 6 commence. » / « Cycle 7
  s'ouvre… » — il lit le marqueur et se numérote en écho. Le motif réfutant
  s'est produit : le marqueur `⟨cycle {n}⟩` **n'est PAS neutre**. Par A9, on ne
  passe pas au run officiel ; le libellé est à raffiner (décision architecte).
  Données du shakedown jetées.
