"""Metric tests (§9, §A4/A7): pure logic only, no AUX/LLM (§13).

These exercise the pure helpers on fixed vectors/texts and the NULL-gating rules.
The compute-at-write orchestration and AUX embedding are NOT tested here (they
touch network/LLM — that is the shakedown run's job, not pytest's)."""

from __future__ import annotations

import math

from atelios import metrics as m
from atelios.mind import extract_mood


def test_cosine():
    assert m.cosine([1, 0, 0], [1, 0, 0]) == 1.0
    assert m.cosine([1, 0], [0, 1]) == 0.0
    assert m.cosine([0, 0], [1, 1]) == 0.0  # degenerate → 0
    assert round(m.cosine([1, 1], [1, 0]), 4) == round(1 / math.sqrt(2), 4)


def test_tokenize_alpha_lower():
    assert m.tokenize("Le Chat, 42 fois!") == ["le", "chat", "fois"]


def test_ttr():
    assert m.ttr(["le chat le chien"]) == 0.75  # {le,chat,chien}=3 / 4
    assert m.ttr([""]) is None
    assert m.ttr([]) is None


def test_self_ref():
    assert m.self_ref("je pense donc je suis, moi") == 3   # je, je, moi
    assert m.self_ref("le ciel est bleu") == 0


def test_mood_entropy():
    assert m.mood_entropy(["a", "a"]) == 0.0                # one category
    assert m.mood_entropy(["a", "b"]) == 1.0               # two equiprobable
    assert m.mood_entropy([]) is None
    # None is its own category, not skipped.
    assert m.mood_entropy([None, None]) == 0.0
    assert m.mood_entropy(["a", None]) == 1.0


def test_action_mix():
    mix = m.action_mix(["thought", "thought", "idle"])
    assert mix["thought"] == 2 / 3
    assert mix["idle"] == 1 / 3
    assert m.action_mix([]) == {}


def test_loop_score():
    assert m.loop_score([1, 0], [[0, 1]]) == 0.0
    assert m.loop_score([1, 0], [[0, 1], [1, 0]]) == 1.0  # max over previous
    assert m.loop_score([1, 0], []) is None                # no history
    assert m.loop_score(None, [[1, 0]]) is None            # no current embed


def test_persona_score_null_before_bootstrap():
    # No persona centroid yet → NULL.
    assert m.persona_score([1, 0], None, [0, 1]) is None
    # No current embedding → NULL.
    assert m.persona_score(None, [1, 0], [0, 1]) is None
    # With both: cos(e,persona) − cos(e,chatbot).
    val = m.persona_score([1, 0], [1, 0], [0, 1])
    assert val == 1.0  # 1 − 0


def test_persona_centroid_bootstrap_then_frozen():
    pc = m.PersonaCentroid(bootstrap_n=3)
    assert pc.value is None
    pc.observe([1.0, 0.0])
    pc.observe([0.0, 1.0])
    assert pc.value is None                # not yet 3
    pc.observe([1.0, 1.0])
    assert pc.value is not None            # frozen at 3
    frozen = pc.value.copy()
    pc.observe([9.0, 9.0])                 # ignored after freeze
    assert (pc.value == frozen).all()


def test_lexical_overlap():
    assert m.lexical_overlap("le chat noir", "un chat blanc") == 0.2  # {chat}/{5}
    assert m.lexical_overlap("", "chat") == 0.0
    assert m.lexical_overlap("chat chien", "chat chien") == 1.0


def test_extract_mood():
    assert extract_mood("je suis las [fatigué].") == "fatigué"
    assert extract_mood("une pensée sans humeur") is None
    assert extract_mood("[calme]") == "calme"
    # A bracket group not at the end is not the mood.
    assert extract_mood("[note] du texte après") is None


def test_blob_roundtrip():
    vec = [0.5, -1.25, 3.0]
    back = m.from_blob(m.to_blob(vec))
    assert [round(float(x), 4) for x in back] == vec
