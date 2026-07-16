import sys
sys.path.insert(0, "/home/cole/Projects/wouldrun")
from wouldrun.evaluate import evaluate_all
from wouldrun.event import Event
from wouldrun.workflow import parse_workflow

wf = parse_workflow("test.yml", """
on:
  push: main
""")
try:
    evaluate_all([wf], Event("push", "refs/heads/main"))
except Exception as e:
    import traceback
    traceback.print_exc()
