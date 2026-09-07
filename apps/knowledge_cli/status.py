from __future__ import annotations

import json

from packages.knowledge.service import knowledge_status


def main() -> None:
    print(json.dumps(knowledge_status(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
