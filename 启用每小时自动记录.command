#!/bin/bash
# 双击本文件 = 启用「每小时自动记录价格」的系统定时任务（crontab）
# 之后即使重启电脑也会每小时第 7 分钟自动抓取一次（电脑需处于开机状态）
(crontab -l 2>/dev/null | grep -v 'aiper-gtm/refresh.py'; echo "7 * * * * /usr/bin/python3 /Users/wayne/aiper-gtm/refresh.py >> /Users/wayne/aiper-gtm/refresh.log 2>&1") | crontab -
echo "✅ 已启用：每小时自动记录 Aiper 各渠道价格"
echo "当前定时任务："
crontab -l | grep aiper-gtm
echo ""
echo "如需停用，终端执行： crontab -l | grep -v aiper-gtm | crontab -"
read -p "按回车关闭窗口..."
