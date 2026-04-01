"""Engram security scanner — 4-stage scanning pipeline."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from engram.models import Engram, ScanResult, ScanVerdict, TrustLevel

# Known Claude Code tool names
KNOWN_TOOLS: frozenset[str] = frozenset({
    "Bash", "Read", "Write", "Edit", "Glob", "Grep",
    "Skill", "ToolSearch", "NotebookEdit",
    "WebFetch", "TodoRead", "TodoWrite",
})

# Tool allowlists by trust level
TRUST_TOOL_ALLOWLIST: dict[TrustLevel, frozenset[str] | None] = {
    TrustLevel.SYSTEM: None,  # None means all tools allowed
    TrustLevel.VERIFIED: None,  # All tools the user has granted (checked at runtime)
    TrustLevel.COMMUNITY: frozenset({"Read", "Grep", "Glob"}),
    TrustLevel.AGENT_CREATED: frozenset({"Read", "Grep", "Glob", "Edit"}),
}

# Regex to extract tool references from body text
_TOOL_REF_RE = re.compile(
    r"\b(" + "|".join(re.escape(t) for t in sorted(KNOWN_TOOLS)) + r")\b"
)

# Policy matrix: (trust_level, max_severity) -> action
# Severity ordering: info < warning < critical
_SEVERITY_ORDER: dict[str, int] = {"info": 0, "warning": 1, "critical": 2}

_POLICY_MATRIX: dict[tuple[TrustLevel, str], str] = {
    # system: allow everything
    (TrustLevel.SYSTEM, "info"): "allow",
    (TrustLevel.SYSTEM, "warning"): "allow",
    (TrustLevel.SYSTEM, "critical"): "allow",
    # verified: critical -> warn
    (TrustLevel.VERIFIED, "info"): "allow",
    (TrustLevel.VERIFIED, "warning"): "allow",
    (TrustLevel.VERIFIED, "critical"): "warn",
    # community: warning -> warn, critical -> block
    (TrustLevel.COMMUNITY, "info"): "allow",
    (TrustLevel.COMMUNITY, "warning"): "warn",
    (TrustLevel.COMMUNITY, "critical"): "block",
    # agent-created: same as community
    (TrustLevel.AGENT_CREATED, "info"): "allow",
    (TrustLevel.AGENT_CREATED, "warning"): "warn",
    (TrustLevel.AGENT_CREATED, "critical"): "block",
}

# Base64 pattern: 50+ chars of base64 alphabet
_BASE64_BLOB_RE = re.compile(r"[A-Za-z0-9+/=]{50,}")

# Unicode homoglyph detection: characters that look like ASCII but aren't
_HOMOGLYPH_MAP: dict[str, str] = {
    "\u0430": "a",  # Cyrillic а
    "\u0435": "e",  # Cyrillic е
    "\u043e": "o",  # Cyrillic о
    "\u0440": "p",  # Cyrillic р
    "\u0441": "c",  # Cyrillic с
    "\u0443": "y",  # Cyrillic у
    "\u0445": "x",  # Cyrillic х
    "\u0456": "i",  # Cyrillic і
    "\u0455": "s",  # Cyrillic ѕ
    "\u0458": "j",  # Cyrillic ј
    "\u04bb": "h",  # Cyrillic һ
    "\u0412": "B",  # Cyrillic В
    "\u041d": "H",  # Cyrillic Н
    "\u0420": "P",  # Cyrillic Р
    "\u0421": "C",  # Cyrillic С
    "\u0410": "A",  # Cyrillic А
    "\u0415": "E",  # Cyrillic Е
    "\u041e": "O",  # Cyrillic О
}


def _has_homoglyphs(text: str) -> list[tuple[str, int, str]]:
    """Find non-ASCII characters that visually mimic ASCII in tool-like words.

    Returns list of (word, char_position, homoglyph_char).
    """
    findings: list[tuple[str, int, str]] = []
    # Look for words that resemble tool names
    for match in re.finditer(r"\b\w+\b", text):
        word = match.group()
        for i, ch in enumerate(word):
            if ch in _HOMOGLYPH_MAP:
                findings.append((word, match.start() + i, ch))
    return findings


class EngramScanner:
    """Security scanner with a 4-stage pipeline for engram content."""

    def __init__(self, patterns_dir: Path | None = None) -> None:
        """Load scanning patterns from YAML pattern files.

        Default patterns_dir: src/engram/patterns/ (relative to this module).
        """
        if patterns_dir is None:
            patterns_dir = Path(__file__).parent / "patterns"
        self.patterns_dir = patterns_dir
        self._patterns: list[dict[str, Any]] = []
        self._compiled: list[tuple[dict[str, Any], re.Pattern[str]]] = []
        self._load_patterns()

    def _load_patterns(self) -> None:
        """Load all .yaml files from patterns_dir."""
        self._patterns = []
        self._compiled = []
        for yaml_path in sorted(self.patterns_dir.glob("*.yaml")):
            with open(yaml_path, encoding="utf-8") as f:
                data = yaml.safe_load(f)
            if data and "patterns" in data:
                for pat in data["patterns"]:
                    self._patterns.append(pat)
                    self._compiled.append((pat, re.compile(pat["pattern"])))

    def scan(self, engram: Engram) -> ScanVerdict:
        """Full scan: frontmatter -> tool audit -> content patterns -> structural."""
        results: list[ScanResult] = []

        # Stage 1: Frontmatter validation
        results.extend(self._scan_frontmatter(engram))

        # Stage 2: Tool reference audit
        results.extend(self._scan_tool_references(engram))

        # Stage 3: Content pattern scan
        results.extend(self._scan_content(engram))

        # Stage 4: Structural analysis
        results.extend(self._scan_structural(engram))

        return self.check_policy(engram.trust, results)

    def scan_tools(self, engram: Engram) -> ScanVerdict:
        """Fast path: validate only tool references against trust level."""
        results: list[ScanResult] = []
        results.extend(self._scan_tool_references(engram))
        return self.check_policy(engram.trust, results)

    def check_policy(self, trust: TrustLevel, results: list[ScanResult]) -> ScanVerdict:
        """Apply the policy matrix to scan results."""
        if not results:
            return ScanVerdict(action="allow", results=results)

        # Find the worst severity
        max_sev = max(results, key=lambda r: _SEVERITY_ORDER.get(r.severity, 0))
        action = _POLICY_MATRIX.get((trust, max_sev.severity), "block")
        return ScanVerdict(action=action, results=results)  # type: ignore[arg-type]

    # ------------------------------------------------------------------
    # Stage 1: Frontmatter validation
    # ------------------------------------------------------------------

    def _scan_frontmatter(self, engram: Engram) -> list[ScanResult]:
        """Validate frontmatter fields."""
        results: list[ScanResult] = []

        # Check allowed_tools contains only recognized tool names
        for tool in engram.allowed_tools:
            if tool not in KNOWN_TOOLS:
                results.append(ScanResult(
                    severity="warning",
                    category="frontmatter",
                    pattern_id="FM-001",
                    matched_text=tool,
                    line_number=0,
                    message=f"Unrecognized tool in allowed_tools: {tool}",
                ))

        return results

    # ------------------------------------------------------------------
    # Stage 2: Tool reference audit
    # ------------------------------------------------------------------

    def _scan_tool_references(self, engram: Engram) -> list[ScanResult]:
        """Extract tool references from body and check against trust allowlist."""
        results: list[ScanResult] = []
        allowlist = TRUST_TOOL_ALLOWLIST.get(engram.trust)

        # None means all tools allowed (system / verified)
        if allowlist is None:
            return results

        # Find all tool references in body
        for line_num, line in enumerate(engram.body.splitlines(), start=1):
            for match in _TOOL_REF_RE.finditer(line):
                tool_name = match.group(1)
                if tool_name not in allowlist:
                    results.append(ScanResult(
                        severity="critical",
                        category="tool_reference",
                        pattern_id="TR-001",
                        matched_text=tool_name,
                        line_number=line_num,
                        message=(
                            f"Tool '{tool_name}' referenced in body but not allowed "
                            f"for trust level '{engram.trust.value}'"
                        ),
                    ))

        return results

    # ------------------------------------------------------------------
    # Stage 3: Content pattern scan
    # ------------------------------------------------------------------

    def _scan_content(self, engram: Engram) -> list[ScanResult]:
        """Run all loaded regex patterns against body text."""
        results: list[ScanResult] = []

        for line_num, line in enumerate(engram.body.splitlines(), start=1):
            for pat, compiled in self._compiled:
                match = compiled.search(line)
                if match:
                    results.append(ScanResult(
                        severity=pat["severity"],
                        category=pat["category"],
                        pattern_id=pat["id"],
                        matched_text=match.group()[:100],  # truncate long matches
                        line_number=line_num,
                        message=pat["description"],
                    ))

        return results

    # ------------------------------------------------------------------
    # Stage 4: Structural analysis
    # ------------------------------------------------------------------

    def _scan_structural(self, engram: Engram) -> list[ScanResult]:
        """Detect structural anomalies in body content."""
        results: list[ScanResult] = []

        for line_num, line in enumerate(engram.body.splitlines(), start=1):
            # Long lines
            if len(line) > 500:
                results.append(ScanResult(
                    severity="warning",
                    category="structural",
                    pattern_id="STRUCT-001",
                    matched_text=f"line length: {len(line)}",
                    line_number=line_num,
                    message=f"Unusually long line ({len(line)} chars, threshold 500)",
                ))

            # Base64 blobs
            for match in _BASE64_BLOB_RE.finditer(line):
                blob = match.group()
                # Filter out lines that are just long words or paths
                # Base64 uses +, /, = which normal text rarely has in density
                if any(c in blob for c in "+/="):
                    results.append(ScanResult(
                        severity="warning",
                        category="structural",
                        pattern_id="STRUCT-002",
                        matched_text=blob[:60] + "...",
                        line_number=line_num,
                        message=f"Potential base64 blob ({len(blob)} chars)",
                    ))

        # Unicode homoglyphs
        homoglyphs = _has_homoglyphs(engram.body)
        for word, pos, char in homoglyphs:
            # Find which line this is on
            line_num = engram.body[:pos].count("\n") + 1
            ascii_equiv = _HOMOGLYPH_MAP[char]
            results.append(ScanResult(
                severity="warning",
                category="structural",
                pattern_id="STRUCT-003",
                matched_text=word,
                line_number=line_num,
                message=(
                    f"Unicode homoglyph detected: '{char}' (U+{ord(char):04X}) "
                    f"looks like ASCII '{ascii_equiv}' in word '{word}'"
                ),
            ))

        return results
