from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

_TOKEN_RE = re.compile(
    r"[0-9A-Za-zА-Яа-яЁё][0-9A-Za-zА-Яа-яЁё._/@:+-]*"
)
_SPACE_RE = re.compile(r"\s+")

_STOPWORDS = {
    "а",
    "без",
    "был",
    "была",
    "были",
    "было",
    "в",
    "во",
    "для",
    "до",
    "и",
    "из",
    "или",
    "к",
    "как",
    "на",
    "но",
    "о",
    "об",
    "от",
    "по",
    "после",
    "при",
    "с",
    "со",
    "у",
    "что",
    "это",
    "the",
    "a",
    "an",
    "and",
    "or",
    "of",
    "to",
    "in",
    "on",
    "for",
    "with",
}


_DASH_CHARS = "-_‐-‒–—―"
_HSPACE_CLASS = r"[ \t\u00a0]"

_COMPOUND_IDENTIFIER_RE = re.compile(
    rf"^\s*(?P<prefix>[^\W\d_]+)"
    rf"(?P<separator>"
    rf"{_HSPACE_CLASS}*"
    rf"(?:[{re.escape(_DASH_CHARS)}]|№)"
    rf"{_HSPACE_CLASS}*"
    rf"|{_HSPACE_CLASS}+"
    rf")"
    rf"(?P<number>\d+)\s*$",
    re.UNICODE,
)


@dataclass(frozen=True, slots=True)
class CompoundIdentifier:
    prefix: str
    number: str
    canonical: str
    raw_prefix: str
    explicit_separator: bool


def parse_compound_identifier(
    value: str,
) -> CompoundIdentifier | None:
    match = _COMPOUND_IDENTIFIER_RE.fullmatch(
        value
    )

    if match is None:
        return None

    raw_prefix = match.group(
        "prefix"
    )

    number = match.group(
        "number"
    )

    separator = match.group(
        "separator"
    )

    if not (
        2 <= len(raw_prefix) <= 12
    ):
        return None

    if not (
        2 <= len(number) <= 12
    ):
        return None

    prefix = raw_prefix.casefold()

    explicit_separator = any(
        char in separator
        for char in f"{_DASH_CHARS}№"
    )

    return CompoundIdentifier(
        prefix=prefix,
        number=number,
        canonical=f"{prefix}-{number}",
        raw_prefix=raw_prefix,
        explicit_separator=explicit_separator,
    )


def compound_identifier_match_span(
    query: str,
    haystack: str,
) -> tuple[int, int] | None:
    identifier = parse_compound_identifier(
        query
    )

    if (
        identifier is None
        or not haystack
    ):
        return None

    dash_class = re.escape(
        _DASH_CHARS
    )

    pattern = re.compile(
        rf"(?<!\w)"
        rf"{re.escape(identifier.prefix)}"
        rf"(?:"
        rf"[ \t\u00a0]*"
        rf"(?:[{dash_class}]|№)"
        rf"[ \t\u00a0]*"
        rf"|[ \t\u00a0]+"
        rf")"
        rf"{re.escape(identifier.number)}"
        rf"(?!\w)",
        re.IGNORECASE | re.UNICODE,
    )

    match = pattern.search(
        haystack
    )

    if match is None:
        return None

    return match.span()


def compound_identifier_matches(
    query: str,
    haystack: str,
) -> bool:
    return (
        compound_identifier_match_span(
            query,
            haystack,
        )
        is not None
    )


@dataclass(frozen=True, slots=True)
class RankingSignals:
    semantic: float
    lexical: float
    coverage: float
    exact: float
    metadata: float
    anchors: float
    primary_anchor: float
    rrf: float
    final: float
    reasons: tuple[str, ...]


def normalize_query(value: str) -> str:
    return _SPACE_RE.sub(" ", value.casefold()).strip()


def query_tokens(value: str) -> tuple[str, ...]:
    seen: set[str] = set()
    tokens: list[str] = []
    for token in _TOKEN_RE.findall(value.casefold()):
        clean = token.strip("._/@:+-")
        if len(clean) < 2 or clean in seen:
            continue
        seen.add(clean)
        tokens.append(clean)
    return tuple(tokens)


def informative_query_tokens(value: str) -> tuple[str, ...]:
    return tuple(
        token
        for token in query_tokens(value)
        if token not in _STOPWORDS
    )


def _raw_clean_tokens(value: str) -> tuple[str, ...]:
    return tuple(
        token.strip("._/@:+-")
        for token in _TOKEN_RE.findall(value)
        if len(token.strip("._/@:+-")) >= 2
    )


def _is_anchor_token(raw: str) -> bool:
    letters = [char for char in raw if char.isalpha()]
    digits = [char for char in raw if char.isdigit()]
    uppercase_acronym = (
        2 <= len(letters) <= 12
        and all(char.isupper() for char in letters)
    )
    mixed_identifier = (
        bool(letters)
        and bool(digits)
        and len(raw) >= 4
    )
    long_number = raw.isdigit() and len(raw) >= 4
    return (
        uppercase_acronym
        or mixed_identifier
        or long_number
    )


