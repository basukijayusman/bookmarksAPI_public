# You ran code you didn't read

If a banner printed your username and hostname when you ran this project, that was
`tripwire.py`. It is harmless: it printed a few public facts **to your own terminal**
and stopped. It opened no network connection, read no private files, and sent nothing
anywhere. Read it — it's short, and it's meant to be read.

That's the entire lesson: **the moment you run someone else's code, it runs with your
permissions.** It can see what you can see and do what you can do. This repo chose to
wave at you. Most that misbehave do not announce themselves.

## What a hostile version could have done instead

The same entry point that printed a banner could just as easily have:

- read this project's `bookmarks.db` and uploaded every URL you saved,
- read `~/.ssh/`, `~/.aws/credentials`, `.env` files, or browser profiles,
- installed a persistent task so it runs again after you close the terminal,
- pulled a second-stage payload from the internet and executed it.

`tripwire.py` does none of these. Its `send()` function is a deliberate no-op — it
prints *where* a real attacker would transmit, so you can see the shape of the attack,
and stops. There is intentionally no HTTP client or socket in it to repurpose.

## How to not get caught by the real thing

Before running any unfamiliar repo:

1. **Read the entry points.** `setup.py`, `pyproject.toml` build hooks, `conftest.py`,
   `__init__.py`, anything named `install`/`setup`/`postinstall`, and whatever the
   README tells you to run first. Code can run at *import* time, not just when called.
2. **Grep for the tells:**
   ```bash
   grep -rEn "subprocess|os\.system|socket|requests\.(post|get)|urllib|eval|exec|base64|pickle\.loads" .
   ```
   None of these is proof of malice — this project uses none of them in its app code —
   but each is a place to stop and understand *why* it's there.
3. **Run it isolated first.** A throwaway VM or container, a fresh user account, no
   real credentials on the machine. Watch its network with `pip install --dry-run`,
   or run under a tool that shows outbound connections.
4. **Pin and audit dependencies.** `pip install` runs arbitrary code from every
   package. `pip-audit` and `bandit -r .` catch a lot cheaply.

The cost of reading a repo before you run it is minutes. The cost of not is your
machine.
