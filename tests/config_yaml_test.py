"""Tests for config loading from YAML and automated pipeline pushes/PRs.

No API key needed.
"""

import os
import sys
import unittest
import tempfile
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import Config
from pipeline.config import PipelineConfig
from pipeline.runner import PipelineRunner, SliceResult
from providers.base import Provider, Response, ToolCall


class TestYAMLAndPipelinePR(unittest.TestCase):
    def setUp(self):
        # Clean environment variables that could interfere
        self.env_patches = {}
        for key in list(os.environ.keys()):
            if key.startswith("HARNESS_"):
                self.env_patches[key] = patch.dict(os.environ, {}, clear=False)
                del os.environ[key]

    def test_yaml_config_loading(self):
        yaml_content = """
harness:
  model: "openai/gpt-4o"
  permission_mode: "allowlist"
  max_steps: 42
pipeline:
  max_iterations: 15
  auto_push: true
  auto_pr: true
"""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Change directory to temp so it reads our temp yaml
            old_cwd = os.getcwd()
            os.chdir(tmpdir)
            try:
                with open(".harness.yaml", "w", encoding="utf-8") as f:
                    f.write(yaml_content)

                config = Config.load()
                pipeline_config = PipelineConfig.load()

                self.assertEqual(config.model, "openai/gpt-4o")
                self.assertEqual(config.permission_mode, "allowlist")
                self.assertEqual(config.max_steps, 42)

                self.assertEqual(pipeline_config.max_iterations, 15)
                self.assertTrue(pipeline_config.auto_push)
                self.assertTrue(pipeline_config.auto_pr)
            finally:
                os.chdir(old_cwd)

    def test_env_override_yaml(self):
        yaml_content = """
harness:
  model: "openai/gpt-4o"
pipeline:
  max_iterations: 15
"""
        with tempfile.TemporaryDirectory() as tmpdir:
            old_cwd = os.getcwd()
            os.chdir(tmpdir)
            try:
                with open(".harness.yaml", "w", encoding="utf-8") as f:
                    f.write(yaml_content)

                os.environ["HARNESS_MODEL"] = "anthropic/claude-3-5"
                os.environ["HARNESS_PIPELINE_MAX_ITERATIONS"] = "99"

                config = Config.load()
                pipeline_config = PipelineConfig.load()

                self.assertEqual(config.model, "anthropic/claude-3-5")
                self.assertEqual(pipeline_config.max_iterations, 99)
            finally:
                if "HARNESS_MODEL" in os.environ:
                    del os.environ["HARNESS_MODEL"]
                if "HARNESS_PIPELINE_MAX_ITERATIONS" in os.environ:
                    del os.environ["HARNESS_PIPELINE_MAX_ITERATIONS"]
                os.chdir(old_cwd)

    @patch("pipeline.worktree.create_worktree")
    @patch("pipeline.worktree.repo_root")
    @patch("pipeline.worktree.diff_stat")
    @patch("pipeline.worktree.push_branch")
    @patch("engine.builtin.github_tool.github_pr_create")
    def test_pipeline_runner_auto_push_and_pr(
        self, mock_pr_create, mock_push_branch, mock_diff_stat, mock_repo_root, mock_create_worktree
    ):
        mock_repo_root.return_value = "/fake/repo"
        mock_create_worktree.return_value = ("/fake/repo/.harness/worktrees/123", "pipeline/123")
        mock_diff_stat.return_value = "fake diff"
        mock_push_branch.return_value = "Pushed branch pipeline/123"
        mock_pr_create.return_value = "PR URL: https://github.com/fake/repo/pull/1"

        # Mocking stages and _implement_loop
        class FakeProvider(Provider):
            def complete(self, messages, tools):
                return Response(
                    text="Done.<promise>COMPLETE</promise>",
                    tool_calls=[],
                    assistant_message={"role": "assistant", "content": "Done.<promise>COMPLETE</promise>"}
                )

        runner = PipelineRunner(
            provider=FakeProvider(),
            registry=MagicMock(),
            harness_config=Config(permission_mode="auto"),
            pipeline_config=PipelineConfig(auto_push=True, auto_pr=True),
        )

        runner._run_stage = MagicMock(return_value="Done.<promise>COMPLETE</promise>")
        runner._implement_loop = MagicMock(return_value="complete")
        runner._run_outer_stages = MagicMock()

        result = runner.run("fix bug")

        self.assertEqual(result.status, "complete")
        mock_push_branch.assert_called_once_with("/fake/repo/.harness/worktrees/123", "pipeline/123")
        mock_pr_create.assert_called_once()
        self.assertIn("PR created", result.summary)


if __name__ == "__main__":
    unittest.main()
