import sys


def square_number(n):
    return n * n


if __name__ == "__main__":
    try:
        input_number = int(sys.argv[1])
        result = square_number(input_number)
        print(f"::set-output name=result::{result}")
        print(f"The square of {input_number} is {result}")
    except (IndexError, ValueError):
        print("Error: Please provide a valid integer as input.")
        sys.exit(1)
