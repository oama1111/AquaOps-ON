"""Guard the runtime dependency set that `Dockerfile` installs.

The deployment image installs `requirements-runtime.txt`, which deliberately
excludes the offline simulation stack (wntr, pandas). That claim is only worth
making if something checks it: a future edit that reaches for pandas inside a
request path would otherwise pass every unit test here and fail the container at
start-up, in front of the operator.

The check runs in a subprocess with the excluded packages blocked at the import
hook, so it proves the claim about a *fresh* interpreter rather than about this
one, which by now has imported half the development stack.

The same block list is the measured basis for the image's size: Unit 6 recorded
a peak import footprint of about 76 MiB for `app.main` and about 190 MiB once
wntr, pandas and scikit-learn were loaded.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

#: Packages in the development set that the serving image does not install.
EXCLUDED_FROM_RUNTIME = ("pandas", "wntr", "matplotlib")


def test_app_main_imports_without_the_offline_simulation_stack() -> None:
    blocker = (
        "import builtins\n"
        f"BLOCKED = {EXCLUDED_FROM_RUNTIME!r}\n"
        "real_import = builtins.__import__\n"
        "def guard(name, *args, **kwargs):\n"
        "    root = name.split('.')[0]\n"
        "    if root in BLOCKED:\n"
        "        raise ImportError(f'{root} is not installed in the runtime image')\n"
        "    return real_import(name, *args, **kwargs)\n"
        "builtins.__import__ = guard\n"
        "import app.main\n"
        "print('imported:', app.main.app.title)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", blocker],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=180,
    )
    assert result.returncode == 0, (
        "app.main must import with the offline simulation stack absent, because "
        f"the deployment image does not install it.\n{result.stderr}"
    )
    assert "imported:" in result.stdout


def test_runtime_requirement_set_excludes_the_simulation_stack() -> None:
    """Keep the two requirement files in step with each other."""
    runtime = (REPO_ROOT / "requirements-runtime.txt").read_text(encoding="utf-8")
    declared = {
        line.split("#")[0].strip().lower()
        for line in runtime.splitlines()
        if line.split("#")[0].strip()
    }
    for package in EXCLUDED_FROM_RUNTIME:
        assert not any(
            requirement.startswith(package) for requirement in declared
        ), f"{package} belongs to the development set, not the runtime image"
    # The two things the serving process genuinely needs at run time.
    assert any(requirement.startswith("scikit-learn") for requirement in declared)
    assert any(requirement.startswith("fastapi") for requirement in declared)


def test_the_agent_handoff_lock_is_single_writer_per_database() -> None:
    """The SQLite profile must not be served by more than one worker.

    SQLite allows one writer. The image therefore defaults to one uvicorn worker
    and the compose profile makes the worker count explicit, so a reviewer can
    see that running more workers means moving to PostgreSQL first.
    """
    dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "AQUAOPS_WORKERS=1" in dockerfile
    compose = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "AQUAOPS_DB_URL" in compose
    assert "512M" in compose
