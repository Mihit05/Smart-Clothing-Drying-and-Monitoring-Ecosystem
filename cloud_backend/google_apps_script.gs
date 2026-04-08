const SECRET_KEY = "SECRET_KEY_GOOGLE_SHEETS"; // It act as a confirmation brige btw the esp32 code and google sheet to know whether it is contacting the same file or not.
const COMMAND_CELL = "H2";   // Numeric command cell: 0 = UNFOLD, 1 = FOLD
const SHEET_NAME = "Sheet1";

function doGet(e) { return handleRequest(e); }
function doPost(e) { return handleRequest(e); }

function handleRequest(e) {
  try {
    var key = (e.parameter && e.parameter.key) ? String(e.parameter.key) : "";
    if (key !== SECRET_KEY) {
      return ContentService.createTextOutput("Unauthorized")
                           .setMimeType(ContentService.MimeType.TEXT);
    }

    var ss = SpreadsheetApp.getActiveSpreadsheet();
    var sheet = ss.getSheetByName(SHEET_NAME);
    if (!sheet) sheet = ss.getActiveSheet(); // fallback

    var action = (e.parameter && e.parameter.action) ? String(e.parameter.action) : "";

    if (action === "get_command") {
      var rawVal = String(sheet.getRange(COMMAND_CELL).getValue()).trim();
      var val = (rawVal === "" ? "" : rawVal);

      // JSON debug mode: ?format=json
      if (e.parameter && e.parameter.format === "json") {
    return ContentService
            .createTextOutput('{"value":"' + String(val) + '"}')
            .setMimeType(ContentService.MimeType.JSON);
}


      if (e.parameter && e.parameter.clear_after === "1") {
        sheet.getRange(COMMAND_CELL).clearContent();
      }
      return ContentService
              .createTextOutput(String(val))
              .setMimeType(ContentService.MimeType.TEXT);
    }

    // SET COMMAND (set numeric value in H2) - use &value=0 or &value=1
    // Usage: ?key=...&action=set_command&value=1
    
    if (action === "set_command") {
      var v = (e.parameter && typeof e.parameter.value !== 'undefined') ? String(e.parameter.value) : "";
      sheet.getRange(COMMAND_CELL).setValue(v);
      return ContentService
              .createTextOutput("OK: set value '" + v + "'")
              .setMimeType(ContentService.MimeType.TEXT);
    }

   
    // CLEAR COMMAND - Usage: ?key=...&action=clear_command
   
    if (action === "clear_command") {
      sheet.getRange(COMMAND_CELL).clearContent();
      return ContentService
              .createTextOutput("OK: cleared")
              .setMimeType(ContentService.MimeType.TEXT);
    }

    
    // DEFAULT: append moisture row (timestamp, moisture_raw, moisture_pct)
    // Parameters: timestamp, moisture_raw, moisture_pct
   
    var timestamp = e.parameter.timestamp || (new Date()).toISOString();
    var moisture_raw = e.parameter.moisture_raw || "";
    var moisture_pct = e.parameter.moisture_pct || "";
    var temperature   = e.parameter.temp_c        || "";   // changed name
    var humidity      = e.parameter.hum_pct       || "";   

    sheet.appendRow([ timestamp, moisture_raw, moisture_pct, temperature, humidity ]);
    return ContentService.createTextOutput("OK")
                         .setMimeType(ContentService.MimeType.TEXT);

  } catch (err) {
    return ContentService.createTextOutput("Error: " + err)
                         .setMimeType(ContentService.MimeType.TEXT);
  }
}

// ---------------------- UI helpers for Buttons / Menu ----------------------
// Use these functions to add UI buttons or menu commands (Fold / Unfold / Toggle).

function setCommandValue_(v) {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sheet = ss.getSheetByName(SHEET_NAME);
  if (!sheet) sheet = ss.getActiveSheet();
  sheet.getRange(COMMAND_CELL).setValue(String(v));
  ss.toast('Command set to ' + String(v) + ' (' + (v === '1' ? 'FOLD' : v === '0' ? 'UNFOLD' : v) + ')', 'ESP Command', 3);
  return 'OK';
}
function fold()    { return setCommandValue_('1'); }  // set H2 = 1 and KEEP it
function unfold()  { return setCommandValue_('0'); }  // set H2 = 0 and KEEP it

// Toggle: if H2 is '1' -> set '0'; if '0' or blank -> set '1'.
// Useful if you prefer a single button that toggles fold/unfold.
function toggleCommand() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sheet = ss.getSheetByName(SHEET_NAME);
  if (!sheet) sheet = ss.getActiveSheet();
  var cur = String(sheet.getRange(COMMAND_CELL).getValue()).trim();
  var next;
  if (cur === '1') next = '0';
  else next = '1';
  sheet.getRange(COMMAND_CELL).setValue(next);
  ss.toast('Toggled command: ' + next + ' (' + (next === '1' ? 'FOLD' : 'UNFOLD') + ')', 'ESP Command', 3);
  return 'OK';
}

function clearCommandUI() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sheet = ss.getSheetByName(SHEET_NAME);
  if (!sheet) sheet = ss.getActiveSheet();
  sheet.getRange(COMMAND_CELL).clearContent();
  ss.toast('Command cleared', 'ESP Command', 3);
  return 'OK';
}

function onOpen(e) {
  SpreadsheetApp.getUi()
    .createMenu('ESP Servo')
    .addItem('Fold (set H2 = 1)', 'fold')
    .addItem('Unfold (set H2 = 0)', 'unfold')
    .addItem('Toggle fold/unfold', 'toggleCommand')
    .addSeparator()
    .addItem('Clear command (clear H2)', 'clearCommandUI')
    .addToUi();
}
