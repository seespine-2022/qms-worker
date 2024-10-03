import sys
import os


def square_number(n):
    return n * n


if __name__ == "__main__":
    try:
        qms_pat = os.environ["INPUT_QMS_PAT"]
        openai_key = os.environ["INPUT_OPENAI_KEY"]
        target_repo = os.environ["INPUT_TARGET_REPO"]

        # For demonstration, we'll use the target_repo as the number to square
        input_number = 5

        result = square_number(input_number)
        print(f"::set-output name=result::{result}")
        print(f"The square of {input_number} is {result}")

        # You can now use qms_pat, openai_key, and target_repo as needed
        print(f"QMS PAT: {qms_pat[:5]}...")  # Print first 5 characters for security
        print(f"OpenAI Key: {openai_key[:5]}...")
        print(f"Target Repo: {target_repo}")
    except KeyError as e:
        print(f"Error: Missing environment variable {e}")
        sys.exit(1)
    except ValueError:
        print("Error: Please provide a valid integer as input.")
        sys.exit(1)
