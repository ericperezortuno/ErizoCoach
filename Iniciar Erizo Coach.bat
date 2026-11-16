@echo off
title Erizo Coach
cd /d "%~dp0"
set UV_LINK_MODE=copy
uv run --frozen erizocoach %*
echo.
pause
