"""Run tests against the isolated development database without exposing credentials."""
import sys

from dev_database import environment

environment()
import pytest  # noqa: E402

raise SystemExit(pytest.main(sys.argv[1:] or ["-q"]))
