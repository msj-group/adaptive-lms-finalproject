"""Fail the Railpack build unless the interpreter is exactly Python 3.14.6."""
import sys

REQUIRED_VERSION = (3, 14, 6)


def main():
    detected = tuple(sys.version_info[:3])
    if detected != REQUIRED_VERSION:
        print("Python 3.14.6 is required; detected " + ".".join(map(str, detected)),
              file=sys.stderr)
        return 1
    print("Verified Python 3.14.6", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
