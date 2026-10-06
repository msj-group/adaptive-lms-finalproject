"""
Operator tool for natural-use research collection (Phase 6).

Run it on the server, as a project owner, in your own terminal. It is not a
web page: no Administrator, Teacher, Student or Researcher view can do what
it does, because each command either names a Student account or deletes
research data.

    python scripts/research_operator.py exclude EMAIL
    python scripts/research_operator.py reinstate EMAIL [--lift-legacy-exclusion]
    python scripts/research_operator.py mark-demo EMAIL
    python scripts/research_operator.py status EMAIL
    python scripts/research_operator.py list-excluded
    python scripts/research_operator.py purge-expired [--execute]

Collection needs no operator step: while a configuration collects, every
active Student account is collected automatically unless it is excluded.
This tool records the exceptions.

``exclude`` records an exclusion decided through the centre's external
process. It works before the Student was ever collected, and it closes any
open session at once. Logging in or opening a page never re-enrols an
excluded Student.

``reinstate`` lifts an exclusion. A legacy exclusion (carried over from a
refusal or withdrawal in the removed consent workflow) is lifted only with
``--lift-legacy-exclusion``, which states that the external process changed
it.

``mark-demo`` marks a demonstration or development account. Its sessions are
stored as ``demo`` and nothing of it is ever exported. Mark every such
Student account before collection starts.

``status`` and ``list-excluded`` print research codes next to account
emails. That is identity recovery: keep the output private.

``purge-expired`` reports sessions older than ``RESEARCH_RETENTION_DAYS``
and every export archive holding data that old; with ``--execute`` it
**permanently deletes** them (events and prompts included). Running it
against live data needs its own authorization.

None of these commands records consent or an ethics approval. The
environment chooses the configuration (``FLASK_ENV``); no credential is read
from the command line.
"""
import argparse
import getpass
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import create_app  # noqa: E402
from app.services import research_operator as operator  # noqa: E402
from app.models import User
from app.security.passwords import verify_password

_MESSAGES = {
    operator.NO_ACCOUNT: "No account has that email address.",
    operator.NOT_A_STUDENT: "That account is not a Student account. Only Students are collected.",
    operator.LEGACY_EXCLUSION: (
        "This Student has a legacy exclusion from the removed consent workflow. It is lifted "
        "only with --lift-legacy-exclusion, after the external process changed it."
    ),
    operator.UNCHANGED: "Nothing changed: the Student is already in that state.",
    operator.NO_SUBJECT: "This Student has no research subject.",
    operator.NOT_EXCLUDED: "This Student is not excluded. Nothing changed.",
    operator.CONFLICT: "The change could not be saved because of a concurrent change. "
                       "Nothing was written.",
}


def _parser():
    parser = argparse.ArgumentParser(description="Natural-use research operator tool.")
    parser.add_argument("--researcher", required=True, help="Email of the actual Researcher operator; password is prompted privately.")
    commands = parser.add_subparsers(dest="command", required=True)
    exclude = commands.add_parser("exclude")
    exclude.add_argument("email")
    reinstate = commands.add_parser("reinstate")
    reinstate.add_argument("email")
    reinstate.add_argument("--lift-legacy-exclusion", action="store_true")
    demo = commands.add_parser("mark-demo")
    demo.add_argument("email")
    status = commands.add_parser("status")
    status.add_argument("email")
    listing = commands.add_parser("list-excluded")
    listing.add_argument("--page", type=int, default=1)
    purge = commands.add_parser("purge-expired")
    purge.add_argument("--execute", action="store_true")
    return parser


def _refused(result, err):
    print(_MESSAGES.get(result, "Nothing was written."), file=err)
    return 0 if result in (operator.UNCHANGED, operator.NOT_EXCLUDED) else 1


def main(argv=None, app=None, out=None, err=None):
    out = out or sys.stdout
    err = err or sys.stderr
    args = _parser().parse_args(argv)
    app = app or create_app(os.environ.get("FLASK_ENV", "development"))
    with app.app_context():
        actor = User.query.filter_by(email=args.researcher.strip().lower(), role="researcher", status="active").first()
        password = getpass.getpass("Researcher password: ")
        if actor is None or not verify_password(actor.password_hash, password):
            print("Researcher authentication failed. Nothing was changed.", file=err)
            return 1
        actor_id = actor.id
        from app.extensions import db
        db.session.info["research_operator_auth"] = (actor.id, actor.auth_version)
        del password
        if args.command == "exclude":
            result, code = operator.exclude(args.email, actor_id=actor_id)
            if result != operator.EXCLUDED:
                return _refused(result, err)
            print(f"Excluded: {code}. Open sessions were closed.", file=out)
            return 0
        if args.command == "reinstate":
            result, code = operator.reinstate(
                args.email, allow_legacy_override=args.lift_legacy_exclusion, actor_id=actor_id)
            if result != operator.REINSTATED:
                return _refused(result, err)
            print(f"Reinstated: {code}. The Student is collected again from the next page.",
                  file=out)
            return 0
        if args.command == "mark-demo":
            result, code = operator.mark_demo(args.email, actor_id=actor_id)
            if result != operator.MARKED_DEMO:
                return _refused(result, err)
            print(f"Marked as demonstration data: {code}. Nothing of it is ever exported.",
                  file=out)
            return 0
        if args.command == "status":
            result, state = operator.status(args.email, actor_id=actor_id)
            if state is None:
                print(_MESSAGES.get(result, "Not found."), file=err)
                return 1
            print(f"{state.subject_code}: {state.collection_status} ({state.status_basis}, "
                  f"{state.provenance})", file=out)
            return 0
        if args.command == "list-excluded":
            accounts = operator.excluded_accounts(actor_id=actor_id, page=args.page)
            for account in accounts:
                print(f"{account.subject_code}\t{account.email}\t{account.status_basis}\t"
                      f"{account.status_changed_at:%Y-%m-%d %H:%M} UTC", file=out)
            print(f"{len(accounts)} excluded Student account(s).", file=out)
            return 0
        days = app.config.get("RESEARCH_RETENTION_DAYS")
        if days is None:
            print("RESEARCH_RETENTION_DAYS is not configured; nothing can be purged.", file=err)
            return 1
        report = operator.retention_report(days, execute=args.execute, actor_id=actor_id)
        verb = "Deleted" if report.executed else "Would delete"
        print(f"{verb} {report.sessions} session(s), {report.events} event(s), "
              f"{report.prompts} prompt(s) and {report.archives} export archive(s) older "
              f"than {days} days.", file=out)
        if not report.executed and (report.sessions or report.archives):
            print("Nothing was deleted. Re-run with --execute only when authorized.", file=out)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
