import sys
sys.path.insert(0, "/home/cole/Projects/wouldrun")
from wouldrun.evaluate import evaluate_all
from wouldrun.event import Event
from wouldrun.workflow import parse_workflow

wf = parse_workflow("test.yml", """
on:
  push:
    branches: main
""")
results = evaluate_all([wf], Event("push", "refs/heads/feature"))
print(results[0].fires)
print(results[0].reasons)
