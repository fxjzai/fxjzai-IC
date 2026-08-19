from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime

import httpx

from ic import __version__
from ic.adapters import HorizonHackerNewsFetchAdapter, NativeRssFetchAdapter
from ic.db import Database
from ic.domain import EndpointKind
from ic.ingest import Ingest
from ic.maintenance import Retention
from ic.settings import Settings


async def run_ingest(endpoint_id: str, settings: Settings) -> int:
    database = Database(settings.database_url)
    database.seed_defaults()
    async with httpx.AsyncClient(
        timeout=settings.http_timeout_seconds,
        headers={"User-Agent": f"fxjzai-ic/{__version__} internal-research"},
    ) as client:
        ingest = Ingest(
            database,
            {
                EndpointKind.RSS: NativeRssFetchAdapter(client),
                EndpointKind.HORIZON_HACKER_NEWS: HorizonHackerNewsFetchAdapter(client),
            },
        )
        result = await ingest.ingest(endpoint_id)
    print(result.model_dump_json())
    return 0 if result.health in {"success", "degraded"} else 1


def main() -> None:
    parser = argparse.ArgumentParser(prog="ic")
    subcommands = parser.add_subparsers(dest="command", required=True)
    ingest_parser = subcommands.add_parser("ingest", help="run one endpoint worker job")
    ingest_parser.add_argument("endpoint_id")
    retention_parser = subcommands.add_parser("retention", help="expire due fulltext payloads")
    retention_parser.add_argument("--at", help="ISO-8601 clock; defaults to now")
    args = parser.parse_args()
    settings = Settings()
    if args.command == "ingest":
        raise SystemExit(asyncio.run(run_ingest(args.endpoint_id, settings)))
    if args.command == "retention":
        now = datetime.fromisoformat(args.at) if args.at else datetime.now(UTC)
        result = Retention(Database(settings.database_url)).expire_due(now)
        print(json.dumps(result.model_dump(mode="json")))


if __name__ == "__main__":
    main()
