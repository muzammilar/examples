import yaml
import os


class SourceInfo:
    def __init__(
        self,
        url: str,
        name: str,
        destination_directory: str,
        comment: str,
        parsing_function,
        expected_min_count: int,
        check_intvl: int,
    ):
        self.url = url
        self.comment = comment
        self.parsing_function = parsing_function
        self.destination_directory = destination_directory
        self.name = name
        self.expected_min_count = expected_min_count
        self.check_intvl = check_intvl  # in seconds
        self._file_path = os.path.join(destination_directory, f"{name}.yaml")
        self._tmp_file_path = os.path.join(destination_directory, "temp", f"{name}.yml")
        self._last_check = self._get_last_check()
        self._stocks = []

    def _get_last_check(self):
        if os.path.exists(self._file_path):
            with open(self._file_path, "r") as f:
                data = yaml.safe_load(f)
                if data and "timestamp" in data:
                    return data["timestamp"]
        return None  # Return None if file doesn't exist or timestamp not found

    def create_temp_directory(self):
        if os.path.exists(self.destination_directory):
            os.makedirs(os.path.dirname(self._tmp_file_path), exist_ok=True)


class StocksInfo:
    def __init__(self, symbol: str, name: str, exchange: str, country: str):
        self.symbol = symbol
        self.name = name
        self.exchange = exchange
        self.country = country
