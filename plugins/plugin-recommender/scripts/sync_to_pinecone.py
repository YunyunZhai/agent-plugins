#!/usr/bin/env python3
"""
插件市场数据同步到 Pinecone
读取本地 marketplace.json，构造向量记录，批量 upsert 到 Pinecone 索引。

用法:
    python3 sync_to_pinecone.py                          # 同步所有市场
    python3 sync_to_pinecone.py --marketplaces official  # 只同步指定市场
    python3 sync_to_pinecone.py --dry-run                # 预览，不实际上传
    python3 sync_to_pinecone.py --batch-size 50          # 自定义批大小

环境变量:
    PINECONE_API_KEY  - Pinecone API 密钥（必需）
    PINECONE_INDEX    - 索引名称（默认 claude-plugins-recommender）
    PINECONE_HOST     - 索引主机名（可选，自动检测）
"""

import argparse
import os
import sys
from typing import Optional

from pinecone_client import (
    DEFAULT_BATCH_SIZE,
    DEFAULT_INDEX_NAME,
    MARKET_NAMESPACES,
    PineconeClient,
    read_all_plugins,
)


# ── 主流程 ────────────────────────────────────────────────────────────────────

def sync(
    api_key: str,
    index_name: str,
    marketplace_filter: Optional[str] = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
    dry_run: bool = False,
) -> bool:
    """执行同步流程"""
    print("=" * 60)
    print("插件市场数据同步到 Pinecone")
    print("=" * 60)

    # 1. 读取本地数据
    print("\n[1/4] 读取本地市场数据...")
    all_records = read_all_plugins(marketplace_filter)
    total = sum(len(r) for r in all_records.values())
    print(f"  共计 {total} 个有效插件")

    if total == 0:
        print("没有找到有效插件，退出")
        return False

    if dry_run:
        print("\n[DRY RUN] 预览前 5 条记录:")
        for market, records in all_records.items():
            for r in records[:2]:
                print(f"  [{market}] {r['name']}: {r['text'][:80]}...")
        print(f"\n[DRY RUN] 将上传 {total} 条记录到 {len(all_records)} 个命名空间")
        return True

    # 2. 初始化 Pinecone 客户端
    print(f"\n[2/4] 连接 Pinecone（索引: {index_name}）...")
    client = PineconeClient(api_key, index_name)

    if not client.index_exists():
        print("  索引不存在，尝试创建...")
        if not client.create_index():
            return False
    else:
        print("  索引已存在")

    # 3. 清理过期记录（增量同步的核心：删除索引有但本地已无的记录）
    print(f"\n[3/4] 清理过期记录...")
    total_cleaned = 0
    for market_name in all_records:
        namespace = MARKET_NAMESPACES.get(market_name, market_name)
        local_ids = {r["_id"] for r in all_records[market_name]}
        existing_ids = set(client.list_ids(namespace))
        stale_ids = existing_ids - local_ids
        if stale_ids:
            if client.delete_ids(namespace, list(stale_ids)):
                total_cleaned += len(stale_ids)
                print(f"  {market_name} ({namespace}): 删除 {len(stale_ids)} 条过期记录")
            else:
                print(f"  {market_name} ({namespace}): 删除失败")
        else:
            print(f"  {market_name} ({namespace}): 无过期记录")

    # 4. 批量 upsert（只上传本地现有记录）
    print(f"\n[4/4] 上传数据（批大小: {batch_size}）...")
    success_count = 0
    fail_count = 0

    for market_name, records in all_records.items():
        namespace = MARKET_NAMESPACES.get(market_name, market_name)
        batches = [records[i : i + batch_size] for i in range(0, len(records), batch_size)]
        print(f"  {market_name} ({namespace}): {len(records)} 条，{len(batches)} 批")

        for i, batch in enumerate(batches):
            if client.upsert(namespace, batch):
                success_count += len(batch)
                print(f"    批 {i + 1}/{len(batches)} ✓ ({len(batch)} 条)")
            else:
                fail_count += len(batch)
                print(f"    批 {i + 1}/{len(batches)} ✗ 失败")

    # 5. 验证
    print(f"\n[5/4] 验证上传结果...")
    stats = client.describe_index_stats()
    if stats:
        namespaces = stats.get("namespaces", {})
        for market_name in all_records.keys():
            namespace = MARKET_NAMESPACES.get(market_name, market_name)
            expected = len(all_records[market_name])
            actual = namespaces.get(namespace, {}).get("vectorCount", 0)
            status = "✓" if actual >= expected else "⚠"
            print(f"  {status} {namespace}: 索引 {actual} / 预期 {expected}")

    print(f"\n完成: 成功 {success_count} 条, 失败 {fail_count} 条, 清理 {total_cleaned} 条过期记录")
    return fail_count == 0


def main():
    parser = argparse.ArgumentParser(description="同步插件市场数据到 Pinecone")
    parser.add_argument(
        "--marketplaces", "-m",
        help="只同步指定市场（逗号分隔，如 official,ecc）",
    )
    parser.add_argument(
        "--batch-size", "-b",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help=f"每批 upsert 的记录数（默认 {DEFAULT_BATCH_SIZE}）",
    )
    parser.add_argument(
        "--dry-run", "-n",
        action="store_true",
        help="预览模式，不实际上传",
    )
    parser.add_argument(
        "--api-key",
        help="Pinecone API 密钥（也可通过 PINECONE_API_KEY 环境变量设置）",
    )
    parser.add_argument(
        "--index",
        default=None,
        help=f"索引名称（默认 {DEFAULT_INDEX_NAME}）",
    )

    args = parser.parse_args()

    api_key = args.api_key or os.environ.get("PINECONE_API_KEY")
    if not api_key and not args.dry_run:
        print("错误: 未设置 PINECONE_API_KEY")
        print("用法: PINECONE_API_KEY=xxx python3 sync_to_pinecone.py")
        print("  或: python3 sync_to_pinecone.py --api-key xxx")
        sys.exit(1)

    index_name = args.index or os.environ.get("PINECONE_INDEX", DEFAULT_INDEX_NAME)

    # SDK 的 upsert_records 单次请求对 batch size 有上限（通常为 96）。
    # 如果用户传得太大，会直接报 400 INVALID_ARGUMENT。
    if args.batch_size > 96:
        print(f"警告: --batch-size 过大（{args.batch_size}），已自动降到 96 以兼容 Pinecone SDK")
        args.batch_size = 96

    # 解析市场过滤器
    marketplace_filter = None
    if args.marketplaces:
        # 支持简写映射
        aliases = {"official": "claude-plugins-official", "community": "claude-community"}
        filters = []
        for f in args.marketplaces.split(","):
            f = f.strip()
            filters.append(aliases.get(f, f))
        if len(filters) == 1:
            marketplace_filter = filters[0]
        else:
            # 多个市场时不做过滤，逐个处理
            marketplace_filter = None

    success = sync(
        api_key=api_key or "",
        index_name=index_name,
        marketplace_filter=marketplace_filter,
        batch_size=args.batch_size,
        dry_run=args.dry_run,
    )

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
