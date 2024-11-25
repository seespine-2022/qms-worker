import os
import sys
import json
import re
import base64
import requests
from github import Github
from openai import OpenAI
import datetime

# Constants
MODEL = "gpt-4o"


# OpenAI Client initialization
def get_openai_client():
    return OpenAI(api_key=os.environ["INPUT_OPENAI_KEY"])


# GitHub Client initialization
def get_github_qms_client():
    return Github(os.environ["INPUT_QMS_PAT"])


def get_github_current_client():
    return Github(os.environ["INPUT_GITHUB_TOKEN"])


# Utility functions
def analyze_instruction(instruction, options):
    client = get_openai_client()
    response = client.chat.completions.create(
        model=MODEL,
        messages=[
            {
                "role": "user",
                "content": f"You are a QMS expert. Analyze this instruction and choose the most appropriate option from: {options}. Respond in JSON format with the key 'option' and the value as the option number.",
            },
            {"role": "user", "content": f"Instruction: \n{instruction}"},
        ],
        response_format={"type": "json_object"},
    )
    return json.loads(response.choices[0].message.content)["option"]


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
    client = get_openai_client()
    response = client.chat.completions.create(
        model=MODEL,
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
    client = get_openai_client()
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
        model=MODEL,
        messages=messages,
        response_format={"type": "json_object"},
    )
    return json.loads(response.choices[0].message.content)


def update_files(
    repo, source_branch, target_branch, files, issue_title, issue_body, instruction
):
    client = get_openai_client()
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
        model=MODEL,
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
            model=MODEL,
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
    g = get_github_qms_client()
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


# Change Control functions
def find_change_request_template(files):
    return next(
        (file for file in files if "change-request-template" in file.lower()), None
    )


def get_latest_cr_number(repo):
    try:
        contents = repo.get_contents("change-requests/change-request-records")
        cr_numbers = []
        for content in contents:
            if content.type == "file" and content.name.startswith("CR"):
                try:
                    number = int(content.name.split("-")[0][2:])
                    cr_numbers.append(number)
                except ValueError:
                    continue
        return max(cr_numbers) if cr_numbers else 0
    except:
        return 0


def create_change_control_record(
    repo, instruction, issue_title, issue_body, issue_url, pr_title, pr_body, pr_url
):
    client = get_openai_client()
    files = list_repo_files(repo)

    template_file = find_change_request_template(files)
    if not template_file:
        print("Error: change-request-template.md not found")
        return None

    template_content = repo.get_contents(template_file).decoded_content.decode("utf-8")
    today = datetime.datetime.now().strftime("%Y-%b-%d")

    # Split template into sections based on ## headers
    sections = re.split(r"(?m)^## ", template_content)
    header = sections[0]  # Contains the # Change Request Form
    sections = ["## " + s for s in sections[1:]]  # Add back the ## to other sections
    i = 0

    filled_sections = [header]
    base_context = {
        "issue_title": issue_title,
        "issue_body": issue_body,
        "issue_url": issue_url,
        "pr_title": pr_title,
        "pr_body": pr_body,
        "pr_url": pr_url,
        "today": today,
    }

    for section in sections:
        i += 1
        section_title = section.split("\n")[0]

        messages = [
            {
                "role": "system",
                "content": f"You are a QMS expert. Fill out this section of the change request template based on the provided information. Only fill out information you can confidently determine from the context. Do not restructure, but stick to the provided template. Your job is to fill it out, nothing else. Do not include any markdown tags like ```markdown, but only markdown formatting on the text itself.Today is {today}.",
            },
            {
                "role": "user",
                "content": (
                    f"You are now filling out section {section_title} of the change request template. The section encapsulates all elements with the same major number in the title, so 2.1 belongs to section 2.Please find specific instructions below. All the way at the end of this prompt, you will find the full template and context. \n"
                    "Section 1: Do not edit this section. Just return the section as it was provided to you. \n"
                    "Section 2: Determine the Major or Minor, insert the GitHub Issue URL and the GitHub PR URL. The Requestor is the Name in the Issue body. The Reviewer is the Management approval in the issue body, and the approver is the QA approval in the issue body.\n"
                    "Section 3: In the Issue body, find whether it is a patch, minor or major change. Insert the reason/scope and source of change, also to be found in the issue body. Only include supporting QMS documentation if it is explicitly mentioned in the issue body. From the PR body you can find the affected software documentation components, such as SOUP, SDD etc.\n"
                    "Section 4: Determine based on the issue an PR what type of change under 4.1 this is, choose one. From the issue body, determine how the items under 4.2 are affected. \n"
                    "Section 5: In the issue body, check out 2.3 to determine DTM impact. \n"
                    "Section 6: In the issue body, check out 2.4 to determine Risk impact. For 6.3, determine whether our current Class A device might change to a different class. This is a high burden, so only do it if you are sure. \n"
                    "Section 7: Check the issue and the PR body to determine if the change is significant in the context of QMS. It is a high burden, so only do it if you are sure. Fill out the rest to the best of your ability. It is all in the context of QMS/Software as a medical device. \n"
                    "Section 8: Do not edit section 8.1 or 8.2, those are the options you can choose from. For the risk/impact matrix, describe the risk, rate it S (for severity) and P (for probability). Example: degraded performance of the device (S1/P1). THen describe how we control for it (we always do automatic testing, validation). In 8.4 see the PR body to see how acceptance test, unit tests, software tests were conducted. In 8.5 favor automatic tests, on an annual basis. Keep 8.6 empty. \n"
                    "Section 9: Estimate if the feature is so different or new that it requires training. Assume smart staff that doesn't need training for clicking a button. Fill out 9.2. Answer to 9.3 is no, unless there is mention of a linked CAPA in the issue body. \n"
                    "Section 10: Only list supporting documentation if it is explicitly mentioned in the issue body. \n"
                ),
            },
            {
                "role": "user",
                "content": f"Full template:\n {template_content}\n\nContext:\n"
                f"Issue Title: {issue_title}\n"
                f"Issue Body: {issue_body}\n"
                f"Issue URL: {issue_url}\n"
                f"PR Title: {pr_title}\n"
                f"PR Body: {pr_body}\n"
                f"PR URL: {pr_url}\n\n"
                f"Only respond with the filled section in markdown format, no other text.",
            },
        ]

        response = client.chat.completions.create(
            model=MODEL,
            messages=messages,
            response_format={"type": "text"},
            temperature=0.2,
        )

        filled_sections.append(response.choices[0].message.content)
        print(f"Appending the filled section: {response.choices[0].message.content}")
        print("--------------------------------")

    # Combine all sections
    filled_template = "\n\n".join(filled_sections)

    # Rest of the function remains the same
    latest_cr_number = get_latest_cr_number(repo)
    new_cr_number = latest_cr_number + 1
    summary = issue_title[:50]
    filename = f"CR{new_cr_number:03d}-{summary}"
    filename = re.sub(r"[^\w\-_\. ]", "_", filename)
    filename = filename.replace(" ", "_") + ".md"

    return filename, filled_template, summary


