"""Creates Selenium web drivers backed by a persistent, on-disk browser profile."""

import argparse
import os

from selenium import webdriver
from selenium.webdriver.remote.webdriver import WebDriver

IMPLICIT_WAIT_SECONDS = 10


def create_driver(args: argparse.Namespace) -> WebDriver:
    """Creates a web driver for the browser selected on the command line.

    Selenium Manager (bundled with selenium>=4.6) downloads a matching chromedriver/geckodriver
    automatically, but the browser itself must already be installed.

    Args:
        args: Parsed command-line arguments.

    Returns:
        A ready-to-use web driver.
    """
    if args.browser == "firefox":
        driver = _firefox(args)
    else:
        driver = _chrome(args)
    driver.implicitly_wait(IMPLICIT_WAIT_SECONDS)
    return driver


def _chrome(args: argparse.Namespace) -> WebDriver:
    options = webdriver.ChromeOptions()
    # a persistent profile keeps cookies (and therefore the login session) between runs
    options.add_argument(f"--user-data-dir={os.path.abspath(args.chrome_user_data_dir)}")
    options.add_argument(f"--profile-directory={args.chrome_profile_directory}")
    options.add_argument("--no-sandbox")
    options.add_argument("--window-size=1920,1080")
    if args.headless:
        options.add_argument("--headless=new")
    return webdriver.Chrome(options=options)


def _firefox(args: argparse.Namespace) -> WebDriver:
    profile = os.path.abspath(args.firefox_profile)
    os.makedirs(profile, exist_ok=True)
    options = webdriver.FirefoxOptions()
    # a persistent profile keeps cookies (and therefore the login session) between runs
    options.add_argument("-profile")
    options.add_argument(profile)
    options.add_argument("--width=1920")
    options.add_argument("--height=1080")
    if args.headless:
        options.add_argument("-headless")
    return webdriver.Firefox(options=options)
