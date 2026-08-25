"""研究実験だけに適用する、再現性設定の読み込み。"""

import json
from pathlib import Path

from services.overpass import set_overpass_snapshot_date


SETTINGS_PATH = Path(__file__).parent.parent / "data" / "experiment_settings.json"


def activate_experiment_overpass_date() -> dict:
    """GraphHopper と同じ日時を Overpass attic query に設定して返す。

    FastAPI の通常起動からは呼ばない。したがって実運用は環境変数
    OVERPASS_SNAPSHOT_DATE が未指定なら live OSM のままになる。
    """
    settings = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    graph_date = settings.get("graphhopper_data_date", "").strip()
    overpass_date = settings.get("overpass_snapshot_date", "").strip()
    if not graph_date or not overpass_date:
        raise ValueError(f"基準日時が未設定です: {SETTINGS_PATH}")
    if graph_date != overpass_date:
        raise ValueError(
            "GraphHopper と Overpass の基準日時が一致しません: "
            f"graph={graph_date}, overpass={overpass_date}"
        )
    set_overpass_snapshot_date(overpass_date)
    return settings
