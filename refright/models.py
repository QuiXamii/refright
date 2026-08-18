"""Data models for refright."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum


class Severity(IntEnum):
    OK = 0
    INFO = 1
    WARNING = 2
    ERROR = 3


@dataclass
class FieldDiff:
    """One piece of evidence: bib value vs reference-database value."""
    field: str
    bib_value: str
    ref_value: str
    source: str          # e.g. "crossref", "arxiv", "dblp"
    url: str = ""        # one-click verification link


@dataclass
class Finding:
    severity: Severity
    code: str            # machine-readable, e.g. "doi-unresolvable"
    message: str         # human-readable summary (zh)
    evidence: list[FieldDiff] = field(default_factory=list)
    suggestion: str = ""  # extra prose, e.g. reverse-lookup record summary
    fix: FieldDiff | None = None  # actionable correction: bib_value (wrong) -> ref_value (suggested)


@dataclass
class EntryResult:
    key: str
    entry_type: str
    title: str
    findings: list[Finding] = field(default_factory=list)
    checked_by: list[str] = field(default_factory=list)  # which sources were queried

    @property
    def severity(self) -> Severity:
        return max((f.severity for f in self.findings), default=Severity.OK)