def update_change_control_record(
    repo, cc_url, issue_title, issue_body, issue_url, pr_title, pr_body, pr_url
):
    client = get_openai_client()

    # Get the PR for change control record
    pr_number = int(cc_url.split("/")[-1])
    pr = repo.get_pull(pr_number)

    # Get the files changed in the PR
    files_changed = pr.get_files()

    # Find the change request record file
    cr_file = next(
        (
            file
            for file in files_changed
            if file.filename.startswith("change-request-records/CR")
        ),
        None,
    )

    if not cr_file:
        print("Error: No change request record found in the PR")
        return None

    # Get the content of the file
    file_content = repo.get_contents(
        cr_file.filename, ref=pr.head.ref
    ).decoded_content.decode("utf-8")

    today = datetime.datetime.now().strftime("%Y-%b-%d")

    messages = [
        {
            "role": "system",
            "content": f"You are a QMS expert. Update the change request record based on the provided information. Focus on filling in TBD fields, but also update other fields if new information is available. Always incrementally update the revisio log with your changes. Today is {today}.",
        },
        {
            "role": "user",
            "content": f"Current change request record:\n{file_content}\n\nIssue Title: {issue_title}\n\nIssue Body: {issue_body}\n\nIssue URL: {issue_url}\n\nPR Title: {pr_title}\n\nPR Body: {pr_body}\n\nPR URL: {pr_url}\n\nRespond in json format with keys 'updated_content' and 'summary' with the updated record in markdown format, followed by a summary of entire issue+PR, also formatted in markdown to serve as the PR body..",
        },
    ]

    response = client.chat.completions.create(
        model=MODEL,
        messages=messages,
        response_format={"type": "json_object"},
    )

    response_content = json.loads(response.choices[0].message.content)

    # Update the file in the PR branch
    repo.update_file(
        cr_file.filename,
        f"Update {cr_file.filename}",
        response_content["updated_content"],
        repo.get_contents(cr_file.filename, ref=pr.head.ref).sha,
        branch=pr.head.ref,
    )

    # Update PR body
    updated_pr_body = f"{response_content['summary']}"
    pr.edit(body=updated_pr_body)

    return pr_url


