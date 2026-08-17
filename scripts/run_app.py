import subprocess
import sys

subprocess.run([sys.executable, "-m", "streamlit", "run", "app/ui.py"], check=True)

