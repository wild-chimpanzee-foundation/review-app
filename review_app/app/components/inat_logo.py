"""The official iNaturalist bird logomark, for use as a "view on iNaturalist" link.

Extracted from iNaturalist's `inaturalisticons` icon font (the `logomark` glyph,
units-per-em 512, ascent 480) and rendered as a standalone single-colour SVG in the
iNaturalist brand green. Used unmodified, purely as a link affordance — see
iNaturalist's logo policy: permitted as long as it does not imply endorsement.
"""

# iNaturalist brand green. Visible on both light and dark themes.
_INAT_GREEN = "#74AC00"

# Glyph path is in font coordinates (y-up), so the y-axis is flipped to SVG space.
_INAT_LOGO_PATH = (
    "M25 414c116-12 208-54 275-127l2-2c3-3 10-11 16-9 5 2 12 14 15 19l0 0c5 8 20 32 "
    "51 42 31 10 70-1 72-2l1 0c2-1 5-2 3-4-1-3-15-8-24-25-9-19-15-54-16-62-6-33-19-63"
    "-38-88-34-45-85-76-146-82-32-3-64 2-93 14 35 3 77 20 111 59 42 50-4 24-44 31-28 5"
    "-51 19-90 68 13 3 37 7 57 8-37 8-85 31-106 67 17 5 40 3 58 3-69 41-98 63-104 90z "
    "m163 44l0 0c7-3 66-29 94-68 41-55 43-108 32-96-26 29-51 47-80 67-23 16-38 61-46 "
    "97z m212-138c-6 0-10-5-9-10 0-6 5-10 11-9 5 0 9 5 9 10-1 6-6 10-11 9z"
)


def inat_logo_svg(size_px: int = 18) -> str:
    """Return inline SVG markup for the iNaturalist logomark at the given pixel size."""
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512" '
        f'width="{size_px}" height="{size_px}" fill="{_INAT_GREEN}">'
        f'<path transform="translate(0,480) scale(1,-1)" d="{_INAT_LOGO_PATH}"/></svg>'
    )
