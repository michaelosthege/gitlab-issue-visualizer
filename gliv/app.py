import base64
import json
import pickle
from pathlib import Path

import streamlit as st

from gliv.classes import Issue, Link, Status
from gliv import render

DP_PICKLES = Path(__file__).parent.parent / "pickles"


def get_projects() -> dict[int, str]:
    with open(DP_PICKLES / "projects.json", "r") as jfile:
        return {
            int(pid) : pname
            for pid, pname in json.load(jfile).items()
        }


def embed_svg(svg: str, zoom: float=1):
    """Embed SVG as an object to make links clickable."""
    b64 = base64.b64encode(svg.encode('utf-8')).decode("utf-8")
    maxwidth = 100 * zoom
    html = rf'<object alt="issue graph" style="max-width: {maxwidth}%;" data="data:image/svg+xml;base64,{b64}"></object>'
    st.write(html, unsafe_allow_html=True)


def run():
    st.set_page_config(layout="wide")
    st.sidebar.header("GitLab Issue Visualizer")

    issues: dict[int, Issue] = pickle.load(open(DP_PICKLES / "issues_conv.p", 'rb'))
    links_related: list[Link] = pickle.load(open(DP_PICKLES / "links_related.p", 'rb'))
    links_blocking: list[Link] = pickle.load(open(DP_PICKLES / "links_blocking.p", 'rb'))
    links_parent: list[Link] = pickle.load(open(DP_PICKLES / "links_parent.p", 'rb'))

    known_labels = {
        l
        for i in issues.values()
        for l in i.labels
    }

    # Graph customization settings
    pnames_pids = {pname:pid for pid,pname in get_projects().items()}
    pnames_selected = st.sidebar.multiselect(
        "Choose projects",
        options=sorted(pnames_pids),
        default=pnames_pids,
    )
    pids_selected = {pnames_pids[pid] for pid in pnames_selected}
    with_closed = st.sidebar.selectbox(
        "For closed issues",
        options=["titles", "numbers", "hide"],
        index=2,
    )
    graph_zoom = st.sidebar.slider("Zoom factor", min_value=1.0, max_value=3.0, value=1.0, step=0.1)
    selected_labels = st.sidebar.multiselect(
        "Select labels",
        options=sorted(known_labels),
        default=known_labels,
    )
    show_unlabeled = st.sidebar.checkbox("Include unlabeled issues", value=True)

    # Filter according to the user settings
    def issue_filter(i: Issue) -> bool:
        if i.status == Status.CLOSED and with_closed == "hide":
            return False
        if selected_labels:
            if not i.labels and show_unlabeled:
                return i.project_id in pids_selected
            if not any(l in selected_labels for l in i.labels):
                return False
        return i.project_id in pids_selected

    issues_selected = {i:iss for i, iss in issues.items() if issue_filter(iss)}
    st.sidebar.write(f"Selected {len(issues_selected)} issues from {len(pids_selected)} projects.")

    # Draw the SVG and embed it on the page
    svgpath = render.render_issues_with_links(
        issues=issues_selected,
        epics={},
        list_related=links_related,
        list_blocks=links_blocking,
        list_parent=links_parent,
        exclude_closed_issues=with_closed == "numbers",
    )
    embed_svg(svgpath.read_text(encoding="utf-8"), zoom=graph_zoom)
    return


if __name__ == "__main__":
    run()
