"""Pure helpers: the returning-client identity key (S1-26c).

The returning-client check keys on a normalized identity derived from the
business name + postcode, replacing the old `email` match. Email is an
unreliable identity: franchises share similar emails but are DIFFERENT
clients, and one business signs with SEVERAL emails over time.

    identity_key = first6(normalize_name(business_name)) + "|" + normalize_postcode(postcode)

The truncation to the first 6 characters is deliberately lenient (so minor
tail differences after normalization still collapse together). Because it is
lenient, two genuinely different businesses that share a 6-char name prefix
AND a postcode (e.g. "Fitness First" vs "Fitness Studio" at the same postcode)
can collide on the key. The caller therefore pairs an `identity_key` match
with `names_materially_diverge` on the FULL normalized names: a clean match
links the returning client, a divergent one is flagged for a human rather
than auto-merged.

Fail-safe to CREATE: `compute_identity_key` returns None whenever it lacks a
usable signal (no normalized business name, or no postcode). A None key makes
the returning-client match self-skip, so an unidentifiable signing becomes a
fresh client rather than being merged into the wrong one.

PURE: no I/O, no DB, no Inngest. Unit-tested against synthetic strings.
"""

from __future__ import annotations

import re

# A leading article carries no identity ("The Gym Group" == "Gym Group") and,
# left in, would eat the 6-char budget ("the gy"). Dropped as a leading word.
_LEADING_ARTICLES = frozenset({"the"})

# Trailing company-type suffixes are noise for identity ("Foobar Ltd" ==
# "Foobar Limited" == "Foobar"). Dropped as a trailing word.
_TRAILING_SUFFIXES = frozenset({"ltd", "limited"})

_NAME_PREFIX_LEN = 6
_KEY_SEPARATOR = "|"

# Tokenize on runs of alphanumerics, so punctuation and whitespace both split
# ("ltd." -> "ltd", "F45  Training" -> "f45", "training").
_ALNUM_TOKEN = re.compile(r"[a-z0-9]+")
_NON_ALNUM = re.compile(r"[^A-Z0-9]+")


def normalize_name(name: str | None) -> str:
    """Normalize a business name to a compact alphanumeric identity stem.

    lowercase -> tokenize on non-alphanumerics -> drop a leading article
    ("the") -> drop a trailing company suffix ("ltd"/"limited") -> join the
    remaining tokens with no separators. Returns "" when nothing usable
    remains (None, blank, or a name that is only an article/suffix).
    """
    if not name:
        return ""
    tokens = _ALNUM_TOKEN.findall(name.lower())
    if tokens and tokens[0] in _LEADING_ARTICLES:
        tokens = tokens[1:]
    if tokens and tokens[-1] in _TRAILING_SUFFIXES:
        tokens = tokens[:-1]
    return "".join(tokens)


def normalize_postcode(postcode: str | None) -> str:
    """Normalize a postcode: uppercase, strip everything non-alphanumeric.

    "E8 1AA" -> "E81AA". Returns "" for None / blank / punctuation-only.
    """
    if not postcode:
        return ""
    return _NON_ALNUM.sub("", postcode.upper())


def compute_identity_key(business_name: str | None, postcode: str | None) -> str | None:
    """Return `first6(normalize_name) + "|" + normalize_postcode`, or None.

    Returns None (match self-skips, fail-safe to CREATE) when the business
    name yields no normalized stem OR the postcode is missing/blank - we do
    not match on a name alone, because a no-postcode name prefix is far too
    collision-prone to safely unite clients on.
    """
    name_norm = normalize_name(business_name)
    if not name_norm:
        return None
    postcode_norm = normalize_postcode(postcode)
    if not postcode_norm:
        return None
    return name_norm[:_NAME_PREFIX_LEN] + _KEY_SEPARATOR + postcode_norm


def names_materially_diverge(name_a: str | None, name_b: str | None) -> bool:
    """True when two names normalize to DIFFERENT full stems.

    Used to distinguish a genuine returning-client match (same normalized
    name -> link) from a lenient-prefix collision on the identity key
    (different normalized names that merely share the first 6 chars + a
    postcode -> flag possible duplicate, do not merge).
    """
    return normalize_name(name_a) != normalize_name(name_b)


__all__ = [
    "compute_identity_key",
    "names_materially_diverge",
    "normalize_name",
    "normalize_postcode",
]
