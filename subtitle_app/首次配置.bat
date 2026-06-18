@echo off
chcp 65001 >nul
cd /d "%~dp0"
if exist config.json (
    echo config.json 已存在，跳过复制。
) else if exist config.json.example (
    copy /Y config.json.example config.json >nul
    echo 已从 config.json.example 创建 config.json
    echo 请编辑 config.json，填写模型路径与 DeepSeek API Key。
) else (
    echo 未找到 config.json.example
)
pause
