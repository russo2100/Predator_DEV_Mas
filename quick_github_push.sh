#!/bin/bash
set -e

echo "🚀 Predator Bot → GitHub Migration"
echo "=================================="
echo ""

if ! command -v git &> /dev/null; then
  echo "❌ Git not installed. Install: apt-get update && apt-get install -y git"
  exit 1
fi

if [ ! -d "/root/Predator_DEV_Mas" ]; then
  echo "❌ /root/Predator_DEV_Mas not found"
  exit 1
fi

cd /root/Predator_DEV_Mas
echo "✓ In directory: $(pwd)"
echo ""

echo "📝 Creating .gitignore..."
cat > .gitignore << 'GITIGNORE_EOF'
# Environment
.env
.env.local
.env.*.local

# Python
__pycache__/
*.py[cod]
*$py.class
*.so
.Python
build/
dist/
downloads/
.eggs/
*.egg-info/
.installed.cfg
*.egg

# Virtual env
.venv/
venv/
ENV/
env/

# IDE
.vscode/
.idea/
*.swp
*.swo
*.iml

# Logs & runtime
*.log
*.jsonl
tradehistory.csv
shadowagentslog.jsonl
news.txt
*.db
.cache/
*.pid

# OS
.DS_Store
Thumbs.db

# Node
node_modules/
package-lock.json

# Jupyter
.ipynb_checkpoints/
*.ipynb
GITIGNORE_EOF
echo "✓ .gitignore created"
echo ""

echo "📝 Creating settings.py.example..."
mkdir -p src/config
cat > src/config/settings.py.example << 'SETTINGS_EOF'
"""
settings.py.example — Copy to settings.py and fill with YOUR keys.
DO NOT commit actual settings.py to GitHub.
"""

from pydantic import SecretStr
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    OPENROUTER_API_KEY: SecretStr
    EIA_API_KEY: str
    TELEGRAM_BOT_TOKEN: SecretStr
    TELEGRAM_CHAT_ID: str
    TINKOFF_TOKEN: SecretStr

    AIMODELANALYST: str = "qwen/qwen-2.5-72b-instruct"
    AIMODELRISK: str = "meta-llama/llama-2-70b-chat-hf"
    AIMODELPLANNER: str = "anthropic/claude-3.5-sonnet"

    OPENROUTERBASEURL: str = "https://openrouter.ai/api/v1"

    class Config:
        env_file = ".env"
        case_sensitive = True

settings = Settings()
SETTINGS_EOF
echo "✓ settings.py.example created"
echo ""

if [ ! -f README.md ]; then
  echo "📝 Creating minimal README.md..."
  cat > README.md << 'README_EOF'
# Predator — Trading Bot

Private trading bot repo.
- Uses Docker
- Secrets are stored in `.env` (ignored by git)
README_EOF
  echo "✓ README.md created"
else
  echo "✓ README.md already exists (keeping yours)"
fi
echo ""

echo "🔧 Git init/config..."
if [ ! -d ".git" ]; then
  git init
fi

if ! git config user.name >/dev/null; then
  git config user.name "Iskatel210"
fi
if ! git config user.email >/dev/null; then
  git config user.email "iskatel210@example.com"
fi

echo "✓ Checking .env is not tracked..."
if git ls-files | grep -q "^\.env$"; then
  echo "❌ ERROR: .env is tracked by git!"
  echo "Fix: git rm --cached .env && git commit -m 'Remove .env'"
  exit 1
fi
echo "✓ .env is safe"
echo ""

echo "📦 Stage + commit..."
git add -A
if git diff --cached --quiet; then
  echo "ℹ️ Nothing to commit (working tree clean)."
else
  git commit -m "Initial commit: Predator project (working state)"
fi
git log --oneline -1
echo ""

echo "🌐 GitHub remote setup..."
if git remote | grep -q "^origin$"; then
  echo "✓ origin already set:"
  git remote -v
else
  read -p "Enter GitHub username: " GITHUB_USER
  read -p "Enter repo name (default Predator_DEV_Mas): " REPO_NAME
  REPO_NAME=${REPO_NAME:-Predator_DEV_Mas}

  echo ""
  echo "📌 Create empty repo now: https://github.com/new"
  echo "   - Name: ${REPO_NAME}"
  echo "   - Private: YES"
  echo "   - Do NOT initialize with README/.gitignore/license"
  echo ""
  read -p "Press ENTER after repo is created..."

  git remote add origin "https://github.com/${GITHUB_USER}/${REPO_NAME}.git"
  git branch -M main
fi
echo ""

echo "📤 Pushing to GitHub..."
git push -u origin main

echo ""
echo "✅ DONE. Repo pushed."
echo "Check remote:"
git remote -v
