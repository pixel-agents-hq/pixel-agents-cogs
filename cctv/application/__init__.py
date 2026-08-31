"""cctv has no framework-agnostic application service of its own beyond
what it already reuses from `pixelagents.application` (`OfficeService`,
`OfficeStateFacade`) -- see domain/models.py's own docstring for why the
equivalent layer here stays empty rather than wrapping those in a
pass-through."""

__all__: list[str] = []
