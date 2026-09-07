from __future__ import annotations

import json
from typing import Any

from apps.outlook_collector.outlook import (
    _folder_path,
    _safe_text,
    connect_outlook,
)


def list_stores(namespace: Any) -> list[dict]:
    result: list[dict] = []
    stores = namespace.Stores

    for index in range(1, stores.Count + 1):
        store = stores.Item(index)
        root = store.GetRootFolder()

        try:
            file_path = _safe_text(store.FilePath)
        except Exception:
            file_path = ""

        try:
            exchange_store_type = int(
                store.ExchangeStoreType
            )
        except Exception:
            exchange_store_type = None

        top_folders: list[str] = []
        for folder_index in range(
            1,
            root.Folders.Count + 1,
        ):
            folder = root.Folders.Item(folder_index)
            top_folders.append(_safe_text(folder.Name))

        result.append(
            {
                "index": index,
                "display_name": _safe_text(store.DisplayName),
                "store_id": _safe_text(
                    getattr(store, "StoreID", "")
                ),
                "root_folder": _folder_path(root),
                "file_path": file_path,
                "exchange_store_type": exchange_store_type,
                "top_folders": top_folders,
            }
        )
    return result


def main() -> None:
    _, namespace = connect_outlook()
    print(
        json.dumps(
            {"stores": list_stores(namespace)},
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
