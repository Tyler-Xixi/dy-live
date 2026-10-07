# 离线卡密管理

- `generated_cards.csv`：100 个卡密明文，仅供管理员保存和发放。
- `cards.db`：卡密哈希数据库，用于重新导出客户端哈希。
- `generate_cards.py`：继续生成新卡密。
- `export_local_keys.py`：把数据库中的卡密哈希导出到项目根目录的 `local_license_keys.py`。

用户发布包中只需要 `dist\DYLiveAssistant`，不要把整个 `card_admin` 目录发给用户。

离线模式不会连接服务器，因此 `generated_cards.csv` 的状态不会自动更新。发放卡密后，需要管理员自行在表格中记录领取人和发放状态。同一卡密可能在不同电脑分别激活，这是纯本地校验无法避免的限制。

如果新增卡密，需要依次运行：

```powershell
python card_admin\generate_cards.py --count 100 --out card_admin\new_cards.csv
python card_admin\export_local_keys.py
```

然后重新执行 `构建EXE.bat`。
