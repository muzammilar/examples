# Stock Scraper

> **Note:** this example may not work as-is. It depends on the markup of a third-party site
> (which changes without notice), on a logged-in browser session, and on a locally installed
> Chrome/Firefox plus a matching driver. It is kept for reference as a pattern for Selenium
> scraping with a persistent browser profile. Last checked: the CLI and Chrome launch work, but
> the scrape itself was not verified end-to-end (no logged-in session), and the Firefox path
> was not verified (the test run stalled while Firefox applied a pending self-update).

Scrapes stock tickers from websites with [Selenium](https://www.selenium.dev/), using a
**persistent browser profile** so a manual login survives between runs. Currently the only
source is the [Musaffa](https://musaffa.com) stock screener, whose table is only shown to
logged-in users.

See [`../selenium-example`](../selenium-example) for a simpler, headless, no-login Selenium
scraper; this example focuses on driving a real (Chrome or Firefox) profile.

## Requirements

- [uv](https://docs.astral.sh/uv/) and Python 3.13+ (`.python-version`); `uv.lock` pins selenium 4.49.0
  and pytest 9.1.1
- Google Chrome or Firefox installed. The matching `chromedriver`/`geckodriver` is downloaded
  automatically by Selenium Manager (bundled with `selenium>=4.6`), which needs network access
  the first time.

## Layout

```
main.py                    # entry point: login mode or scrape mode
stockscraper/
  argparser.py             # command-line flags
  browser.py               # Chrome/Firefox drivers with an on-disk profile
  sourceinfo.py            # SourceInfo / StockInfo types and CSV writer
  sources.py               # registry of scrape sources
  musaffa.py               # musaffa.com screener scraper
tests/                     # pytest unit tests (fake driver, no browser)
```

## Usage

```sh
uv sync

# 1. First run: opens a browser window with a fresh profile in ./_userdatachrome.
#    Log in, then press Enter in the terminal to close the browser and keep the session.
uv run main.py --initial-login

# 2. Scrape using the saved session. Writes data/musaffa.csv (symbol,name,page_number).
uv run main.py --max-pages 3      # a few pages
uv run main.py                    # every page until there is no "next" button
uv run main.py --headless         # no window, once the profile is logged in

# Firefox instead of Chrome (profile in ./_userdatafirefox)
uv run main.py --browser firefox --initial-login
uv run main.py --browser firefox

uv run main.py --help             # all flags
```

`make login`, `make run`, `make format`, `make lint`, `make test` wrap the same commands.

## Tests

```sh
uv run pytest    # or: make test
```

The unit tests cover argument parsing, the CSV writer, the source registry and the Musaffa
paging/stop logic using a fake driver, so they need neither a browser nor network access.

There is no benchmark: a scrape's runtime is dominated by the network and the browser, so timing
this code would not measure anything meaningful.

### Reusing your existing browser profile

Instead of logging in again, you can copy an existing profile (close the browser first):

```sh
# Chrome: see chrome://version for "Profile Path"
cp -r ~/.config/google-chrome/ _userdatachrome                          # Linux
cp -r ~/Library/Application\ Support/Google/Chrome/ _userdatachrome     # macOS
uv run main.py --chrome-profile-directory "Profile 1"                   # if not "Default"

# Firefox: see about:profiles for "Root Directory"
uv run main.py --browser firefox --firefox-profile /path/to/profile
```

The profile directories (`_userdata*`) and `data/` are git-ignored.

## Notes

- The first run needs a manual login; afterwards the cookies live in the profile directory.
- Chrome refuses to open a user data directory that another Chrome process is using, so don't
  point `--chrome-user-data-dir` at the profile of a browser that is currently open.
- The CSS selectors in `stockscraper/musaffa.py` match the site's markup at the time of writing
  and will need updating if the site changes.
- Adding a new source: write a `scrape(driver, max_pages)` generator yielding `StockInfo` and
  register it in `stockscraper/sources.py`.
