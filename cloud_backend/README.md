# Google Apps Script Backend

This script acts as a bridge between ESP32 and Google Sheets.

## Features
- Stores sensor data in Sheets
- Sends commands to ESP32
- Allows remote control (fold/unfold)

## API Endpoints

GET COMMAND:
?action=get_command

SET COMMAND:
?action=set_command&value=1

CLEAR COMMAND:
?action=clear_command

## Deployment
1. Open Google Apps Script
2. Paste code
3. Deploy as Web App
4. Copy URL and paste in ESP32 code
