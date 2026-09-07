from __future__ import annotations

import json

from packages.knowledge.extraction import extraction_status


def main() -> None:
    print(json.dumps(extraction_status(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
