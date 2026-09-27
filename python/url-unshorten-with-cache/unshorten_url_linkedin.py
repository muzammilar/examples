#!/usr/bin/env python3
"""
This script fetches the real URL from a LinkedIn shortened URL.
"""

import argparse
import functools
import logging
import os
import json
import re
import sys
import time
import contextlib

from bs4 import BeautifulSoup
import requests

# sleep time between requests to avoid rate limiting
SLEEP_BETWEEN_REQUESTS = 1
LINKEDIN_BODY_TEXT = "This link will take you to a page that’s not on LinkedIn"
A_HREF_CLASS = "artdeco-button"
LINKEDIN_SHORTENED = "//lnkd.in/"


RAW_URL_DATA = """"""

URL_PATTERN = "https?:\\/\\/(?:www\\.)?[-a-zA-Z0-9@:%._\\+~#=]{1,256}\\.[a-zA-Z0-9()]{1,6}\\b(?:[-a-zA-Z0-9()@:%_\\+.~#?&\\/=]*)"

REQUESTS_CONNECT_TIMEOUT = 5
REQUEST_READ_TIMEOUT = 15

## Caching Information
CACHE_FILE_PATH = "._cache.json"
FILE_BACKED_CACHE = None
FILE_BACKED_CACHE_FLUSH_INTERVAL = 10  # number of events before flushing

## Status Codes
STATUS_CACHED = "CACHED"
STATUS_EXCEPTION = "EXCEPTION"


@contextlib.contextmanager
def use_file_backed_cache(cache_file_path):
    global FILE_BACKED_CACHE
    FILE_BACKED_CACHE = FileCache(cache_file_path)
    yield # after the with block, FILE_BACKED_CACHE will be flushed
    FILE_BACKED_CACHE.flush() # force a flush, always

# FileCache Class Implements a simple file-based cache for storing and retrieving data
class FileCache:

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.flush()
        return

    def __init__(self, cache_file_path):
        self._cache_file_path = cache_file_path
        self._cache = {}
        self._cache_flush_interval = FILE_BACKED_CACHE_FLUSH_INTERVAL
        self._unflushed_events = 0
        # initialize the cache
        self.get_all()

    def get_all(self):
        # if the cache is already populated, return it
        if self._cache:
            return self._cache
        # if the cache file doesn't exist, return an empty dictionary
        if not os.path.exists(self._cache_file_path):
            return {}
        # if the cache file exists, load the data into the cache
        with open(self._cache_file_path, "r") as f:
            self._cache = json.load(f)
        return self._cache

    def set_all(self, data):
        self._cache = data
        # force a write - expensive flush operation
        self.flush()

    def get(self, key):
        return self._cache.get(key)

    def set(self, key, value):
        # skip null values
        if key is None or value is None:
            return

        self._cache[key] = value
        # add the key for value as well so we don't repeat if we run on the same file
        self._cache[value] = value

        # try flushing the cache
        self.try_flush()

    def try_flush(self):
        self._unflushed_events += 1
        self._unflushed_events %= self._cache_flush_interval
        # flush the cache
        if self._unflushed_events == 0:
            self.flush()

    def flush(self):
        logging.debug("Flushing cache...")
        # write the cache
        with open(self._cache_file_path, "w") as f:
            json.dump(self._cache, f, indent=4)


def get_linkedin_urls(raw_url_data):
    """
    Extracts LinkedIn URLs from the provided raw URL data.

    Args:
        raw_url_data (str): A string containing raw URL data which may include LinkedIn shortened URLs.

    Returns:
        list: A list of LinkedIn URLs found in the raw URL data.
    """
    return re.findall(URL_PATTERN, raw_url_data)


# cache the function results for future use
@functools.lru_cache(maxsize=128)
def get_real_url(linkedin_url: str) -> tuple[str, int]:
    """Fetches the real URL from a LinkedIn shortened URL."""
    cached_result = FILE_BACKED_CACHE.get(linkedin_url)
    if cached_result:
        return cached_result, STATUS_CACHED
    try:
        response = requests.head(linkedin_url, allow_redirects=True, timeout=(REQUESTS_CONNECT_TIMEOUT, REQUEST_READ_TIMEOUT))
        # response.raise_for_status()
        if LINKEDIN_SHORTENED in response.url: # need to get the real url from the body as redirect failed
            return get_real_url_deep(linkedin_url)
        return response.url, response.status_code
    except requests.exceptions.RequestException as _:
        logging.exception("Error fetching URL")
        return linkedin_url, STATUS_EXCEPTION

