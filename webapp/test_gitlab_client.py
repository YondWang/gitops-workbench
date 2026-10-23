import json
import unittest
from io import BytesIO
from unittest.mock import patch
from urllib.error import HTTPError

from gitlab_client import GitLabClient, GitLabConfig, GitLabError


class GitLabClientErrorTest(unittest.TestCase):
    def test_expired_token_error_names_the_environment_variable(self):
        client = GitLabClient(
            GitLabConfig(
                "https://gitlab.example",
                "OS/simos",
                "glpat-expired",
                token_env="GITLAB_TOKEN_SIMOS",
            )
        )
        payload = {
            "error": "invalid_token",
            "error_description": "Token is expired. You can either do re-authorization or token refresh.",
        }
        error = HTTPError(
            "https://gitlab.example/api/v4/projects/OS%2Fsimos",
            401,
            "Unauthorized",
            {},
            BytesIO(json.dumps(payload).encode("utf-8")),
        )

        with patch("gitlab_client.urlopen", side_effect=error):
            with self.assertRaises(GitLabError) as caught:
                client.request("GET", client.project_api_path("/repository/tags"))

        message = str(caught.exception)
        self.assertEqual(caught.exception.status, 401)
        self.assertIn("invalid_token", message)
        self.assertIn("Token is expired", message)
        self.assertIn("GITLAB_TOKEN_SIMOS", message)
        self.assertIn("已过期", message)

    def test_feed_token_is_rejected_before_an_api_call(self):
        client = GitLabClient(
            GitLabConfig(
                "https://gitlab.example",
                "OS/simos",
                "glft-feed-token",
                token_env="GITLAB_TOKEN",
            )
        )

        with self.assertRaisesRegex(GitLabError, "Feed Token.*glpat-.*api scope"):
            client.tags()


if __name__ == "__main__":
    unittest.main()
