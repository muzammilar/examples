#!/usr/bin/env -S uv run --script

# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "selenium",
#     "webdriver-manager",
# ]
# ///

import csv
import os

import selenium.common.exceptions
from selenium import webdriver
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager

DRIVER_IMPLICIT_WAIT = 10
DATA_DIR = "hoap-data"


def main():
    """
    Main function for scraping data from a website.

    Parameters
    ----------
    None

    Returns
    -------
    None

    Notes
    -----
    - The function scrapes data from a website for each region in the region_map.
    - The data is stored in a csv file in the data directory.
    - The function creates the data directory if it does not exist.
    """
    url_base = "https://www.hoap.org.pk/Website/Members?Region=%d&page=%d"  # Replace with the URL you want to scrape
    region_map = {
        "Islamabad": 1,
        "Lahore": 2,
        "Karachi": 3,
        "Quetta": 4,
        "Peshawar": 5,
        "Multan": 6,
    }

    # make data directory if it doesn't exist
    os.makedirs(DATA_DIR, exist_ok=True)

    for region, region_id in region_map.items():
        region_data = get_data_for_region(url_base, region, region_id)
        # write the data as a csv file
        with open(os.path.join(DATA_DIR, f"{region}.csv"), "w", newline="", encoding="utf-8") as f:
            csv_writer = csv.writer(f)
            csv_writer.writerows(region_data)


def get_data_for_region(url_base, region, region_id):
    """
    Scrapes data from HOAP website for the given region.

    Parameters
    ----------
    url_base : str
        The base URL for the HOAP website.
    region : str
        The name of the region to scrape data for.
    region_id : int
        The ID of the region to scrape data for.

    Returns
    -------
    List of Lists containing the scraped data.
    """
    # 1. Set up ChromeDriver
    # Set up the Chrome WebDriver using webdriver-manager to handle driver installation
    print(f"{region}: Setting up Chrome WebDriver...")

    # Configure Chrome options (optional: for headless mode)
    chrome_options = ChromeOptions()
    chrome_options.add_argument("--headless")  # Runs Chrome in headless mode (without a GUI)
    chrome_options.add_argument("--disable-gpu")  # Recommended for headless mode on some systems

    try:
        service = ChromeService(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=chrome_options)
    except Exception as e:  # pylint: disable=broad-exception-caught
        print(f"{region}: Error setting up WebDriver: {e}")
        return

    # 3. Interact with the page and extract data
    results = []
    try:
        get_data(driver, url_base, region, region_id, results)
    except selenium.common.exceptions.TimeoutException:
        print("Possible Reason For Timeout: No more data exists")
        print(f"Timeout Error: {e}")
        print("Possible Reason For Timeout: No more data exists")
    except Exception as e:  # pylint: disable=broad-exception-caught
        print(f"An error occurred: {e}")
    finally:
        # 4. Close the browser
        driver.quit()
    return results


def get_data(driver, url_base, region, region_id, results):
    """
    Interacts with the page and extracts data using the provided Chrome WebDriver instance.

    Args:
        driver (webdriver.Chrome): The Chrome WebDriver instance.
        url_base (str): The base URL for the page to scrape.
        region (str): The region to scrape (e.g. Islamabad, Lahore, etc.).
        region_id (int): The region ID to use in the URL.
        results (list): The list to store the scraped data in.
    """
    # get data from all pages
    page_number = 1
    more_data = True  # flag to check if more data exists. Currently, it won't work since we rely on timeout exception

    # iterate over all pages and then raise an exception if more data doesn't exist
    while more_data:
        url = url_base % (region_id, page_number)

        print(f"{region}: Navigating to {url}...")
        driver.get(url)

        # print data
        # print(driver.page_source)

        # Wait for the table with member data to be present on the page.
        # This is a robust way to handle dynamically loaded content.
        # driver.implicitly_wait(10)
        wait = WebDriverWait(driver, DRIVER_IMPLICIT_WAIT)
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "div#MemberList table.table.table-hover.table-responsive")))  # pylint: disable=line-too-long
        print(f"{region}: Page {page_number} loaded successfully. Extracting...")
        # extract table data
        table = driver.find_element(By.CSS_SELECTOR, "div#MemberList table.table.table-hover.table-responsive")
        rows = table.find_elements(By.TAG_NAME, "tr")
        rows_read = 0
        for row in rows:
            # get the columns of the row
            cols = row.find_elements(By.TAG_NAME, "td")
            ret_row = [col.text for col in cols[1:-1]]  # skip first and last columns
            # add row if the data exists
            if ret_row:
                results.append(ret_row)
                rows_read += 1
                print(ret_row)

        # wait a little to avoid issues
        driver.implicitly_wait(DRIVER_IMPLICIT_WAIT)

        # update page number and get the new url
        page_number += 1

        print(f"{region}: Rows read: {rows_read}")
        # return if no more data
        if rows_read <= 0:  #  there is no more data
            more_data = False
            print(f"{region}: No more data exists.")


if __name__ == "__main__":
    main()
