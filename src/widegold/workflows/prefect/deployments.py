"""Deployment blueprint.

The production deployment will wrap the real `analysis_pipeline` and use the approved
18:10 Asia/Shanghai schedule plus a China A-share trading-calendar guard inside the flow.
This file intentionally avoids auto-creating infrastructure during import.
"""

CLOSE_ANALYSIS_CRON = "10 18 * * 1-5"
CLOSE_ANALYSIS_TIMEZONE = "Asia/Shanghai"
WORK_POOL = "widegold-process"
