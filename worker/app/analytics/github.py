"""Minimal GitHub REST client for learning-loop PRs (contents + pull requests only). The token
is a fine-grained token limited to this repository; it is never logged."""

import base64
from typing import Any

import httpx

from app.config import get_settings

API = "https://api.github.com"


class GitHubError(RuntimeError):
    pass


class GitHub:
    def __init__(
        self, token: str, repo: str, base: str = "main", http: httpx.Client | None = None
    ) -> None:
        self.repo, self.base = repo, base
        self._http = http or httpx.Client(timeout=20.0)
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def _call(self, method: str, path: str, **kw: Any) -> Any:
        resp = self._http.request(
            method, f"{API}/repos/{self.repo}{path}", headers=self._headers, **kw
        )
        if resp.status_code >= 300:
            raise GitHubError(
                f"{method} {path}: HTTP {resp.status_code}"
            )  # no body: may echo inputs
        return resp.json() if resp.content else {}

    def get_file(self, path: str) -> tuple[str, str]:
        d = self._call("GET", f"/contents/{path}", params={"ref": self.base})
        return base64.b64decode(d["content"]).decode("utf-8"), str(d["sha"])

    def create_branch(self, branch: str) -> None:
        sha = self._call("GET", f"/git/ref/heads/{self.base}")["object"]["sha"]
        self._call("POST", "/git/refs", json={"ref": f"refs/heads/{branch}", "sha": sha})

    def put_file(self, path: str, content: str, sha: str, branch: str, message: str) -> None:
        self._call(
            "PUT",
            f"/contents/{path}",
            json={
                "message": message,
                "branch": branch,
                "sha": sha,
                "content": base64.b64encode(content.encode()).decode(),
            },
        )

    def create_pr(self, branch: str, title: str, body: str) -> tuple[int, str]:
        d = self._call(
            "POST", "/pulls", json={"title": title, "head": branch, "base": self.base, "body": body}
        )
        return int(d["number"]), str(d["html_url"])

    def merge_pr(self, number: int) -> None:
        self._call("PUT", f"/pulls/{number}/merge", json={"merge_method": "squash"})


def from_settings() -> GitHub | None:
    st = get_settings()
    if not (st.github_token_config_pr and st.github_config_repo):
        return None
    return GitHub(st.github_token_config_pr, st.github_config_repo)
