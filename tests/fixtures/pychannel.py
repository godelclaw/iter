from pathlib import Path


def receive():
    return "from python"


def send(content):
    Path(".runtime/pychannel-sent").write_text(content)