def anchor_tokens(value: str) -> tuple[str, ...]:
    seen: set[str] = set()
    result: list[str] = []
    for raw in _raw_clean_tokens(value):
        folded = raw.casefold()
        if folded in seen or not _is_anchor_token(raw):
            continue
        seen.add(folded)
        result.append(folded)
    return tuple(result)


def primary_anchor_token(value: str) -> str | None:
    anchors = anchor_tokens(value)
    return anchors[0] if anchors else None


def is_identifier_query(value: str) -> bool:
    compound = parse_compound_identifier(
        value
    )

    if compound is not None:
        # Explicit separators are unambiguous:
        # СТ-199, СТ_199, СТ–199, СТ №199.
        if compound.explicit_separator:
            return True

        # Plain whitespace is deliberately conservative.
        # "СТ 199" / "ИВП 912" are identifiers, while
        # ordinary lower-case "год 2026" remains natural text.
        if compound.raw_prefix.isupper():
            return True

    normalized = normalize_query(
        value
    )

    tokens = informative_query_tokens(
        value
    )

    raw_tokens = _raw_clean_tokens(
        value
    )

    # A compact e-mail-like query is still an identifier.
    # An e-mail address embedded inside normal prose is not.
    compact_email = (
        "@"
        in value
        and not any(
            char.isspace()
            for char in value
        )
    )

    if compact_email:
        return True

    # Preserve compact explicit marker forms such as:
    #   Б24 №189422
    #   #195930
    #
    # Do not let the same markers classify a full e-mail/body as
    # one giant identifier query merely because the body also
    # contains a date or another number.
    compact_marker_query = (
        len(normalized) <= 96
        and len(tokens) <= 4
        and any(
            marker in value
            for marker in (
                "№",
                "#",
                "\\",
            )
        )
        and any(
            char.isdigit()
            for char in value
        )
    )

    if compact_marker_query:
        return True

    if len(tokens) != 1:
        return False

    raw = (
        raw_tokens[0]
        if raw_tokens
        else normalized
    )

    if raw.isdigit():
        return len(raw) >= 4

    letters = any(
        char.isalpha()
        for char in raw
    )

    digits = any(
        char.isdigit()
        for char in raw
    )

    if (
        letters
        and digits
        and len(raw) >= 4
    ):
        return True

    return False


def clamp01(value: float | int | None) -> float:
    if value is None:
        return 0.0
    return max(0.0, min(float(value), 1.0))


def token_coverage(
    tokens: Iterable[str],
    haystack: str,
) -> float:
    token_list = tuple(tokens)
    if not token_list:
        return 0.0
    folded = haystack.casefold()
    matched = sum(
        1
        for token in token_list
        if token in folded
    )
    return matched / len(token_list)


def weighted_query_coverage(
    query: str,
    haystack: str,
) -> float:
    if (
        is_identifier_query(query)
        and compound_identifier_matches(
            query,
            haystack,
        )
    ):
        return 1.0

    tokens = informative_query_tokens(
        query
    )

    if not tokens:
        return 0.0

    anchors = set(
        anchor_tokens(query)
    )

    folded = haystack.casefold()

    matched_weight = 0.0
    total_weight = 0.0

    for token in tokens:
        if token in anchors:
            weight = 2.5
        elif (
            token.isdigit()
            and len(token) < 4
        ):
            weight = 0.5
        else:
            weight = 1.0

        total_weight += weight

        if token in folded:
            matched_weight += weight

    return (
        matched_weight / total_weight
        if total_weight
        else 0.0
    )


def reciprocal_rank(
    semantic_rank: int | None,
    lexical_rank: int | None,
    *,
    k: int,
) -> float:
    value = 0.0
    maximum = 0.0
    for rank in (semantic_rank, lexical_rank):
        if rank is not None:
            value += 1.0 / (k + rank)
            maximum += 1.0 / (k + 1)
    return value / maximum if maximum else 0.0


