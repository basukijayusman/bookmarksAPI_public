"""A HARMLESS tripwire. It transmits nothing, reads no private files, opens no socket.

If you are reading this because it just ran on your machine: that is the whole point.
You ran a stranger's code without reading it first. This script only proves it could
see you, then stops. A hostile version of this same file would not have stopped. See
LESSON.md.

Everything below is deliberately visible and inert. Nothing here leaves your computer.
"""

import getpass
import os
import platform
import socket
import sys

BANNER = r"""
  ______________________________________________________________
 |                                                              |
 |   You just ran code from a repository you did not read.      |
 |   This one is friendly. The next one won't announce itself.  |
 |______________________________________________________________|
"""


def observe() -> dict[str, str]:
    """A few trivially-public facts about this machine.

    None of this is secret and none of it is transmitted. It is printed straight back
    to *your* console so you can see what any code you run can trivially read. A real
    payload would keep going: browser profiles, SSH keys, ~/.aws, the bookmarks.db this
    project writes. This function reads none of those on purpose.
    """
    return {
        "user": getpass.getuser(),
        "host": socket.gethostname(),
        "os": f"{platform.system()} {platform.release()}",
        "python": platform.python_version(),
        "cwd": os.getcwd(),
    }


def send(payload: dict[str, str]) -> None:
    """Where a hostile version would exfiltrate. This one only says where.

    Left as a no-op on purpose. There is intentionally no HTTP client, no socket
    connect, no subprocess here for anyone to 'just uncomment' and point at a victim.
    The lesson is the gap between this print and a real requests.post(...).
    """
    print("  [exfil] a malicious build would now POST the above to an attacker's server.")
    print("  [exfil] this build does not. nothing was sent.\n")


def main() -> None:
    print(BANNER)
    print("  What this stranger's code can see about you, with zero effort:\n")
    for key, value in observe().items():
        print(f"    {key:8} = {value}")
    print()
    send(observe())
    print("  Lesson: read code before you run it. Details in LESSON.md.\n")


if __name__ == "__main__":
    main()
    # Never fail the caller's build; this is a lesson, not sabotage.
    sys.exit(0)
