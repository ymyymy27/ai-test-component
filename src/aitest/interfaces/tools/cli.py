"""Offline skeleton diagnostics and resource discovery; never opens a business workspace."""

import argparse
import json
from importlib.resources import files
from uuid import uuid4

from aitest.application.errors import CapabilityUnavailable
from aitest.bootstrap import create_api
from aitest.contracts.commands import Command
from aitest.contracts.templates import TemplatePack
from aitest.interfaces.local.api import EntryKind, Session
from aitest.interfaces.tools.agent_relay import run


def main() -> int:
    parser = argparse.ArgumentParser(prog="aitest")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor", help="Offline diagnostic; exit 2 until core is ready")
    sub.add_parser("templates", help="List installed draft template versions")
    relay = sub.add_parser("mcp-relay", help="Reserved stdio relay; not available yet")
    relay.add_argument("--binding", required=True)
    args = parser.parse_args()
    if args.command == "templates":
        packs = []
        for directory in sorted(
            files("aitest.resources").joinpath("templates").iterdir(), key=lambda item: item.name
        ):
            source = directory.joinpath("1.0.0.json")
            if directory.is_dir() and source.is_file():
                pack = TemplatePack.model_validate_json(source.read_text(encoding="utf-8"))
                packs.append(
                    {
                        "template_id": pack.template_id,
                        "version": pack.version,
                        "status": pack.implementation_status,
                        "delivery_method": pack.delivery_method,
                    }
                )
        print(json.dumps(packs, ensure_ascii=False, indent=2))
        return 0
    if args.command == "mcp-relay":
        try:
            run(args.binding)
        except CapabilityUnavailable as error:
            import sys

            print(json.dumps({"code": error.code, "message": str(error)}), file=sys.stderr)
            return 2
    response = create_api().dispatch(
        Command(request_id=str(uuid4()), action="doctor"),
        Session("offline-cli", EntryKind.INTERACTIVE_CLI),
    )
    print(response.model_dump_json(indent=2))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
