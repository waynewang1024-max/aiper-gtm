#!/bin/bash
# 本地小时任务：抓 Amazon/Boulanger（云端机房 IP 被拦的渠道），推送到 GitHub 仓库
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
cd /Users/wayne/aiper-gtm || exit 1
git pull --rebase --autostash -q
/usr/bin/python3 refresh.py
git add aiper-gtm-feed-local.js
if ! git diff --cached --quiet; then
  git -c user.name="gtm-price-bot-local" -c user.email="wanyi@aiper.com" commit -q -m "chore: local feed (amazon/boulanger) $(date +'%Y-%m-%d %H:00')"
  git push -q
fi
