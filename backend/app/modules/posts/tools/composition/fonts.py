"""Resolve DesignSpec family tokens to real font faces.

A DesignSpec names families the agent invented (``brand-display``), so the
renderer needs an explicit map from those tokens to files it can actually load.
Anything else would silently redraw approved typography in a different face.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from PIL import ImageFont

from .schemas import CompositionError, CompositionFailure

#: The face Pillow embeds. Present in every worker regardless of host fonts,
#: which is what keeps rendering reproducible inside a slim container.
EMBEDDED_FAMILY = "embedded-sans"
EMBEDDED_WEIGHT = 400

_FONT_SUFFIXES = (".ttf", ".otf")


@dataclass(frozen=True, slots=True)
class FontFace:
    """A loaded face plus what the DesignSpec had actually asked for."""

    family: str
    weight: int
    requested_family: str
    requested_weight: int
    font: ImageFont.FreeTypeFont

    @property
    def substituted_family(self) -> bool:
        return self.family != self.requested_family

    @property
    def synthetic_bold(self) -> bool:
        return self.requested_weight - self.weight >= 200

    def describe(self) -> str:
        detail = f"{self.family} {self.weight}"
        if self.substituted_family:
            detail += f" substituted for {self.requested_family} {self.requested_weight}"
        elif self.weight != self.requested_weight:
            detail += f" standing in for weight {self.requested_weight}"
        if self.synthetic_bold:
            detail += " with synthetic bold"
        return detail


class FontLibrary:
    """Map family tokens to faces, either substituting or failing closed.

    With a fallback the library renders any token, recording the substitution on
    the component. Constructed with ``fallback_family=None`` it accepts only
    registered families, so a spec asking for a font nobody installed fails
    before a single pixel is drawn.
    """

    def __init__(
        self,
        faces: Mapping[str, Mapping[int, Path]] | None = None,
        *,
        fallback_family: str | None = EMBEDDED_FAMILY,
    ) -> None:
        self._faces = {
            family: dict(sorted(weights.items()))
            for family, weights in sorted((faces or {}).items())
        }
        if fallback_family is not None and not self._knows(fallback_family):
            raise ValueError(f"fallback family '{fallback_family}' is not registered")
        self._fallback_family = fallback_family
        self._cache: dict[tuple[str, int, int], ImageFont.FreeTypeFont] = {}

    @classmethod
    def from_directory(
        cls, directory: Path, *, fallback_family: str | None = EMBEDDED_FAMILY
    ) -> "FontLibrary":
        """Register ``<family>-<weight>.ttf`` files, weight defaulting to 400."""
        faces: dict[str, dict[int, Path]] = {}
        for path in sorted(directory.iterdir()):
            if path.suffix.lower() not in _FONT_SUFFIXES:
                continue
            family, _, tail = path.stem.rpartition("-")
            if family and tail.isdigit():
                weight = int(tail)
            else:
                family, weight = path.stem, 400
            faces.setdefault(family, {})[weight] = path
        return cls(faces, fallback_family=fallback_family)

    def families(self) -> frozenset[str]:
        return frozenset({*self._faces, EMBEDDED_FAMILY})

    def restricted_families(self) -> frozenset[str] | None:
        """Families a typography plan may use, or None when anything resolves."""
        return None if self._fallback_family is not None else self.families()

    def load(self, family: str, weight: int, size: int) -> FontFace:
        resolved = family if self._knows(family) else self._fallback_family
        if resolved is None:
            raise CompositionError(
                CompositionFailure.FONT_UNAVAILABLE,
                f"no font face is registered for family '{family}'",
            )
        actual_weight = self._nearest_weight(resolved, weight)
        return FontFace(
            family=resolved,
            weight=actual_weight,
            requested_family=family,
            requested_weight=weight,
            font=self._font(resolved, actual_weight, size),
        )

    def _knows(self, family: str) -> bool:
        return family == EMBEDDED_FAMILY or family in self._faces

    def _nearest_weight(self, family: str, weight: int) -> int:
        if family == EMBEDDED_FAMILY:
            return EMBEDDED_WEIGHT
        available = self._faces[family]
        if weight in available:
            return weight
        return min(available, key=lambda candidate: (abs(candidate - weight), -candidate))

    def _font(self, family: str, weight: int, size: int) -> ImageFont.FreeTypeFont:
        key = (family, weight, size)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        try:
            font = (
                ImageFont.load_default(size=size)
                if family == EMBEDDED_FAMILY
                else ImageFont.truetype(self._faces[family][weight], size=size)
            )
        except OSError as exc:
            raise CompositionError(
                CompositionFailure.FONT_UNAVAILABLE,
                f"font face '{family}' weight {weight} could not be loaded",
            ) from exc
        self._cache[key] = font
        return font


__all__ = ["EMBEDDED_FAMILY", "EMBEDDED_WEIGHT", "FontFace", "FontLibrary"]
