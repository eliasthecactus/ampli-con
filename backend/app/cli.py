"""Command line: `docker exec -it amplicon amplicon reset-password`."""
import argparse
import sys

from . import auth, config, db


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="amplicon", description="Amplicon maintenance commands")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("reset-password", help="set a new web password (signs out all browsers and apps)")
    r.add_argument("--username", help="also change the username")
    r.add_argument("--password", help="the new password (default: a generated one)")
    sub.add_parser("version", help="print the version")
    args = p.parse_args(argv)

    if args.cmd == "version":
        print(config.VERSION)
        return 0

    db.init()
    user = auth.the_user()
    username = args.username or (user["username"] if user else auth.DEFAULT_USERNAME)
    password = args.password or auth.generate_password()
    try:
        auth.set_credentials(username, password, sign_out=True)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(f"Username: {username}")
    print(f"Password: {password}")
    print("All browsers and apps were signed out.")
    if config.env_user_set():
        print("\nNote: AMPLICON_USERNAME and AMPLICON_PASSWORD are set in the container's environment. "
              "They replace this password on the next restart; change them in docker compose instead.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
