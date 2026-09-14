from __future__ import annotations

import re
from collections.abc import Collection, Mapping
from typing import Any


_REDACTED_PREFIX = "[REDACTED:"


_EXACT_SECRET_KEYS: dict[
    str,
    str,
] = {
    "password": "password",
    "passwd": "password",
    "pwd": "password",
    "passphrase": "password",
    "пароль": "password",
    "api_key": "api_key",
    "apikey": "api_key",
    "client_secret": "client_secret",
    "access_token": "access_token",
    "refresh_token": "refresh_token",
    "auth_token": "auth_token",
    "bearer_token": "bearer_token",
    "private_key": "private_key",
    "secret_access_key": "secret",
    "aws_secret_access_key": "secret",
    "secret": "secret",
    "token": "token",
}


_SECRET_SUFFIXES: tuple[
    tuple[
        str,
        str,
    ],
    ...,
] = (
    (
        "_password",
        "password",
    ),
    (
        "_passwd",
        "password",
    ),
    (
        "_pwd",
        "password",
    ),
    (
        "_passphrase",
        "password",
    ),
    (
        "_api_key",
        "api_key",
    ),
    (
        "_apikey",
        "api_key",
    ),
    (
        "_client_secret",
        "client_secret",
    ),
    (
        "_access_token",
        "access_token",
    ),
    (
        "_refresh_token",
        "refresh_token",
    ),
    (
        "_auth_token",
        "auth_token",
    ),
    (
        "_bearer_token",
        "bearer_token",
    ),
    (
        "_private_key",
        "private_key",
    ),
    (
        "_secret",
        "secret",
    ),
    (
        "_token",
        "token",
    ),
)


_LABEL_PATTERN = (
    r"(?:"
    r"пароль|"
    r"apiKey|"
    r"clientSecret|"
    r"secretAccessKey|"
    r"awsSecretAccessKey|"
    r"accessToken|"
    r"refreshToken|"
    r"authToken|"
    r"bearerToken|"
    r"privateKey|"
    r"databasePassword|"
    r"(?:"
    r"[0-9A-Za-zА-Яа-яЁё]+"
    r"[\s_-]+"
    r")*"
    r"(?:"
    r"password|passwd|pwd|passphrase|"
    r"api[\s_-]*key|apikey|"
    r"client[\s_-]*secret|"
    r"secret[\s_-]*access[\s_-]*key|"
    r"access[\s_-]*token|"
    r"refresh[\s_-]*token|"
    r"auth[\s_-]*token|"
    r"bearer[\s_-]*token|"
    r"private[\s_-]*key|"
    r"secret|token"
    r")"
    r")"
)


_PRIVATE_KEY_RE = re.compile(
    (
        r"-----BEGIN "
        r"(?P<label>"
        r"(?:"
        r"RSA |"
        r"EC |"
        r"DSA |"
        r"OPENSSH |"
        r"ENCRYPTED "
        r")?"
        r"PRIVATE KEY"
        r")-----"
        r".*?"
        r"-----END (?P=label)-----"
    ),
    flags=(
        re.IGNORECASE
        | re.DOTALL
    ),
)


_BEARER_RE = re.compile(
    (
        r"(?i)\bBearer\s+"
        r"[A-Za-z0-9._~+/=-]{8,}"
    )
)


_BASIC_RE = re.compile(
    (
        r"(?i)\bBasic\s+"
        r"[A-Za-z0-9+/=]{8,}"
    )
)


_URL_CREDENTIAL_RE = re.compile(
    (
        r"(?i)"
        r"(?P<prefix>"
        r"\b[a-z][a-z0-9+.-]*://"
        r"[^/\s:@]+:"
        r")"
        r"(?P<secret>[^@\s/]+)"
        r"(?=@)"
    )
)


_QUOTED_ASSIGNMENT_RE = re.compile(
    (
        rf"(?i)"
        rf"(?P<prefix>"
        rf"(?<!"
        rf"[0-9A-Za-zА-Яа-яЁё_]"
        rf")"
        rf"[\"']?"
        rf"{_LABEL_PATTERN}"
        rf"[\"']?"
        rf"\s*[:=]\s*"
        rf")"
        rf"(?P<quote>[\"'])"
        rf"(?P<secret>.*?)"
        rf"(?P=quote)"
    )
)


_BARE_ASSIGNMENT_RE = re.compile(
    (
        rf"(?i)"
        rf"(?P<prefix>"
        rf"(?<!"
        rf"[0-9A-Za-zА-Яа-яЁё_]"
        rf")"
        rf"{_LABEL_PATTERN}"
        rf"\s*[:=]\s*"
        rf")"
        rf"(?P<secret>"
        rf"[^\s,;}}\]\)\"\'\[]+"
        rf")"
    )
)


