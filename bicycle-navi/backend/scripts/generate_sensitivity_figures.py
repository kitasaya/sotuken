"""凍結点の点データから感度分析CSV・発表用PNGを再生成する。

入力明細が現行ツリーから削減済みの場合は、measurement-freeze-20260828
tagの同一パスを読む。判定ロジックは呼び出さず、凍結済みの点別明細だけを
集計する。
"""

from __future__ import annotations

import csv
import io
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


SCRIPT_PATH = Path(__file__).resolve()
BACKEND_DIR = SCRIPT_PATH.parent.parent
DATA_DIR = BACKEND_DIR / "data"
REPO_ROOT = SCRIPT_PATH.parents[3]
FREEZE_TAG = "measurement-freeze-20260828"

DECISION_INPUT = DATA_DIR / "decision_ambiguity_points.csv"
MARGIN_INPUT = DATA_DIR / "verify_match_margin_points.csv"
DECISION_CSV = DATA_DIR / "decision_ambiguity_sensitivity.csv"
DECISION_PNG = DATA_DIR / "decision_ambiguity_sensitivity.png"
MARGIN_CSV = DATA_DIR / "margin_threshold_sensitivity.csv"
MARGIN_PNG = DATA_DIR / "margin_threshold_sensitivity.png"

THRESHOLDS = [0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 7.5, 10.0]
SELECTED_THRESHOLDS = {1.5, 2.0, 2.5}


