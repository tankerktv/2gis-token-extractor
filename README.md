# 2gis-token-extractor

Prints a fresh 2GIS access token to stdout. Nothing else.

*[Русская версия](README.ru.md)*

```bash
2gis-token login                        # once: a browser window opens, you sign in
export ZOND_TOKEN="$(2gis-token get)"   # from then on
```

2GIS is a mapping service widely used across Russia and the CIS. Anything that
talks to its API needs an access token, and the only documented way to get one
is to open DevTools and copy it out of a WebSocket URL by hand. This program
does that part for you.

It works like `aws configure` or `gh auth token`: it hands you a credential.
What you do with it afterwards is none of its business — it does not talk to
Home Assistant, does not write config files for other programs, and does not
know what you are building.

---

## What it does not do

**It does not implement the 2GIS login.** Signing in means a phone number and
an SMS code, and services guard that path hardest of all — captcha, app
signatures, device checks. A real browser signs in; this program only picks up
the result. Your phone number and the code never pass through it.

**It does not store the token.** The token goes to stdout and disappears. It
lands in a file only if you ask for one with `--out`, and it never appears in
logs — messages show a fingerprint instead.

## Install

**With [pipx](https://pipx.pypa.io/)** (recommended):

```bash
pipx install git+https://github.com/tankerktv/2gis-token-extractor.git
2gis-token install-browser
```

`install-browser` downloads Chromium for Playwright. It is a one-time step of
roughly 150 MB.

It is not on PyPI: this is a small tool, and everyone who installs it arrives
from this repository anyway. Installing straight from git needs `git` on your
machine and gives you exactly the same `2gis-token` command.

**Without Python:** grab a single-file build for your system from
[Releases](https://github.com/tankerktv/2gis-token-extractor/releases), rename
it to `2gis-token` (`2gis-token.exe` on Windows), put it somewhere on your
`PATH`, and run `2gis-token install-browser` once.

It is a command-line program: run it from a terminal, with a command.
Double-clicking it on Windows only shows the help and waits for Enter — without
a command there is nothing for it to do.

## Use

### `login` — once

```bash
2gis-token login
```

A browser window opens on 2gis.ru. Sign in the usual way: phone number, code
from the SMS. The window closes by itself as soon as the session is ready.

What ends up on disk is the browser session — cookies, nothing more. See
[Where the session lives](#where-the-session-lives).

### `get` — every time you need a token

```bash
2gis-token get
```

Runs a headless browser with the saved session and prints the token:

```
0123456789abcdef0123456789abcdef01234567
```

That is the entire output — one line, no decoration — so it drops straight into
a pipeline:

```bash
export ZOND_TOKEN="$(2gis-token get)"
curl "https://api.auth.2gis.com/2.1/users/me?access_token=$(2gis-token get)"
```

| Flag | What it does |
|---|---|
| `--json` | prints details instead: token, where it was captured, session path |
| `--out FILE` | writes the token to a file; stdout stays empty |
| `--timeout SEC` | how long to wait for the token (default 60) |
| `--headed` | shows the browser window — for when something is off |
| `--profile NAME` | use a named session — see [Several accounts](#several-accounts) |

If no token turns up, the page is reloaded once and watched again: the app
does not always arrive on the first try. The two attempts **share** the
`--timeout` budget rather than doubling it, so the flag still means what it
says. Below 30 seconds there is only one attempt — two short ones are worse
than a single longer one.

### `check` — is the token still good?

```bash
2gis-token check                  # takes a fresh token from the saved session
2gis-token check --token TOKEN    # checks the one you pass
echo "$TOKEN" | 2gis-token check --token -
```

It asks `api.auth.2gis.com` for the account profile and tells the three
outcomes apart, because they are fixed differently:

| Outcome | Exit code | What to do |
|---|---|---|
| token works | 0 | nothing |
| token rejected | 2 | `2gis-token login` |
| 2GIS returned an error of its own | 1 | wait; the session is fine |
| no network | 4 | check your connection |

## A window instead of commands

```bash
2gis-token gui
```

Opens a small window: sign in, get a token, copy it. Double-clicking the
single-file build on Windows opens the same window — that is what a
double-click is for.

The window speaks the system language: Russian on a Russian Windows, English
otherwise. On Windows it follows the **display** language, not the regional
format — so a Russian region with an English Windows still gets English.
`--lang ru`, `--lang en` or `TWOGIS_TOKEN_LANG` picks one explicitly.

The token stays hidden until you press Show, and reaches the clipboard only
when you press Copy. Nothing is saved: copy it before you close the window.

## Exit codes

Meant for scripts:

| Code | Meaning |
|---|---|
| 0 | success |
| 1 | token not found, or not accepted |
| 2 | you need to sign in: no session, or it expired |
| 3 | environment is not ready: no Playwright or no browser |
| 4 | network unavailable |
| 5 | session busy: another run is using it right now |

## About the token

Measured, not assumed:

* **40 characters, `0-9` and `a-f` only.** An opaque string — **not a JWT**.
  No dots, no base64, nothing to decode; the expiry date is not written inside.
* It rides in the query string of the requests the web app makes — including
  the WebSocket it opens for the friends feature:

  ```
  wss://zond.api.2gis.ru/api/1.1/user/ws?appVersion=6.31.0&channels=markers,sharing,routes&token=<40 hex>
  ```

  The socket is the documented place to look for it by hand, but it is not the
  only one. In every run observed on a live account the socket never opened at
  all, and the token arrived in an ordinary request instead. Both are watched.

* **Every run mints a new one, and the old ones keep working.** Measured: take
  a token, take another, and the first still answers. So there is no need to
  share one token between several programs — give each its own.

* **How long it lives is not known.** A token issued on 2026-07-26 still worked
  on 2026-08-16 — so at least three weeks. The upper bound has not been found,
  and no refresh endpoint is known either. `tools/token_watch.py` exists to
  settle the question by observation.

Because tokens live long, this program is a convenience, not a lifeline. That
is deliberate: it stays small.

## How it works

The token is taken by **watching the page's network activity**, not by digging
through the web app's internal state. The internals change whenever the
frontend is redesigned; the socket URL with a `token` parameter is their
protocol and outlives that.

Three nets, from reliable to backup: Playwright's `websocket` event, its
`request` event (URLs and headers), and — as a fallback — `WebSocket` and
`fetch` replaced by a script on the page.

**A headless browser has to stop announcing itself.** Playwright introduces
itself as `HeadlessChrome`, and with that name 2GIS serves a page that makes
fifteen requests and stops: no map, no sign-in, no token. Put a plain `Chrome`
there and the count goes past a hundred and the app comes alive. The program
therefore asks the browser for its own string and fixes one word in it —
inventing a version would drift away from the real browser at the next
Playwright update.

Candidates are ranked by where they came from. Forty hex characters are also
exactly what a sha1 looks like, and a map page is full of those: image
fingerprints, build ids, tile ids. A token found in the socket URL therefore
beats one merely spotted in the page text, whichever arrived first.

## Where the session lives

`storage_state.json` holds the browser cookies, which is effectively access to
your account. It is stored outside the working directory so it cannot be
committed by accident:

| System | Path |
|---|---|
| Windows | `%APPDATA%\2gis-token-extractor\storage_state.json` |
| macOS | `~/Library/Application Support/2gis-token-extractor/storage_state.json` |
| Linux | `~/.config/2gis-token-extractor/storage_state.json` |

Override it with `--state PATH` or the `TWOGIS_TOKEN_STATE` environment
variable. On Unix the file is written with `600` permissions.

Deleting the file means signing in again — nothing else breaks.

The file is replaced, never edited in place: a new one is written beside it and
moved over the old one, so a crash halfway through cannot leave you with half a
session. Two runs cannot share one session either — the second is turned away
with exit code 5, because 2GIS refreshes the cookies on every visit and the
loser of that race would overwrite fresh cookies with stale ones.

## Several accounts

A personal account and a work one are two different sessions. Name them:

```bash
2gis-token login --profile work
2gis-token get --profile work
```

Profiles live under `profiles/<name>/storage_state.json` inside the same config
directory, so nothing has to be remembered but the name. `TWOGIS_TOKEN_PROFILE`
sets one for a whole shell; a `--profile` on the command line always wins over
it, so a variable left in a shell profile cannot silently send work to the
wrong account.

```bash
2gis-token profiles
```

lists the ones that have been signed in.

## When the session expires

`get` fails with a plain message:

```
2gis-token: токен не появился за 60 с.
Чаще всего это значит, что сессия истекла — войди заново:
  2gis-token login
```

Exit code 2. Run `2gis-token login` and carry on.

## Development

```bash
pip install -e ".[dev]"
python -m pytest -q
```

The tests need neither a browser nor a network connection, and they run in a
fraction of a second. That is a design constraint, not luck: the logic —
recognising a token, parsing the socket URL, reading the profile response,
choosing paths, exit codes — lives in modules with no heavy dependencies, and
Playwright stays a thin wrapper around them.

The browser plumbing — the part no unit test can reach — is covered separately,
by driving a real Chromium against a fake 2GIS page that ships with the tests.
Those need a downloaded browser, so they are kept out of the default run:

```bash
python -m playwright install chromium
python -m pytest -m browser
```

The suite is verified by mutation: break a rule on purpose, and a test must go
red. All twenty-six mutations tried so far were caught, plus the browser suite:
remove the second attempt and its test goes red.

## License

MIT — see [LICENSE](LICENSE).