_JWT_RE = re.compile(
    (
        r"\beyJ"
        r"[A-Za-z0-9_-]{5,}"
        r"\."
        r"[A-Za-z0-9_-]{5,}"
        r"\."
        r"[A-Za-z0-9_-]{5,}"
        r"\b"
    )
)


_KNOWN_TOKEN_PATTERNS: tuple[
    tuple[
        re.Pattern[str],
        str,
    ],
    ...,
] = (
    (
        re.compile(
            r"\bsk-"
            r"[A-Za-z0-9_-]{12,}"
            r"\b"
        ),
        "api_key",
    ),
    (
        re.compile(
            r"\bsk_live_"
            r"[A-Za-z0-9_-]{12,}"
            r"\b"
        ),
        "api_key",
    ),
    (
        re.compile(
            r"\brk_live_"
            r"[A-Za-z0-9_-]{12,}"
            r"\b"
        ),
        "api_key",
    ),
    (
        re.compile(
            r"\bgithub_pat_"
            r"[A-Za-z0-9_]{20,}"
            r"\b"
        ),
        "api_key",
    ),
    (
        re.compile(
            r"\bgh[pousr]_"
            r"[A-Za-z0-9]{20,}"
            r"\b"
        ),
        "api_key",
    ),
    (
        re.compile(
            r"\bglpat-"
            r"[A-Za-z0-9_-]{16,}"
            r"\b"
        ),
        "access_token",
    ),
    (
        re.compile(
            r"\bhf_"
            r"[A-Za-z0-9]{20,}"
            r"\b"
        ),
        "access_token",
    ),
    (
        re.compile(
            r"\bxox[baprs]-"
            r"[A-Za-z0-9-]{10,}"
            r"\b"
        ),
        "access_token",
    ),
    (
        re.compile(
            r"\bAKIA"
            r"[0-9A-Z]{16}"
            r"\b"
        ),
        "access_key",
    ),
    (
        re.compile(
            r"\bAIza"
            r"[0-9A-Za-z_-]{25,}"
            r"\b"
        ),
        "api_key",
    ),
)


def _marker(
    kind: str,
) -> str:
    return (
        f"[REDACTED:{kind}]"
    )


def _normalize_key(
    value: Any,
) -> str:
    raw = str(
        value
    ).strip()

    # clientSecret -> client_Secret
    # databasePassword -> database_Password
    raw = re.sub(
        (
            r"(?<="
            r"[a-z0-9а-яё]"
            r")"
            r"(?="
            r"[A-ZА-ЯЁ]"
            r")"
        ),
        "_",
        raw,
    )

    # APIKey -> API_Key
    # AWSSecretAccessKey -> AWS_Secret_Access_Key
    raw = re.sub(
        (
            r"(?<="
            r"[A-ZА-ЯЁ]"
            r")"
            r"(?="
            r"[A-ZА-ЯЁ]"
            r"[a-zа-яё]"
            r")"
        ),
        "_",
        raw,
    )

    return re.sub(
        r"[^0-9a-zа-яё]+",
        "_",
        raw.casefold(),
    ).strip(
        "_"
    )


def _secret_kind_for_key(
    value: Any,
) -> str | None:
    normalized = (
        _normalize_key(
            value
        )
    )

    exact = (
        _EXACT_SECRET_KEYS.get(
            normalized
        )
    )

    if exact is not None:
        return exact

    for (
        suffix,
        kind,
    ) in _SECRET_SUFFIXES:
        if normalized.endswith(
            suffix
        ):
            return kind

    return None


def _assignment_kind(
    prefix: str,
) -> str:
    label = re.sub(
        r"[:=].*$",
        "",
        prefix,
    ).strip(
        "\"' "
    )

    normalized = (
        _normalize_key(
            label
        )
    )

    exact = (
        _EXACT_SECRET_KEYS.get(
            normalized
        )
    )

    if exact is not None:
        return exact

    for (
        suffix,
        kind,
    ) in _SECRET_SUFFIXES:
        if normalized.endswith(
            suffix
        ):
            return kind

    return "secret"