def _tag_relative(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def read_frozen_csv(path: Path) -> list[dict[str, str]]:
    if path.exists():
        text = path.read_text(encoding="utf-8-sig")
    else:
        result = subprocess.run(
            ["git", "show", f"{FREEZE_TAG}:{_tag_relative(path)}"],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
        )
        text = result.stdout.decode("utf-8-sig")
    return list(csv.DictReader(io.StringIO(text)))


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def as_bool(value: str) -> bool:
    return value.strip().lower() == "true"


def pct(count: int, denominator: int) -> float:
    return round(100.0 * count / denominator, 2)


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    # Windows 候補を先に並べる。既存PNGはこの系統で生成されているため、
    # Windows 上での描画結果を変えないよう順序は維持する。
    # macOS 候補は Windows 候補がすべて不在のときだけ使われる。
    candidates = [
        Path("C:/Windows/Fonts/YuGothB.ttc" if bold else "C:/Windows/Fonts/YuGothM.ttc"),
        Path("C:/Windows/Fonts/meiryob.ttc" if bold else "C:/Windows/Fonts/meiryo.ttc"),
        Path("C:/Windows/Fonts/msgothic.ttc"),
        Path("/System/Library/Fonts/ヒラギノ角ゴシック W6.ttc" if bold
             else "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc"),
        Path("/System/Library/Fonts/Hiragino Sans GB.ttc"),
        Path("/Library/Fonts/Arial Unicode.ttf"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return ImageFont.truetype(str(candidate), size=size)
    raise RuntimeError("日本語フォントが見つかりません")


def draw_chart(
    path: Path,
    rows: list[dict[str, object]],
    title: str,
    subtitle: str,
    series: list[tuple[str, str, str]],
) -> None:
    scale = 2
    width, height = 1200 * scale, 760 * scale
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    left, right, top, bottom = 145 * scale, 65 * scale, 145 * scale, 120 * scale
    plot_width = width - left - right
    plot_height = height - top - bottom

    def xy(x_value: float, y_value: float) -> tuple[float, float]:
        x = left + (x_value - 0.5) / 9.5 * plot_width
        y = top + (100.0 - y_value) / 100.0 * plot_height
        return x, y

    draw.text(
        (width / 2, 40 * scale),
        title,
        fill="#172B4D",
        font=font(28 * scale, True),
        anchor="mm",
    )
    draw.text(
        (width / 2, 83 * scale),
        subtitle,
        fill="#52606D",
        font=font(16 * scale),
        anchor="mm",
    )

    for y_tick in range(0, 101, 10):
        x1, y = xy(0.5, y_tick)
        x2, _ = xy(10.0, y_tick)
        draw.line((x1, y, x2, y), fill="#D9E2EC", width=2 * scale)
        draw.text(
            (left - 18 * scale, y),
            f"{y_tick}%",
            fill="#52606D",
            font=font(13 * scale),
            anchor="rm",
        )

    for threshold in THRESHOLDS:
        x, bottom_y = xy(threshold, 0)
        _, top_y = xy(threshold, 100)
        if threshold in SELECTED_THRESHOLDS:
            draw.line((x, top_y, x, bottom_y), fill="#CBD5E1", width=2 * scale)
        draw.text(
            (x, top + plot_height + 18 * scale),
            f"{threshold:g}",
            fill="#52606D",
            font=font(12 * scale),
            anchor="ma",
        )

    draw.line((left, top, left, top + plot_height), fill="#334E68", width=3 * scale)
    draw.line(
        (left, top + plot_height, left + plot_width, top + plot_height),
        fill="#334E68",
        width=3 * scale,
    )
    draw.text(
        (left + plot_width / 2, height - 40 * scale),
        "マージン閾値（m、margin < 閾値）",
        fill="#334E68",
        font=font(17 * scale),
        anchor="mm",
    )
    draw.text(
        (left, top - 24 * scale),
        "該当率（%）",
        fill="#334E68",
        font=font(16 * scale),
        anchor="lm",
    )

    for label, key, color in series:
        points = [xy(float(row["threshold_m"]), float(row[key])) for row in rows]
        draw.line(points, fill=color, width=5 * scale, joint="curve")
        for row, (x, y) in zip(rows, points):
            radius = (7 if float(row["threshold_m"]) in SELECTED_THRESHOLDS else 5) * scale
            draw.ellipse(
                (x - radius, y - radius, x + radius, y + radius),
                fill=color,
                outline="white",
                width=2 * scale,
            )

    legend_x = left + 34 * scale
    legend_y = top + 32 * scale
    for index, (label, _, color) in enumerate(series):
        y = legend_y + index * 34 * scale
        draw.line((legend_x, y, legend_x + 45 * scale, y), fill=color, width=5 * scale)
        draw.text(
            (legend_x + 58 * scale, y),
            label,
            fill="#243B53",
            font=font(14 * scale),
            anchor="lm",
        )

    draw.text(
        (width - right, height - 16 * scale),
        f"出典: {FREEZE_TAG}",
        fill="#7B8794",
        font=font(10 * scale),
        anchor="rs",
    )
    image.resize((1200, 760), Image.Resampling.LANCZOS).save(path, optimize=True)


def main() -> int:
    decision_points = read_frozen_csv(DECISION_INPUT)
    margin_points = read_frozen_csv(MARGIN_INPUT)
    if len(decision_points) != 379 or len(margin_points) != 379:
        raise RuntimeError(
            f"凍結母集団は379点である必要があります: "
            f"decision={len(decision_points)}, margin={len(margin_points)}"
        )

    current_oneway = [row for row in margin_points if as_bool(row["oneway_violation"])]
    if len(current_oneway) != 12:
        raise RuntimeError(f"現行oneway検出点は12点である必要があります: {len(current_oneway)}")

    decision_rows: list[dict[str, object]] = []
    margin_rows: list[dict[str, object]] = []
    for threshold in THRESHOLDS:
        geometric = sum(float(row["margin_m"]) < threshold for row in decision_points)
        decision_impact = sum(
            float(row["margin_m"]) < threshold and as_bool(row["decision_tuple_diff"])
            for row in decision_points
        )
        violation_flip = sum(
            float(row["margin_m"]) < threshold and as_bool(row["violation_flip"])
            for row in decision_points
        )
        current_unavailable = sum(
            float(row["match_margin_m"]) < threshold for row in current_oneway
        )
        decision_rows.append({
            "threshold_m": threshold,
            "denominator_points": 379,
            "geometric_count": geometric,
            "geometric_pct": pct(geometric, 379),
            "decision_impact_count": decision_impact,
            "decision_impact_pct": pct(decision_impact, 379),
            "violation_flip_count": violation_flip,
            "violation_flip_pct": pct(violation_flip, 379),
            "source": f"{FREEZE_TAG}:decision_ambiguity_points.csv",
        })
        margin_rows.append({
            "threshold_m": threshold,
            "all_points_denominator": 379,
            "all_points_below_count": geometric,
            "all_points_below_pct": pct(geometric, 379),
            "current_oneway_denominator": 12,
            "current_oneway_unavailable_count": current_unavailable,
            "current_oneway_unavailable_pct": pct(current_unavailable, 12),
            "source": f"{FREEZE_TAG}:verify_match_margin_points.csv",
        })

    selected_decision = {float(row["threshold_m"]): row for row in decision_rows}
    selected_margin = {float(row["threshold_m"]): row for row in margin_rows}
    expected = {
        1.5: (156, 0),
        2.0: (188, 3),
        2.5: (216, 4),
    }
    for threshold, (all_count, oneway_count) in expected.items():
        if selected_decision[threshold]["geometric_count"] != all_count:
            raise RuntimeError(f"全379点の凍結値不一致: {threshold}m")
        if selected_margin[threshold]["current_oneway_unavailable_count"] != oneway_count:
            raise RuntimeError(f"現行12点の凍結値不一致: {threshold}m")
    if (
        selected_decision[2.0]["decision_impact_count"],
        selected_decision[2.0]["violation_flip_count"],
    ) != (150, 12):
        raise RuntimeError("2.0mの3段階指標が凍結値188/150/12と一致しません")

    write_csv(DECISION_CSV, decision_rows)
    write_csv(MARGIN_CSV, margin_rows)
    draw_chart(
        DECISION_PNG,
        decision_rows,
        "閾値別の曖昧性（全379判定点）",
        "幾何条件・判定内容の変化・違反有無の反転を区別",
        [
            ("幾何的曖昧", "geometric_pct", "#D64545"),
            ("判定影響曖昧", "decision_impact_pct", "#6B46C1"),
            ("違反有無反転", "violation_flip_pct", "#147D92"),
        ],
    )
    draw_chart(
        MARGIN_PNG,
        margin_rows,
        "マージン閾値による判定不能率",
        "全判定点（n=379）と現行oneway検出点（n=12）は分母が異なる",
        [
            ("全判定点（n=379）", "all_points_below_pct", "#D64545"),
            ("現行oneway検出点（n=12）", "current_oneway_unavailable_pct", "#147D92"),
        ],
    )
    print(f"wrote {DECISION_CSV}")
    print(f"wrote {DECISION_PNG}")
    print(f"wrote {MARGIN_CSV}")
    print(f"wrote {MARGIN_PNG}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
