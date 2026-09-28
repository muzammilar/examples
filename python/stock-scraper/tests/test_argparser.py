import pytest

from stockscraper.argparser import parse_arguments


def test_defaults():
    args = parse_arguments([])
    assert args.source == "musaffa"
    assert args.browser == "chrome"
    assert args.initial_login is False
    assert args.headless is False
    assert args.max_pages == 0
    assert args.output_dir == "data"
    assert args.chrome_user_data_dir == "_userdatachrome"
    assert args.chrome_profile_directory == "Default"
    assert args.firefox_profile == "_userdatafirefox"


def test_overrides():
    args = parse_arguments(["--browser", "firefox", "--headless", "--max-pages", "3", "--output-dir", "out"])
    assert (args.browser, args.headless, args.max_pages, args.output_dir) == ("firefox", True, 3, "out")


def test_initial_login_with_headless_is_rejected(capsys):
    with pytest.raises(SystemExit) as exc:
        parse_arguments(["--initial-login", "--headless"])
    assert exc.value.code == 2
    assert "--initial-login needs a visible browser window" in capsys.readouterr().err


@pytest.mark.parametrize("argv", [["--browser", "safari"], ["--source", "nope"], ["--max-pages", "x"]])
def test_invalid_values_are_rejected(argv):
    with pytest.raises(SystemExit):
        parse_arguments(argv)