def redact_text(
    value: str,
) -> str:
    """
    Redact likely credentials from untrusted text.

    The original value is never hashed or included
    in a redaction marker.
    """

    if not value:
        return value

    result = value

    result = (
        _PRIVATE_KEY_RE.sub(
            _marker(
                "private_key"
            ),
            result,
        )
    )

    result = (
        _URL_CREDENTIAL_RE.sub(
            lambda match: (
                match.group(
                    "prefix"
                )
                + _marker(
                    "password"
                )
            ),
            result,
        )
    )

    result = (
        _BEARER_RE.sub(
            (
                "Bearer "
                + _marker(
                    "bearer_token"
                )
            ),
            result,
        )
    )

    result = (
        _BASIC_RE.sub(
            (
                "Basic "
                + _marker(
                    "basic_credential"
                )
            ),
            result,
        )
    )

    result = (
        _QUOTED_ASSIGNMENT_RE.sub(
            lambda match: (
                match.group(
                    "prefix"
                )
                + match.group(
                    "quote"
                )
                + _marker(
                    _assignment_kind(
                        match.group(
                            "prefix"
                        )
                    )
                )
                + match.group(
                    "quote"
                )
            ),
            result,
        )
    )

    result = (
        _BARE_ASSIGNMENT_RE.sub(
            lambda match: (
                match.group(
                    "prefix"
                )
                + _marker(
                    _assignment_kind(
                        match.group(
                            "prefix"
                        )
                    )
                )
            ),
            result,
        )
    )

    result = (
        _JWT_RE.sub(
            _marker(
                "jwt"
            ),
            result,
        )
    )

    for (
        pattern,
        kind,
    ) in (
        _KNOWN_TOKEN_PATTERNS
    ):
        result = pattern.sub(
            _marker(
                kind
            ),
            result,
        )

    return result


def redact_data(
    value: Any,
    *,
    allowed_keys: Collection[
        str
    ] = (),
) -> Any:
    """
    Return a recursively redacted copy.

    allowed_keys applies only to keys on the ROOT mapping.

    This is intentional: protocol capabilities such as the
    top-level MCP lease_token may need to cross one specific
    boundary, while an identically named nested/untrusted value
    must still be redacted.

    Audit logs and remote model packets should use the strict
    default with no allowed keys.
    """

    normalized_allowed = {
        _normalize_key(
            key
        )
        for key in allowed_keys
    }

    def walk(
        item: Any,
        *,
        depth: int,
    ) -> Any:

        if (
            item is None
            or isinstance(
                item,
                (
                    bool,
                    int,
                    float,
                ),
            )
        ):
            return item

        if isinstance(
            item,
            str,
        ):
            return redact_text(
                item
            )

        if isinstance(
            item,
            bytes,
        ):
            return redact_text(
                item.decode(
                    "utf-8",
                    errors="replace",
                )
            )

        if isinstance(
            item,
            Mapping,
        ):
            result: dict[
                Any,
                Any,
            ] = {}

            for (
                key,
                nested,
            ) in item.items():

                normalized_key = (
                    _normalize_key(
                        key
                    )
                )

                kind = (
                    _secret_kind_for_key(
                        key
                    )
                )

                root_allowed = (
                    depth == 0
                    and normalized_key
                    in normalized_allowed
                )

                if root_allowed:
                    # A root protocol capability is normally scalar.
                    # If a caller ever places structured data beneath
                    # an allowed key, recurse rather than creating a
                    # container-sized redaction bypass.
                    if isinstance(
                        nested,
                        (
                            Mapping,
                            list,
                            tuple,
                            set,
                        ),
                    ):
                        result[
                            key
                        ] = walk(
                            nested,
                            depth=depth + 1,
                        )
                    else:
                        result[
                            key
                        ] = nested

                elif (
                    kind is not None
                    and nested is not None
                ):
                    result[
                        key
                    ] = _marker(
                        kind
                    )

                else:
                    result[
                        key
                    ] = walk(
                        nested,
                        depth=depth + 1,
                    )

            return result

        if isinstance(
            item,
            tuple,
        ):
            return tuple(
                walk(
                    nested,
                    depth=depth + 1,
                )
                for nested in item
            )

        if isinstance(
            item,
            list,
        ):
            return [
                walk(
                    nested,
                    depth=depth + 1,
                )
                for nested in item
            ]

        if isinstance(
            item,
            set,
        ):
            return {
                walk(
                    nested,
                    depth=depth + 1,
                )
                for nested in item
            }

        return item

    return walk(
        value,
        depth=0,
    )


def contains_redaction(
    value: Any,
) -> bool:
    """
    Return True when a redaction marker
    exists anywhere in the value.
    """

    if isinstance(
        value,
        str,
    ):
        return (
            _REDACTED_PREFIX
            in value
        )

    if isinstance(
        value,
        Mapping,
    ):
        return any(
            contains_redaction(
                item
            )
            for item
            in value.values()
        )

    if isinstance(
        value,
        (
            list,
            tuple,
            set,
        ),
    ):
        return any(
            contains_redaction(
                item
            )
            for item in value
        )

    return False
