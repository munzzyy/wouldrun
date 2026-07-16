import sys
sys.path.insert(0, "/home/cole/Projects/wouldrun")
from wouldrun.globmatch import translate

print(translate(r"[a\]b]"))
