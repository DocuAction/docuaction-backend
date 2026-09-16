"""The runtime must say which commit it is (AUD-20260913-07).

/health carries `git_sha`, set from the GIT_SHA environment variable that the Dockerfile
bakes in at build time. Outside a CI-built image the value is the literal "unknown", which
the deploy gate treats as "not attributable", never as a pass.
"""
import os


def test_health_reports_git_sha_field(client):
    data = client.get("/health").json()
    assert "git_sha" in data
    assert isinstance(data["git_sha"], str) and data["git_sha"]


def test_health_reports_unknown_when_not_built_by_ci(client, monkeypatch):
    monkeypatch.delenv("GIT_SHA", raising=False)
    assert client.get("/health").json()["git_sha"] == "unknown"


def test_health_reports_the_baked_commit(client, monkeypatch):
    monkeypatch.setenv("GIT_SHA", "75383cf8ebfd5822aebbe50a61cad0a7c6f8ab96")
    assert client.get("/health").json()["git_sha"] == "75383cf8ebfd5822aebbe50a61cad0a7c6f8ab96"
