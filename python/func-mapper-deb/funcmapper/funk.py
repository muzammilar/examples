
import funcmapper.maps as funkmap

def main():
    mappers = [
            ("rails", "inter-gelactic", "62 inches", "", "other")
    ]

    for func, first, second, *_ in mappers:
        print(funkmap.maps[func](first, second))
