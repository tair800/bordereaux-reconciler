"""Deterministic randomness, in one place, for one purpose.

Two rules, and the whole reproducibility claim rests on both of them.

**Never the module-level `random` functions.** Those share one global generator, so anything else in
the process that draws from it — a library, a test, a future caller — silently shifts every value
this corpus produces. Every draw here comes from a :class:`random.Random` instance this object owns
and nothing else can reach.

**A stream is seeded from what it is for, not from a counter.** ``FixtureRandom(seed, variant_id)``
hashes its parts into the seed, so inserting a thirteenth variant between the fourth and the fifth
leaves the other seventeen byte-identical. A ``seed + index`` scheme would re-roll the entire corpus
whenever the catalogue grew, and a corpus that changes wholesale on an unrelated edit is one nobody
will regenerate — which would quietly end the reproducibility claim while appearing to keep it.
"""

from __future__ import annotations

import hashlib
import random
from collections.abc import Sequence
from decimal import Decimal

__all__ = ["FixtureRandom"]


class FixtureRandom:
    """A named, reproducible stream of draws for synthetic fixture data.

    Every method carries a ``noqa: S311`` because the linter is right in general and wrong here:
    these draws decide what a fake premium is, never what a token or a key is. There is no
    cryptographic use anywhere in this package, and reaching for :mod:`secrets` would make the
    corpus unreproducible, which is the one property it cannot lose.
    """

    __slots__ = ("_rng", "name")

    def __init__(self, *parts: str | int) -> None:
        self.name = "|".join(str(part) for part in parts)
        digest = hashlib.blake2b(self.name.encode("utf-8"), digest_size=8).digest()
        self._rng = random.Random(int.from_bytes(digest, "big"))

    def integer(self, low: int, high: int) -> int:
        """An integer in ``[low, high]``."""
        return self._rng.randint(low, high)

    def cents(self, low: int, high: int) -> Decimal:
        """An exact 2dp amount, drawn in minor units so no float ever exists.

        Drawing the minor unit and scaling by a power of ten is exact; drawing a float and rounding
        it would introduce the representation error this project bans, in the very data it is meant
        to prove the ban against.
        """
        return Decimal(self.integer(low, high)).scaleb(-2)

    def choice[T](self, options: Sequence[T]) -> T:
        return self._rng.choice(options)

    def sample[T](self, population: Sequence[T], count: int) -> list[T]:
        return self._rng.sample(population, count)

    def chance(self, numerator: int, denominator: int) -> bool:
        """True ``numerator`` times in ``denominator``, without touching a float."""
        return self.integer(1, denominator) <= numerator