def create_pr_for_change_control(repo, filename, content, summary):
    # Create a new branch
    source_branch = "main"
    target_branch = f"change-control-{filename.split('.')[0]}"
    sb = repo.get_branch(source_branch)
    repo.create_git_ref(ref=f"refs/heads/{target_branch}", sha=sb.commit.sha)

    # Create the file in the new branch
    repo.create_file(
        f"change-request-records/{filename}",
        f"Create Change Request {filename.split('-')[0]}",
        content,
        branch=target_branch,
    )

    # Create a pull request
    pr = repo.create_pull(
        title=f"Change Control: {summary}",
        body=f"This PR adds opens new Change Control Record: {filename}",
        head=target_branch,
        base=source_branch,
    )
    return pr.html_url


def get_design_matrix_content():
    g = get_github_qms_client()
    qms_repo = g.get_repo("seespine-2022/qms-docs")
    file_path = "design/design-matrix/design-matrix.json"

    try:
        file_content = qms_repo.get_contents(file_path, ref="main")
        decoded_content = base64.b64decode(file_content.content).decode("utf-8")
        return decoded_content
    except Exception as e:
        print(f"Error: Unable to get contents of {file_path}. Exception: {e}")
        sys.exit(1)
        return None


def get_fmea_content():
    g = get_github_qms_client()
    qms_repo = g.get_repo("seespine-2022/qms-docs")
    file_path = "risk/fmea/fmea.json"
    print("Repo: ", qms_repo)

    try:
        file_content = qms_repo.get_contents(file_path, ref="main")
        decoded_content = base64.b64decode(file_content.content).decode("utf-8")
        print("FMEA content: ", decoded_content)
        return decoded_content
    except Exception as e:
        print(f"Error: Unable to get contents of {file_path}. Exception: {e}")
        sys.exit(1)
        return None


def propose_design_matrix_updates(design_matrix_content, issue_body):
    client = get_openai_client()
    response = client.chat.completions.create(
        model=MODEL,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a QMS expert specialized in Design Traceability Matrix analysis. "
                    "Review the current DTM and propose updates if necessary based on the issue description. "
                    "In the issue, the result you produce has to go between the <!--qms-section:dtm--> and <!--/qms-section:dtm--> tags. "
                    "So keep the original text which gives you the options Yes/No/Other to the question whether to update. "
                    "Check which option is applicable, when there should be updates, add them in the existing textblock in the correct place. "
                    "Return the block between the tags in such a way that it can be inserted back between the tags. "
                    "For each ADD/UPDATE/DELETE, first say 'ADD/DELETE/UPDATE User Need x' and then add a formatted json object with the new/updated User Need, with ```json tags. "
                    "Prepend and append the tags with a line break."
                ),
            },
            {
                "role": "user",
                "content": f"Current DTM content:\n{design_matrix_content}\n\nIssue description:\n{issue_body}\n\n Analyze if any updates are needed to the DTM based on this issue.",
            },
        ],
        temperature=0.2,
    )
    return response.choices[0].message.content


def propose_fmea_updates(fmea_content, issue_body):
    client = get_openai_client()
    response = client.chat.completions.create(
        model=MODEL,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a QMS expert specialized in FMEA (Failure Mode and Effects Analysis). "
                    "Review the current FMEA and propose updates based on the issue description."
                    "Consider new failure modes, risks, or controls that might be needed."
                    "In the issue, the result you produce has to go between the <!--qms-section:fmea--> and <!--/qms-section:fmea--> tags. "
                    "So keep the original text which gives you the options Yes/No/Other to the question whether to update. "
                    "Check which option is applicable, when there should be updates, add them in the existing textblock in the correct place. "
                    "Return the block between the tags in such a way that it can be inserted back between the tags. "
                    "For each ADD/UPDATE/DELETE, first say 'ADD/DELETE/UPDATE Failure mode x' and then add a formatted json object with the new/updated Failure mode, with ```json tags. "
                    "Prepend and append the tags with a line break."
                ),
            },
            {
                "role": "user",
                "content": f"Current FMEA content:\n{fmea_content}\n\nIssue description:\n{issue_body}\n\nAnalyze if any updates are needed to the FMEA based on this issue.  ",
            },
        ],
        temperature=0.2,
    )
    return response.choices[0].message.content


