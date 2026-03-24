"""
AEGIS PHANTOM — Safe Deploy Script
Reads Anthropic key from .env and embeds it cleanly before Firebase deploy
Run: python deploy.py
"""
import os, subprocess
from dotenv import load_dotenv

# Load .env
script_dir = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(script_dir, '.env'))

ANTHROPIC_KEY = os.getenv("ANTHROPIC_API_KEY", "")

if not ANTHROPIC_KEY:
    print("❌ No ANTHROPIC_API_KEY in .env")
    exit(1)

print(f"✅ Key loaded: {ANTHROPIC_KEY[:12]}...")

# Read clean HTML
html_path = os.path.join(script_dir, 'aegis-command.html')
with open(html_path, 'r', encoding='utf-8') as f:
    html = f.read()

# Verify placeholder exists
if 'PASTE_NEW_KEY_HERE' not in html:
    print("⚠️ Placeholder not found — key may already be embedded")
else:
    html = html.replace('PASTE_NEW_KEY_HERE', ANTHROPIC_KEY)
    print("✅ Key embedded")

# Write back UTF-8 clean
with open(html_path, 'w', encoding='utf-8', newline='\n') as f:
    f.write(html)

print("✅ File written UTF-8 clean")

# Deploy
print("🚀 Deploying to Firebase...")
result = subprocess.run(
    ['firebase', 'deploy', '--only', 'hosting:aegis-phantom-ops', '--project', 'cybergrid'],
    cwd=script_dir
)

if result.returncode == 0:
    print("✅ DEPLOYED — https://aegis-phantom-ops.web.app/aegis-command.html")
else:
    print("❌ Deploy failed")
