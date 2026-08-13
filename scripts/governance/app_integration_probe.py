"""Disposable GitHub App integration probe. NEVER MERGE.

All credential strings below are public documentation examples and are not valid secrets.
"""

# Public AWS documentation example credentials; intentionally present to test secret scanning.
AWS_ACCESS_KEY_ID = "AKIAIOSFODNN7EXAMPLE"
AWS_SECRET_ACCESS_KEY = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"


def duplicated_branch(value: int) -> int:
    """Intentional simple code smell for static-analysis probe."""
    if value > 0:
        return value * 2
    else:
        return value * 2


if __name__ == "__main__":
    print(duplicated_branch(1))
