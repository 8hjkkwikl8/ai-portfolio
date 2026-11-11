"""生成 48 行确定性电商订单；不依赖随机数或第三方包。"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

FIELDS = ("order_id", "order_date", "category", "region", "channel", "quantity", "revenue")
CATEGORIES = ("厨房电器", "个护健康", "智能家居", "运动户外")
REGIONS = ("华东", "华南", "华北", "西南")
CHANNELS = ("天猫", "京东", "抖音", "线下门店")
BASE_PRICES = {"厨房电器": 699, "个护健康": 299, "智能家居": 599, "运动户外": 399}


def generate_rows() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for month_index in range(6):
        month = month_index + 1
        for slot in range(8):
            category = CATEGORIES[(month_index + slot) % len(CATEGORIES)]
            region = REGIONS[(2 * month_index + slot) % len(REGIONS)]
            channel = CHANNELS[(month_index + 2 * slot) % len(CHANNELS)]
            quantity = 10 + ((month_index * 11 + slot * 7) % 31)
            unit_price = BASE_PRICES[category] + (month_index % 3) * 20
            revenue = float(f"{quantity * unit_price + (slot % 3) * 99:.2f}")
            day = 1 + ((slot * 3 + month_index * 2) % 27)
            rows.append(
                {
                    "order_id": f"SO-2025{month:02d}-{slot + 1:03d}",
                    "order_date": f"2025-{month:02d}-{day:02d}",
                    "category": category,
                    "region": region,
                    "channel": channel,
                    "quantity": quantity,
                    "revenue": revenue,
                }
            )
    return rows


def write_csv(destination: str | Path) -> Path:
    path = Path(destination).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(generate_rows())
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description="生成 DataChat BI 确定性订单 CSV")
    parser.add_argument("--output", type=Path, default=Path(__file__).with_name("orders.csv"))
    args = parser.parse_args()
    path = write_csv(args.output)
    print(f"已生成 48 行订单：{path}")


if __name__ == "__main__":
    main()
