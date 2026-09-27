"""Content probes for build and deployment files."""

from pathlib import Path

from repotree.buildfiles import classify_file


def _file(tmp_path: Path, name: str, text: str) -> tuple[str, str]:
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    relative = path.relative_to(tmp_path).as_posix()
    return classify_file(path, relative)


def test_named_build_files_skip_the_probe(tmp_path: Path) -> None:
    assert _file(tmp_path, "Makefile", "")[0] == "build"
    assert _file(tmp_path, "Dockerfile.api", "")[0] == "build"
    assert _file(tmp_path, "rules.mk", "")[0] == "build"
    assert _file(tmp_path, "PageCompiler.make", "")[0] == "build"
    assert _file(tmp_path, "Makefile.am", "")[0] == "build"
    assert _file(tmp_path, "Makefile.in", "")[0] == "build"
    assert _file(tmp_path, "main.tf", "")[0] == "build"
    assert _file(tmp_path, "stack.tf.json", "")[0] == "build"
    assert _file(tmp_path, "ansible.cfg", "[defaults]\n")[0] == "build"
    assert _file(tmp_path, ".circleci/config.yml", "version: 2\n")[0] == "build"
    assert _file(tmp_path, ".gitlab-ci.yml", "stages: [test]\n")[0] == "build"
    assert _file(tmp_path, "Jenkinsfile.prod", "echo hi\n")[0] == "build"


def test_package_marker_wins_over_a_yaml_probe(tmp_path: Path) -> None:
    text = "on:\n  push:\njobs:\n  test:\n    runs-on: ubuntu-latest\n"
    assert _file(tmp_path, "pnpm-workspace.yaml", text) == ("marker", "")


def test_cloudformation_yaml_and_json(tmp_path: Path) -> None:
    yaml = "Resources:\n  Bucket:\n    Type: AWS::S3::Bucket\n"
    raw = '{"Resources": {"Bucket": {"Type": "AWS::S3::Bucket"}}}\n'
    assert _file(tmp_path, "web.yaml", yaml)[0] == "build"
    assert _file(tmp_path, "app.json", raw)[0] == "build"


def test_azure_arm_json(tmp_path: Path) -> None:
    raw = '{"$schema": "https://schema.management.azure.com/schemas/2019-04-01/deploymentTemplate.json#", "resources": [{"type": "Microsoft.Storage/storageAccounts"}]}\n'
    assert _file(tmp_path, "main.json", raw)[0] == "build"


def test_github_workflow_any_name(tmp_path: Path) -> None:
    text = "on:\n  push:\njobs:\n  test:\n    runs-on: ubuntu-latest\n"
    assert _file(tmp_path, "ci/pipeline.yml", text)[0] == "build"
    nested = "jobs:\n  test:\n    on: push\n"
    assert _file(tmp_path, "not-a-workflow.yml", nested)[0] == "file"


def test_ansible_playbook_and_role_task(tmp_path: Path) -> None:
    play = "- hosts: all\n  tasks:\n    - name: Ping\n      ansible.builtin.ping:\n"
    role = "- name: Install\n  ansible.builtin.apt:\n    name: nginx\n"
    assert _file(tmp_path, "webservers.yml", play)[0] == "build"
    assert _file(tmp_path, "roles/web/tasks/main.yml", role) == ("file", "other")


def test_kubernetes_and_compose(tmp_path: Path) -> None:
    manifest = "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: web\n"
    compose = "services:\n  web:\n    image: nginx\n"
    assert _file(tmp_path, "deploy.yaml", manifest)[0] == "build"
    assert _file(tmp_path, "stack.yml", compose)[0] == "build"


def test_docker_and_make_by_content(tmp_path: Path) -> None:
    image = "FROM alpine:3.20\nRUN echo hi\n"
    readme = "FROM alpine:3.20\nRUN echo hi\n"
    made = ".PHONY: all\nall:\n\techo hi\n"
    conditional = "ifndef CC\nCC = gcc\nendif\n"
    recipe = "dist-hook:\n\trm -rf tests/log\n"
    go = "func Get(ctx context.Context) string {\n\treturn \"\"\n}\n"
    neon = "parameters:\n\tignoreErrors:\n"
    words = "ifdef\nifdefed\n"
    assert _file(tmp_path, "images/api", image)[0] == "build"
    assert _file(tmp_path, "README.md", readme) == ("file", "other")
    assert _file(tmp_path, "buildfile", made)[0] == "build"
    assert _file(tmp_path, "flags.inc", conditional)[0] == "build"
    assert _file(tmp_path, "hook.frag", recipe) == ("file", "other")
    assert _file(tmp_path, "api.go", go) == ("file", "Go")
    assert _file(tmp_path, "baseline.neon", neon) == ("file", "other")
    assert _file(tmp_path, "words.txt", words) == ("file", "other")


def test_nul_prefix_is_not_probed(tmp_path: Path) -> None:
    path = tmp_path / "weird.txt"
    path.write_bytes(b"\0FROM alpine\nRUN true\n")
    assert classify_file(path, "weird.txt") == ("file", "other")
