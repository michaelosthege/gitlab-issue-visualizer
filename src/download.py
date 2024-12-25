import logging
import sys
from typing import Mapping, Sequence

import gitlab.v4
import gitlab.v4.objects
sys.path.append("..")

import gitlab
import pickle
import tomllib
from pathlib import Path
import time

from model.classes import *
from src.utils import time_string

_log = logging.getLogger(__name__)
logging.basicConfig(level=logging.DEBUG)

projects_raw = []

with open("../settings/config.toml", mode="rb") as filehandle:
    config = tomllib.load(filehandle)


def main():
    (epics_raw, issues) = download()
    epics: dict[int, Epic] = parse_epics(epics_raw)
    #print(epics)
    links_related, links_blocking, links_parent = parse_links(issues)

    # dump
    print("***")
    print("Dump parsed stuff")
    Path("../pickles").mkdir(parents=True, exist_ok=True)
    pickle.dump(issues, open("../pickles/issues_conv.p", "wb"))
    pickle.dump(links_related, open("../pickles/links_related.p", "wb"))
    pickle.dump(links_blocking, open("../pickles/links_blocking.p", "wb"))
    pickle.dump(links_parent, open("../pickles/links_parent.p", "wb"))
    pickle.dump(epics, open("../pickles/epics_conv.p", "wb"))
    print("***")


def download() -> tuple[list[gitlab.base.RESTObject], dict[int, Issue]]:
    # private token or personal token authentication (GitLab.com)

    url = config['server']['url']

    gl = gitlab.Gitlab(url, config['server']['private_token'])
    gq = gitlab.GraphQL(url, token=config['server']['private_token'])

    print("Authenticate...")
    gl.auth()
    print("Successful!")

    group_no = config['server']['group_no']
    project_group = gl.groups.get(group_no)
    print(f"Downloading things from {url}, group {group_no} \"{project_group.name}\"...")

    projects = project_group.projects.list()
    print("** Projects in group: ({n}) **".format(n=len(projects)))

    try:
        epics_raw = [e for e in project_group.epics.list(get_all=True, scope='all')]
    except gitlab.exceptions.GitlabListError:
        epics_raw = []
    print("** Epics in group: ({n}) **".format(n=len(epics_raw)))


    projects_conf = config['projects']
    if not projects_conf:
        projects_take = projects
    else:
        projects_take = projects_conf
    print(f"** Requesting Issues in {len(projects_take)} projects...**")

    issues: dict[int, Issue] = {}
    for p in projects_take:
        issues.update(download_project_issues(gl, gq, p['project_no']))

    return epics_raw, issues


def download_project_issues(gl: gitlab.Gitlab, gq: gitlab.GraphQL, project_id: int) -> dict[int, Issue]:
    issues = {}
    for riss in gl.projects.get(project_id).issues.list(all=True):
        issues[riss.id] = to_issue(riss, gq)

    print(f"** Issues in project {project_id}: ({len(issues)}) **")
    return issues


def to_issue(iss: gitlab.v4.objects.ProjectIssue, client: gitlab.GraphQL) -> Issue:
    q = """
    query workItem {
    workItem(id: "gid://gitlab/WorkItem/%i") {
        #id
        #iid
        #title
        #state
        #webUrl
        widgets {
        #... on WorkItemWidgetLabels {
        #    labels {
        #    nodes {
        #        id
        #        title
        #        description
        #        color
        #        textColor
        #        __typename
        #    }
        #    __typename
        #    }
        #    __typename
        #}
        #... on WorkItemWidgetTimeTracking {
        #    timeEstimate
        #    totalTimeSpent
        #    __typename
        #}
        ... on WorkItemWidgetHierarchy {
            hasParent
            parent {
                id
                #iid
                #title
            }
            __typename
        }
        __typename
        }
        __typename
    }
    __typename
    }
    """ % (iss.id,)
    res = client.execute(q)
    # Extract parent issues from the corresponding entry in the widget list
    parent = None
    for widget in res["workItem"]["widgets"]:
        if widget["__typename"] != "WorkItemWidgetHierarchy":
            continue
        if widget["hasParent"]:
            parent = int(widget["parent"]["id"].split("/")[-1])

    wi = Issue(
        uid=iss.id,
        iid=iss.iid,
        project_id=iss.project_id,
        title=iss.title,
        status={
            "closed": Status.CLOSED,
            "opened": Status.OPENED,
        }[iss.state],
        links=iss.links.list(),
        url=iss.web_url,
        has_iteration=bool(getattr(iss, "iteration", [])),
        epic_id=getattr(iss, "epic_iid", None),
        parent=parent,
    )
    return wi


def parse_epics(epics_from_gl) -> dict[int, Epic]:
    print("Parsing epics...")
    epics_parsed: list[Epic] = []
    for epic in epics_from_gl:
        if epic.state == 'opened':
            s = Status.OPENED
        else:
            s = Status.CLOSED

        def count_closed(issue_list) -> int:
            c = 0
            for issue in issue_list:
                if issue.state == 'closed':
                    c = c + 1
            return c

        epic_issues = epic.issues.list(get_all=True)

        n = count_closed(epic_issues)
        m = len(epic_issues)

        #print(s, epic.iid, epic.title, n, '/', m)

        # if there are issues attached, get their uids
        if n > 0:
            issue_uids = {issue.id for issue in epic_issues}
        else:
            issue_uids = None

        epics_parsed.append(Epic(s, epic.iid, epic.title, epic.labels, epic.description, n, m, issue_uids))
    return {item.uid: item for item in epics_parsed}


def parse_links(issues: Mapping[int, Issue]) -> tuple[list[Link], list[Link], list[Link]]:
    print("'************\n\n************\nLinking...")
    verbose = False
    links_blocking: list[Link] = []
    links_related: list[Link] = []
    links_parent: list[Link] = []

    for src in issues.values():
        for link in src.links:
            dst = issues.get(link.id)
            if dst is None:
                _log.warning("Can't find target %s of link in %i/%i (%s).", link.id, src.project_id, src.iid, src.title)
                continue

            if link.link_type == 'is_blocked_by':
                print("skip\n" if verbose else "s", end='')
                break
            elif link.link_type == 'blocks':
                # here we have a blocker
                link_conv = Link(src, dst, Link_Type.BLOCKS)
                links_blocking.append(link_conv)
                print(f"Added: {link_conv}\n" if verbose else ".", end="")

            elif link.link_type == 'relates_to':
                # check for duplication
                dub = False
                for l in links_related:
                    if l.target is None:
                        dub = True
                    if l.target.uid == src.uid:
                        dub = True

                if not dub:
                    link_conv = Link(src, dst, Link_Type.RELATES_TO)
                    links_related.append(link_conv)

                print(f"Added: {link_conv}\n" if verbose else ".", end="")

        if src.parent:
            links_parent.append(Link(src, issues.get(src.parent), Link_Type.IS_CHILD_OF))

    _log.info("Found %i relations, %i blocking and %i parent relationships.", len(links_related), len(links_blocking), len(links_parent))
    return links_related, links_blocking, links_parent


if __name__ == "__main__":
    start = time.time()
    main()
    finish = time.time()
    time_taken = finish - start
    print(f"download.py took {time_string(time_taken)}")