def get_real_url_deep(linkedin_url: str) -> tuple[str, int]:
    """Fetches the real URL from a LinkedIn shortened URL by fetching the body."""
    try:
        response = requests.get(linkedin_url, timeout=(REQUESTS_CONNECT_TIMEOUT, REQUEST_READ_TIMEOUT))
        response.raise_for_status()
        return parse_body_and_get_url(response.text), response.status_code
    except requests.exceptions.RequestException as _:
        logging.exception("Error fetching URL")
        return linkedin_url, STATUS_EXCEPTION

def soupify(response_data: str) -> BeautifulSoup:
    """Converts the response data to a BeautifulSoup object."""
    return BeautifulSoup(response_data, "html.parser")

def parse_body_and_get_url(response_data: str) -> str:
    """Parses the response data and returns the real URL."""
    soup = soupify(response_data)
    soup_finder = soup.find("a", class_=A_HREF_CLASS)
    if soup_finder is None:
        return None
    url = soup_finder.get("href")
    return url

def print_linkedin_urls():
    """Prints the real URLs for the given list of LinkedIn URLs."""
    # get the list of LinkedIn URLs
    url_list = get_linkedin_urls(RAW_URL_DATA)

    # print the real URLs
    for linkedin_url in url_list:
        real_url, status = get_real_url(linkedin_url)

        logging.info(f"""Status: {status}.
                        LinkedIn URL:{linkedin_url}.
                        Real URL: {real_url}.
                        ========================""")
        # write to cache file and sleep for non-cached requests
        if status == STATUS_CACHED:
            continue

        # write to cache
        FILE_BACKED_CACHE.set(linkedin_url, real_url)
        # sleep to avoid rate limiting
        time.sleep(SLEEP_BETWEEN_REQUESTS)


def auto_replace_linkedin_urls() -> str:
    """
    Automatically replaces all LinkedIn shortened URLs in the RAW_URL_DATA string with the real URL they point to.

    Prints the new string to the console.
    """
    new_url_data = RAW_URL_DATA
    # use zeroth element of the tuple returned by get_real_url
    new_url_data, num_susbtitutions = re.subn(URL_PATTERN,
                          lambda match: get_real_url(match.group())[0],
                          new_url_data)

    logging.info(f"Number of Substitutions: {num_susbtitutions}")

    return new_url_data

def _check_file_exists(filename:str):
    """
    Helper function to check if the given filename exists and is a file

    Args:
        filename (str): The path to the file

    Returns:
        str: The filename if it exists and is a file

    Raises:
        argparse.ArgumentTypeError: If the file does not exist or is not a file
    """

    if not os.path.exists(filename):
        raise argparse.ArgumentTypeError(f"File '{filename}' does not exist")
    if not os.path.isfile(filename):
         raise argparse.ArgumentTypeError(f"'{filename}' is not a file")
    return filename

def parse_args():
    """
    Parse Command Line Arguments for reading and writing files for RAW_URL_DATA
    """
    parser = argparse.ArgumentParser(description="Unshorten LinkedIn URLs")
    parser.add_argument("-i", "--input", type=_check_file_exists, help="Input file", required=True, )
    parser.add_argument("-l", "--log-level", type=str, default="INFO", help="Log level")
    # mutually exclusive group
    group = parser.add_mutually_exclusive_group()
    group.add_argument("-r", "--replace", action="store_true", help="Replace Input file with updated URLs")
    group.add_argument("-o", "--output", type=str, help="Output file")
    return parser.parse_args()

def read_file(input_file):
    with open(input_file, "r", encoding="utf-8") as f:
        return f.read()

def write_file(output_file, data):
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(data)

def setup_logging(log_level: str):
    """
    Set up logging.
    """
    logging.basicConfig(level=log_level, stream=sys.stdout)

def main():
    """
    Main function for unshortening LinkedIn URLs.
    """
    # parse command line arguments
    args = parse_args()

    # set up logging
    setup_logging(args.log_level)

    # use a fo;e baccked cache as a context manager
    with use_file_backed_cache(CACHE_FILE_PATH):
        # read the input file
        global RAW_URL_DATA # pylint: disable=global-statement
        RAW_URL_DATA = read_file(args.input) # already global

        # print the real URLs for debugging
        print_linkedin_urls()
        logging.info("========================================")
        result = auto_replace_linkedin_urls()
        logging.info(result)

    # write the result to the output file
    output_file = args.output if args.output else args.input
    if args.replace:
        write_file(output_file, result)

# Main
if __name__ == "__main__":
    main()
