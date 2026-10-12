"""Command-line interface: python -m shield.cli <command>."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="shield", description="Shield AI command-line tools")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("seed", help="build the demo city, back-fill history and replay to a date")
    s.add_argument("--clock", default="2026-10-10", help="replay the live feed up to this date (default 2026-10-10)")
    s.add_argument("--backfill-end", default="2026-09-14")
    s.add_argument("--no-history", action="store_true", help="skip the historical 2024 runs and feedback")
    a = sub.add_parser("advance", help="ingest the next N days of the demo feed and run the agent daily")
    a.add_argument("--days", type=int, default=1)
    r = sub.add_parser("rewind", help="restore the post-back-fill snapshot and replay to DATE")
    r.add_argument("date")
    g = sub.add_parser("run-agent", help="run the agent as of DATE (default: the current clock)")
    g.add_argument("--as-of")
    sub.add_parser("evaluate", help="compare alerts with simulation ground truth")
    sub.add_parser("check", help="run the data-sovereignty checks")
    sub.add_parser("verify-audit", help="verify the hash chain of the audit log")
    u = sub.add_parser("create-user", help="create or reset a user")
    u.add_argument("username")
    u.add_argument("role", choices=["decision_maker", "epidemiologist", "data_steward", "auditor"])
    u.add_argument("--name", default="")
    u.add_argument("--password", required=True)
    v = sub.add_parser("serve", help="run the web application")
    v.add_argument("--host", default="127.0.0.1")
    v.add_argument("--port", type=int, default=8000)
    args = p.parse_args(argv)

    from .db import get_conn, init_db, migrate
    init_db(get_conn())
    migrate(get_conn())
    if args.cmd == "seed":
        from .demo import seed
        res = seed(clock=args.clock, backfill_end=args.backfill_end, history=not args.no_history)
        print(json.dumps(res))
    elif args.cmd == "advance":
        from datetime import timedelta
        from .demo import advance_to, get_clock
        target = (date.fromisoformat(get_clock()) + timedelta(days=args.days)).isoformat()
        advance_to(target)
    elif args.cmd == "rewind":
        from .demo import rewind_to
        rewind_to(args.date)
    elif args.cmd == "run-agent":
        from .agent.orchestrator import run_agent
        from .demo import get_clock
        print(json.dumps(run_agent(date.fromisoformat(args.as_of or get_clock()), actor="cli"), indent=1))
    elif args.cmd == "evaluate":
        from .evaluation import evaluate
        print(json.dumps(evaluate(), indent=1))
    elif args.cmd == "check":
        from .security.residency import install_egress_guard, run_checks
        install_egress_guard()
        bad = 0
        for c in run_checks():
            print(f"[{c['status'].upper():4s}] {c['check']}: {c['detail']}")
            bad += c["status"] == "fail"
        return 1 if bad else 0
    elif args.cmd == "verify-audit":
        from .security.audit import verify_chain
        res = verify_chain()
        print(json.dumps(res, indent=1))
        return 0 if res["valid"] else 1
    elif args.cmd == "create-user":
        from .security.auth import create_user
        create_user(args.username, args.name or args.username, args.role, args.password)
        print(f"user {args.username} ({args.role}) saved")
    elif args.cmd == "serve":
        import uvicorn
        uvicorn.run("shield.api.main:app", host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
