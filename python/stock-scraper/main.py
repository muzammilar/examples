import selenium
import selenium.webdriver
import selenium.webdriver.chrome.options
import selenium.webdriver.common.window

# from selenium.webdriver.common.by import By
import time
import argparse

def parse_arguments():
    """Parses command-line arguments."""
    parser = argparse.ArgumentParser(description="Stock Scraper Application")
    # add an initial login parser
    parser.add_argument(
        "--initial-login",
        action="store_true",
        help="Perform initial login if specified.",
    )
    # add parser for user-data-dir
    parser.add_argument(
        "--chrome-user-data-dir",
        action="store",
        default="_userdatachrome",
        type=str,
        help="Absolute path to the user data directory for chrome.",
    )
    parser.add_argument(
        "--chrome-profile-directory",
        action="store",
        default="Defaults",
        type=str,
        help="Name of the profile directory for chrome.",
    )

    return parser.parse_args()

def main():
    args = parse_arguments()

    chrome(args)
    #firefox(args)

def firefox(args):
    # Access the value of --initial-login
    login_sleep_interval = 5
    if args.initial_login:
        login_sleep_interval = 5 * 60

    # Set up Firefox options
    options = selenium.webdriver.FirefoxOptions()


def chrome(args):
    # Access the value of --initial-login
    login_sleep_interval = 5
    if args.initial_login:
        login_sleep_interval = 5 * 60

    # Set up Chrome options
    options = selenium.webdriver.chrome.options.Options()
    # used for using your current users' data directory
    options.add_argument("--no-sandbox")
    options.add_argument(f"--user-data-dir={args.chrome_user_data_dir}")
    options.add_argument(f"--profile-directory={args.chrome_profile_directory}")
    options.add_argument("--remote-debugging-port=9222")

    # Create a new instance of the Chrome driver
    driver = selenium.webdriver.Chrome(options=options)

    # Store the ID of the original window
    original_window = driver.current_window_handle
    print(driver.get_cookies())
    driver.get("https://musaffa.com")
    print(driver.get_cookies())
    # Navigate to the Google homepage
    #new_window = driver.switch_to().new_window(selenium.webdriver.common.window.WindowType.WINDOW)
    #print(new_window)
    #new_window.maximize_window()
    #print("maxed")
    #new_window.get("https://musaffa.com")

    time.sleep(login_sleep_interval)  # Pause for some time to either login manually
    #new_window.close()

    # Close the browser
    #driver.switch_to.window(original_window)
    driver.quit()



if __name__ == "__main__":
    main()
