import os
import requests
import base64
from github import Github
import sys


def square_number(n):
    return n * n


def list_repo_files(repo):
    contents = repo.get_contents("")
    file_list = []
    while contents:
        file_content = contents.pop(0)
        if file_content.type == "dir":
            contents.extend(repo.get_contents(file_content.path))
        else:
            file_list.append(file_content.path)
    return file_list


def update_repo(target_repo, file_path, content):
    g = Github(os.environ["INPUT_QMS_PAT"])
    repo = g.get_repo(target_repo)
    files = list_repo_files(repo)
    print("Files in the repository:")
    for file in files:
        print(f"- {file}")
    print(f"Successfully connected to Github, and repo: {repo}")
    print(f"Instruction: {os.environ['INPUT_INSTRUCTION']}")

    # Create a new branch
    source_branch = "main"
    target_branch = f"update-{file_path.replace('/', '-')}"
    sb = repo.get_branch(source_branch)

    # Check if the target branch already exists
    try:
        repo.get_branch(target_branch)
        print(f"Branch {target_branch} already exists. Exiting.")
        return None
    except:
        # Branch doesn't exist, so we can create it
        repo.create_git_ref(ref=f"refs/heads/{target_branch}", sha=sb.commit.sha)

    # Get the file content
    file = repo.get_contents(file_path, ref=target_branch)
    file_content = base64.b64decode(file.content).decode("utf-8")

    # Update the file content (this is a simple append, adjust as needed)
    updated_content = file_content + "\n\n" + content

    # Commit the change
    repo.update_file(
        file_path,
        f"Update {file_path} based on LLM query",
        updated_content,
        file.sha,
        branch=target_branch,
    )

    # Create a pull request
    pr = repo.create_pull(
        title=f"Update {file_path} based on LLM query",
        body=f"This PR updates {file_path} based on an LLM query.",
        head=target_branch,
        base=source_branch,
    )

    return pr.html_url


if __name__ == "__main__":
    try:
        # qms_pat = os.environ["INPUT_QMS_PAT"]
        openai_key = os.environ["INPUT_OPENAI_KEY"]
        target_repo = os.environ["INPUT_TARGET_REPO"]

        # For demonstration, we'll use the target_repo as the number to square
        input_number = 5

        result = square_number(input_number)
        print(f"::set-output name=result::{result}")
        print(f"The square of {input_number} is {result}")

        pr_url = update_repo(
            target_repo,
            "TEST.MD",
            f"This is a test update based on LLM query: {result}",
        )

        if pr_url:
            print(f"Pull request created: {pr_url}")
        else:
            print("No pull request created.")

        # You can now use qms_pat, openai_key, and target_repo as needed
        # print(f"QMS PAT: {qms_pat[:5]}...")  # Print first 5 characters for security
        print(f"OpenAI Key: {openai_key[:5]}...")
        print(f"Target Repo: {target_repo}")
    except KeyError as e:
        print(f"Error: Missing environment variable {e}")
        sys.exit(1)
    except ValueError:
        print("Error: Please provide a valid integer as input.")
        sys.exit(1)
