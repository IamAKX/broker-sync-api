"""Tamil script -> "Tanglish": the same Tamil words typed in English letters.

Rule-based and deterministic, so a Tanglish answer says exactly what the Tamil
one says (the model writing Tanglish directly picked wrong words; its Tamil-script
translation is good). Spelling follows how people type Tamil on a phone rather
than an academic scheme: no diacritics, and the hard consonants are written the
way they sound in their position - e.g. "பங்குகள்" -> "pangugal",
"விழுந்த" -> "vizhundha", "அன்று" -> "andru".

Anything that is not Tamil script (stock symbols, numbers, Markdown) passes
through untouched.
"""

from __future__ import annotations

_PULLI = "்"
_INDEPENDENT = {
    "அ": "a", "ஆ": "aa", "இ": "i", "ஈ": "ee", "உ": "u", "ஊ": "oo",
    "எ": "e", "ஏ": "e", "ஐ": "ai", "ஒ": "o", "ஓ": "o", "ஔ": "au",
}
_SIGNS = {
    "ா": "aa", "ி": "i", "ீ": "ee", "ு": "u", "ூ": "oo",
    "ெ": "e", "ே": "e", "ை": "ai", "ொ": "o", "ோ": "o", "ௌ": "au",
}
# Consonants that always read the same.
_PLAIN = {
    "ங": "ng", "ஞ": "ny", "ண": "n", "ந": "n", "ன": "n", "ம": "m", "ய": "y", "ர": "r",
    "ல": "l", "வ": "v", "ழ": "zh", "ள": "l", "ஜ": "j", "ஷ": "sh", "ஸ": "s", "ஹ": "h",
}
_HARD = "கசடதபற"
_NASALS = "ஙஞணநமன"
_CONSONANTS = set(_PLAIN) | set(_HARD)


def _is_tamil(ch: str) -> bool:
    return "஀" <= ch <= "௿"


def _tokens(word: str) -> list[tuple[str, str | None]]:
    """[(consonant or "", vowel or None)] - vowel None is a consonant with no vowel (pulli)."""
    out: list[tuple[str, str | None]] = []
    i = 0
    while i < len(word):
        ch = word[i]
        nxt = word[i + 1] if i + 1 < len(word) else ""
        if ch in _INDEPENDENT:
            out.append(("", _INDEPENDENT[ch]))
        elif ch in _CONSONANTS:
            if nxt == _PULLI:
                out.append((ch, None))
                i += 1
            elif nxt in _SIGNS:
                out.append((ch, _SIGNS[nxt]))
                i += 1
            else:
                out.append((ch, "a"))
        # anything else in the Tamil block (aytham, stray signs, Tamil numerals) is dropped
        i += 1
    return out


def _consonant(c: str, dead: bool, prev: tuple[str, str | None] | None, nxt_c: str) -> str:
    """Latin letters for consonant *c*. *prev* is the token before it (None at the
    start of a word); *nxt_c* is the consonant after it when *c* has no vowel."""
    if c in _PLAIN:
        if dead and c == "ங" and nxt_c == "க":
            return "n"            # ங்க -> "ng"
        if dead and c == "ஞ" and nxt_c == "ச":
            return "n"            # ஞ்ச -> "nj"
        return _PLAIN[c]
    after_vowel = prev is not None and prev[1] is not None
    prev_dead = prev[0] if prev is not None and prev[1] is None else ""
    after_nasal = prev_dead in _NASALS and prev_dead != ""
    if c == "க":
        return "g" if (after_vowel or after_nasal) and not dead else "k"
    if c == "ச":
        if dead:
            return "c" if nxt_c == "ச" else "ch"   # ச்ச -> "cch"
        if prev_dead == "ஞ":
            return "j"
        if prev_dead in ("ச", "ட", "ற"):
            return "ch"                            # காட்சி -> kaatchi, முயற்சி -> muyarchi
        return "s"
    if c == "ட":
        return "d" if (after_vowel or after_nasal) and not dead else "t"
    if c == "த":
        if dead:
            return "t" if nxt_c == "த" else "th"   # த்த -> "tth"
        return "dh" if after_vowel or after_nasal else "th"
    if c == "ப":
        return "b" if (after_vowel or after_nasal) and not dead else "p"
    # ற
    if dead:
        return "t" if nxt_c == "ற" else "r"        # ற்ற -> "tr"
    return "dr" if prev_dead == "ன" else "r"       # ன்ற -> "ndr"


def _word(word: str) -> str:
    tokens = _tokens(word)
    out = []
    for i, (c, vowel) in enumerate(tokens):
        if c:
            nxt_c = tokens[i + 1][0] if vowel is None and i + 1 < len(tokens) else ""
            out.append(_consonant(c, vowel is None, tokens[i - 1] if i else None, nxt_c))
        if vowel:
            out.append(vowel)
    return "".join(out)


def to_tanglish(text: str) -> str:
    out, run = [], []
    for ch in text:
        if _is_tamil(ch):
            run.append(ch)
            continue
        if run:
            out.append(_word("".join(run)))
            run = []
        out.append(ch)
    if run:
        out.append(_word("".join(run)))
    return "".join(out)
