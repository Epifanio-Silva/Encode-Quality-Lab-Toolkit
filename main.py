from __future__ import annotations

import sys

from encoding_advisor.cli import main as advisor_main


def main() -> int:
    """Route eqlab subcommands to the phase-specific command modules."""
    args = sys.argv[1:]
    if args and args[0] in {"advisor", "advise"}:
        return advisor_main(args[1:])
    if args and args[0] in {"experiment", "experiments"}:
        from experiment import main as experiment_main

        return experiment_main(args[1:])
    if args and args[0] in {"encoder", "encode"}:
        from encoder import main as encoder_main

        return encoder_main(args[1:])
    if args and args[0] in {"validate", "validation"}:
        from validate import main as validate_main

        return validate_main(args[1:])
    if args and args[0] in {"compare", "comparison"}:
        from compare import main as compare_main

        return compare_main(args[1:])
    return advisor_main(args)


if __name__ == "__main__":
    raise SystemExit(main())
