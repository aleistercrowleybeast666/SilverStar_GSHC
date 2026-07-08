from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from config import LOG_DIR


class JsonlLogger:
    def __init__(self) -> None:
        Path(LOG_DIR).mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        self.path = Path(LOG_DIR) / f'fc_log_{ts}.jsonl'
        self._fp = self.path.open('a', encoding='utf-8')

    def write(self, record: dict[str, Any]) -> None:
        self._fp.write(json.dumps(record, ensure_ascii=False) + '\n')
        self._fp.flush()

    def close(self) -> None:
        try:
            self._fp.close()
        except Exception:
            pass
