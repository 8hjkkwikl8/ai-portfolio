"""生成 48 行确定性电商订单及产品/渠道维度；不依赖随机数或第三方包。"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

FIELDS = ("order_id", "order_date", "category", "region", "channel", "quantity", "revenue")
PRODUCT_FIELDS = ("category", "product_line", "warranty_days")
CHANNEL_FIELDS = ("channel", "channel_type", "commission_rate")
CATEGORIES = ("厨房电器", "个护健康", "智能家居", "运动户外")
REGIONS = ("华东", "华南", "华北", "西南")
CHANNELS = ("天猫", "京东", "抖音", "线下门店")
BASE_PRICES = {"厨房电器": 699, "个护健康": 299, "智能家居": 599, "运动户外": 399}
PRODUCT_LINES = {
    "厨房电器": ("厨房烹饪", 365),
    "个护健康": ("个人护理", 90),
    "智能家居": ("智能家居", 180),
    "运动户外": ("运动健康", 180),
}
CHANNEL_TYPES = {
    "天猫": ("平台电商", 0.08),
    "京东": ("自营电商", 0.05),
    "抖音": ("内容电商", 0.12),
    "线下门店": ("线下零售", 0.0),
}


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


def generate_products() -> list[dict[str, object]]:
    return [
        {
            "category": category,
            "product_line": PRODUCT_LINES[category][0],
            "warranty_days": PRODUCT_LINES[category][1],
        }
        for category in CATEGORIES
    ]


def generate_channels() -> list[dict[str, object]]:
    return [
        {
            "channel": channel,
            "channel_type": CHANNEL_TYPES[channel][0],
            "commission_rate": CHANNEL_TYPES[channel][1],
        }
        for channel in CHANNELS
    ]


def _write_csv(destination: str | Path, fields: tuple[str, ...], rows: list[dict[str, object]]) -> Path:
    path = Path(destination).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        # 固定 LF，避免 Windows 与 Linux 生成出不同字节的“确定性”数据。
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    return path


def write_csv(destination: str | Path) -> Path:
    return _write_csv(destination, FIELDS, generate_rows())


def main() -> None:
    parser = argparse.ArgumentParser(description="生成 DataChat BI 确定性订单与维度 CSV")
    parser.add_argument("--output", type=Path, default=Path(__file__).with_name("orders.csv"))
    args = parser.parse_args()
    orders_path = write_csv(args.output)
    products_path = _write_csv(
        orders_path.with_name("products.csv"), PRODUCT_FIELDS, generate_products()
    )
    channels_path = _write_csv(
        orders_path.with_name("channels.csv"), CHANNEL_FIELDS, generate_channels()
    )
    print(f"已生成 48 行订单：{orders_path}")
    print(f"已生成 4 行产品维度：{products_path}")
    print(f"已生成 4 行渠道维度：{channels_path}")


if __name__ == "__main__":
    main()
