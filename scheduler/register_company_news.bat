@echo off
REM Register DailyBriefing-CompanyNews task (replaces deprecated DailyBriefing-GeminiDocs)
REM Run as Administrator if needed

schtasks /delete /tn "DailyBriefing-GeminiDocs" /f 2>nul && echo Removed DailyBriefing-GeminiDocs || echo DailyBriefing-GeminiDocs not found
schtasks /delete /tn "DailyBriefing-Twitter" /f 2>nul && echo Removed DailyBriefing-Twitter || echo DailyBriefing-Twitter not found
schtasks /create /xml "<workspace>\DailyBriefing\scheduler\DailyBriefing-CompanyNews.xml" /tn "DailyBriefing-CompanyNews" /f && echo Created DailyBriefing-CompanyNews || echo FAILED to create DailyBriefing-CompanyNews
