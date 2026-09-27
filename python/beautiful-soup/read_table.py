
from bs4 import BeautifulSoup
import requests

# read_url_and_print_code reads the file url using requests and print the grid
def read_url_and_print_code(url:str):

    # parameters
    params = {"export": "download", "confirm":"1"}
    r = requests.get(url, params=params, stream=True)

    # check errors raised by requests
    r.raise_for_status()

    # find the table in the document
    soup = BeautifulSoup(r.text, "html.parser")

    # find the table with a specific class in the document
    table = soup.find("table")
    if table is None:
        raise ValueError("Table not found in the document")

    # two dimentional array grid using a dictionary
    grid = {}
    max_x = 0
    max_y = 0

    # convert table to a csv
    for row in table.find_all("tr")[1:]: # skip the first row
        x_cell, char_cell, y_cell  = row.find_all("td")
        x = int(x_cell.text)
        y = int(y_cell.text)
        char = char_cell.text
        # set up the grid values
        grid[x] = grid.get(x, {})
        grid[x][y] = char
        # compute new max values
        max_x = max(max_x, x)
        max_y = max(max_y, y)

    # print the grid
    for y in range(max_y, -1, -1): # grid is inverted for y-axis
        for x in range(max_x + 1): # x-axis
            print(grid.get(x, {}).get(y, " "), end="")  # print empty space if no character
        print() # new line


if __name__ == "__main__":
    # each table has three columns: x, character, y
    read_url_and_print_code("some-google-docs-url-with-a-table")
