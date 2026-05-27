"""Command line interface for seamless-share."""

from __future__ import annotations

import argparse
import sys

from .why_not.api import transformation_diff, why_not
from .why_not.errors import DeepBufferUnavailable, EndpointError, UsageError
from .why_not.render import render_transformation_diff_text, render_why_not_text
from .why_not.models import to_json
from .replay import ReplayConfig, ReplaySetupError, ReplayUsageError, replay
from .replay.render import render_replay_text
from .replay.models import to_json as replay_to_json


def _add_common_flags(parser: argparse.ArgumentParser, *, default_format: str) -> None:
    parser.add_argument("--endpoint", action="append", default=[], help="Database endpoint")
    parser.add_argument("--config", help="Configuration path for named endpoints")
    parser.add_argument("--deep", action="store_true", help="Fetch read-only deep diffs")
    parser.add_argument(
        "--deep-best-effort",
        action="store_true",
        help="Return 0 when deep buffers are unavailable",
    )
    parser.add_argument(
        "--format",
        choices=("json", "text"),
        default=default_format,
        help="Output format",
    )
    parser.add_argument("-q", "--quiet", action="store_true", help="Suppress optional text")
    parser.add_argument("-v", "--verbose", action="store_true", help="Include verbose details")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="seamless-share")
    subparsers = parser.add_subparsers(dest="command", required=True)

    diff_parser = subparsers.add_parser(
        "transformation-diff", help="Compare exactly two transformation references"
    )
    diff_parser.add_argument("ref_a")
    diff_parser.add_argument("ref_b")
    _add_common_flags(diff_parser, default_format="json")

    why_parser = subparsers.add_parser(
        "why-not", help="Diagnose why an expected transformation was not a cache hit"
    )
    why_parser.add_argument("ref")
    why_parser.add_argument("--candidate", help="Explicit candidate transformation reference")
    why_parser.add_argument(
        "--explain-selection",
        action="store_true",
        help="Include deterministic candidate-selection details",
    )
    _add_common_flags(why_parser, default_format="text")

    replay_parser = subparsers.add_parser(
        "replay", help="Run a script against a crystallized artifact in replay mode"
    )
    replay_parser.add_argument("script")
    replay_parser.add_argument("script_args", nargs=argparse.REMAINDER)
    replay_parser.add_argument("--artifact", required=True, help="Path to seamless.db")
    replay_parser.add_argument("--bufferdir", required=True, help="Path to companion bufferdir")
    replay_parser.add_argument("--authorization", help="Replay authorization JSON path")
    replay_parser.add_argument("--driver-cache", choices=("bypass", "enabled"), default="bypass")
    replay_parser.add_argument("--report", help="Write report to this path")
    replay_parser.add_argument("--report-format", choices=("json", "text"))
    replay_parser.add_argument("--config", help="Isolated replay config path")
    replay_parser.add_argument("--inherit-config", action="store_true", help="Inherit normal Seamless config")
    replay_parser.add_argument("--allow-remote", action="store_true", help="Allow remote dispatch")
    replay_parser.add_argument(
        "--fail-on",
        choices=("none", "any", "unauthorized-only"),
        default="none",
    )
    replay_parser.add_argument("--timeout", type=float)
    replay_parser.add_argument("-q", "--quiet", action="store_true")
    replay_parser.add_argument("-v", "--verbose", action="store_true")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "transformation-diff":
            result = transformation_diff(
                args.ref_a,
                args.ref_b,
                endpoints=args.endpoint,
                config=args.config,
                deep=args.deep,
                deep_best_effort=args.deep_best_effort,
                verbose=args.verbose,
            )
            output = (
                to_json(result)
                if args.format == "json"
                else render_transformation_diff_text(result, quiet=args.quiet, verbose=args.verbose)
            )
        elif args.command == "why-not":
            result = why_not(
                args.ref,
                endpoints=args.endpoint,
                config=args.config,
                candidate=args.candidate,
                deep=args.deep,
                deep_best_effort=args.deep_best_effort,
                explain_selection=args.explain_selection,
                verbose=args.verbose,
            )
            output = (
                to_json(result)
                if args.format == "json"
                else render_why_not_text(result, quiet=args.quiet, verbose=args.verbose)
            )
        elif args.command == "replay":
            config = (
                ReplayConfig.inherit()
                if args.inherit_config
                else ReplayConfig.from_file(args.config)
                if args.config
                else ReplayConfig.synthesized()
            )
            result = replay(
                script=args.script,
                script_args=args.script_args,
                artifact=args.artifact,
                bufferdir=args.bufferdir,
                authorization=args.authorization,
                driver_cache=args.driver_cache,
                config=config,
                timeout=args.timeout,
                allow_remote=args.allow_remote,
            )
            stdout_format = args.report_format or ("json" if args.report else "text")
            output = (
                replay_to_json(result)
                if stdout_format == "json"
                else render_replay_text(result, quiet=args.quiet, verbose=args.verbose)
            )
            if args.report:
                report_format = args.report_format or "json"
                with open(args.report, "w", encoding="utf-8") as handle:
                    handle.write(
                        (
                            replay_to_json(result)
                            if report_format == "json"
                            else render_replay_text(result, quiet=args.quiet, verbose=args.verbose)
                        )
                        + "\n"
                    )
            print(output)
            if result.outcome.phase == "timeout":
                return 6
            if result.outcome.phase == "script_error":
                return 4
            unauthorized_kinds = {
                "unauthorized_materialization",
                "unauthorized_fingertip",
                "authorized_materialization_unsatisfied_dependency",
                "authorization_incoherent",
            }
            if args.fail_on == "any" and result.findings:
                return 5
            if args.fail_on == "unauthorized-only" and any(
                item.kind in unauthorized_kinds for item in result.findings
            ):
                return 5
            return 0
        else:  # pragma: no cover - argparse enforces this
            raise UsageError(f"unknown command: {args.command}")
    except ReplayUsageError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except ReplaySetupError as exc:
        if exc.report is not None:
            print(replay_to_json(exc.report), file=sys.stderr)
        else:
            print(str(exc), file=sys.stderr)
        return 3
    except UsageError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except EndpointError as exc:
        print(str(exc), file=sys.stderr)
        return 3
    except DeepBufferUnavailable as exc:
        print(str(exc), file=sys.stderr)
        if exc.output:
            print(exc.output)
        return 0 if exc.best_effort else 4
    except Exception as exc:  # pragma: no cover - defensive CLI boundary
        print(f"internal error: {exc}", file=sys.stderr)
        return 1

    print(output)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
