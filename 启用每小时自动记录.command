#!/bin/bash
# 双击本文件 = 启用「每小时自动记录 Amazon/Boulanger 价格并同步到 GitHub」的系统定时任务
# 其余渠道由 GitHub Actions 在云端每小时自动抓取，与电脑开关机无关
(crontab -l 2>/dev/null | grep -v 'aiper-gtm/refresh'; echo "11 * * * * /bin/bash /Users/wayne/aiper-gtm/refresh-local.sh >> /Users/wayne/aiper-gtm/refresh.log 2>&1") | crontab -
echo "✅ 已启用：每小时自动记录 Amazon/Boulanger 价格并同步到云端看板"
echo "当前定时任务："
crontab -l | grep aiper-gtm
echo ""
echo "如需停用，终端执行： crontab -l | grep -v aiper-gtm | crontab -"
read -p "按回车关闭窗口..."
