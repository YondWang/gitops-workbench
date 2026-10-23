#!/usr/bin/env python3
"""Configure Feature checkout authentication for the GitLab HTTPS endpoint."""
import os
import subprocess
import sys
from urllib.parse import quote


def configure(server_url, server_host, token):
    protocol, address = server_url.rstrip("/").split("://", 1)
    base = f"{protocol}://gitlab-ci-token:{quote(token, safe='')}@{address}/"
    # Match the complete origin first: host-only prefixes do not match :9900.
    for old_url in (
        server_url.rstrip("/") + "/",
        f"https://{server_host}/",
        f"http://{server_host}/",
        f"ssh://git@{server_host}:22222/",
        f"git@{server_host}:",
    ):
        subprocess.run(
            ["git", "config", "--global", "--add", f"url.{base}.insteadOf", old_url],
            check=True,
        )
    return base


if __name__ == "__main__":
    configure(sys.argv[1], sys.argv[2], os.environ["CI_JOB_TOKEN"])
