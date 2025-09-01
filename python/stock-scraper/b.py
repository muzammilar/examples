# Scrapes the tickers from the website
# import libraries
from selenium import webdriver
from selenium.webdriver.common.by import By
import time, os, csv, sys
from selenium.webdriver.common.action_chains import ActionChains

def main():
    # Set options (prevents the browser from closing after opening)
    options = webdriver.ChromeOptions()
    options.add_experimental_option("detach", True)
    # options.add_argument('--headless')
    options.add_argument('--no-sandbox')
    # Open the browser
    print('Opening browser')
    driver = webdriver.Chrome(options=options)
    print('Opened browser')
    actions = ActionChains(driver)
    # Maximize the browser to fullscreen
    driver.maximize_window()
    print('Maxed browser')
    # Open the link
    driver.implicitly_wait(10)
    driver.get('https://screener.musaffa.com/cabinet/onboarding')
    page_number = 1
    for i in range(759):
        # gather company names
        company_names = driver.find_element(By.CLASS_NAME, 'table--body').find_elements(By.CLASS_NAME, "mb-0.company--name")
        company_names = list(map(lambda name: name.text, company_names))

        # gather company tickers
        company_tickers = driver.find_element(By.CLASS_NAME, 'table--body').find_elements(By.CLASS_NAME, "mb-0.stock--name")
        company_tickers = list(map(lambda name: name.text, company_tickers))

        # save the links
        save(company_names, company_tickers, page_number)
        # go to the next page
        if page_number != 3:
            go_to_next_page(driver)
        page_number += 1


def go_to_next_page(driver):
    driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
    time.sleep(3.5)
    driver.find_element(By.CLASS_NAME, 'bi.bi-chevron-right').click()




def save(company_names, company_tickers, page_number):
    i = 0
    with open('musaffa_tickers.csv', 'a') as file:
        writer = csv.DictWriter(file, fieldnames=["company_name", "company_ticker", "page_number"])
        if page_number == 1:
            writer.writeheader()
        for company_name in company_names:
            writer.writerow({"company_name": company_name,
                             "company_ticker": company_tickers[i],
                             "page_number": page_number})
            i += 1
        print(f"Saved page {page_number}")





if __name__ == "__main__":
    main()