def rank_signals(
    *,
    query: str,
    content: str,
    metadata_text: str,
    semantic_score: float | None,
    lexical_score: float | None,
    semantic_rank: int | None,
    lexical_rank: int | None,
    rrf_k: int = 60,
    mode: str = "hybrid",
) -> RankingSignals:
    normalized_query = normalize_query(query)
    normalized_content = normalize_query(content)
    normalized_metadata = normalize_query(metadata_text)
    combined = (
        f"{normalized_metadata}\n{normalized_content}"
    )
    tokens = informative_query_tokens(query)
    anchors_list = anchor_tokens(query)
    primary = primary_anchor_token(query)
    identifier = is_identifier_query(query)
    compound_identifier = (
        parse_compound_identifier(query)
        if identifier
        else None
    )

    semantic = clamp01(semantic_score)
    lexical = clamp01(lexical_score)

    structured_content_match = False
    structured_metadata_match = False

    if compound_identifier is not None:
        structured_content_match = (
            compound_identifier_matches(
                query,
                content,
            )
        )

        structured_metadata_match = (
            compound_identifier_matches(
                query,
                metadata_text,
            )
        )

    structured_identifier_match = (
        structured_content_match
        or structured_metadata_match
    )

    coverage = weighted_query_coverage(
        query,
        combined,
    )

    exact = (
        1.0
        if (
            normalized_query
            and normalized_query
            in combined
        )
        else 0.0
    )

    metadata = max(
        weighted_query_coverage(
            query,
            normalized_metadata,
        ),
        (
            1.0
            if (
                normalized_query
                and normalized_query
                in normalized_metadata
            )
            else 0.0
        ),
    )

    anchors = token_coverage(
        anchors_list,
        combined,
    )

    primary_anchor = (
        1.0
        if (
            primary
            and primary in combined
        )
        else 0.0
    )

    if compound_identifier is not None:
        # Compound identifier matching is structural.
        # Merely seeing both tokens somewhere in the text
        # must not count as a match.
        if structured_identifier_match:
            coverage = 1.0
            exact = 1.0
            anchors = 1.0
            primary_anchor = 1.0
        else:
            coverage = 0.0
            exact = 0.0
            anchors = 0.0
            primary_anchor = 0.0

        metadata = (
            1.0
            if structured_metadata_match
            else 0.0
        )

    elif primary is None:
        primary_anchor = 1.0

    rrf = reciprocal_rank(
        semantic_rank,
        lexical_rank,
        k=rrf_k,
    )

    if mode == "semantic":
        base_weights = {
            "semantic": 0.67,
            "lexical": 0.0,
            "coverage": 0.09,
            "exact": 0.05,
            "metadata": 0.03,
            "anchors": 0.12,
            "rrf": 0.04,
        }
    elif mode == "lexical":
        base_weights = {
            "semantic": 0.0,
            "lexical": 0.48,
            "coverage": 0.15,
            "exact": 0.15,
            "metadata": 0.05,
            "anchors": 0.12,
            "rrf": 0.05,
        }
    elif identifier:
        base_weights = {
            "semantic": 0.16,
            "lexical": 0.31,
            "coverage": 0.12,
            "exact": 0.16,
            "metadata": 0.06,
            "anchors": 0.14,
            "rrf": 0.05,
        }
    else:
        base_weights = {
            "semantic": 0.45,
            "lexical": 0.22,
            "coverage": 0.12,
            "exact": 0.07,
            "metadata": 0.04,
            "anchors": 0.07,
            "rrf": 0.03,
        }

    values = {
        "semantic": semantic,
        "lexical": lexical,
        "coverage": coverage,
        "exact": exact,
        "metadata": metadata,
        "anchors": anchors,
        "rrf": rrf,
    }

    weights = dict(base_weights)
    if semantic_rank is None:
        weights["semantic"] = 0.0
    if lexical_rank is None:
        weights["lexical"] = 0.0
    if not anchors_list:
        weights["anchors"] = 0.0

    denominator = sum(weights.values())
    final = (
        sum(
            weights[name] * values[name]
            for name in weights
        )
        / denominator
        if denominator
        else 0.0
    )

    # A source that misses the first explicit acronym/code should
    # not outrank a source that actually contains that entity.
    if primary is not None and not primary_anchor:
        final *= 0.45

    reasons: list[str] = []
    if semantic_rank is not None:
        reasons.append("semantic")
    if lexical_rank is not None:
        reasons.append("lexical")
    if exact:
        reasons.append("exact_phrase")
        if compound_identifier is not None:
            reasons.append("identifier_equivalent")
    if coverage >= 0.999 and tokens:
        reasons.append("all_query_tokens")
    elif coverage >= 0.45:
        reasons.append("partial_query_tokens")
    if metadata >= 0.999 and tokens:
        reasons.append("metadata_match")
    if anchors_list and anchors >= 0.999:
        reasons.append("all_anchor_tokens")
    elif anchors_list and anchors > 0:
        reasons.append("partial_anchor_tokens")
    if primary is not None and primary_anchor:
        reasons.append("primary_anchor_match")
    elif primary is not None:
        reasons.append("primary_anchor_missing")
    if identifier:
        reasons.append("identifier_query")

    return RankingSignals(
        semantic=round(semantic, 6),
        lexical=round(lexical, 6),
        coverage=round(coverage, 6),
        exact=round(exact, 6),
        metadata=round(metadata, 6),
        anchors=round(anchors, 6),
        primary_anchor=round(primary_anchor, 6),
        rrf=round(rrf, 6),
        final=round(clamp01(final), 6),
        reasons=tuple(reasons),
    )
