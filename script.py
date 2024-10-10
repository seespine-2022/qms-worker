import os
import requests
import base64
from github import Github
import sys
from openai import OpenAI
import json
import re


def analyze_instruction(instruction, options):
    client = OpenAI(
        api_key=os.environ["INPUT_OPENAI_KEY"],
    )
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {
                "role": "user",
                "content": f"You are a QMS expert. One of our QA people just gave you an instruction. Analyze it and choose the most appropriate option from the following: {options}. Respond in JSON format with the key 'option' and the value as the option number.",
            },
            {"role": "user", "content": f"Instruction: \n{instruction}"},
        ],
        response_format={"type": "json_object"},
    )
    response = json.loads(response.choices[0].message.content)
    return response["option"]


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


def determine_files_to_update(files, instruction, issue_title, issue_body):
    # Use OpenAI to determine the files to update
    client = OpenAI(
        api_key=os.environ["INPUT_OPENAI_KEY"],
    )
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {
                "role": "user",
                "content": f"You are a QMS expert. Given instruction, issue title and issue body, determine which files to update. Reply in JSON format. The key is 'files' and the value is a list of file paths. \n\n Instruction: {instruction}\n\n Issue Title: {issue_title}\n\n Issue Body: {issue_body} \n\n Files: {files}",
            }
        ],
        response_format={"type": "json_object"},
    )
    response = json.loads(response.choices[0].message.content)
    print(f"Files to update: {response}")
    return response["files"]


def summarize_pr(response_outline):
    client = OpenAI(
        api_key=os.environ["INPUT_OPENAI_KEY"],
    )
    messages = [
        {
            "role": "user",
            "content": f"You are a QMS expert. Given the outline of the PR, summarize it and provide a title and body. Write it objectively, without mentioning LLM or AI. Respond in JSON format with keys 'title' and 'body'. Write the body in markdown format.",
        },
        {
            "role": "user",
            "content": f"Outline: {response_outline}",
        },
    ]
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=messages,
        response_format={"type": "json_object"},
    )
    response_summary = json.loads(response.choices[0].message.content)
    return response_summary


def update_files(
    repo, source_branch, target_branch, files, issue_title, issue_body, instruction
):
    client = OpenAI(
        api_key=os.environ["INPUT_OPENAI_KEY"],
    )
    messages = [
        {
            "role": "user",
            "content": f"You are a QMS expert. Given instruction, issue title, issue body and the files to be updated, outline how you would update the files.",
        },
        {
            "role": "user",
            "content": f"Instruction: {instruction}\n\n Issue Title: {issue_title}\n\n Issue Body: {issue_body}",
        },
    ]
    for file_path in files:
        file_content = repo.get_contents(file_path, ref=target_branch)
        decoded_content = base64.b64decode(file_content.content).decode("utf-8")
        messages.append(
            {
                "role": "user",
                "content": f"File: {file_path} \n File content:\n{decoded_content}",
            }
        )
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=messages,
        response_format={"type": "text"},
    )
    response_outline = response.choices[0].message.content
    response_summary = summarize_pr(response_outline)

    messages.append({"role": "assistant", "content": response_outline})
    i = 0
    for file_path in files:
        if i != 0:
            messages.pop()
        messages.append(
            {
                "role": "user",
                "content": f"Now update this file: {file_path} according to your own outline. Respond with just the full updated contents of the file, keeping original formatting.",
            }
        )
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=messages,
            response_format={"type": "text"},
        )
        response_content = response.choices[0].message.content
        file_content = repo.get_contents(file_path, ref=target_branch)
        repo.update_file(
            file_path,
            f"Update {file_path}",
            response_content,
            file_content.sha,
            branch=target_branch,
        )
        i += 1

    # Create a pull request
    pr = repo.create_pull(
        title=response_summary["title"],
        body=response_summary["body"],
        head=target_branch,
        base=source_branch,
    )
    return pr.html_url


def update_qms(target_repo, instruction, issue_title, issue_body, issue_url):
    g = Github(os.environ["INPUT_QMS_PAT"])
    repo = g.get_repo(target_repo)
    files = list_repo_files(repo)
    print("Files in the repository:")
    for file in files:
        print(f"- {file}")
    print(f"Successfully connected to Github, and repo: {repo}")
    print(f"Instruction: {instruction}")

    files_to_update = determine_files_to_update(
        files, instruction, issue_title, issue_body
    )

    # Create a new branch
    source_branch = "main"
    target_branch = f"update-{issue_title.replace(' ', '-').replace('/', '-')}"
    sb = repo.get_branch(source_branch)

    # Check if the target branch already exists
    try:
        repo.get_branch(target_branch)
        print(f"Branch {target_branch} already exists. Exiting.")
        return None
    except:
        # Branch doesn't exist, so we can create it
        repo.create_git_ref(ref=f"refs/heads/{target_branch}", sha=sb.commit.sha)

    pr_url = update_files(
        repo,
        source_branch,
        target_branch,
        files_to_update,
        issue_title,
        issue_body,
        instruction,
    )

    return pr_url


if __name__ == "__main__":
    try:
        target_repo = os.environ["INPUT_TARGET_REPO"]
        instruction = os.environ["INPUT_INSTRUCTION"]

        options = {
            0: "No clear instruction",
            1: "Create a change control record",
            2: "Update QMS documentation",
        }

        option = analyze_instruction(instruction, options)
        print(f"Option: {option}")

        try:
            issue_title = os.environ["INPUT_ISSUE_TITLE"]
            issue_body = os.environ["INPUT_ISSUE_BODY"]
            issue_url = os.environ["INPUT_ISSUE_URL"]
        except KeyError:
            issue_title = None
            issue_body = None
            issue_url = None

        try:
            pr_title = os.environ["INPUT_PR_TITLE"]
            pr_body = os.environ["INPUT_PR_BODY"]
            pr_url = os.environ["INPUT_PR_URL"]
        except KeyError:
            pr_title = None
            pr_body = None
            pr_url = None

        if issue_title and not pr_title:
            # Just an issue present, no PR yet
            qms_pr_url = update_qms(
                target_repo,
                instruction,
                issue_title,
                issue_body,
                issue_url,
            )
        elif issue_title and pr_title:
            # Issue and PR present
            qms_pr_url_match = re.search(
                r"<qms_pr_creation>(.*?)</qms_pr_creation>", issue_body
            )
            if qms_pr_url_match:
                qms_pr_url = qms_pr_url_match.group(1)
                # Extract the branch name from the PR URL
                branch_name = qms_pr_url.split("/")[-2]

                # Update the existing PR
                g = Github(os.environ["INPUT_QMS_PAT"])
                repo = g.get_repo(target_repo)
                pr = repo.get_pull(int(qms_pr_url.split("/")[-1]))

                # Logic of updating the PR

            else:
                print("Error: Could not find QMS PR URL in the issue body")
                qms_pr_url = None

        if qms_pr_url:
            print(f"Pull request created: {qms_pr_url}")
            print(
                f"::set-output name=result::<qms_pr_creation>{qms_pr_url}</qms_pr_creation>"
            )
        else:
            print(f"::set-output name=result::No pull request created.")
    except KeyError as e:
        print(f"Error: Missing environment variable {e}")
        sys.exit(1)
    except ValueError:
        print("Error: Please provide a valid integer as input.")
        sys.exit(1)