def update_issue_section(issue_url, section_to_update, updates):
    g = get_github_current_client()
    # Handle both API URLs and web URLs
    if "api.github.com" in issue_url:
        # API URL format: https://api.github.com/repos/owner/repo/issues/number
        parts = issue_url.split("/repos/")[1].split("/")
        repo_name = f"{parts[0]}/{parts[1]}"
        issue_number = int(parts[3])
    else:
        # Web URL format: https://github.com/owner/repo/issues/number
        repo_name = "/".join(issue_url.split("/")[-4:-2])
        issue_number = int(issue_url.split("/")[-1])

    repo = g.get_repo(repo_name)
    issue = repo.get_issue(issue_number)
    issue_body = issue.body

    section_start = f"<!--{section_to_update}-->"
    section_end = f"<!--/{section_to_update}-->"

    if section_start in issue_body and section_end in issue_body:
        start_index = issue_body.index(section_start) + len(section_start)
        end_index = issue_body.index(section_end)
        new_issue_body = (
            issue_body[:start_index]  # Everything up to and including start tag
            + updates  # New content between tags
            + issue_body[end_index:]  # Everything from end tag onwards
        )

        issue.edit(body=new_issue_body)
        print(f"{section_to_update} section updated in the issue.")
    else:
        print(
            f"Error: Could not find the {section_to_update} section in the issue body."
        )


def main():
    try:
        target_repo = os.environ["INPUT_TARGET_REPO"]
        instruction = os.environ["INPUT_INSTRUCTION"]

        options = {
            0: "No clear instruction",
            1: "Create a change control record",
            2: "Update change control record",
            3: "Propose Design Traceability Matrix and/or FMAE updates",
        }

        option = analyze_instruction(instruction, options)
        print(f"Option: {option}")

        issue_title = os.environ.get("INPUT_ISSUE_TITLE")
        issue_body = os.environ.get("INPUT_ISSUE_BODY")
        issue_url = os.environ.get("INPUT_ISSUE_URL")
        pr_title = os.environ.get("INPUT_PR_TITLE")
        pr_body = os.environ.get("INPUT_PR_BODY")
        pr_url = os.environ.get("INPUT_PR_URL")

        if option == 1:
            g = get_github_qms_client()
            repo = g.get_repo(target_repo)

            cr_result = create_change_control_record(
                repo,
                instruction,
                issue_title,
                issue_body,
                issue_url,
                pr_title,
                pr_body,
                pr_url,
            )

            if cr_result:
                filename, content, summary = cr_result
                pr_url = create_pr_for_change_control(repo, filename, content, summary)

                if pr_url:
                    print(f"Change Control Record PR created: {pr_url}")
                    print(
                        f"::set-output name=result::<change_control_pr>{pr_url}</change_control_pr>"
                    )
                else:
                    print("Failed to create Change Control Record PR")
                    print(
                        "::set-output name=result::No Change Control Record PR created."
                    )
            else:
                print("Failed to create Change Control Record")
                print("::set-output name=result::No Change Control Record created.")
        elif option == 2:

            g = get_github_qms_client()
            repo = g.get_repo(target_repo)
            cc_pr_url_match = re.search(
                r"<change_control_pr>(.*?)</change_control_pr>", issue_body
            )
            if cc_pr_url_match:
                cc_pr_url = cc_pr_url_match.group(1)
                updated_pr_url = update_change_control_record(
                    repo,
                    cc_pr_url,
                    issue_title,
                    issue_body,
                    issue_url,
                    pr_title,
                    pr_body,
                    pr_url,
                )

                if updated_pr_url:
                    print(f"Change Control Record updated: {updated_pr_url}")
                    print(
                        f"::set-output name=result::<change_control_pr_updated>{updated_pr_url}</change_control_pr_updated>"
                    )
                else:
                    print("Failed to update Change Control Record")
                    print("::set-output name=result::No Change Control Record updated.")
            else:
                print(
                    "Error: Could not find Change Control Record PR URL in the issue body"
                )
                print(
                    "::set-output name=result::No Change Control Record updated, because no PR URL was found."
                )
        elif option == 3:
            print("Propose Design Traceability Matrix and/or FMAE updates")
            design_matrix_content = get_design_matrix_content()
            design_matrix_updates = propose_design_matrix_updates(
                design_matrix_content, issue_body
            )
            print("Design matrix updates: ", design_matrix_updates)

            update_issue_section(issue_url, "qms-section:dtm", design_matrix_updates)

            print("Design matrix updates added to the issue")

            fmea_content = get_fmea_content()
            print("FMEA content: ", fmea_content)
            fmea_updates = propose_fmea_updates(fmea_content, issue_body)
            print("FMEA updates: ", fmea_updates)

            update_issue_section(issue_url, "qms-section:fmea", fmea_updates)

            print("FMEA updates added to the issue")
            print(f"::set-output name=result::DTM and FMEA updates added to the issue")
        else:
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
                    g = get_github_qms_client()
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


if __name__ == "__main__":
    main()
