import my_module.my_rs_lib as rsbr

def main():
    print(dir(rsbr))
    rsbr.greet("Jack Sparrow")
    print("12 + 13 = ", rsbr.add(12, 13))
    print("11 + 61 = ",rsbr.sum_as_string(11, 61))


if __name__ == "__main__":
    main()
